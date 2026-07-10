"""Llama 4 Scout 17B-16E Instruct ModelSpec.

Llama 4 Community License (gated). 109B total params (~218 GiB BF16) with 16
experts of which 1 is active per token (~17B effective active params). Fits
on p4d.24xlarge (8x A100-40GB, 320 GiB total VRAM, TP=8) with
``--kv-cache-dtype fp8`` to give usable context (32-64K).
"""
from __future__ import annotations

from vllm_ec2_bench import ModelSpec


LLAMA_4_SCOUT_17B = ModelSpec(
    resource_prefix="llama-4-scout-17b",
    display_name="Llama 4 Scout 17B-16E Instruct",
    hf_model_id="meta-llama/Llama-4-Scout-17B-16E-Instruct",
    served_model_name="llama-4-scout",
    weight_size_gib=218.0,
    default_max_model_len=32768,
    gated=True,
    dtype="bfloat16",
)


__all__ = ["LLAMA_4_SCOUT_17B"]
