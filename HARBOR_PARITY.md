# PRBench parity with Harbor

This branch adds what the original-benchmark side of the Harbor adapter's parity
runs needs (harbor-framework/adapters, `src/prbench`). PRBench samples replies
from a model API; for parity the replies come from a coding agent instead, and
`evals.py` grades them as prefilled replies.

- `harbor_parity/run_agent.py` builds each conversation with this repository's
  own code (`process_reference_texts`, `process_row_get_convo`) on the dataset
  revision the adapter pins, writes it to `/workspace/conversation.md`, gives
  Claude Code the Harbor task's instruction (`harbor_parity/instruction.md` plus
  the task's canary lines) in a container with the task's image and limits, and
  collects `/workspace/response.txt`. Claude Code is launched the way Harbor's
  `claude-code` agent launches it.
- `harbor_parity/parity_task_ids.txt`: the 200-task parity subset, a copy of the
  adapter's list (100 per domain).

Changes to the existing code, each needed for the runs above:

- `process_reference_texts` prepends the reference texts as the paper describes.
  `to_pandas()` returns the column as a NumPy array, so the `isinstance(..., list)`
  test never matched and no reference text reached the prompt (17 tasks).
- `process_row_get_convo` moved to `util.py` so the runner uses the same code.
- New config keys: `dataset_revision` (pins `ScaleAI/PRBench`), `task_ids_path`
  (grades only the listed tasks; `prefilled` otherwise needs a reply for every
  task of the split), and an empty `litellm_key_path` reads `OPENAI_API_KEY`.
- `results["failed_query_count"]` counts judge calls that still failed after
  five attempts (each one is graded as "not met"); `evals.py` exits with status
  1 when it is not 0.

## Run

Needs Docker and Python 3.11 with `requirements.txt` installed.

```bash
export ANTHROPIC_API_KEY=... CLAUDE_CODE_VERSION=<pinned version>
python harbor_parity/run_agent.py --model claude-haiku-5-5 --trial 1 --out harbor_runs/run1
export OPENAI_API_KEY=... OPENAI_BASE_URL=...   # judge endpoint
python evals.py harbor_runs/run1/config_finance.yaml
python evals.py harbor_runs/run1/config_legal.yaml
```

`--judge_model` (default `gpt-4.1`, the adapter's default) and `--judge_base_url`
(default `$OPENAI_BASE_URL`) go into the two generated configs. Use a new
`--trial` for every run: the judge cache is keyed by `trial_number`.

Each run directory holds `replies_<domain>.json` (task -> reply),
`config_<domain>.yaml`, `per_task.jsonl` (agent status, cost, reply length) and
`agent_summary.json`, plus per-task agent logs under `agent_logs/`. The runner
exits with status 1 when an agent run timed out or did not end cleanly; rerun
those tasks before grading. A missing reply is graded as an empty one.

`--dry_run_dir DIR --all` writes `DIR/<task_id>/instruction.md` and
`conversation.md` for all 1,100 tasks without starting an agent. Both files are
byte-identical to the ones the Harbor adapter generates.

The benchmark score per domain is `mean_clipped` in the results file, as in the
paper.

## Runs reported in the adapter

Runs 1 and 2 (2026-11-21): Claude Code 2.1.322 (`CLAUDE_CODE_VERSION=2.1.322`),
`--model claude-haiku-5-5`, `--judge_model gpt-5.6-terra`, `--num_threads 12`,
`--trial 1` and `--trial 2`, on `parity_task_ids.txt`. The Harbor side ran the
same agent, model and judge (`src/prbench/prbench_parity.yaml` with
`JUDGE_MODEL=gpt-5.6-terra`). Replies, agent logs and grading results are in the
Harbor parity-experiments dataset under `adapters/prbench/`.
