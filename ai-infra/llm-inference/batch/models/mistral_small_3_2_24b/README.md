# Mistral-Small-3.2-24B batch

Per-model batch config for
[`mistralai/Mistral-Small-3.2-24B-Instruct-2506`](https://huggingface.co/mistralai/Mistral-Small-3.2-24B-Instruct-2506),
a dense 24B Apache-2.0 model. The Mistral tokenizer + chat template require
`--tokenizer-mode mistral` and `--config-format mistral` — already wired into
the runtime.

## Files

```
batch/models/mistral_small_3_2_24b/
├── __init__.py
├── model_spec.py      # MISTRAL_SMALL_3_2_24B: ModelSpec
└── batch_plans.py     # 2 BatchDeploymentPlan factories
```

## Plan factories

| Factory | Compute env | When to use |
|---|---|---|
| `g7e_spot_single_queue()` | `g7e.{2x,4x}large` spot (1x Blackwell, 96 GiB) | **Default.** Single-GPU fit at BF16 with 32K context; cheapest in 2026. |
| `g6e_spot_single_queue()` | `g6e.12xlarge` spot (4x L40S, 192 GiB total) | Fallback when g7e capacity is tight; runs `tensor_parallel=2` for headroom. |

## Task

Travel-booking JSON extraction (sample-data domain `travel/`).

## Usage

```python
from models.mistral_small_3_2_24b import g7e_spot_single_queue
plan = g7e_spot_single_queue()
```

See [`batch/notebooks/mistral_small_3_2_24b_batch.ipynb`](../../notebooks/mistral_small_3_2_24b_batch.ipynb).
