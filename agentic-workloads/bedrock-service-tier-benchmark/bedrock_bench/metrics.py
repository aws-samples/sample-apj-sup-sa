"""Statistics for a cell's samples and for tier-vs-default comparisons.

* Per metric: n, mean, stdev, min, max, p10, p50, p90, p95, p99 (numpy 'linear').
* Tier deltas: Δpq = pq(tier) − pq(default) for q in 10, 50, 90, 99, each with a
  seeded bootstrap 95% confidence interval. A delta whose interval contains 0 is
  reported as not significant (``docs/DESIGN.md`` section 3). The top-level
  fields of :class:`Delta` are the p50 values; ``by_percentile`` holds all four.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

PERCENTILES = (10, 50, 90, 95, 99)
#: Percentiles compared between tiers. Tail values need many samples: with n=5, p90 and p99
#: are close to the slowest sample.
DELTA_PERCENTILES = (10, 50, 90, 99)
METRICS = ("ttft", "ttfat", "e2e", "itl", "output_tps")
BOOTSTRAP_RESAMPLES = 10_000


def _clean(values: list[float | None]) -> np.ndarray:
    arr = np.asarray([v for v in values if v is not None], dtype=float)
    return arr[np.isfinite(arr)]


@dataclass
class MetricStats:
    n: int = 0
    mean: float | None = None
    stdev: float | None = None
    min: float | None = None
    max: float | None = None
    p10: float | None = None
    p50: float | None = None
    p90: float | None = None
    p95: float | None = None
    p99: float | None = None

    @classmethod
    def from_values(cls, values: list[float | None]) -> MetricStats:
        arr = _clean(values)
        if arr.size == 0:
            return cls()
        p = np.percentile(arr, PERCENTILES)
        return cls(
            n=int(arr.size),
            mean=float(arr.mean()),
            stdev=float(arr.std(ddof=1)) if arr.size > 1 else 0.0,
            min=float(arr.min()),
            max=float(arr.max()),
            p10=float(p[0]),
            p50=float(p[1]),
            p90=float(p[2]),
            p95=float(p[3]),
            p99=float(p[4]),
        )


@dataclass
class Delta:
    """Difference of a tier versus default for one metric (top-level fields: p50)."""

    metric: str
    default_p50: float | None
    tier_p50: float | None
    delta: float | None = None  # tier − default (seconds, or tok/s for output_tps)
    pct: float | None = None  # relative to default
    ci_low: float | None = None
    ci_high: float | None = None
    significant: bool | None = None
    #: "p10" / "p50" / "p90" / "p99" -> {default, tier, delta, pct, ci_low, ci_high, significant}
    by_percentile: dict[str, dict[str, Any]] = field(default_factory=dict)


def bootstrap_delta(
    default: list[float | None],
    tier: list[float | None],
    *,
    metric: str,
    seed: int = 0,
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> Delta:
    """Δ at p10/p50/p90/p99 with percentile-bootstrap 95% CIs (independent resampling of both groups)."""
    a, b = _clean(default), _clean(tier)
    d = Delta(
        metric=metric,
        default_p50=float(np.median(a)) if a.size else None,
        tier_p50=float(np.median(b)) if b.size else None,
    )
    if a.size < 2 or b.size < 2:
        return d
    rng = np.random.default_rng(seed)
    ra = rng.choice(a, size=(resamples, a.size), replace=True)
    rb = rng.choice(b, size=(resamples, b.size), replace=True)
    for q in DELTA_PERCENTILES:
        da, db = float(np.percentile(a, q)), float(np.percentile(b, q))
        diff = np.percentile(rb, q, axis=1) - np.percentile(ra, q, axis=1)
        lo, hi = (float(x) for x in np.percentile(diff, (2.5, 97.5)))
        delta = db - da
        d.by_percentile[f"p{q}"] = {
            "default": da,
            "tier": db,
            "delta": delta,
            "pct": (delta / da * 100.0) if da else None,
            "ci_low": lo,
            "ci_high": hi,
            "significant": not (lo <= 0.0 <= hi),
        }
    p50 = d.by_percentile["p50"]
    d.delta, d.pct = p50["delta"], p50["pct"]
    d.ci_low, d.ci_high, d.significant = p50["ci_low"], p50["ci_high"], p50["significant"]
    return d


@dataclass
class CellSummary:
    """Everything reported for one cell (one tier in one context)."""

    cell: dict[str, Any]  # dimension values (model, endpoint, api, scope, region, ...)
    requested: int
    succeeded: int
    failed: int
    excluded: dict[str, int] = field(default_factory=dict)  # reason -> count
    errors: dict[str, int] = field(default_factory=dict)  # error_kind -> count
    served_tiers: dict[str, int] = field(default_factory=dict)
    cache_hit_rate: float | None = None  # share of samples with cache_read_tokens > 0
    #: Share of samples whose decode phase took < 5% of E2E: the stream arrived as one
    #: burst after queueing (seen on flex), so ITL is not a decode-speed measure there.
    burst_share: float | None = None
    tokens: dict[str, float | None] = field(default_factory=dict)  # mean per sample
    stats: dict[str, MetricStats] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _mean(values: list[int | None]) -> float | None:
    arr = _clean([float(v) if v is not None else None for v in values])
    return float(arr.mean()) if arr.size else None


def summarize(cell: dict[str, Any], samples: list[dict[str, Any]]) -> CellSummary:
    """Reduce raw sample records to a :class:`CellSummary`.

    Each sample is a dict as written to ``raw.jsonl``: measurement fields plus
    ``included`` (bool) and ``exclude_reason``. Only included, successful
    samples feed the statistics.
    """
    ok = [s for s in samples if s.get("error") is None]
    bad = [s for s in samples if s.get("error") is not None]
    used = [s for s in ok if s.get("included", True)]

    summary = CellSummary(cell=cell, requested=len(samples), succeeded=len(ok), failed=len(bad))
    for s in ok:
        if not s.get("included", True):
            r = s.get("exclude_reason") or "excluded"
            summary.excluded[r] = summary.excluded.get(r, 0) + 1
        key = s.get("served_tier") or "(not reported)"
        summary.served_tiers[key] = summary.served_tiers.get(key, 0) + 1
    for s in bad:
        k = s.get("error_kind") or "error"
        summary.errors[k] = summary.errors.get(k, 0) + 1

    if ok:
        hits = sum(1 for s in ok if (s.get("usage") or {}).get("cache_read_tokens"))
        summary.cache_hit_rate = hits / len(ok)
    timed = [s for s in used if s.get("ttft") is not None and s.get("e2e")]
    if timed:
        bursts = sum(1 for s in timed if (s["e2e"] - s["ttft"]) < 0.05 * s["e2e"])
        summary.burst_share = bursts / len(timed)
    for tk in ("input_tokens", "output_tokens", "reasoning_tokens", "cache_read_tokens"):
        summary.tokens[tk] = _mean([(s.get("usage") or {}).get(tk) for s in used])
    for metric in METRICS:
        summary.stats[metric] = MetricStats.from_values([s.get(metric) for s in used])
    summary.stats["server_first_byte"] = MetricStats.from_values(
        [s.get("server_first_byte") for s in used]
    )
    return summary
