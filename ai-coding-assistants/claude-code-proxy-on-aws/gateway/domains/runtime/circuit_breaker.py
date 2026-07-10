"""In-memory circuit breaker for Bedrock provider health.

State machine per breaker key (a Bedrock region + model pair):

    CLOSED  -- record_failure --> OPEN
    OPEN    -- timer expired   --> HALF (next call probes)
    HALF    -- record_success  --> CLOSED
    HALF    -- record_failure  --> OPEN (timer reset)

Keying by (region, model) rather than region alone keeps one model's outage or
throttle from diverting unrelated models in the same region to 1P. A true
region outage still trips every model independently as each one fails.

Only provider-outage and throttle failures should call record_failure.
Client-bug failures (ValidationException, AccessDenied, ...) do NOT trip
the breaker because the Bedrock service itself is still healthy.

State is per-task in-memory. With multiple ECS tasks behind ALB, each task
discovers Bedrock outage independently on its first failed request.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum


class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF = "half"


@dataclass
class _KeyState:
    state: BreakerState = BreakerState.CLOSED
    opened_at: float = 0.0


class CircuitBreaker:
    """Per-key breaker that decides whether to call Bedrock.

    The key is opaque to the breaker; callers pass a (region, model) identity
    so failures are isolated per model.
    """

    def __init__(self, open_seconds: float = 300.0) -> None:
        self._open_seconds = open_seconds
        self._states: dict[str, _KeyState] = {}
        self._lock = threading.Lock()

    def _now(self) -> float:
        return time.monotonic()

    def _get(self, key: str) -> _KeyState:
        return self._states.setdefault(key, _KeyState())

    def allow_bedrock(self, key: str) -> bool:
        """Return True if the next request for this key should hit Bedrock.

        Side effect: transitions OPEN -> HALF when the open window has expired,
        so the caller is implicitly the probe.
        """
        with self._lock:
            entry = self._get(key)
            if entry.state in (BreakerState.CLOSED, BreakerState.HALF):
                return True
            if self._now() - entry.opened_at >= self._open_seconds:
                entry.state = BreakerState.HALF
                return True
            return False

    def record_success(self, key: str) -> None:
        with self._lock:
            entry = self._get(key)
            entry.state = BreakerState.CLOSED
            entry.opened_at = 0.0

    def record_failure(self, key: str) -> None:
        with self._lock:
            entry = self._get(key)
            entry.state = BreakerState.OPEN
            entry.opened_at = self._now()

    def state(self, key: str) -> BreakerState:
        with self._lock:
            return self._get(key).state
