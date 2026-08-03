# benchmark — vLLM on EC2

Reproducible benchmarks for deploying open-source LLMs onto **AWS EC2** with
**vLLM**, measured with [LLMeter](https://github.com/awslabs/llmeter).

The project is split into two layers:

* **`src/vllm_ec2_bench/`** — model-agnostic deployment infrastructure. A
  `pip install -e .`-able Python package with Pydantic data models, a
  strategy-pattern capacity sourcer (spot → on-demand → ODCR), a Jinja2
  user-data renderer, and a thin `DeploymentRunner` orchestrator.
* **`models/`** — per-model configuration. Each model gets its own subfolder
  with a `ModelSpec`, a dictionary of `ExperimentConfig` instances, and any
  model-specific prompts.

## Models

| Model | Folder | Experiments |
|---|---|---|
| `Qwen/Qwen3-8B` | [`models/qwen3_8b/`](./models/qwen3_8b/) | 7 |
| `mistralai/Mistral-Small-3.2-24B-Instruct-2506` | [`models/mistral_small_3_2_24b/`](./models/mistral_small_3_2_24b/) | 7 |
| `Qwen/Qwen3-30B-A3B-Instruct-2507` | [`models/qwen3_30b_a3b/`](./models/qwen3_30b_a3b/) | 7 |
| `google/gemma-4-31B-it` | [`models/gemma_4_31b/`](./models/gemma_4_31b/) | 7 |
| `google/medgemma-27b-text-it` | [`models/medgemma_27b/`](./models/medgemma_27b/) | 8 |
| `meta-llama/Llama-4-Scout-17B-16E-Instruct` | [`models/llama_4_scout_17b/`](./models/llama_4_scout_17b/) | 2 |
| `openai/gpt-oss-20b` | [`models/gpt_oss_20b/`](./models/gpt_oss_20b/) | 6 |
| `Qwen/Qwen3-Coder-Next` | [`models/qwen3_coder_next/`](./models/qwen3_coder_next/) | 4 |

## Quick start

```bash
# 1. Create Python venv, install deps + package editable (Python 3.11+)
./scripts/setup_env.sh

# 2. Sample data is in ../sample-data/. To regenerate from scratch:
source .venv/bin/activate
python ../sample-data/scripts/synthesize.py --domain travel --per-seed 10000

# 3. Launch Jupyter
./scripts/start_jupyter.sh
```

Then open `models/<model>/<model>-vllm-ec2-benchmark.ipynb` (e.g.
`models/qwen3_8b/qwen3-8b-vllm-ec2-benchmark.ipynb`).

## Repository layout

```
benchmark/
├── README.md                           # This file
├── pyproject.toml                      # pip install -e '.[dev]' — editable install
├── .gitignore                          # .venv, outputs, secrets (sample data lives in ../sample-data/)
│
├── src/
│   └── vllm_ec2_bench/                 # Generic package (model-agnostic)
│       ├── data/                       # HardwareFacts, ModelSpec, DeploymentPlan,
│       │                               # ExperimentConfig (Pydantic, frozen);
│       │                               # Catalog service (code only — data lives
│       │                               # at models/<name>/catalog_cache.json)
│       ├── deployer/                   # DeploymentRunner + ResourceManager +
│       │   ├── capacity/               #   strategy pattern: spot, ondemand,
│       │   │                           #   odcr, capacity_block
│       │   └── user_data.py            # Jinja2 cloud-init renderer
│       ├── endpoint/vllm_openai.py     # LLMeter endpoint adapter +
│       │                               #   UniquePayloadEndpoint (per-request
│       │                               #   distinct inputs) + make_http_client
│       ├── verify.py                   # Per-tier gates: completeness, timing
│       │                               #   cross-check, /metrics scrape
│       ├── templates/                  # Jinja2 user-data templates
│       └── cleanup.py                  # Emergency bulk-terminate helpers
│
├── models/
│   ├── qwen3_8b/                       # Per-model config — one folder per model
│   ├── mistral_small_3_2_24b/
│   ├── qwen3_30b_a3b/
│   ├── gemma_4_31b/
│   ├── medgemma_27b/
│   └── llama_4_scout_17b/
│       ├── __init__.py                 # Re-exports + CATALOG_CACHE +
│       │                               #   INSTANCE_TYPES + load_catalog()
│       ├── model_spec.py               # ModelSpec
│       ├── experiments.py              # EXPERIMENTS: 7 ExperimentConfigs (2 for Llama-4-Scout)
│       ├── prompts.py                  # Domain-appropriate prompt + seed input
│       ├── catalog_cache.json          # Hardware + prices cache (checked-in)
│       └── <model>-vllm-ec2-benchmark.ipynb        # Generated notebook
│
├── tests/                              # 198 pytest unit tests
│
└── scripts/
    ├── setup_env.sh                    # Create venv + pip install -e '.[dev,notebook]'
    ├── start_jupyter.sh                # Launch JupyterLab
    ├── smoke_test.py                   # End-to-end live smoke test (LLM_BENCH_SMOKE=YES)
    └── build_notebook.py               # Regenerate per-model notebook(s):
                                        #   build_notebook.py --model <name>
                                        #   build_notebook.py --all
```

## Conventions

* **Default region**: `us-west-2` (PDX). Fallbacks: `us-east-2` (CMH),
  `us-east-1` (IAD).
* **AWS profile**: `default` (designed for Isengard-style dev accounts).
* **vLLM**: OpenAI-compatible server on TCP **port 8000**, authenticated with
  a per-deployment API key, firewalled to the notebook caller's public IP.
* **SSH**: none. Access via **AWS Systems Manager Session Manager**.
* **IAM**: instance profile is derived from `ModelSpec.resource_prefix` — e.g.
  `Qwen38bBenchmarkInstanceProfile`. Created idempotently on first use.
* **Tags**: all project resources get `Project=<resource_prefix>-benchmark` so
  they can be bulk-terminated in an emergency.

## Trusting a throughput number

A benchmark that reports a big number is easy; one you can defend is not. Four
things silently corrupt results in this harness's problem space, so the
framework handles each of them by default. If you write your own runner instead
of using the generated notebook, carry them over.

1. **Distinct input per request.** LLMeter seeds its per-client payload shuffle
   with a constant, so every client replays the same inputs in the same order.
   With `--enable-prefix-caching` those repeats become near-free cache hits: one
   40,000-request tier here was served from just 49 distinct prompts and
   reported a 96% prefix-cache hit rate. `UniquePayloadEndpoint` substitutes a
   fresh input inside `prepare_payload`, which LLMeter calls *outside* the
   response timer, so it costs nothing measurable. Relative A/B comparisons
   survive the bias; absolute throughput does not.
2. **Client connection ceiling.** The OpenAI SDK caps at 1,000 connections, so
   a c=1200 tier quietly measures ~1,000 in flight and the curve flattens for a
   client-side reason. Pass `make_http_client(4096)` and assert the pool took
   effect before trusting anything above c=1000.
3. **Completeness.** LLMeter reports `failed_requests=0` even when clients time
   out and get dropped, because a dropped client returns an empty list rather
   than an error. Gate on responses actually on disk (`verify.check_completeness`);
   one run here lost 9% of responses while reporting a clean sweep.
4. **Preemption.** `usage.cached_tokens` stays 0 even with prefix caching on, so
   the Prometheus `/metrics` endpoint is the only window into cache behaviour —
   and into `num_preemptions_total`, the single most important health signal at
   high concurrency. A tier with hundreds of preemptions is the engine evicting
   running sequences and recomputing their prefill; its throughput is neither
   stable nor reproducible however good the headline looks. `verify_tier`
   defaults to `max_preemptions=0`.

Also note that **concurrency must be swept per instance type, not fixed**. The
same 8-GPU box measured $1.03/1M tokens at c=100 and $0.177/1M at c=800 — a
5.8× swing from concurrency alone. A single `concurrency_high` copied from a
smaller SKU will understate a large one by several-fold. Find the highest tier
that is still clean, and stop there.

Finally, prefer **live spot pricing** over the `0.7×OD` heuristic for scarce
accelerators. On `p6-b200.48xlarge` the heuristic reads ~$79.75/hr against an
actual ~$40/hr, which doubles every derived $/token figure. The generated
notebook calls `Catalog.live_spot()` first and labels which basis it used.

## Adding a new model

1. Create `models/my_model/` mirroring any existing model folder (e.g.
   `models/qwen3_8b/`):
   * `model_spec.py` — one `ModelSpec(resource_prefix='my-model', ...)`.
   * `experiments.py` — dict of `ExperimentConfig` instances.
   * `prompts.py` — domain-specific system prompt + seed.
   * `catalog_cache.json` — copy from any existing model folder (the
     hardware/price catalog is model-independent).
2. Add an entry to `scripts/build_notebook.py`'s `MODEL_CONFIGS` dict
   then run `build_notebook.py --model my_model` to emit the notebook.
3. Sample data is in `../sample-data/` (regenerate with
   `../sample-data/scripts/synthesize.py` if needed).
4. Update this README's models table.

## Development

```bash
pip install -e '.[dev]'     # install package + pytest/ruff/mypy
pytest tests/               # run the unit suite (198 tests, ~3s)
python scripts/build_notebook.py --all   # regenerate all 8 notebooks
```
