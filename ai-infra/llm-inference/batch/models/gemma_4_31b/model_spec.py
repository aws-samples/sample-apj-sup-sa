"""Gemma 4 31B Instruct (Apache-2.0 — released Apr 2026, dense, 256K context, multimodal)."""
from llm_batch_deploy import ModelSpec

GEMMA_4_31B = ModelSpec(
    resource_prefix="gemma-4-31b",
    hf_model_id="google/gemma-4-31B-it",
    served_model_name="gemma-4-31b",
    weight_size_gib=64.0,         # ~64 GiB BF16
    default_max_model_len=32768,  # native 256K but cap KV cache for default plans
    gated=False,                  # Gemma 4 ships under Apache-2.0
    dtype="bfloat16",
)
