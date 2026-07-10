"""Gemma 4 31B Instruct ModelSpec.

Apache-2.0 dense 31B model from Google (Apr 2026 release), 256K native context,
multimodal-capable but used text-only here. ~64 GiB BF16.
"""
from __future__ import annotations

from vllm_ec2_bench import ModelSpec


GEMMA_4_31B = ModelSpec(
    resource_prefix="gemma-4-31b",
    display_name="Gemma 4 31B Instruct",
    hf_model_id="google/gemma-4-31B-it",
    served_model_name="gemma-4-31b",
    weight_size_gib=64.0,
    default_max_model_len=32768,
    gated=False,
    dtype="bfloat16",
)


__all__ = ["GEMMA_4_31B"]
