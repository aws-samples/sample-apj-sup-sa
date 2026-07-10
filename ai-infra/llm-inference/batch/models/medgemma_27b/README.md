# MedGemma-27B batch

Per-model batch config for
[`google/medgemma-27b-text-it`](https://huggingface.co/google/medgemma-27b-text-it),
a 27B medical-tuned Gemma variant under Google's HAI-DEF gated license.
Requires an HF access token; the batch runtime injects it via Secrets Manager
at task-start time.

## Files

```
batch/models/medgemma_27b/
├── __init__.py
├── model_spec.py      # MEDGEMMA_27B: ModelSpec(gated=True)
└── batch_plans.py     # 4 BatchDeploymentPlan factories
```

## Plan factories

| Factory | Compute env | When to use |
|---|---|---|
| `g7e_spot_single_queue(...)` | `g7e.{2x,4x}large` spot (1x Blackwell) | **Default.** 96 GiB single-GPU fit; cheapest. |
| `g7e_family_spot_with_od_failover()` | `g7e.{2x,4x,12x}large` spot + on-demand failover | Tighter SLA: spot-first across the g7e family with an on-demand queue when spot is exhausted. |
| `p4d_spot_single_queue()` | `p4d.24xlarge` spot (8x A100-40GB) | When you need 8-GPU TP for higher throughput, or g7e is unavailable in your region. |
| `p4d_spot_and_on_demand_failover()` | `p4d.24xlarge` spot + on-demand fallback queue | Production-style: keeps long-running jobs alive through capacity churn. |

## Task

Travel-booking detail extraction from confirmation emails (sample-data
domain `travel/`). All text models in this code sample share the same
prompt so that throughput numbers across the matrix are directly
comparable.

## HuggingFace token

Set `HF_TOKEN=hf_...` before submitting; the deployer upserts it into
Secrets Manager and the JobDefinition's `Secrets` block injects it as
`HF_TOKEN` into the container at task-start time.

```python
import os
from llm_batch_deploy.submitter.secrets import upsert_hf_token
upsert_hf_token(stack.hf_token_secret_arn, os.environ["HF_TOKEN"])
```

## Usage

```python
from models.medgemma_27b import g7e_spot_single_queue
plan = g7e_spot_single_queue()
```

See [`batch/notebooks/medgemma_27b_batch.ipynb`](../../notebooks/medgemma_27b_batch.ipynb).
