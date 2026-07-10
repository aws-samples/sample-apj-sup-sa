"""Qwen3-30B-A3B Instruct ModelSpec.

MoE Apache-2.0 model: 30B total params (~62 GiB BF16) of which 3.3B are active
per token. Per-token compute roughly matches an 8B dense model, but the full
30B weights still need to fit in VRAM. Native 256K context, capped here.
"""
from __future__ import annotations

from vllm_ec2_bench import ModelSpec


QWEN3_30B_A3B = ModelSpec(
    resource_prefix="qwen3-30b-a3b",
    display_name="Qwen3 30B A3B Instruct",
    hf_model_id="Qwen/Qwen3-30B-A3B-Instruct-2507",
    served_model_name="qwen3-30b-a3b",
    weight_size_gib=62.0,
    default_max_model_len=32768,
    gated=False,
    dtype="bfloat16",
)


__all__ = ["QWEN3_30B_A3B"]
