"""MedGemma-27B ModelSpec.

This is the single place where MedGemma-specific model facts live. Adding a
new model = create a sibling folder with its own ``model_spec.py``,
``experiments.py``, and ``prompts.py``.
"""
from __future__ import annotations

from vllm_ec2_bench import ModelSpec


MEDGEMMA_27B = ModelSpec(
    resource_prefix="medgemma-27b",
    display_name="MedGemma 27B",
    hf_model_id="google/medgemma-27b-text-it",
    served_model_name="medgemma-27b",
    # ~27B parameters × 2 bytes (BF16) ≈ 54 GiB. We use 55 to leave a small
    # margin for the embeddings / LM head.
    weight_size_gib=55.0,
    default_max_model_len=16384,
    gated=True,
    dtype="bfloat16",
)


__all__ = ["MEDGEMMA_27B"]
