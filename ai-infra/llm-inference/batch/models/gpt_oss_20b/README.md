# gpt-oss-20b batch

Per-model batch config for
[`openai/gpt-oss-20b`](https://huggingface.co/openai/gpt-oss-20b), a 21B
total / 3.6B-active MoE Apache-2.0 model with 32 experts (top-4 + 1
shared) and native MXFP4 quantization on Blackwell GPUs.

## Files

```
batch/models/gpt_oss_20b/
├── __init__.py
├── model_spec.py      # GPT_OSS_20B: ModelSpec
└── batch_plans.py     # 2 BatchDeploymentPlan factories
```

## Plan factories

| Factory | Compute env | When to use |
|---|---|---|
| `g7e_spot_single_queue()` | `g7e.2xlarge` / `g7e.4xlarge` spot, 1x Blackwell RTX PRO 6000 each | **Default.** MXFP4 native (~13 GiB resident) with `VLLM_USE_FLASHINFER_MOE_MXFP4_MXFP8=1`. 131K context comfortably. |
| `p4d_spot_single_queue()` | `p4d.24xlarge` spot, 8x A100-40G | Fallback when Blackwell spot is tight. BF16 (~42 GiB) TP=8, capped to 64K context, Triton attention backend. |

## Required env vars

* g7e (Blackwell): `VLLM_USE_FLASHINFER_MOE_MXFP4_MXFP8=1`
* p4d (Ampere):    `VLLM_ATTENTION_BACKEND=TRITON_ATTN_VLLM_V1`

Both are threaded through `BatchDeploymentPlan.extra_env_vars` and exported
into the JobDef container environment by the deployer.

## Required serve flags

* `--tool-call-parser openai`
* `--enable-auto-tool-choice`
* `--reasoning-parser openai_gptoss`
* `--kv-cache-dtype fp8`

## Task

Travel-booking JSON extraction (sample-data domain `travel/`). Same
SYSTEM_PROMPT shape as Qwen3-8B; gpt-oss adds an OpenAI-format reasoning
trace that the parser preserves but does NOT add to the JSON output.

## Usage

```python
from llm_batch_deploy import deploy, teardown
from models.gpt_oss_20b import g7e_spot_single_queue
plan = g7e_spot_single_queue()
stack = deploy(plan)
# ... submit jobs ...
teardown(plan.model_spec.resource_prefix, region=plan.region)
```
