"""ExperimentConfig — ties a ModelSpec to a DeploymentPlan.

Since :class:`DeploymentPlan` no longer embeds a HardwareSpec, this config
can no longer validate "weight fits on TP group" at construction time either.
Instead, callers invoke :meth:`validate_against` with a populated
:class:`~vllm_ec2_bench.data.catalog.Catalog` before launching.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from .deployment import DeploymentPlan
from .model import ModelSpec

if TYPE_CHECKING:
    from .catalog import Catalog


class ExperimentConfig(BaseModel):
    """One row in the benchmark matrix: one model, one deployment plan.

    The plan + model_spec + a few serving knobs. Validation that needs
    hardware facts (weight-fit, TP×DP×PP match) happens in
    :meth:`validate_against` once a :class:`Catalog` is loaded.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_spec: ModelSpec
    deployment: DeploymentPlan

    # --- vLLM serving knobs --------------------------------------------------
    gpu_memory_utilization: float = Field(
        default=0.90, gt=0, le=0.98,
        description=(
            "vLLM --gpu-memory-utilization. 0.90 is a safe default that "
            "leaves headroom for activations."
        ),
    )
    enable_prefix_caching: bool = Field(default=True)

    # --- EBS sizing ---------------------------------------------------------
    ebs_headroom_gib: int = Field(
        default=50, ge=10,
        description="GiB added on top of 2.5 × weight_size_gib when sizing the root volume.",
    )

    # ---------------------------------------------------------------------
    # Catalog-independent derived properties
    # ---------------------------------------------------------------------
    @property
    def root_ebs_gib(self) -> int:
        """Root-volume size — derived from weight size + headroom."""
        est = int(self.model_spec.weight_size_gib * 2.5 + self.ebs_headroom_gib)
        return max(100, min(2048, est))

    @property
    def effective_max_model_len(self) -> int:
        """vLLM --max-model-len — per-experiment override or the model default."""
        return self.deployment.max_model_len or self.model_spec.default_max_model_len

    @property
    def model_replicas(self) -> int:
        return self.deployment.model_replicas

    # ---------------------------------------------------------------------
    # Catalog-dependent helpers
    # ---------------------------------------------------------------------
    def price_per_replica_usd_per_hour(self, catalog: "Catalog") -> float | None:
        """$/hr/replica at on-demand price in ``deployment.region``, or None.

        Returns None when the catalog has no OD price for this
        (instance, region) — e.g. Capacity-Block-only instances or regions
        not yet refreshed.
        """
        price = catalog.price_od(self.deployment.instance_type, self.deployment.region)
        if price is None:
            return None
        return price / self.model_replicas

    def validate_against(self, catalog: "Catalog") -> None:
        """Run hardware-dependent cross-field checks. Raises on any failure."""
        # 1. Delegate structural hardware checks to the plan
        self.deployment.validate_against(catalog)

        # 2. Weight-fit check — per-replica VRAM must accommodate the model
        facts = catalog.hardware(self.deployment.instance_type)
        dep = self.deployment

        if dep.mig_profile is None:
            vram_per_replica = facts.vram_gib_per_accelerator * dep.tensor_parallel
        else:
            m = re.search(r"(\d+(?:\.\d+)?)\s*gb$", dep.mig_profile, re.IGNORECASE)
            if not m:
                raise ValueError(f"Cannot parse memory size from MIG profile {dep.mig_profile!r}")
            vram_per_replica = float(m.group(1))

        min_required = self.model_spec.weight_size_gib * 1.05  # 5% overhead
        if vram_per_replica < min_required:
            raise ValueError(
                f"{dep.experiment_id}: weight-fit check failed: "
                f"per-replica VRAM {vram_per_replica:.1f} GiB < "
                f"{min_required:.1f} GiB required "
                f"(weight={self.model_spec.weight_size_gib:.1f} GiB × 1.05 overhead). "
                "Increase TP, use a larger MIG profile, or pick bigger hardware."
            )
