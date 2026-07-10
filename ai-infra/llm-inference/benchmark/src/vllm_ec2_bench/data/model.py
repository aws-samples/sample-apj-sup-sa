"""ModelSpec — what you need to know about a model to deploy it with vLLM.

This is the central knob that lets ``vllm_ec2_bench`` serve different models.
Everything model-specific — the HF repo id, the weight footprint, the container
image — lives here, so the deployer can stay model-agnostic.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Backend = Literal["vllm-openai", "vllm-neuronx"]
"""vLLM backend flavor.

- ``"vllm-openai"``: standard NVIDIA-GPU backend shipped in ``vllm/vllm-openai``.
- ``"vllm-neuronx"``: Neuron-specific backend running inside the AWS DLC.
"""


class ModelSpec(BaseModel):
    """Everything ``vllm_ec2_bench`` needs to know about a model.

    Instances should be immutable; one per model. Add a new model by creating
    a new ``ModelSpec`` (see ``models/medgemma_27b/model_spec.py`` for the
    reference example).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    resource_prefix: str = Field(
        ...,
        description=(
            "Short, dash-separated identifier used to derive AWS resource names "
            "(IAM role, SG, instance name tag). Must be DNS-label-safe: lowercase "
            "alphanumerics and dashes, 3-40 chars, no leading/trailing dash."
        ),
    )
    display_name: str = Field(..., description="Human-readable name, e.g. 'MedGemma 27B'.")
    hf_model_id: str = Field(
        ...,
        description="Hugging Face repository id, e.g. 'google/medgemma-27b-text-it'.",
    )
    served_model_name: str = Field(
        ...,
        description="Value passed to ``vllm --served-model-name``; what clients query for.",
    )
    weight_size_gib: float = Field(
        ...,
        gt=0,
        description=(
            "Approximate size of the model weights in GiB at the deployment dtype "
            "(usually BF16 for current-gen serving). Drives EBS sizing and MIG "
            "feasibility checks."
        ),
    )
    default_max_model_len: int = Field(
        default=16384,
        ge=1024,
        description="Default ``--max-model-len`` for vLLM; per-experiment override is allowed.",
    )
    vllm_gpu_image: str = Field(
        default="vllm/vllm-openai:v0.20.2",
        description="Container image for NVIDIA-GPU backends. Pin a digest in prod.",
    )
    neuron_image_template: str = Field(
        default="public.ecr.aws/neuron/pytorch-inference-vllm-neuronx:0.13.0-neuronx-py312-sdk2.28.0-ubuntu24.04",
        description=(
            "Image for Neuron instances (inf2/trn1). Uses the public ECR vLLM Neuron DLC "
            "which includes vLLM + NxD Inference pre-installed."
        ),
    )
    gated: bool = Field(
        default=False,
        description="True if the HF repo is gated and requires a token to download.",
    )
    dtype: str = Field(
        default="bfloat16",
        description="Dtype passed to vLLM. Change alongside weight_size_gib if you switch.",
    )
    required_serve_flags: tuple[str, ...] = Field(
        default=(),
        description=(
            "Model-level flag fragments that every experiment using this spec "
            "must thread through ``DeploymentPlan.extra_serve_flags`` (e.g. "
            "``('--tokenizer-mode mistral', '--config-format mistral', "
            "'--load-format mistral')`` for Mistral models that ship only the "
            "Mistral-native artefact). The registry test asserts each plan's "
            "``extra_serve_flags`` contains every fragment as a substring; if "
            "it doesn't, the deploy would fail at runtime with a "
            "tokenizer/config-format error. Plan-specific flags (e.g. "
            "``--kv-cache-dtype fp8`` only on A100-40G) belong on the plan, "
            "not here. Default is the empty tuple."
        ),
    )

    # ---------------------------------------------------------------------
    # Derived resource names
    # ---------------------------------------------------------------------
    @property
    def iam_role_name(self) -> str:
        """IAM role name the deployer creates (idempotently) for this model.

        Uses the DNS-safe ``resource_prefix`` verbatim plus a stable suffix.
        IAM names are case-sensitive in some APIs (RunInstances) and case-
        insensitive in others (GetInstanceProfile), so we keep the casing
        predictable and identical across references.
        """
        return f"{self.resource_prefix}-benchmark-role"

    @property
    def iam_instance_profile_name(self) -> str:
        return f"{self.resource_prefix}-benchmark-instance-profile"

    @property
    def project_tag_value(self) -> str:
        """Value for the project-wide ``Project`` tag on all AWS resources."""
        return f"{self.resource_prefix}-benchmark"

    @property
    def container_name(self) -> str:
        """Docker container name for the vLLM server."""
        return f"{self.resource_prefix}-vllm"

    # ---------------------------------------------------------------------
    # Validators
    # ---------------------------------------------------------------------
    @field_validator("resource_prefix")
    @classmethod
    def _validate_prefix(cls, v: str) -> str:
        v = v.strip()
        # 3-40 chars, lowercase alphanum+dash, must start+end with alphanum
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,38}[a-z0-9]", v):
            raise ValueError(
                "resource_prefix must be 3-40 chars, lowercase alphanumerics + dashes, "
                "no leading/trailing dash (DNS-label-safe)."
            )
        return v

    @field_validator("hf_model_id")
    @classmethod
    def _validate_hf(cls, v: str) -> str:
        v = v.strip()
        if "/" not in v:
            raise ValueError("hf_model_id must be of the form 'namespace/model-id'.")
        return v

    @model_validator(mode="after")
    def _consistency(self) -> ModelSpec:
        # served_model_name defaulting & normalization could live here; keep
        # explicit for now so experiments are readable.
        if not self.served_model_name:
            raise ValueError("served_model_name must not be empty.")
        return self
