"""Unit tests for the SC-001 gate verdict math (T030).

The verdict is gate-critical: a wrong PASS/FAIL would invalidate G1 (Constitution II). These
tests pin the thresholds and percentile behavior from contracts/metrics-contract.md.
"""

from __future__ import annotations

from harness.aggregate import (
    HARD_GATE_P50_MS,
    P50_PASS_MS,
    P95_PASS_MS,
    aggregate,
    percentile,
)


def _turns(gaps: list[int]) -> list[dict]:
    return [
        {
            "turn_index": i,
            "response_gap_ms": g,
            "stt_finalization_ms": 200,
            "reply_ttft_ms": 400,
            "tts_first_audio_ms": 220,
            "orchestration_ms": 50,
        }
        for i, g in enumerate(gaps)
    ]


def test_percentile_basic():
    assert percentile([100], 50) == 100
    assert percentile([], 50) == 0.0
    # 10 evenly spaced values 100..1000
    vals = list(range(100, 1100, 100))
    assert percentile(vals, 50) == 550.0  # interpolated midpoint
    assert round(percentile(vals, 95)) == 955


def test_clear_pass():
    # All gaps comfortably under target.
    v = aggregate(_turns([800, 850, 900, 870, 820, 910, 880, 860, 840, 890]), "agentcore")
    assert v["verdict"] == "PASS"
    assert v["hard_gate_pass"] is True
    assert v["response_gap_p50_ms"] < P50_PASS_MS
    assert v["response_gap_p95_ms"] < P95_PASS_MS
    assert v["n"] == 10


def test_clear_fail_on_p95():
    # p50 fine, but a couple of slow turns push p95 over 1500.
    v = aggregate(_turns([800, 820, 850, 900, 870, 880, 860, 1800, 1900, 2000]), "agentcore")
    assert v["verdict"] == "FAIL"


def test_hard_gate_boundary():
    # p50 exactly at the hard gate (1200) must still count as hard_gate_pass (<=),
    # but verdict is FAIL because p50 is not < 1000.
    gaps = [1200] * 10
    v = aggregate(_turns(gaps), "bedrock_direct")
    assert v["response_gap_p50_ms"] == HARD_GATE_P50_MS
    assert v["hard_gate_pass"] is True
    assert v["verdict"] == "FAIL"


def test_hard_gate_fail_above_1200():
    v = aggregate(_turns([1300] * 10), "agentcore")
    assert v["hard_gate_pass"] is False
    assert v["verdict"] == "FAIL"


def test_p50_pass_threshold_is_strict():
    # p50 exactly 1000 must FAIL (threshold is strict <).
    gaps = [1000] * 10
    v = aggregate(_turns(gaps), "agentcore")
    assert v["response_gap_p50_ms"] == P50_PASS_MS
    assert v["verdict"] == "FAIL"


def test_breakdown_present():
    v = aggregate(_turns([900] * 10), "agentcore")
    b = v["breakdown_p50"]
    assert b["stt_finalization_ms"] == 200
    assert b["reply_ttft_ms"] == 400
    assert b["tts_first_audio_ms"] == 220
    assert b["orchestration_ms"] == 50
