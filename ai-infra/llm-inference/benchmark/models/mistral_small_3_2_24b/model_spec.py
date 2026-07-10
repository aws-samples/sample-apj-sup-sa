"""Mistral-Small 3.2 24B Instruct ModelSpec.

Dense 24B Apache-2.0 model, 128K native context, multimodal-capable but used
text-only here. ~55 GiB BF16.
"""
from __future__ import annotations

from vllm_ec2_bench import ModelSpec


MISTRAL_SMALL_3_2_24B = ModelSpec(
    resource_prefix="mistral-small-3-2-24b",
    display_name="Mistral Small 3.2 24B",
    hf_model_id="mistralai/Mistral-Small-3.2-24B-Instruct-2506",
    served_model_name="mistral-small-3-2-24b",
    weight_size_gib=55.0,
    default_max_model_len=32768,
    gated=False,
    dtype="bfloat16",
    # Mistral-Small-3.2 ships only the Mistral-native artefact (no HF-format
    # weights). Without these three flags vLLM fails to load with a
    # tokenizer/config-format error. Every experiment inherits the requirement.
    required_serve_flags=(
        "--tokenizer-mode mistral",
        "--config-format mistral",
        "--load-format mistral",
    ),
)


__all__ = ["MISTRAL_SMALL_3_2_24B"]
