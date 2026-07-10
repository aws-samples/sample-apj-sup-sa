"""HardwareFacts — immutable hardware description for one EC2 instance type.

This is the "what does this instance have?" object. It never contains
pricing information; that belongs to :class:`vllm_ec2_bench.data.catalog.Catalog`
and lives in the cache file's ``prices`` section.

The split matters because hardware facts and prices move at completely
different rates:

* Hardware facts: effectively immutable once AWS publishes an instance type.
  Refreshed once in a blue moon.
* Prices: drift weekly and vary by region. Refreshed frequently.

Storing them together (as the old :class:`HardwareSpec` did) created the
temptation to hand-maintain prices in source code. Keeping them separate
removes that temptation structurally.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


Family = Literal["gpu", "neuron"]
"""Hardware family.

- ``"gpu"``: NVIDIA-based (A10G, L4, L40S, A100, H100, H200, Blackwell, ...).
- ``"neuron"``: AWS-custom (Inferentia2, Trainium1, ...).
"""


class HardwareFacts(BaseModel):
    """Pure hardware description — no pricing.

    Source of truth: AWS ``DescribeInstanceTypes``, except for
    ``accelerator_architecture`` which isn't returned by the API and comes
    from a code-level lookup in
    :mod:`vllm_ec2_bench.data.aws_sources.guess_architecture`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    instance_type: str = Field(..., description="EC2 instance type, e.g. 'g5.12xlarge'.")
    family: Family = Field(..., description="Hardware family: 'gpu' or 'neuron'.")

    accelerator_model: str = Field(
        ...,
        description="Human-readable accelerator name, e.g. 'NVIDIA A10G', 'AWS Inferentia2'.",
    )
    accelerator_architecture: str = Field(
        ...,
        description="Generation/architecture, e.g. 'Ampere', 'Ada Lovelace', 'Blackwell', 'Neuron 2nd gen'.",
    )
    num_accelerators: int = Field(..., gt=0, description="Count of accelerators on the instance.")
    vram_gib_per_accelerator: float = Field(
        ..., gt=0,
        description="HBM / per-device memory in GiB (as reported by nvidia-smi or Neuron tools).",
    )

    # Host resources
    vcpu: int = Field(..., gt=0)
    ram_gib: int = Field(..., gt=0, description="System RAM, NOT accelerator VRAM.")

    @property
    def vram_gib_total(self) -> float:
        """Total accelerator memory across all devices on the instance."""
        return self.num_accelerators * self.vram_gib_per_accelerator

    @field_validator("instance_type")
    @classmethod
    def _validate_instance_type(cls, v: str) -> str:
        v = v.strip()
        if not v or "." not in v:
            raise ValueError(f"Invalid EC2 instance_type: {v!r}")
        return v

    # ---------------------------------------------------------------------
    # Convenience constructor — builds from DescribeInstanceTypes response.
    # ---------------------------------------------------------------------
    @classmethod
    def from_describe_instance_types(cls, instance_type: str, ec2_client: Any) -> "HardwareFacts":
        """Call AWS and return :class:`HardwareFacts` for ``instance_type``.

        Wraps :func:`vllm_ec2_bench.data.aws_sources.fetch_hardware_from_describe_instance_types`.
        """
        from .aws_sources import fetch_hardware_from_describe_instance_types
        fields = fetch_hardware_from_describe_instance_types(instance_type, ec2_client)
        return cls(instance_type=instance_type, **fields)


__all__ = ["Family", "HardwareFacts"]
