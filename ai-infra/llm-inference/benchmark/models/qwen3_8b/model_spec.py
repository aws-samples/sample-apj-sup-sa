"""Qwen3-8B ModelSpec.

Dense 8B Apache-2.0 model, native 32K context. Smallest model in the lineup -
fits comfortably on a single 24-GiB GPU (g5/g6/g6.xlarge tier) or any single
L40S/Blackwell with abundant KV-cache headroom.
"""
from __future__ import annotations

from vllm_ec2_bench import ModelSpec


QWEN3_8B = ModelSpec(
    resource_prefix="qwen3-8b",
    display_name="Qwen3 8B",
    hf_model_id="Qwen/Qwen3-8B",
    served_model_name="qwen3-8b",
    # ~8.2B parameters x 2 bytes (BF16) ~ 16.4 GiB. Round to 17 for headroom.
    weight_size_gib=17.0,
    default_max_model_len=32768,
    gated=False,
    dtype="bfloat16",
)


__all__ = ["QWEN3_8B"]
