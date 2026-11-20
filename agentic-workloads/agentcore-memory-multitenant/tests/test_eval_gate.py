"""The release gate's comparison logic (scripts/eval_gate.py), without
running the suites it wraps: it must block on an isolation regression, a
RAG score drop beyond the tolerance, or any cross-brand leak."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import eval_gate  # noqa: E402

BASELINE = {"passed": 29, "failed": 0, "rag_score": 95.0}
OK_ISOLATION = {"passed": 29, "failed": 0}
OK_RAG = {"score": 95.0, "leaks": 0, "errors": 0}


def test_gate_passes_at_baseline_and_within_tolerance():
    assert eval_gate.gate_failures(OK_ISOLATION, OK_RAG, BASELINE) == []
    within = {**OK_RAG, "score": BASELINE["rag_score"] - eval_gate.RAG_TOLERANCE}
    assert eval_gate.gate_failures(OK_ISOLATION, within, BASELINE) == []


def test_gate_blocks_on_rag_score_drop():
    dropped = {**OK_RAG, "score": BASELINE["rag_score"] - eval_gate.RAG_TOLERANCE - 0.1}
    failures = eval_gate.gate_failures(OK_ISOLATION, dropped, BASELINE)
    assert len(failures) == 1 and "rag: score" in failures[0]


def test_gate_blocks_on_leak_even_with_high_score():
    leaky = {**OK_RAG, "score": 100.0, "leaks": 1}
    assert any("leak" in f for f in eval_gate.gate_failures(OK_ISOLATION, leaky, BASELINE))


def test_gate_blocks_on_isolation_regression():
    regressed = {"passed": 28, "failed": 1}
    failures = eval_gate.gate_failures(regressed, OK_RAG, BASELINE)
    assert len(failures) == 2 and all(f.startswith("isolation") for f in failures)
