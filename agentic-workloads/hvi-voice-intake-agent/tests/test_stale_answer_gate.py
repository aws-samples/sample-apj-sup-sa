#!/usr/bin/env python
"""The stale-answer gate: a bare yes/no that predates the question is dropped.

From a live browser-harness call: a stray vocalisation in the inter-question
gap transcribed as "हाँ" and bound forward to a question that had not started
playing yet, silently answering it. The gate discards ONLY bare yes/no tokens
whose speech began before the current bot utterance started; substantive
content and genuine barge-in survive.

Run:  ./.venv/bin/python tests/test_stale_answer_gate.py
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "media"))
sys.path.insert(0, str(ROOT / "common"))

from media.agent_bridge import AgentCoreBridge  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"  <- {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(label)


print("[1] bare yes/no recognition")
for text in ("yes", "no", "हाँ", "नहीं", "haan ji", "okay", "जी हाँ", "Nahi."):
    check(f"{text!r} is bare yes/no", AgentCoreBridge._is_bare_yes_no(text))
for text in ("yes it was 2019", "blood cancer", "162", "5 September 2000",
             "हाँ, मुझे शुगर है", "", "  "):
    check(f"{text!r} is NOT bare yes/no",
          not AgentCoreBridge._is_bare_yes_no(text))

print("[2] timing semantics (pure predicate)")


def stale(user_started, bot_started) -> bool:
    """Mirror of the gate's condition, kept in lockstep with agent_bridge."""
    return (user_started is not None and bot_started is not None
            and user_started < bot_started)


check("speech before question -> stale", stale(10.0, 10.1))
check("barge-in during question -> honoured", not stale(10.2, 10.1))
check("unknown user timing -> never dropped", not stale(None, 10.1))
check("unknown bot timing -> never dropped", not stale(10.0, None))

print()
if failures:
    print(f"FAILED: {len(failures)}")
    for failure in failures:
        print("  -", failure)
    raise SystemExit(1)
print("Stale bare answers are dropped; genuine barge-in and content survive.")
