"""Llama 4 Scout 17B-16E Instruct (Llama-4 Community License, 109B-MoE-17B-active).

Total ~109B params (BF16 weights ~218 GiB on disk), 16 experts of which 1 is
active per token (~17B effective active params). Fits on p4d.24xlarge (8xA100-40G,
320 GiB total VRAM, TP=8) with ``--kv-cache-dtype fp8`` to get usable context.

Gated on HuggingFace — requires accepted Llama 4 Community License + HF_TOKEN.
"""
from llm_batch_deploy import ModelSpec

LLAMA_4_SCOUT_17B = ModelSpec(
    resource_prefix="llama-4-scout-17b",
    hf_model_id="meta-llama/Llama-4-Scout-17B-16E-Instruct",
    served_model_name="llama-4-scout",
    weight_size_gib=218.0,        # ~218 GiB BF16 (109B params × 2 bytes)
    default_max_model_len=65536,  # 64K with fp8 KV; native ctx is up to 10M
    gated=True,                   # Llama 4 Community License
    dtype="bfloat16",
)
