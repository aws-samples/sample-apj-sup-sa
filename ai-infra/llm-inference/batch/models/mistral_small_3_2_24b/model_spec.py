"""Mistral-Small 3.2 24B Instruct (Apache-2.0, dense, 128K context, multimodal)."""
from llm_batch_deploy import ModelSpec

MISTRAL_SMALL_3_2_24B = ModelSpec(
    resource_prefix="mistral-small-3-2-24b",
    hf_model_id="mistralai/Mistral-Small-3.2-24B-Instruct-2506",
    served_model_name="mistral-small-3-2-24b",
    weight_size_gib=55.0,          # ~55 GiB BF16
    default_max_model_len=32768,
    gated=False,                   # Apache-2.0
    dtype="bfloat16",
    # Mistral-Small-3.2 ships only the Mistral-native artefact (no HF-format
    # weights). Without these three flags vLLM fails to load with a
    # tokenizer/config-format error. Every plan inherits the requirement.
    required_serve_flags=(
        "--tokenizer-mode mistral",
        "--config-format mistral",
        "--load-format mistral",
    ),
)
