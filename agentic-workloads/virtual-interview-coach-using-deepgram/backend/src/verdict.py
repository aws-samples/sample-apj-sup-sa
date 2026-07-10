"""SC-001 gate verdict computation (shared by the API read path).

The authoritative, unit-tested implementation lives in voice-worker/harness/aggregate.py;
this is the identical logic for the backend's GET /sessions/{id}/latency convenience read.
Both MUST encode the same thresholds (contracts/metrics-contract.md):

    verdict = PASS  iff  p50 < 1000 AND p95 < 1500
    hard_gate_pass  iff  p50 <= 1200
"""

from __future__ import annotations

P50_PASS_MS = 1000
P95_PASS_MS = 1500
HARD_GATE_P50_MS = 1200


def percentile(values: list[float], pct: float) -> float:
    """Linear-interpolation percentile (pct in [0,100]). Matches aggregate.py."""
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    rank = (pct / 100.0) * (len(s) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(s) - 1)
    frac = rank - lo
    return float(s[lo] + (s[hi] - s[lo]) * frac)


def compute_verdict(response_gaps_ms: list[float]) -> dict:
    n = len(response_gaps_ms)
    p50 = round(percentile(response_gaps_ms, 50))
    p95 = round(percentile(response_gaps_ms, 95))
    verdict = "PASS" if (p50 < P50_PASS_MS and p95 < P95_PASS_MS) else "FAIL"
    return {
        "n": n,
        "response_gap_p50_ms": p50,
        "response_gap_p95_ms": p95,
        "verdict": verdict,
        "hard_gate_p50_ms": HARD_GATE_P50_MS,
        "hard_gate_pass": p50 <= HARD_GATE_P50_MS,
    }
