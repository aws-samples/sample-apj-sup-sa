"""ModelSpec — what model are we serving?

For batch inference we don't need notebook-specific things like
served_model_name stylings, so this is a compact Pydantic model
tailored to the container runtime.
"""
from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

_RESOURCE_PREFIX_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{1,38}[a-z0-9])$")
_HF_ID_RE = re.compile(r"^[^/\s]+/[^/\s]+$")


class ModelSpec(BaseModel):
    """Declarative description of the LLM to serve in the Batch container.

    Field guide
    -----------
    resource_prefix
        Kebab-case identifier used in AWS resource names. e.g. 'medgemma-27b'.
    hf_model_id
        Hugging Face model id, e.g. 'google/medgemma-27b-text-it'.
    served_model_name
        The name clients use in ChatCompletions requests (``model`` field).
    weight_size_gib
        Approximate size on disk (used to reason about EBS / timeout budgets).
    default_max_model_len
        Default ``--max-model-len`` for vLLM. Can be overridden per-plan.
    vllm_image
        Docker image URI that runs vLLM. Defaults to the official image.
    gated
        True if HF repo requires accepting a license; Batch job env must
        carry HF_TOKEN.
    dtype
        vLLM --dtype flag.
    required_serve_flags
        Tuple of model-specific flag fragments that **every** plan using this
        spec must thread through ``extra_serve_flags`` — e.g.
        ``("--kv-cache-dtype fp8",)`` for Llama-4-Scout (won't fit on 8xA100-40G
        without it) or ``("--tokenizer-mode mistral", ...)`` for Mistral models
        that ship only the Mistral-native artefact. The registry test asserts
        each plan's ``extra_serve_flags`` contains every fragment as a
        substring; if it doesn't, the deploy would fail at runtime with an
        OOM or a tokenizer/config-format error. Default is the empty tuple
        (most models need no model-level flags).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    resource_prefix: str = Field(..., min_length=3, max_length=40)
    hf_model_id: str
    served_model_name: str = Field(..., min_length=1, max_length=60)
    weight_size_gib: float = Field(..., gt=0)
    default_max_model_len: int = Field(16384, gt=0)
    vllm_image: str = "vllm/vllm-openai:v0.20.2"
    gated: bool = False
    dtype: str = "bfloat16"
    required_serve_flags: tuple[str, ...] = ()

    @field_validator("resource_prefix")
    @classmethod
    def _check_resource_prefix(cls, v: str) -> str:
        if not _RESOURCE_PREFIX_RE.match(v):
            raise ValueError(
                f"resource_prefix {v!r} must be lowercase alphanumeric + "
                "hyphens, 3-40 chars, start + end with alphanumeric "
                "(DNS-label rules)."
            )
        return v

    @field_validator("hf_model_id")
    @classmethod
    def _check_hf_id(cls, v: str) -> str:
        if not _HF_ID_RE.match(v):
            raise ValueError(
                f"hf_model_id {v!r} must be of the form 'owner/repo'."
            )
        return v

    # ------------------------------------------------------------------
    # Derived names (kept short so 64-char IAM role limits aren't hit).
    # ------------------------------------------------------------------
    @property
    def stack_name(self) -> str:
        """Default CloudFormation stack name for this model."""
        return f"{self.resource_prefix}-batch"

    @property
    def job_definition_name(self) -> str:
        return f"{self.resource_prefix}-batch-jobdef"

    @property
    def container_name(self) -> str:
        return f"{self.resource_prefix}-vllm"

    @property
    def tag_value(self) -> str:
        """Value applied as Project tag on every stack resource."""
        return f"{self.resource_prefix}-batch"
