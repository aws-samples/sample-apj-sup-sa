"""DeploymentPlan — how to deploy a model on a specific EC2 instance type.

The plan references its instance type by **id** (``instance_type: str``),
not by value. Hardware facts (num_accelerators, family, VRAM) are looked up
from a :class:`vllm_ec2_bench.data.catalog.Catalog` at the point of
use — :meth:`DeploymentPlan.validate_against`, the runner, or the notebook
comparison table.

This separation keeps plans serializable, independent of AWS availability,
and safe to construct at import time in ``models/<name>/experiments.py``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    from .catalog import Catalog


CapacityMode = Literal["spot", "on-demand", "odcr", "capacity-block"]
"""Capacity sourcing mode.

- ``"spot"``:          EC2 Spot Fleet with capacity-optimized allocation.
- ``"on-demand"``:     Plain RunInstances.
- ``"odcr"``:          Auto-create a targeted On-Demand Capacity Reservation.
- ``"capacity-block"``: Automated ML Capacity Block purchase (p5/p5e only).
"""


# Known NVIDIA MIG profile IDs — (GI profile id, CI profile id).
# IDs are identical across datacenter-GPU generations for the same
# geometry; only the memory sizes differ. Verified live on A100-40GB 2026-05-02.
KNOWN_MIG_PROFILES: dict[str, tuple[int, int]] = {
    # A100 40GB
    "1g.5gb":   (19, 0),
    "2g.10gb":  (14, 0),
    "3g.20gb":  (9, 0),
    "4g.20gb":  (5, 0),
    "7g.40gb":  (0, 0),
    # A100 80GB
    "1g.10gb":  (19, 0),
    "2g.20gb":  (14, 0),
    "3g.40gb":  (9, 0),
    "4g.40gb":  (5, 0),
    "7g.80gb":  (0, 0),
    # H100 80GB (same IDs as A100 family)
    # H200 141GB
    "1g.18gb":  (19, 0),
    "2g.35gb":  (14, 0),
    "3g.71gb":  (9, 0),
    "4g.71gb":  (5, 0),
    "7g.141gb": (0, 0),
}


class DeploymentPlan(BaseModel):
    """Plan for deploying a model on a specific EC2 instance type.

    Fields this plan *knows* on its own (so construction has no
    catalog dependency):

    * ``instance_type`` — the EC2 type string.
    * ``region`` — target region (governs pricing).
    * ``tensor_parallel / data_parallel / pipeline_parallel``.
    * ``mig_profile / mig_replicas_per_gpu`` — validated against
      :data:`KNOWN_MIG_PROFILES` structurally (MIG-only-on-GPU check moves
      to :meth:`validate_against` since it needs the hardware family).
    * ``capacity_preference`` — ordered list of modes to try.
    * ``preferred_azs`` — optional AZ list.

    What this plan **can't** check without a catalog:

    * TP × DP × PP == effective device count (needs num_accelerators).
    * MIG only on GPU family (needs hardware.family).
    * Weight fits on TP group (needs VRAM — this lives on ExperimentConfig).

    Use :meth:`validate_against` (or ``ExperimentConfig.validate_against``)
    to run those checks once a Catalog is available.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    experiment_id: str = Field(..., description="Short id like 'exp_1' for notebook cells.")
    instance_type: str = Field(
        ..., description="EC2 instance type, e.g. 'g5.12xlarge'. Must exist in the Catalog."
    )

    tensor_parallel: int = Field(..., ge=1, description="vLLM --tensor-parallel-size.")
    data_parallel: int = Field(..., ge=1, description="vLLM --data-parallel-size (== replicas for inference).")
    pipeline_parallel: int = Field(default=1, ge=1, description="vLLM --pipeline-parallel-size.")

    mig_profile: str | None = Field(
        default=None,
        description="NVIDIA MIG profile name, e.g. '3g.71gb'. Only valid for GPU family.",
    )
    mig_replicas_per_gpu: int = Field(
        default=1, ge=1,
        description="Number of MIG slices per physical GPU. Must be 1 when mig_profile is None.",
    )

    max_model_len: int | None = Field(
        default=None,
        description="vLLM --max-model-len. If None, falls back to ModelSpec.default_max_model_len.",
    )

    region: str = Field(default="us-east-2", description="AWS region where this experiment runs.")
    preferred_azs: tuple[str, ...] = Field(
        default=(),
        description=(
            "Preferred AZs in priority order. Empty = deployer discovers AZs "
            "where the instance type is offered."
        ),
    )
    capacity_preference: list[CapacityMode] = Field(
        ..., min_length=1,
        description="Ordered list of capacity modes to try, first-match-wins.",
    )

    concurrency_high: int = Field(
        default=20, ge=1,
        description="Highest concurrency tier for the LLMeter sweep (1, 10, this).",
    )
    extra_serve_flags: str = Field(
        default="",
        description=(
            "Optional extra flags appended verbatim to the ``vllm serve`` "
            "invocation rendered into user-data (e.g. ``--kv-cache-dtype fp8`` "
            "for Llama-4-Scout on p4d to fit KV cache on 8xA100-40G). "
            "Whitespace-trimmed before render. Plan-author-provided; never "
            "user input."
        ),
    )
    extra_env_vars: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Optional per-plan environment variables exported into the vLLM "
            "container (e.g. ``{'VLLM_USE_FLASHINFER_MOE_MXFP4_MXFP8': '1'}`` "
            "for gpt-oss-20b on Blackwell, or "
            "``{'VLLM_ATTENTION_BACKEND': 'TRITON_ATTN_VLLM_V1'}`` for "
            "gpt-oss-20b on Ampere). Names must match ``[A-Z_][A-Z0-9_]*``. "
            "Reserved names like ``HF_TOKEN``/``HUGGING_FACE_HUB_TOKEN`` and "
            "every framework-set variable cannot be overridden."
        ),
    )
    vllm_ready_timeout_s: int = Field(
        default=2400, ge=60,
        description=(
            "How long DeploymentRunner waits for vLLM's /v1/models to return "
            "200 before giving up. The default (40 min) covers the in-lineup "
            "models up to ~55 GiB (MedGemma-27B). Llama-4-Scout (218 GiB BF16) "
            "needs ~75 min worst case for the HuggingFace download alone, plus "
            "warmup — bump to 5400 for the p4d/p4de plans. Same shape as the "
            "batch driver vllm_startup_timeout_seconds — every layer that "
            "wraps the HF weight download must cover the slow path."
        ),
    )
    notes: str = Field(default="", description="Short prose describing the packing.")

    # ---------------------------------------------------------------------
    # Derived properties (no catalog needed)
    # ---------------------------------------------------------------------
    @property
    def model_replicas(self) -> int:
        """Number of independent model copies served (equal to data_parallel).

        DP already accounts for MIG multiplication — a p5e plan with DP=16
        reflects 8 GPUs × 2 MIG slices.
        """
        return self.data_parallel

    @property
    def mig_profile_ids(self) -> tuple[int, int] | None:
        """``(GI id, CI id)`` for the configured MIG profile, or None."""
        if self.mig_profile is None:
            return None
        return KNOWN_MIG_PROFILES[self.mig_profile]

    # ---------------------------------------------------------------------
    # Structural validators (no catalog needed)
    # ---------------------------------------------------------------------
    @field_validator("extra_env_vars")
    @classmethod
    def _validate_extra_env_vars(cls, v: dict[str, str]) -> dict[str, str]:
        import re
        # Reserve framework-controlled variables. Plans must not shadow them.
        reserved = {
            "HF_TOKEN", "HUGGING_FACE_HUB_TOKEN",
            "HF_HOME",  # set in user_data template
        }
        name_re = re.compile(r"^[A-Z_][A-Z0-9_]*$")
        for name in v:
            if not name_re.match(name):
                raise ValueError(
                    f"extra_env_vars name {name!r} must match [A-Z_][A-Z0-9_]*."
                )
            if name in reserved:
                raise ValueError(
                    f"extra_env_vars name {name!r} is reserved by the framework."
                )
        return v

    @field_validator("instance_type")
    @classmethod
    def _validate_instance_type(cls, v: str) -> str:
        v = v.strip()
        if not v or "." not in v:
            raise ValueError(f"Invalid EC2 instance_type: {v!r}")
        return v

    @field_validator("mig_profile")
    @classmethod
    def _validate_mig_profile(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if v not in KNOWN_MIG_PROFILES:
            raise ValueError(
                f"Unknown MIG profile {v!r}. Known: {sorted(KNOWN_MIG_PROFILES)}"
            )
        return v

    @field_validator("capacity_preference")
    @classmethod
    def _validate_capacity_preference(cls, v: list[CapacityMode]) -> list[CapacityMode]:
        if len(set(v)) != len(v):
            raise ValueError("capacity_preference must not contain duplicates.")
        return v

    # ---------------------------------------------------------------------
    # Cross-field validation that requires the catalog
    # ---------------------------------------------------------------------
    def effective_device_count(self, catalog: "Catalog") -> int:
        """Devices vLLM will see (``num_accelerators × mig_replicas_per_gpu``)."""
        facts = catalog.hardware(self.instance_type)
        if self.mig_profile is not None:
            return facts.num_accelerators * self.mig_replicas_per_gpu
        return facts.num_accelerators

    def validate_against(self, catalog: "Catalog") -> None:
        """Run the cross-field checks that require hardware facts.

        Raises :class:`ValueError` on any inconsistency. Call this before
        launching, or from :meth:`ExperimentConfig.validate_against`.
        """
        facts = catalog.hardware(self.instance_type)

        if self.mig_profile is not None and facts.family != "gpu":
            raise ValueError(
                f"{self.experiment_id}: mig_profile is GPU-only; "
                f"{self.instance_type} has family={facts.family}."
            )
        if self.mig_profile is None and self.mig_replicas_per_gpu != 1:
            raise ValueError(
                f"{self.experiment_id}: mig_replicas_per_gpu must be 1 "
                "when mig_profile is not set."
            )

        tp_dp_pp = self.tensor_parallel * self.data_parallel * self.pipeline_parallel
        effective = self.effective_device_count(catalog)
        if tp_dp_pp != effective:
            raise ValueError(
                f"{self.experiment_id}: Parallelism mismatch — "
                f"TP × DP × PP = {tp_dp_pp}, but effective device count is "
                f"{effective} (num_accelerators={facts.num_accelerators}, "
                f"mig_replicas_per_gpu={self.mig_replicas_per_gpu})."
            )
