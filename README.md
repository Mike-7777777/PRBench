# PRBench: Professional Reasoning Benchmark

> **This fork (`harbor-parity` branch)** holds the original-benchmark side of the parity runs of the Harbor PRBench adapter ([harbor-framework/adapters#83](https://github.com/harbor-framework/adapters/pull/83)). The reported runs used commit [`e7b4453`](https://github.com/Mike-7777777/PRBench/tree/e7b4453a1e4bb543f8726efced94355087cea6fc). [`HARBOR_PARITY.md`](HARBOR_PARITY.md) lists every change to the upstream code (scoring is unchanged) and the commands that reproduce the runs:
>
> ```bash
> pip install -r requirements.txt
> export ANTHROPIC_API_KEY=... CLAUDE_CODE_VERSION=2.1.322   # agent endpoint; ANTHROPIC_BASE_URL if not Anthropic's
> python harbor_parity/run_agent.py --model claude-haiku-5-5 --trial 1 --out harbor_runs/run1 --judge_model gpt-5.6-terra --num_threads 12
> export OPENAI_API_KEY=... OPENAI_BASE_URL=...              # judge endpoint
> python evals.py harbor_runs/run1/config_finance.yaml
> python evals.py harbor_runs/run1/config_legal.yaml
> ```

> A lightweight evaluation harness for PRBench, a large-scale expert-annotated benchmark for high-stakes reasoning in professional domains. Current version covers Legal and Finance domains.

This repository provides the code necessary to run rubric-based evaluations using the PRBench dataset and pipeline. The main entry point is evals.py, which conducts model response generation, automated scoring via an LLM-judge, caching, retries, and reporting.

PRBench consists of:

* 1,100 expert-authored conversations across Finance and Legal domains

* 19,356 expert-curated rubric criteria (10–30 per task)

* Coverage of 114 countries, 47 U.S. jurisdictions, and 25 total professional topics.

* Hard subsets (Finance-300, Legal-250) representing the most challenging tasks

See the release for the paper and full details at: https://scale.com/research/prbench

Explore PRBench using our visualizer at: https://prbench-explorer.vercel.app/


## Quickstart

1. Install dependencies in `requirements.txt`
2. Set API key, url for the endpoint to sample responses from and model grading.
3. Configure the config.yaml to point to the right API key path `litellm_key_path` and base url. The code uses OpenAI SDK--make sure to use the right key for your endpoint.
4. Select which response models to evaluate in `response_model_names`.
5. For debugging set `debug` to `true` which will only run evaluation for the first 2 samples.
6. Run with `python evals.py config.yaml`.

## Source of Responses

By default, the script will sample responses to the conversations from the models listed under `response_model_names` when `final_response_source` is set as `sampled`. Alternatively, setting `final_response_source: prefilled`, will load responses from `filename`.json. Format the json to be `task->response`.

## Outputs

Results are saved under `results/`. We report `mean_clipped` scores in the paper. Results for individual data points may be found under `outputs`.

## Acknowledgments

We are grateful to the domain experts who contributed their time and expertise to PRBench.
