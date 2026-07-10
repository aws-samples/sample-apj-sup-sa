"""Percentile statistics for a cell's samples.

We compute our own stats (rather than llmeter's ``Result.stats``) because we run
requests through the scheduler one-by-one, not via llmeter's ``Runner``, and we
want a compact, serialisable summary keyed exactly to TTFT + total latency.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

# Percentiles requested by the user, plus a couple of useful anchors.
PERCENTILES = (20, 50, 90)


@dataclass
class Sample:
    """One invocation's measured outcome."""

    ttft: float | None  # time to first token (s)
    total: float | None  # time to last token (s)
    served_tier: str | None
    num_tokens_output: int | None
    error: str | None
    request_time: str | None = None  # ISO-8601


@dataclass
class MetricStats:
    """Percentile/summary stats for one metric (e.g. TTFT) over a cell."""

    n: int
    mean: float | None
    min: float | None
    max: float | None
    p20: float | None = None
    p50: float | None = None
    p90: float | None = None

    @classmethod
    def from_values(cls, values: list[float | None]) -> MetricStats:
        """Build percentile stats, ignoring ``None`` and NaN entries."""
        clean = [v for v in values if v is not None and not (isinstance(v, float) and np.isnan(v))]
        if not clean:
            return cls(n=0, mean=None, min=None, max=None)
        arr = np.asarray(clean, dtype=float)
        # 'linear' interpolation = numpy default; fine for n>=30.
        pcts = np.percentile(arr, PERCENTILES)
        stats = cls(
            n=int(arr.size),
            mean=float(arr.mean()),
            min=float(arr.min()),
            max=float(arr.max()),
        )
        for p, v in zip(PERCENTILES, pcts, strict=True):
            setattr(stats, f"p{p}", float(v))
        return stats


@dataclass
class CellSummary:
    """Everything we report for one (model, transport, tier, region) cell."""

    label: str
    family: str
    model_key: str
    display_name: str
    transport: str
    tier: str
    region: str
    model_id: str

    requested: int
    succeeded: int
    failed: int
    served_tiers: dict[str, int] = field(default_factory=dict)  # observed tier -> count
    errors: dict[str, int] = field(default_factory=dict)  # error class -> count

    ttft: MetricStats | None = None
    total_latency: MetricStats | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def summarize(
    *,
    label: str,
    family: str,
    model_key: str,
    display_name: str,
    transport: str,
    tier: str,
    region: str,
    model_id: str,
    requested: int,
    samples: list[Sample],
) -> CellSummary:
    """Reduce a cell's samples to a :class:`CellSummary`."""
    ok = [s for s in samples if s.error is None]
    bad = [s for s in samples if s.error is not None]

    served_counts: dict[str, int] = {}
    for s in ok:
        key = s.served_tier or "(unreported)"
        served_counts[key] = served_counts.get(key, 0) + 1

    error_counts: dict[str, int] = {}
    for s in bad:
        # Bucket by the leading token of the error (e.g. exception class name).
        key = (s.error or "error").split(":")[0].strip()[:60] or "error"
        error_counts[key] = error_counts.get(key, 0) + 1

    return CellSummary(
        label=label,
        family=family,
        model_key=model_key,
        display_name=display_name,
        transport=transport,
        tier=tier,
        region=region,
        model_id=model_id,
        requested=requested,
        succeeded=len(ok),
        failed=len(bad),
        served_tiers=served_counts,
        errors=error_counts,
        ttft=MetricStats.from_values([s.ttft for s in ok]),
        total_latency=MetricStats.from_values([s.total for s in ok]),
    )
