"""MedGemma-27B model specification."""
from llm_batch_deploy import ModelSpec

MEDGEMMA_27B = ModelSpec(
    resource_prefix="medgemma-27b",
    hf_model_id="google/medgemma-27b-text-it",
    served_model_name="medgemma-27b",
    weight_size_gib=55.0,
    default_max_model_len=16384,
    gated=True,
    dtype="bfloat16",
)
