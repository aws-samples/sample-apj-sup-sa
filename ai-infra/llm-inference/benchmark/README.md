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
│       ├── endpoint/vllm_openai.py     # LLMeter endpoint adapter
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
├── tests/                              # 160 pytest unit tests
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
pytest tests/               # run the unit suite (160 tests, ~3s)
python scripts/build_notebook.py --all   # regenerate all 6 notebooks
```
