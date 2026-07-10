# Gemma-4-31B batch

Per-model batch config for
[`google/gemma-4-31B-it`](https://huggingface.co/google/gemma-4-31B-it),
a dense 31B Apache-2.0 instruction-tuned Gemma variant (released April
2026, ungated unlike MedGemma).

## Files

```
batch/models/gemma_4_31b/
├── __init__.py
├── model_spec.py      # GEMMA_4_31B: ModelSpec
└── batch_plans.py     # 2 BatchDeploymentPlan factories
```

## Plan factories

| Factory | Compute env | When to use |
|---|---|---|
| `g7e_spot_single_queue()` | `g7e.{2x,4x}large` spot (1x Blackwell, 96 GiB) | **Default.** Fits 31B BF16 weights with KV-cache headroom at 32K context. |
| `g6e_spot_single_queue()` | `g6e.12xlarge` spot (4x L40S, 192 GiB total) | Fallback at `tensor_parallel=2` when g7e capacity tightens. |

## Task

Travel-booking JSON extraction (sample-data domain `travel/`).

## Usage

```python
from models.gemma_4_31b import g7e_spot_single_queue
plan = g7e_spot_single_queue()
```

See [`batch/notebooks/gemma_4_31b_batch.ipynb`](../../notebooks/gemma_4_31b_batch.ipynb).
