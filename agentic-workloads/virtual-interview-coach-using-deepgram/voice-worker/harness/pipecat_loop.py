"""Pipecat-loop SC-001 spike driver (Feature 007, T005-T008) — the GO/NO-GO gate.

Produces a run JSON that `harness/aggregate.py` turns into the SC-001 verdict, so the Pipecat loop is
measured with the IDENTICAL, immutable metric definition + thresholds as G1. Two modes:

  --live   : run the REAL assembled InterviewPipeline behind the worker's /offer and drive it with the
             existing headless aiortc peer (harness/loop_e2e.py), then read the authoritative
             server-side turn_latency rows. This is the only gate-eligible path. Needs Deepgram +
             Bedrock creds and a running worker (DEFERRED until spend is approved).
  (default): DRY — exercises the LatencyObserver -> turn_latency -> aggregate plumbing offline with
             deterministic simulated stage latencies for BOTH strategies, so the verdict pipeline and
             the A/B comparison are runnable without credentials. DRY numbers are clearly labeled and
             MUST NOT be used as a gate decision (Constitution II).

The A/B (contracts/latency-strategy-ab.md): run both LEAD_CLAUSE_STRATEGY=processor and =native and
compare with `python -m harness.aggregate --compare`. The default is locked to the PASSING strategy
with the better p50 margin; if NEITHER clears the hard gate, the migration HALTS (do not migrate prod).

Run:
    # DRY (offline, plumbing + A/B shape):
    python -m harness.pipecat_loop --strategy processor --out runs/pipecat_lead.json
    python -m harness.pipecat_loop --strategy native    --out runs/pipecat_native.json
    python -m harness.aggregate --compare runs/pipecat_lead.json runs/pipecat_native.json

    # LIVE (deferred — needs creds + a running worker):
    #   terminal 1: python -m src.server
    #   terminal 2: python -m harness.pipecat_loop --live --strategy processor --out runs/live_lead.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os

# Generic scripted student turns (no PII — Constitution III); ~10 turns mirrors the G1 harness.
_STUDENT_TURNS = [
    "Sure, I'm a final-year student studying computer science and I love building things.",
    "I think my greatest strength is that I stay calm under pressure and break problems down.",
    "Last semester our group project nearly fell apart and I had to step in and re-plan it.",
    "I want this role because it lets me work on real systems that people actually use.",
    "In five years I'd like to be leading a small team and mentoring junior engineers.",
    "Once a teammate and I disagreed on an approach, so we prototyped both and compared them.",
    "A weakness I'm working on is saying yes to too much, so I'm learning to prioritize.",
    "I led a hackathon team of four and we shipped a working demo in two days.",
    "When things get stressful I make a short list and tackle the highest-impact item first.",
    "Yes, what does success look like for someone in this role in the first six months?",
]


def _dry_turn(strategy: str, i: int) -> dict:
    """Deterministic simulated per-turn latency dict in the shape aggregate.py reads.

    Encodes the EXPECTED structural difference between the two strategies (NOT a real measurement):
      - processor (lead-clause): the gate gap is stt_finalization + lead-in TTS first-audio; the LLM
        is OFF the critical path, so reply_ttft is reported but excluded from the gap. substantive
        reply lands later (substantive_reply_ms).
      - native: the LLM is ON the critical path, so gap includes reply_ttft (which gate-decision.md
        showed pushes p50 over budget). This makes the DRY A/B mirror the real expected verdict shape.
    No randomness (Math.random is unavailable in workflows and we want determinism); vary by index.
    """
    stt = 270 + (i % 3) * 10          # ~270-290ms acoustic-offset constant + small jitter
    reply_ttft = 760 + (i % 5) * 20   # ~760-840ms direct-Bedrock TTFT (from gate-decision.md)
    lead_tts = 210 + (i % 4) * 15     # ~210-255ms Aura first-audio
    if strategy == "processor":
        gap = stt + lead_tts          # LLM off the gap clock
        substantive = stt + reply_ttft + lead_tts
        orchestration = max(0, gap - stt - lead_tts)
        return {
            "response_gap_ms": gap,
            "stt_finalization_ms": stt,
            "reply_ttft_ms": reply_ttft,
            "tts_first_audio_ms": lead_tts,
            "orchestration_ms": orchestration,
            "substantive_reply_ms": substantive,
        }
    # native: LLM on the gap clock
    gap = stt + reply_ttft + lead_tts
    return {
        "response_gap_ms": gap,
        "stt_finalization_ms": stt,
        "reply_ttft_ms": reply_ttft,
        "tts_first_audio_ms": lead_tts,
        "orchestration_ms": max(0, gap - stt - reply_ttft),
    }


def _run_dry(strategy: str, turns: int, reply_provider: str) -> dict:
    return {
        "mode": "DRY",  # NOT a gate decision — see module docstring (Constitution II)
        "strategy": strategy,
        "reply_provider": reply_provider,
        "network_path": "direct",
        "turns": [_dry_turn(strategy, i) for i in range(turns)],
    }


async def _run_live(strategy: str, turns: int, url: str, secret: str, reply_provider: str) -> dict:
    """Drive the running worker's /offer with the headless aiortc peer, then read the authoritative
    server-side turn_latency rows. Requires a worker started with LEAD_CLAUSE_STRATEGY=`strategy`.

    The peer (harness/loop_e2e) confirms the loop LIVES on real media; the gate numbers come from the
    server's turn_latency rows (metrics-contract.md), collected here from the DB if reachable, else the
    caller aggregates the server's run artifact. Kept thin: the heavy media drive is reused, not
    duplicated."""
    from harness import loop_e2e

    print(f"[live] driving {turns} turns against {url} (strategy={strategy} on the WORKER side)")
    rc = await loop_e2e.run(url, turns, secret)
    print(f"[live] peer drive returned rc={rc}; read the server turn_latency rows for the verdict.")
    # The authoritative per-component numbers are the server's turn_latency rows. This driver returns
    # an empty turns list with a pointer; collect rows via the DB/CloudWatch path used in Step 6.
    return {
        "mode": "LIVE",
        "strategy": strategy,
        "reply_provider": reply_provider,
        "network_path": "direct",
        "peer_drive_rc": rc,
        "turns": [],
        "note": "authoritative turn_latency rows are server-side; aggregate them per Step 6",
    }


def _compare(lead_path: str, native_path: str) -> int:
    """Strategy-labeled A/B summary + HALT decision (latency-strategy-ab.md). Wraps aggregate.py so
    the two arms are labeled by STRATEGY (aggregate's --compare keys on reply_provider, which is the
    same for both arms here)."""
    from harness.aggregate import _load_run, _verdict_from_run

    arms = []
    for label, path in (("processor (lead-clause)", lead_path), ("native (LLM on gap)", native_path)):
        v = _verdict_from_run(_load_run(path))
        arms.append((label, v))
        print(
            f"{label:>26}: p50={v['response_gap_p50_ms']}ms p95={v['response_gap_p95_ms']}ms "
            f"verdict={v['verdict']} hard_gate_pass={v['hard_gate_pass']}"
        )
    passing = [(lbl, v) for lbl, v in arms if v["hard_gate_pass"]]
    if not passing:
        print("\nHALT: neither strategy clears the hard gate — do NOT migrate production "
              "(spec FR-008 / latency-strategy-ab.md). Report the breakdown.")
        return 1
    winner = min(passing, key=lambda lv: (lv[1]["verdict"] != "PASS", lv[1]["response_gap_p50_ms"]))
    print(f"\nLocked default LEAD_CLAUSE_STRATEGY: "
          f"{'processor' if winner[0].startswith('processor') else 'native'} "
          f"({winner[0]}, p50={winner[1]['response_gap_p50_ms']}ms)")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Pipecat-loop SC-001 spike driver (go/no-go)")
    p.add_argument("--strategy", choices=["processor", "native"], default="processor")
    p.add_argument("--turns", type=int, default=10)
    p.add_argument("--out", help="write the run JSON here")
    p.add_argument("--live", action="store_true", help="drive the real worker (needs creds + running server)")
    p.add_argument("--url", default="http://127.0.0.1:8080")
    p.add_argument("--reply-provider", default=os.environ.get("REPLY_PROVIDER", "bedrock_direct"))
    p.add_argument("--compare", nargs=2, metavar=("LEAD_JSON", "NATIVE_JSON"),
                   help="strategy-labeled A/B summary + HALT decision over two run JSONs")
    args = p.parse_args(argv)

    if args.compare:
        return _compare(args.compare[0], args.compare[1])
    if not args.out:
        p.error("--out is required unless --compare is used")

    if args.live:
        secret = os.environ.get("VOICE_TOKEN_SECRET")
        if not secret:
            print("VOICE_TOKEN_SECRET must be set for --live (same value the worker uses)")
            return 1
        run = asyncio.run(
            _run_live(args.strategy, args.turns, args.url, secret, args.reply_provider)
        )
    else:
        run = _run_dry(args.strategy, args.turns, args.reply_provider)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(run, f, indent=2)
    print(f"wrote {run['mode']} run ({len(run['turns'])} turns, strategy={args.strategy}) -> {args.out}")
    if run["mode"] == "DRY":
        print("NOTE: DRY numbers are simulated and MUST NOT be used as a gate decision.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
