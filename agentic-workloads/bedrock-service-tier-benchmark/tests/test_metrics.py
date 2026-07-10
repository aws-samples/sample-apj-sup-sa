"""Unit tests for percentile metrics and summarisation."""

from __future__ import annotations

import math

from bedrock_bench.metrics import MetricStats, Sample, summarize


def test_metric_stats_basic():
    values = [float(i) for i in range(1, 101)]  # 1..100
    stats = MetricStats.from_values(values)
    assert stats.n == 100
    assert stats.min == 1.0
    assert stats.max == 100.0
    # p50 of 1..100 (linear interp) ~ 50.5
    assert 49 < stats.p50 < 52
    assert stats.p20 < stats.p50 < stats.p90
    assert not hasattr(stats, "p99")


def test_metric_stats_empty_is_none():
    stats = MetricStats.from_values([])
    assert stats.n == 0
    assert stats.mean is None and stats.p50 is None


def test_metric_stats_ignores_nan_and_none():
    stats = MetricStats.from_values([1.0, None, float("nan"), 3.0])  # type: ignore[list-item]
    assert stats.n == 2
    assert stats.min == 1.0 and stats.max == 3.0


def _sample(ttft, total, tier, err=None):
    return Sample(ttft=ttft, total=total, served_tier=tier, num_tokens_output=10, error=err)


def test_summarize_counts_and_served_tiers():
    samples = [
        _sample(0.5, 1.0, "flex"),
        _sample(0.6, 1.1, "flex"),
        _sample(None, None, None, err="ValidationException: nope"),
    ]
    cs = summarize(
        label="m|invoke|flex|us-east-1",
        family="Fam",
        model_key="m",
        display_name="M",
        transport="invoke",
        tier="flex",
        region="us-east-1",
        model_id="m",
        requested=3,
        samples=samples,
    )
    assert cs.requested == 3
    assert cs.succeeded == 2
    assert cs.failed == 1
    assert cs.served_tiers == {"flex": 2}
    assert "ValidationException" in next(iter(cs.errors))
    assert cs.ttft.n == 2
    # ttft <= total invariant holds on the inputs
    assert cs.ttft.p50 <= cs.total_latency.p50 or math.isclose(cs.ttft.p50, cs.total_latency.p50)
