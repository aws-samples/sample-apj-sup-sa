"""gpt-oss-20b ModelSpec.

21B-total / 3.6B-active MoE Apache-2.0 model with 32 experts (top-4 + 1
shared) and native MXFP4 quantization on Blackwell. Native context 131K.
"""
from __future__ import annotations

from vllm_ec2_bench import ModelSpec


GPT_OSS_20B = ModelSpec(
    resource_prefix="gpt-oss-20b",
    display_name="gpt-oss 20B",
    hf_model_id="openai/gpt-oss-20b",
    served_model_name="gpt-oss-20b",
    # BF16 resident on Ampere; MXFP4 native on Blackwell ~13 GiB. Round to
    # 42 for the BF16 fallback path's headroom.
    weight_size_gib=42.0,
    default_max_model_len=131072,
    gated=False,
    dtype="bfloat16",
)


__all__ = ["GPT_OSS_20B"]
