"""Qwen3-30B-A3B Instruct (Apache-2.0, MoE 30B total / 3.3B active, 256K context).

The MoE design means 3.3B params are active per token, so per-token throughput
is closer to an 8B dense model — but the full 30B weights still need to fit.
"""
from llm_batch_deploy import ModelSpec

QWEN3_30B_A3B = ModelSpec(
    resource_prefix="qwen3-30b-a3b",
    hf_model_id="Qwen/Qwen3-30B-A3B-Instruct-2507",
    served_model_name="qwen3-30b-a3b",
    weight_size_gib=62.0,          # ~62 GiB BF16
    default_max_model_len=32768,   # native 256K but cap KV cache for default plans
    gated=False,                   # Apache-2.0
    dtype="bfloat16",
)
