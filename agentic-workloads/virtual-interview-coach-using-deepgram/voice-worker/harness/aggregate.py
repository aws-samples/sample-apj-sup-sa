"""Aggregate a measured session into the SC-001 gate verdict (T029).

This is the AUTHORITATIVE, unit-tested implementation of the gate decision
(contracts/metrics-contract.md):

    verdict = PASS  iff  response_gap_p50_ms <  1000  AND  response_gap_p95_ms < 1500
    hard_gate_pass  iff  response_gap_p50_ms <= 1200

Percentiles are computed over a FULL session's coach turns — no outlier trimming
(Constitution II, honest measurement). Also emits a per-stage p50 breakdown so a FAIL is
diagnosable (which sub-component blew the budget).

Run:
    python -m harness.aggregate runs/agentcore.json
    python -m harness.aggregate --compare runs/agentcore.json runs/bedrock_direct.json
"""

from __future__ import annotations

import argparse
import json
import sys

P50_PASS_MS = 1000
P95_PASS_MS = 1500
HARD_GATE_P50_MS = 1200


def percentile(values: list[float], pct: float) -> float:
    """Linear-interpolation percentile (pct in [0,100])."""
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


def _p50(values: list[float]) -> int:
    return round(percentile(values, 50))


def aggregate(turns: list[dict], reply_provider: str, network_path: str | None = None) -> dict:
    """Compute the verdict artifact from a list of per-turn latency dicts.

    Each turn dict must carry at least `response_gap_ms`; sub-components are used for the
    diagnostic breakdown when present.
    """
    gaps = [t["response_gap_ms"] for t in turns]
    p50 = _p50(gaps)
    p95 = round(percentile(gaps, 95))
    verdict = "PASS" if (p50 < P50_PASS_MS and p95 < P95_PASS_MS) else "FAIL"

    def comp_p50(key: str) -> int:
        vals = [t[key] for t in turns if t.get(key) is not None]
        return _p50(vals) if vals else 0

    result = {
        "reply_provider": reply_provider,
        "network_path": network_path,
        "n": len(gaps),
        "response_gap_p50_ms": p50,
        "response_gap_p95_ms": p95,
        "verdict": verdict,
        "hard_gate_p50_ms": HARD_GATE_P50_MS,
        "hard_gate_pass": p50 <= HARD_GATE_P50_MS,
        "breakdown_p50": {
            "stt_finalization_ms": comp_p50("stt_finalization_ms"),
            "reply_ttft_ms": comp_p50("reply_ttft_ms"),
            "tts_first_audio_ms": comp_p50("tts_first_audio_ms"),
            "orchestration_ms": comp_p50("orchestration_ms"),
        },
    }

    # Lead-clause strategy: the gate gap measures time-to-first-coach-audio (a backchannel), but
    # the SUBSTANTIVE answer lands later. Surface that second number so the trade is never hidden:
    # a fast gate gap paired with a slow substantive reply is reported, not masked (Constitution II).
    sub = [t["substantive_reply_ms"] for t in turns if t.get("substantive_reply_ms")]
    if sub:
        result["substantive_reply_p50_ms"] = _p50(sub)
        result["substantive_reply_p95_ms"] = round(percentile(sub, 95))
    return result


def _load_run(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def _verdict_from_run(run: dict) -> dict:
    return aggregate(
        run.get("turns", []),
        run.get("reply_provider", "unknown"),
        run.get("network_path"),
    )


def _print_verdict(v: dict) -> None:
    print(json.dumps(v, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate a session into the SC-001 gate verdict")
    parser.add_argument("runs", nargs="+", help="one run JSON, or two with --compare")
    parser.add_argument("--compare", action="store_true", help="compare two provider runs")
    args = parser.parse_args(argv)

    if args.compare:
        if len(args.runs) != 2:
            parser.error("--compare requires exactly two run files")
        a, b = (_verdict_from_run(_load_run(p)) for p in args.runs)
        print("=== Provider comparison (SC-001) ===")
        for v in (a, b):
            print(
                f"{v['reply_provider']:>14}: p50={v['response_gap_p50_ms']}ms "
                f"p95={v['response_gap_p95_ms']}ms verdict={v['verdict']} "
                f"hard_gate_pass={v['hard_gate_pass']}"
            )
        winner = min((a, b), key=lambda v: (v["verdict"] != "PASS", v["response_gap_p50_ms"]))
        print(f"\nRecommended default REPLY_PROVIDER: {winner['reply_provider']}")
        return 0

    run = _load_run(args.runs[0])
    verdict = _verdict_from_run(run)
    _print_verdict(verdict)
    return 0 if verdict["hard_gate_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
