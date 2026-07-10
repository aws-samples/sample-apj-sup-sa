"""openai/gpt-oss-20b (Apache-2.0, MoE 21B total / 3.6B active, MXFP4 native).

21B total parameters across 32 experts (top-4 + 1 shared). Native MXFP4
quantization on Blackwell GPUs gives a ~13 GiB resident footprint; on Ampere
(A100) the model decompresses to BF16 and resides at ~42 GiB.

Native context is 131,072 with attention sinks + sliding-window attention.
The model emits an OpenAI-format reasoning trace; vLLM exposes it via the
``openai_gptoss`` reasoning parser.
"""
from llm_batch_deploy import ModelSpec

GPT_OSS_20B = ModelSpec(
    resource_prefix="gpt-oss-20b",
    hf_model_id="openai/gpt-oss-20b",
    served_model_name="gpt-oss-20b",
    weight_size_gib=42.0,         # BF16 resident on Ampere; MXFP4 on Blackwell ~13 GiB
    default_max_model_len=131072,
    gated=False,                  # Apache-2.0
    dtype="bfloat16",
)
