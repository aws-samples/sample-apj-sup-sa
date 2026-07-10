"""Tests for report writing: redaction and atomic output."""

from __future__ import annotations

import json

from bedrock_bench import report
from bedrock_bench.config import BenchmarkConfig
from bedrock_bench.metrics import CellSummary, MetricStats


def _summary() -> CellSummary:
    m = MetricStats(n=1, mean=0.5, min=0.5, max=0.5, p20=0.5, p50=0.5, p90=0.5)
    return CellSummary(
        label="k|invoke|flex|us-east-1",
        family="Fam",
        model_key="k",
        display_name="Model",
        transport="invoke",
        tier="flex",
        region="us-east-1",
        model_id="m",
        requested=1,
        succeeded=1,
        failed=0,
        served_tiers={"flex": 1},
        ttft=m,
        total_latency=m,
    )


def _meta() -> dict:
    return {
        "account_id": "123456789012",
        "started": "2026-01-01T00:00:00Z",
        "finished": "2026-01-01T00:01:00Z",
        "version": "0.2.1",
    }


def test_redaction_masks_account_and_drops_profile(tmp_path):
    cfg = BenchmarkConfig(
        profile="my-secret-profile", regions=("us-east-1",), run_id="run-x", redact=True
    )
    report.write_all(tmp_path, cfg, _meta(), [_summary()])

    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["meta"]["account_id"] == "••••••••9012"  # last 4 kept, rest masked
    assert summary["config"]["profile"] is None

    for name in ("report.md", "report.html"):
        text = (tmp_path / name).read_text()
        assert "123456789012" not in text
        assert "my-secret-profile" not in text


def test_no_redaction_by_default(tmp_path):
    cfg = BenchmarkConfig(profile="my-profile", regions=("us-east-1",), run_id="run-y")
    report.write_all(tmp_path, cfg, _meta(), [_summary()])
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["meta"]["account_id"] == "123456789012"
    assert summary["config"]["profile"] == "my-profile"


def test_writes_are_atomic_no_tmp_left(tmp_path):
    cfg = BenchmarkConfig(regions=("us-east-1",), run_id="run-z")
    report.write_all(tmp_path, cfg, _meta(), [_summary()])
    # No leftover ".<name>.tmp" files from the atomic temp+replace.
    assert not list(tmp_path.glob(".*.tmp"))
    assert (tmp_path / "summary.json").exists()
