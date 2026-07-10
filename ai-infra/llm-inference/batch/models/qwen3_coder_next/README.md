# Qwen3-Coder-Next batch

Per-model batch config for
[`Qwen/Qwen3-Coder-Next`](https://huggingface.co/Qwen/Qwen3-Coder-Next), an
80B-total / 3B-active MoE Apache-2.0 coding model with the **qwen3_next**
hybrid architecture (Gated DeltaNet + Gated Attention layers, 512 experts
top-10 + 1 shared, 262K native context).

## Files

```
batch/models/qwen3_coder_next/
├── __init__.py
├── model_spec.py      # QWEN3_CODER_NEXT: ModelSpec
└── batch_plans.py     # 2 BatchDeploymentPlan factories
```

## Plan factories

| Factory | Compute env | When to use |
|---|---|---|
| `g6e_spot_single_queue()` | `g6e.12xlarge` spot, 4x L40S | **Default.** FP8 quant (~80 GiB resident), TP=4. 32K cap on context. Cheapest path. |
| `p4de_spot_single_queue()` | `p4de.24xlarge` spot, 8x A100-80G | Fallback. BF16, TP=2. Six GPUs idle in this batch plan; expand to TP=2/DP=4 once verified. |

## Required serve flags

* `--enable-auto-tool-choice`
* `--tool-call-parser qwen3_coder`
* `--quantization fp8` (g6e plan only)

## vLLM compatibility

Requires vLLM **>= 0.15.0** for the qwen3_next architecture. The repo pin
(v0.20.2) is well past that floor.

## Sampling notes

The model recommends `temperature=1.0`, `top_p=0.95`, `top_k=40`. It does
NOT emit `<think>` traces; do not pass `enable_thinking=True`.

## Task

The travel-booking JSON sample data is reused, but the SYSTEM_PROMPT asks
for **code generation against the JSON** (e.g., "write a Python function
that parses this email into a dataclass") rather than the JSON itself.
This adds coverage of a coding-specialist model on the same plumbing.
See [`prompts.py`](../../../benchmark/models/qwen3_coder_next/prompts.py).

## Usage

```python
from llm_batch_deploy import deploy, teardown
from models.qwen3_coder_next import g6e_spot_single_queue
plan = g6e_spot_single_queue()
stack = deploy(plan)
# ... submit jobs ...
teardown(plan.model_spec.resource_prefix, region=plan.region)
```
