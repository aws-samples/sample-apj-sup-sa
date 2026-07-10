"""Base types for capacity-sourcing strategies.

A ``CapacityStrategy`` is a class that knows how to provision **one EC2
instance** using a specific capacity mode (spot, on-demand, ODCR, or
capacity-block). The strategy is called by :class:`DeploymentRunner`; if it
can't fulfil the request it raises :class:`CapacityExhausted` and the runner
falls through to the next strategy in the preference list.

Strategies receive a :class:`LaunchContext` so they don't need to know about
the runner internals. This keeps each strategy ~100-200 lines and independently
unit-testable.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from ...data import ExperimentConfig


# -----------------------------------------------------------------------------
# Exceptions
# -----------------------------------------------------------------------------
class CapacityError(Exception):
    """Base class for strategy failures."""


class CapacityExhausted(CapacityError):
    """Strategy couldn't provision capacity in any AZ.

    Raised to signal the runner to try the next mode. Includes the underlying
    cause (usually an :class:`botocore.exceptions.ClientError` with
    ``InsufficientInstanceCapacity``).
    """


class StrategyMisconfigured(CapacityError):
    """Strategy was asked to do something it can't — e.g. CB without confirm flag."""


# -----------------------------------------------------------------------------
# Data passed between runner and strategies
# -----------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class LaunchContext:
    """Everything a strategy needs to launch an instance.

    The runner builds this once per experiment and passes it to whichever
    strategy is next in the preference list.
    """

    config: ExperimentConfig
    security_group_id: str
    ami_id: str
    user_data_b64: str
    iam_instance_profile_name: str
    tags: list[dict[str, str]]              # pre-built list of ``{"Key":..,"Value":..}``

    # Services
    ec2: Any                                # boto3 EC2 client
    iam: Any                                # boto3 IAM client

    # Callbacks the strategy can use (injected so strategies don't import Runner)
    get_subnets_for_preferred_azs: Callable[[], dict[str, str]]
    cleanup_partial_launch: Callable[[str | None, str | None], None]
    """cleanup_partial_launch(instance_id, fleet_id) — best-effort teardown on failure."""


@dataclass(slots=True)
class LaunchResult:
    """What a strategy returns on success."""

    instance_id: str
    availability_zone: str
    subnet_id: str
    capacity_mode: str                      # "spot" | "on-demand" | "odcr" | "capacity-block"

    # Auxiliary resources the runner must clean up on teardown:
    auto_created_odcr_id: str | None = None
    """ODCR this strategy created on the fly (must be cancelled on teardown)."""

    spot_fleet_id: str | None = None
    """Spot Fleet this strategy created (must be deleted on teardown)."""

    launch_template_id: str | None = None
    """Launch template this strategy created (must be deleted on teardown)."""

    # Free-form debugging info:
    metadata: dict[str, Any] = field(default_factory=dict)


# -----------------------------------------------------------------------------
# Strategy interface
# -----------------------------------------------------------------------------
class CapacityStrategy(ABC):
    """Abstract capacity-sourcing strategy.

    Subclasses implement :meth:`launch`. The :class:`DeploymentRunner` picks
    the subclass based on the current item in ``config.deployment.capacity_preference``.
    """

    #: Machine-readable mode name; must match the strings in :data:`CapacityMode`.
    mode: str = ""

    @abstractmethod
    def launch(self, ctx: LaunchContext) -> LaunchResult:
        """Provision one instance. Raise :class:`CapacityExhausted` on ICE."""


class _StrategyFactory(Protocol):
    def __call__(self, **kwargs: Any) -> CapacityStrategy: ...


__all__ = [
    "CapacityError",
    "CapacityExhausted",
    "CapacityStrategy",
    "LaunchContext",
    "LaunchResult",
    "StrategyMisconfigured",
]
