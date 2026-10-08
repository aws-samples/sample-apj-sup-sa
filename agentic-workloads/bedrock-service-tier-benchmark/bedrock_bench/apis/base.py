"""Shared measurement primitives for every API adapter.

Each adapter sends one request and feeds stream events into a
:class:`StreamRecorder`, which timestamps them with ``time.perf_counter`` and
produces a :class:`Measurement`. The definitions follow NVIDIA AIPerf and
Artificial Analysis (see ``docs/DESIGN.md`` section 3):

* TTFT  — first event carrying non-empty generated text *or* reasoning.
* TTFAT — first event carrying non-empty answer text.
* E2E   — last event carrying generated text or reasoning.
* ITL   — ``(E2E - TTFT) / (output_tokens - 1)``.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol


@dataclass
class Usage:
    """Token accounting as reported by the API (``None`` = not reported)."""

    input_tokens: int | None = None  # total prompt tokens, cached included
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None


@dataclass
class Measurement:
    """One request's outcome. Times are seconds from request start."""

    ttft: float | None = None
    ttfat: float | None = None
    e2e: float | None = None
    served_tier: str | None = None
    usage: Usage = field(default_factory=Usage)
    server_first_byte: float | None = None  # Bedrock-reported, seconds
    error: str | None = None
    error_kind: str | None = None  # throttled | tier_unsupported | timeout | client | server
    finish_reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.e2e is not None

    @property
    def itl(self) -> float | None:
        """Mean inter-token latency over the decode phase (AIPerf definition)."""
        n = self.usage.output_tokens
        if self.ttft is None or self.e2e is None or not n or n < 2:
            return None
        return max(self.e2e - self.ttft, 0.0) / (n - 1)

    @property
    def output_tps(self) -> float | None:
        """Output tokens per second after the first token."""
        itl = self.itl
        return 1.0 / itl if itl else None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["itl"] = self.itl
        d["output_tps"] = self.output_tps
        return d


class StreamRecorder:
    """Timestamps stream events relative to a start mark."""

    def __init__(self) -> None:
        self._t0: float | None = None
        self.m = Measurement()

    def start(self) -> None:
        self._t0 = time.perf_counter()

    def _now(self) -> float:
        if self._t0 is None:
            raise RuntimeError("StreamRecorder.start() was not called")
        return time.perf_counter() - self._t0

    def reasoning(self, text: str | None) -> None:
        """Record a reasoning delta (counts toward TTFT, not TTFAT)."""
        if text:
            t = self._now()
            if self.m.ttft is None:
                self.m.ttft = t
            self.m.e2e = t

    def answer(self, text: str | None) -> None:
        """Record an answer-text delta."""
        if text:
            t = self._now()
            if self.m.ttft is None:
                self.m.ttft = t
            if self.m.ttfat is None:
                self.m.ttfat = t
            self.m.e2e = t

    def finish(self) -> None:
        """Mark completion of a non-streaming response (E2E only, no TTFT)."""
        t = self._now()
        if self.m.e2e is None:
            self.m.e2e = t

    def fail(self, exc: BaseException) -> Measurement:
        self.m.error = f"{type(exc).__name__}: {str(exc)[:300]}"
        self.m.error_kind = classify_error(exc)
        return self.m


_TIER_ERRORS = (
    "service tier is not supported",
    "unsupported service_tier",
    "servicetier",
    "service_tier",
)


def classify_error(exc: BaseException) -> str:
    """Bucket an exception for reporting. Never raises."""
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if "timeout" in name or "timed out" in msg:
        return "timeout"
    if "throttl" in name or "throttl" in msg or "too many requests" in msg or " 429" in msg:
        return "throttled"
    if any(s in msg for s in _TIER_ERRORS) and ("not supported" in msg or "unsupported" in msg):
        return "tier_unsupported"
    if "accessdenied" in name or "access denied" in msg or " 403" in msg:
        return "access_denied"
    if any(s in msg for s in ("not found", "does not exist", "isn't supported on this route")):
        return "not_found"
    if "validation" in name or " 400" in msg:
        return "client"
    return "server"


@dataclass(frozen=True)
class Request:
    """Everything an adapter needs to send one request.

    ``document`` is cacheable shared context; ``question`` follows it. When
    ``explicit_cache`` is set the adapter places its native cache checkpoint
    between them.
    """

    model_id: str
    region: str
    document: str
    question: str
    max_tokens: int
    tier: str | None  # None = omit (Standard)
    explicit_cache: bool = False
    temperature: float | None = 0.0
    timeout: float = 180.0
    #: Reasoning effort for reasoning models (``None`` = model default).
    reasoning_effort: str | None = None


class Adapter(Protocol):
    """One API on one endpoint."""

    def send(self, req: Request) -> Measurement:  # pragma: no cover - protocol
        ...


def as_int(v: Any) -> int | None:
    """Coerce an API token count to int, tolerating absent / null values."""
    try:
        return None if v is None else int(v)
    except (TypeError, ValueError):
        return None
