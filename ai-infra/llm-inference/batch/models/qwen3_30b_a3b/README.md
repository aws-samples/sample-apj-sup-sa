# Qwen3-30B-A3B batch

Per-model batch config for
[`Qwen/Qwen3-30B-A3B-Instruct-2507`](https://huggingface.co/Qwen/Qwen3-30B-A3B-Instruct-2507),
a 30B-total / 3.3B-active mixture-of-experts model under Apache-2.0. The
"A3B" denotes 3 active experts per token from the 128-expert pool.

## Files

```
batch/models/qwen3_30b_a3b/
├── __init__.py
├── model_spec.py      # QWEN3_30B_A3B: ModelSpec
└── batch_plans.py     # 2 BatchDeploymentPlan factories
```

## Plan factories

| Factory | Compute env | When to use |
|---|---|---|
| `g7e_spot_single_queue()` | `g7e.{2x,4x}large` spot (1x Blackwell) | **Default.** 96 GiB Blackwell holds the 30B weights + KV cache at 32K context. |
| `g6e_spot_single_queue()` | `g6e.12xlarge` spot (4x L40S) | Multi-GPU fallback at `tensor_parallel=2` for spot-capacity churn. |

## Task

Travel-booking JSON extraction (sample-data domain `travel/`).

## Usage

```python
from models.qwen3_30b_a3b import g7e_spot_single_queue
plan = g7e_spot_single_queue()
```

See [`batch/notebooks/qwen3_30b_a3b_batch.ipynb`](../../notebooks/qwen3_30b_a3b_batch.ipynb).
