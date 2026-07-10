# llm-inference

End-to-end AWS code samples for deploying open-source LLMs with vLLM:

* **`batch/`** — high-throughput, cost-optimized inference on **AWS Batch + EC2 spot**. Submit a JSONL of prompts, get back a JSONL of completions. Best when you don't need a low-latency endpoint and want to minimize $ / 1M output tokens. You would go to this folder if you need samples on how to deploy LLMs for bulk inference that can benefit from the cost-savings of spot instances. The notebook also generates a report on the throughput and estimated $/tokens.
* **`benchmark/`** — reproducible single-instance vLLM benchmarks across `g5`, `g6`, `g6e`, `g7e`, `p4d`, `p4de`. Helps you pick the right instance family for your model + workload. You would go to this folder if you need samples on running inference benchmark for LLM across several GPU instances in AWS. The notebook generates report that compares throughput and $/tokens across the instance types.

## Models

The same eight open-source models run across both functionalities:

| Model | Notes |
| --- | --- |
| `Qwen/Qwen3-8B` | dense 8B, single-GPU fit |
| `mistralai/Mistral-Small-3.2-24B-Instruct-2506` | dense 24B |
| `Qwen/Qwen3-30B-A3B-Instruct-2507` | MoE 30B / 3.3B-active |
| `google/gemma-4-31B-it` | dense 31B |
| `google/medgemma-27b-text-it` | medical-tuned Gemma |
| `meta-llama/Llama-4-Scout-17B-16E-Instruct` | 109B MoE / 17B-active, needs 8× A100 |
| `openai/gpt-oss-20b` | 21B / 3.6B-A MoE, native MXFP4 on Blackwell |
| `Qwen/Qwen3-Coder-Next` | 80B / 3B-A hybrid Mamba+attention MoE |

## Sample data

Data synthesized by **Amazon Bedrock**:

* `sample-data/travel/` — 10 × 1K = **10K** booking-confirmation emails. Used for booking-detail extraction by every text model in the matrix. Run `synthesize.py --per-seed 10000` to top up to a 100K-row benchmark dataset.

The shipped data is released under [CC0 1.0 Universal](sample-data/LICENSE). See `sample-data/README.md` for the record format and reproduction steps.

## Quick start

Install the CLI tooling (Python ≥ 3.11, AWS CLI ≥ 2.22):

```bash
git clone git@github.com:aws-samples/sample-apj-sup-sa.git
cd ai-infra/llm-inference

# Pick the path you care about
cd batch          # or  benchmark
python -m venv .venv && source .venv/bin/activate
pip install -e '.[notebook]'   # package + JupyterLab, widgets, and notebook deps
jupyter lab
```

The `[notebook]` extra pulls in JupyterLab plus the libraries the notebooks
import (see `[project.optional-dependencies]` in each `pyproject.toml`); a plain
`pip install -e .` installs only the core library and `jupyter lab` will not be
found. Each subpackage also ships `./scripts/setup_env.sh` (creates the venv,
installs `.[dev,notebook]`, and registers a Jupyter kernel) and
`./scripts/start_jupyter.sh` as a one-command alternative.

Then open the appropriate notebook for the model you want to run.

**On a managed notebook environment** (SageMaker Studio, Colab, JupyterHub, the
VS Code notebook extension) JupyterLab is already running — skip the
`python -m venv` and `jupyter lab` lines. Just `pip install -e '.[notebook]'`
into the active kernel's environment, then open the notebook and select that
kernel.

## How to use this repo

Pick the scenario and model, open one notebook, and run it cell-by-cell. Every notebook is self-contained: deploy → run → tear down.

### Pick your scenario × model

#### Batch (one notebook per model)

| Model | Notebook |
|---|---|
| Qwen3-8B | [`batch/notebooks/qwen3_8b_batch.ipynb`](batch/notebooks/qwen3_8b_batch.ipynb) |
| Mistral-Small-3.2-24B | [`batch/notebooks/mistral_small_3_2_24b_batch.ipynb`](batch/notebooks/mistral_small_3_2_24b_batch.ipynb) |
| Qwen3-30B-A3B | [`batch/notebooks/qwen3_30b_a3b_batch.ipynb`](batch/notebooks/qwen3_30b_a3b_batch.ipynb) |
| Gemma-4-31B | [`batch/notebooks/gemma_4_31b_batch.ipynb`](batch/notebooks/gemma_4_31b_batch.ipynb) |
| MedGemma-27B | [`batch/notebooks/medgemma_27b_batch.ipynb`](batch/notebooks/medgemma_27b_batch.ipynb) |
| Llama-4-Scout-17B | [`batch/notebooks/llama_4_scout_17b_batch.ipynb`](batch/notebooks/llama_4_scout_17b_batch.ipynb) |
| GPT-OSS-20B | [`batch/notebooks/gpt_oss_20b_batch.ipynb`](batch/notebooks/gpt_oss_20b_batch.ipynb) |
| Qwen3-Coder-Next | [`batch/notebooks/qwen3_coder_next_batch.ipynb`](batch/notebooks/qwen3_coder_next_batch.ipynb) |

Example: for **batch on Llama-4-Scout**, open
[`batch/notebooks/llama_4_scout_17b_batch.ipynb`](batch/notebooks/llama_4_scout_17b_batch.ipynb).

#### Benchmark (one notebook per model)

| Model | Notebook |
|---|---|
| Qwen3-8B | [`benchmark/models/qwen3_8b/qwen3-8b-vllm-ec2-benchmark.ipynb`](benchmark/models/qwen3_8b/qwen3-8b-vllm-ec2-benchmark.ipynb) |
| Mistral-Small-3.2-24B | [`benchmark/models/mistral_small_3_2_24b/mistral-small-3-2-24b-vllm-ec2-benchmark.ipynb`](benchmark/models/mistral_small_3_2_24b/mistral-small-3-2-24b-vllm-ec2-benchmark.ipynb) |
| Qwen3-30B-A3B | [`benchmark/models/qwen3_30b_a3b/qwen3-30b-a3b-vllm-ec2-benchmark.ipynb`](benchmark/models/qwen3_30b_a3b/qwen3-30b-a3b-vllm-ec2-benchmark.ipynb) |
| Gemma-4-31B | [`benchmark/models/gemma_4_31b/gemma-4-31b-vllm-ec2-benchmark.ipynb`](benchmark/models/gemma_4_31b/gemma-4-31b-vllm-ec2-benchmark.ipynb) |
| MedGemma-27B | [`benchmark/models/medgemma_27b/medgemma-27b-vllm-ec2-benchmark.ipynb`](benchmark/models/medgemma_27b/medgemma-27b-vllm-ec2-benchmark.ipynb) |
| Llama-4-Scout-17B | [`benchmark/models/llama_4_scout_17b/llama-4-scout-17b-vllm-ec2-benchmark.ipynb`](benchmark/models/llama_4_scout_17b/llama-4-scout-17b-vllm-ec2-benchmark.ipynb) |
| GPT-OSS-20B | [`benchmark/models/gpt_oss_20b/gpt-oss-20b-vllm-ec2-benchmark.ipynb`](benchmark/models/gpt_oss_20b/gpt-oss-20b-vllm-ec2-benchmark.ipynb) |
| Qwen3-Coder-Next | [`benchmark/models/qwen3_coder_next/qwen3-coder-next-vllm-ec2-benchmark.ipynb`](benchmark/models/qwen3_coder_next/qwen3-coder-next-vllm-ec2-benchmark.ipynb) |

### Where to change the configuration

| What you want to change | Where to edit |
|---|---|
| **Spot/on-demand/ODCR/Capacity-Block** strategy | `batch/models/<model>/batch_plans.py` (`capacity_mode=...` on each `ComputeEnvironment`); `benchmark/models/<model>/experiments.py` (`capacity_strategy=...`) |
| **Instance type / family** | Same files above (`instance_types=[...]`) |
| **Tensor / data / pipeline parallelism** | `<scenario>/models/<model>/model_spec.py` for batch+benchmark |
| **vLLM serve flags** (e.g. `--kv-cache-dtype fp8`, `--max-model-len`) | Same files as the strategy row above (`extra_serve_flags=[...]`) |
| **vLLM startup grace period** (large weight downloads) | `vllm_startup_timeout_seconds` on the same plan/service object |
| **Concurrency** (requests in flight per container) | `in_flight_per_job` (batch); LLMeter `clients` (benchmark `experiments.py`) |
| **Region** | `region=` on the plan/service object — defaults to `us-west-2` |
| **HF_TOKEN** for gated models (MedGemma, Llama-4-Scout) | Stored at run time in AWS Secrets Manager — the notebook prompts. Never check it into the repo |

The framework code in `batch/src/` and `benchmark/src/` is **model-agnostic**: you only edit `models/<model>/`.

### Where to put your data

Inputs live under [`sample-data/`](sample-data/), one folder per domain (`travel/`). Each shard is JSONL.

* **To use the shipped data as-is**: do nothing — every notebook reads from `sample-data/travel/` (or `sample-data/vision/` for the VL model).
* **To add your own prompts**: drop a JSONL file into the right domain folder. Each line is `{"text": "..."}` for text models or `{"image_url": "...", "prompt": "..."}` for vision. The notebook will
  pick it up automatically.
* **To regenerate the synthetic dataset at a different size**: `python sample-data/scripts/synthesize.py --per-seed N` (writes `sample-data/travel/*.jsonl`).

Sample-data conventions and the record schema are documented in [`sample-data/README.md`](sample-data/README.md).

### Adding a new model

Mirror any of the existing per-model folders under `<scenario>/models/<existing_model>/` to `<scenario>/models/<your_model>/`, edit `model_spec.py` and the plan file, then run the per-scenario notebook generator (`scripts/build_notebook.py --model <your_model>`). Each subpackage README has the step-by-step.

## Running the test suites

Each subpackage (`batch/`, `benchmark/`, `sample-data/`) has its own pytest config. To run all three suites sequentially against a shared top-level `.venv`:

```bash
./run-tests.sh
```

The runner exits non-zero on the first failure.

## Costs

* **Batch:** dominated by GPU spot $/hour and total tokens generated. The notebook prints an exact $/1M-output-token after every run, computed by walking each EC2 instance's lifecycle and integrating spot-price segments.
* **Benchmark:** per-experiment cost ≈ (GPU $/hour) × (model load + load test wall-clock, typically 15–25 minutes).
* **Sample-data synth:** ~$6.50 once for the full 100K rows.

## License

* Code: MIT-0. See [`LICENSE`]()../../LICENSE).
* Sample data under `sample-data/`: CC0 1.0 Universal. See
  [`sample-data/LICENSE`](sample-data/LICENSE).

## Repository layout

```
llm-inference/
├── batch/             # AWS Batch + EC2 spot offline inference
├── benchmark/         # single-instance vLLM benchmarks
├── sample-data/       # synthesized prompts (CC0)
│   ├── travel/
│   ├── scripts/synthesize.py
│   └── LICENSE
├── dev/               # internal docs (security scan results, etc.)
├── run-tests.sh
├── LICENSE
└── README.md
```
