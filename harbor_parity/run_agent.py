# Collect PRBench replies from a coding agent, for parity runs against the Harbor
# adapter (harbor-framework/adapters, src/prbench). The replies are then graded by
# this repository's evals.py with final_response_source: prefilled.
#
# The conversation comes from this repository's own code (process_reference_texts,
# process_row_get_convo) on the pinned dataset revision. It is written to
# /workspace/conversation.md the way the Harbor task holds it, and the agent gets the
# Harbor task's instruction text. Claude Code is launched the way Harbor's claude-code
# agent launches it.
#
# Examples (from the repository root):
# python harbor_parity/run_agent.py --dry_run_dir /tmp/prbench-tasks          # no agent, no cost
# python harbor_parity/run_agent.py --model claude-haiku-5-5 --trial 1 --out harbor_runs/run1
# python evals.py harbor_runs/run1/config_finance.yaml
# python evals.py harbor_runs/run1/config_legal.yaml

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from datasets import load_dataset  # noqa: E402

from util import process_reference_texts, process_row_get_convo  # noqa: E402

# The revision the Harbor adapter pins (src/prbench/dataset.py).
REVISION = "01947c6ef913df518e6e52626a7b29f132b12d4f"
DOMAINS = ("finance", "legal")
HERE = Path(__file__).resolve().parent
# A copy of the adapter's task-template/instruction.md; the adapter appends the canaries.
INSTRUCTION = (HERE / "instruction.md").read_text()

# The Harbor task image (task-template/environment/Dockerfile) plus Claude Code,
# installed the way Harbor's claude-code agent installs it.
DOCKERFILE = """FROM python:3.11-slim
WORKDIR /workspace
RUN pip install --no-cache-dir openai==3.49.0
RUN apt-get update && apt-get install -y --no-install-recommends \\
        curl bash ca-certificates nodejs npm procps \\
    && rm -rf /var/lib/apt/lists/*
ARG CLAUDE_CODE_VERSION=
RUN curl -fsSL https://downloads.claude.ai/claude-code-releases/bootstrap.sh \\
        | bash -s -- ${CLAUDE_CODE_VERSION} \\
    && /root/.local/bin/claude --version
ENV PATH="/root/.local/bin:${PATH}"
"""

RUN_SCRIPT = (
    "mkdir -p $CLAUDE_CONFIG_DIR/debug $CLAUDE_CONFIG_DIR/projects/-app "
    "$CLAUDE_CONFIG_DIR/shell-snapshots $CLAUDE_CONFIG_DIR/statsig "
    "$CLAUDE_CONFIG_DIR/todos $CLAUDE_CONFIG_DIR/skills && "
    'printf "%s" "$PRBENCH_INSTRUCTION" | claude --verbose --output-format=stream-json '
    "--permission-mode=bypassPermissions --print 2>&1 | tee /logs/agent/claude-code.txt; "
    'chown -R "$HOST_UID:$HOST_GID" /workspace /logs/agent'
)


def render_conversation(convo):
    """conversation.md as the Harbor adapter writes it (render_conversation in adapter.py)."""
    total = len(convo)
    body = "\n\n".join(
        f"===== {role.upper()} (message {n} of {total}) =====\n\n{content}"
        for n, (role, content) in enumerate(convo, 1)
    )
    return body + "\n"


def build_tasks(wanted=None):
    tasks = []
    for domain in DOMAINS:
        hard = load_dataset("ScaleAI/PRBench", split=f"{domain}_hard", revision=REVISION).to_pandas()
        hard_canaries = dict(zip(hard["task"], hard["canary"]))
        df = load_dataset("ScaleAI/PRBench", split=domain, revision=REVISION).to_pandas()
        df = df.apply(process_reference_texts, axis=1)
        for _, row in df.iterrows():
            task_id = f"{domain}-{row['task']}"
            if wanted is not None and task_id not in wanted:
                continue
            canaries = [row["canary"]] + ([hard_canaries[row["task"]]] if row["task"] in hard_canaries else [])
            instruction = INSTRUCTION + "\n" + "".join(f"<!-- canary: {c} -->\n" for c in canaries)
            tasks.append(
                {
                    "task_id": task_id,
                    "domain": domain,
                    "task": row["task"],
                    "instruction": instruction,
                    "conversation": render_conversation(process_row_get_convo(row)),
                }
            )
    return tasks


class ClaudeCodeRunner:
    """One container per task, with the Harbor task's resource limits (1 CPU, 2 GB)."""

    def __init__(self, model, version, timeout_sec, log_dir):
        self.model = model
        self.timeout_sec = timeout_sec
        self.log_dir = Path(log_dir)
        self.image = f"prbench-claude-code:{version or 'latest'}"
        assert os.environ.get("ANTHROPIC_API_KEY"), "Please set ANTHROPIC_API_KEY"
        with tempfile.TemporaryDirectory() as context:
            Path(context, "Dockerfile").write_text(DOCKERFILE)
            subprocess.run(
                ["docker", "build", "-q", "-t", self.image]
                + ["--build-arg", f"CLAUDE_CODE_VERSION={version or ''}", context],
                check=True,
            )

    def __call__(self, task):
        trial = self.log_dir / task["task_id"]
        workspace, logs = trial / "workspace", trial / "agent"
        workspace.mkdir(parents=True)
        logs.mkdir()
        (workspace / "conversation.md").write_text(task["conversation"], encoding="utf-8")
        env = {
            "PRBENCH_INSTRUCTION": task["instruction"],
            "ANTHROPIC_MODEL": self.model,
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "IS_SANDBOX": "1",
            "CLAUDE_CONFIG_DIR": "/logs/agent/sessions",
            "HOST_UID": str(os.getuid()),
            "HOST_GID": str(os.getgid()),
        }
        for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"):
            if os.environ.get(name):
                env[name] = os.environ[name]
        command = ["docker", "run", "--rm", "--cpus", "1", "--memory", "2g"]
        command += ["-v", f"{workspace.resolve()}:/workspace", "-v", f"{logs.resolve()}:/logs/agent"]
        for name in env:
            command += ["-e", name]
        command += [self.image, "bash", "-c", RUN_SCRIPT]
        status = "ok"
        try:
            subprocess.run(command, env={**os.environ, **env}, capture_output=True, timeout=self.timeout_sec)
        except subprocess.TimeoutExpired:
            status = "timeout"
        reply = workspace / "response.txt"
        text = reply.read_text(encoding="utf-8", errors="replace") if reply.is_file() else None
        return text, {"status": status, **self._final_result(logs / "claude-code.txt")}

    @staticmethod
    def _final_result(stream):
        if not stream.is_file():
            return {}
        for line in reversed(stream.read_text(errors="replace").splitlines()):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("type") == "result":
                return {key: event.get(key) for key in ("total_cost_usd", "num_turns", "is_error")}
        return {}


def write_configs(out, args):
    """One prefilled evals.py config per domain; the cache is keyed by trial_number."""
    for domain in DOMAINS:
        config = {
            "judge_model_name": args.judge_model,
            "base_url": args.judge_base_url,
            "litellm_key_path": "",
            "filename": str(out / f"replies_{domain}"),
            "split_name": domain,
            "dataset_revision": REVISION,
            "task_ids_path": str(Path(args.ids).resolve()),
            "trial_number": args.trial,
            "debug": False,
            "web_search": False,
            "background": False,
            "final_response_source": "prefilled",
            "timeout_seconds": 300,
            "cache": True,
            "response_model_names": [f"{args.agent}/{args.model}"],
        }
        (out / f"config_{domain}.yaml").write_text(
            "".join(f"{key}: {json.dumps(value)}\n" for key, value in config.items())
        )


def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=str, default=None, help="model id passed to the agent")
    parser.add_argument("--agent", type=str, default="claude-code", choices=["claude-code"])
    parser.add_argument("--agent_version", type=str, default=os.environ.get("CLAUDE_CODE_VERSION"))
    parser.add_argument("--ids", type=str, default=str(HERE / "parity_task_ids.txt"), help="task ids to run")
    parser.add_argument("--all", action="store_true", help="every task of both domains instead of --ids")
    parser.add_argument("--trial", type=int, default=1, help="trial_number of the run (separate judge cache)")
    parser.add_argument("--judge_model", type=str, default="gpt-4.1")
    parser.add_argument("--judge_base_url", type=str, default=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    parser.add_argument("--num_threads", type=int, default=4, help="number of agent containers run in parallel")
    parser.add_argument("--timeout_sec", type=float, default=1800.0, help="agent time limit per task (task.toml)")
    parser.add_argument("--out", type=str, default=None, help="run directory (default: harbor_runs/<timestamp>)")
    parser.add_argument("--dry_run_dir", type=str, default=None, help="only write <task_id>/ instruction and conversation")
    return parser.parse_args()


def main():
    args = get_args()
    wanted = None if args.all else set(Path(args.ids).read_text().split())
    tasks = build_tasks(wanted)
    if wanted is not None:
        missing = wanted - {task["task_id"] for task in tasks}
        assert not missing, f"unknown task ids: {sorted(missing)[:5]}"
    print(f"{len(tasks)} tasks")

    if args.dry_run_dir:
        for task in tasks:
            target = Path(args.dry_run_dir) / task["task_id"]
            target.mkdir(parents=True, exist_ok=True)
            (target / "instruction.md").write_text(task["instruction"], encoding="utf-8", newline="")
            (target / "conversation.md").write_text(task["conversation"], encoding="utf-8", newline="")
        print(f"wrote {len(tasks)} tasks to {args.dry_run_dir}")
        return

    assert args.model, "--model is required unless --dry_run_dir is set"
    assert not args.all, "parity runs use --ids"
    out = Path(args.out or f"harbor_runs/{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}").resolve()
    out.mkdir(parents=True, exist_ok=False)
    runner = ClaudeCodeRunner(args.model, args.agent_version, args.timeout_sec, out / "agent_logs")

    def run_task(task):
        text, meta = runner(task)
        return {"task_id": task["task_id"], "domain": task["domain"], "task": task["task"],
                "reply_chars": len(text or ""), "agent": meta, "reply": text}

    with ThreadPoolExecutor(max_workers=args.num_threads) as executor:
        records = list(executor.map(run_task, tasks))

    for domain in DOMAINS:
        # evals.py needs a reply for every listed task; a missing one goes to the judge as "".
        replies = {r["task"]: r["reply"] or "" for r in records if r["domain"] == domain}
        (out / f"replies_{domain}.json").write_text(json.dumps(replies, indent=2, ensure_ascii=False) + "\n")
    write_configs(out, args)
    (out / "per_task.jsonl").write_text(
        "".join(json.dumps({k: v for k, v in r.items() if k != "reply"}) + "\n" for r in records)
    )
    agent_errors = [r["task_id"] for r in records
                    if r["agent"].get("status") != "ok" or r["agent"].get("is_error") is not False]
    empty = [r["task_id"] for r in records if not (r["reply"] or "").strip()]
    summary = {
        "model": args.model, "agent": f"{args.agent}@{args.agent_version}", "trial": args.trial,
        "tasks": len(records), "agent_errors": len(agent_errors), "empty_replies": len(empty),
        "agent_cost_usd": sum(r["agent"].get("total_cost_usd") or 0 for r in records),
    }
    (out / "agent_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))
    print(f"next: python evals.py {out}/config_finance.yaml && python evals.py {out}/config_legal.yaml")
    if agent_errors:
        print(f"{len(agent_errors)} agent runs failed and need a rerun: {agent_errors[:10]}")
        sys.exit(1)


if __name__ == "__main__":
    main()
