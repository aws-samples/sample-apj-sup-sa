# Llama-4-Scout-17B-16E batch

Per-model batch config for
[`meta-llama/Llama-4-Scout-17B-16E-Instruct`](https://huggingface.co/meta-llama/Llama-4-Scout-17B-16E-Instruct),
a 109B-total / 17B-active mixture-of-experts model under Meta's gated
Llama-4 license. The smallest viable instance is `p4d.24xlarge` (8x
A100-40GB) with `--kv-cache-dtype fp8`.

## Files

```
batch/models/llama_4_scout_17b/
├── __init__.py
├── model_spec.py      # LLAMA_4_SCOUT_17B: ModelSpec(gated=True)
└── batch_plans.py     # 2 BatchDeploymentPlan factories
```

## Plan factories

| Factory | Compute env | When to use |
|---|---|---|
| `p4d_spot_single_queue()` | `p4d.24xlarge` spot (8x A100-40GB) | **Default.** Cheapest fit, requires `--kv-cache-dtype fp8` to reach 32K context. |
| `p4de_spot_single_queue()` | `p4de.24xlarge` spot (8x A100-80GB) | More VRAM headroom — supports up to 64K context without fp8 KV-cache. |

Both plans run `tensor_parallel=8`. **Never use `p5.48xlarge`** for this
lineup. We exclude p5/p5e (capacity-blocks-only) from this sample.

## Task

Travel-booking JSON extraction (sample-data domain `travel/`).

## HuggingFace token

Same pattern as MedGemma — set `HF_TOKEN=hf_...` and let the deployer
upsert it via `upsert_hf_token`.

## Usage

```python
from models.llama_4_scout_17b import p4d_spot_single_queue
plan = p4d_spot_single_queue()
```

See [`batch/notebooks/llama_4_scout_17b_batch.ipynb`](../../notebooks/llama_4_scout_17b_batch.ipynb).
