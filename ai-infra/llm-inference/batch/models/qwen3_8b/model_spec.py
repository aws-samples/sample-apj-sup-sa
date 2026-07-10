"""Qwen3-8B model specification (Apache-2.0, dense, 32K context)."""
from llm_batch_deploy import ModelSpec

QWEN3_8B = ModelSpec(
    resource_prefix="qwen3-8b",
    hf_model_id="Qwen/Qwen3-8B",
    served_model_name="qwen3-8b",
    weight_size_gib=16.0,         # ~16 GiB BF16
    default_max_model_len=32768,
    gated=False,                  # Apache-2.0, no HF token required
    dtype="bfloat16",
)
