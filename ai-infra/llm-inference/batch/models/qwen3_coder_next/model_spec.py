"""Qwen/Qwen3-Coder-Next (Apache-2.0, 80B total / 3B active MoE, qwen3_next arch).

512 experts (top-10 + 1 shared) with the **qwen3_next** hybrid architecture:
Gated DeltaNet layers interleaved with Gated Attention layers. 262K native
context (capped to 32K for default plans to keep KV cache bounded). Sampling
recommended at temperature=1.0, top_p=0.95, top_k=40 (per Qwen).

The model does NOT emit `<think>` traces — do not pass `enable_thinking=True`.
Tool calling uses the dedicated ``qwen3_coder`` parser.

Requires vLLM >= 0.15.0 for the qwen3_next architecture. The repo's pinned
vLLM image (v0.20.2) is well past that floor.
"""
from llm_batch_deploy import ModelSpec

QWEN3_CODER_NEXT = ModelSpec(
    resource_prefix="qwen3-coder-next",
    hf_model_id="Qwen/Qwen3-Coder-Next",
    served_model_name="qwen3-coder-next",
    weight_size_gib=160.0,         # ~160 GiB BF16 (80B params x 2 bytes)
    default_max_model_len=32768,   # native 262K, cap to 32K for default KV
    gated=False,                   # Apache-2.0
    dtype="bfloat16",
)
