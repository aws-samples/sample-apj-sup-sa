"""DeploymentState — mutable state for a running experiment.

The runner mutates this as the deployment progresses (instance id, SG id,
public IP, …). Notebook cells can pickle / JSON-dump it between runs.
"""
from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DeploymentState(BaseModel):
    """Everything the runner tracks about one deployment.

    This is NOT frozen — the runner fills it in during ``launch()``.
    """

    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    instance_type: str
    region: str

    # Auth / endpoint
    api_key: str = Field(description="Generated bearer token for vLLM")
    base_url: str | None = None

    # AWS resources (populated as we go)
    security_group_id: str | None = None
    ami_id: str | None = None
    instance_id: str | None = None
    public_ip: str | None = None
    placement_az: str | None = None
    capacity_mode: str | None = None

    # Auto-created resources that teardown must clean up
    spot_fleet_id: str | None = None
    launch_template_id: str | None = None
    auto_created_odcr_id: str | None = None

    # External / pre-existing resources (caller-provided; don't clean up on teardown)
    capacity_reservation_id: str | None = None

    # Access control
    caller_ip_cidr: str | None = None

    # Bookkeeping
    launched_at: float | None = None
    terminated_at: float | None = None

    # Timing breakdown (seconds). These isolate *capacity-acquisition + boot*
    # latency from the benchmark's own run time. They matter for scarce
    # accelerators (p4d/p5/p6) where the deployer may spend many minutes
    # waiting for a spot slot: that wait must NOT be charged against the
    # model's measured throughput. LLMeter separately measures the benchmark
    # window (from first request), so these two never overlap.
    capacity_wait_s: float | None = None
    """Wall-clock spent inside the capacity strategy acquiring an instance
    (spot polling, ICE retries, ODCR creation). Excludes vLLM warmup."""
    vllm_ready_wait_s: float | None = None
    """Wall-clock from instance-acquired to vLLM answering /v1/models 200
    (boot + image pull + HF weight download + warmup)."""

    @property
    def launch_overhead_s(self) -> float | None:
        """Total non-benchmark setup latency = capacity wait + vLLM warmup.

        This is the amount of wall-clock that must be *excluded* when
        reasoning about the model's steady-state benchmark performance.
        """
        if self.capacity_wait_s is None and self.vllm_ready_wait_s is None:
            return None
        return (self.capacity_wait_s or 0.0) + (self.vllm_ready_wait_s or 0.0)

    def mark_launched(self) -> None:
        self.launched_at = time.time()

    def mark_terminated(self) -> None:
        self.terminated_at = time.time()

    def as_public_dict(self) -> dict[str, Any]:
        """Serialize to plain dict, redacting the API key."""
        d = self.model_dump()
        if d.get("api_key"):
            k = d["api_key"]
            d["api_key"] = f"{k[:4]}…{k[-4:]}" if len(k) >= 8 else "•••"
        return d


__all__ = ["DeploymentState"]
