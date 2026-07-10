"""Qwen3-Coder-Next ModelSpec.

80B-total / 3B-active MoE Apache-2.0 coding model, qwen3_next hybrid
architecture (Gated DeltaNet + Gated Attention layers, 512 experts top-10
+ 1 shared, 262K native context). Requires vLLM >= 0.15.0.
"""
from __future__ import annotations

from vllm_ec2_bench import ModelSpec


QWEN3_CODER_NEXT = ModelSpec(
    resource_prefix="qwen3-coder-next",
    display_name="Qwen3 Coder Next",
    hf_model_id="Qwen/Qwen3-Coder-Next",
    served_model_name="qwen3-coder-next",
    weight_size_gib=160.0,        # ~160 GiB BF16 (80B params x 2 bytes)
    default_max_model_len=32768,  # native 262K, capped to bound KV
    gated=False,
    dtype="bfloat16",
)


__all__ = ["QWEN3_CODER_NEXT"]
