"""Capacity-sourcing strategies for EC2 experiments.

Each strategy provisions one instance using a specific sourcing mode, raising
:class:`CapacityExhausted` to signal the runner to try the next mode.

Public API::

    from vllm_ec2_bench.deployer.capacity import (
        CapacityStrategy, LaunchContext, LaunchResult,
        CapacityError, CapacityExhausted, StrategyMisconfigured,
        SpotFleetStrategy, OnDemandStrategy, ODCRStrategy,
    )
"""
from .base import (
    CapacityError,
    CapacityExhausted,
    CapacityStrategy,
    LaunchContext,
    LaunchResult,
    StrategyMisconfigured,
)
from .odcr import ODCRStrategy
from .ondemand import OnDemandStrategy
from .spot import SpotFleetStrategy

__all__ = [
    "CapacityError",
    "CapacityExhausted",
    "CapacityStrategy",
    "LaunchContext",
    "LaunchResult",
    "ODCRStrategy",
    "OnDemandStrategy",
    "SpotFleetStrategy",
    "StrategyMisconfigured",
]
