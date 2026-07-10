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
