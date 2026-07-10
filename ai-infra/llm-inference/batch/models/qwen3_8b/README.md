# Qwen3-8B batch

Per-model batch config for [`Qwen/Qwen3-8B`](https://huggingface.co/Qwen/Qwen3-8B),
a dense 8B Apache-2.0 model (32K native context, ~16 GiB BF16).

## Files

```
batch/models/qwen3_8b/
├── __init__.py
├── model_spec.py      # QWEN3_8B: ModelSpec
└── batch_plans.py     # 2 BatchDeploymentPlan factories
```

## Plan factories

| Factory | Compute env | When to use |
|---|---|---|
| `g6e_spot_single_queue()` | `g6e.{x,2x,4x}large` spot, 1x L40S each | **Default.** Cheapest single-GPU plan with 32K context comfortably; broad spot availability across us-west-2c/d. |
| `g7e_spot_single_queue()` | `g7e.{2x,4x}large` spot, 1x Blackwell each | Newer hardware, much more KV-cache headroom; useful for large prompts or higher in-flight concurrency. |

Both plans run `tensor_parallel=1`, `data_parallel=1`, `pipeline_parallel=1`,
`max_model_len=32768`. The g6e plan defaults to `in_flight_per_job=128`; the
g7e plan to `200` because the larger VRAM tolerates more concurrent requests.

## Task

Travel-booking JSON extraction (sample-data domain `travel/`). The
parametric notebook `batch/notebooks/qwen3_8b_batch.ipynb` defaults to
`sample-data/travel/01-domestic-flight.jsonl` and uses the prompts
re-exported from
[`benchmark/models/qwen3_8b/prompts.py`](../../../benchmark/models/qwen3_8b/prompts.py).

## Usage

```python
from llm_batch_deploy import deploy, teardown
from models.qwen3_8b import g6e_spot_single_queue
plan = g6e_spot_single_queue()
stack = deploy(plan)
# ... submit jobs ...
teardown(plan.model_spec.resource_prefix, region=plan.region)
```

See [`batch/notebooks/qwen3_8b_batch.ipynb`](../../notebooks/qwen3_8b_batch.ipynb)
for the full deployment + cost-accounting flow.
