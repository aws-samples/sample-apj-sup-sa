#!/usr/bin/env python3
"""Replay the live asthma failure from call-47ce35b0 and prove it is fixed.

WHAT HAPPENED. The caller answered the lung screen with the SHORT form and a
Devanagari full stop - "हाँ, अस्थमा।" - and it resolved to J46 Status asthmaticus, a
life-threatening asthma attack, instead of J45 Asthma. The agent read J46 back to her
and she said yes. J46 is a reference-tier code with no follow-up protocol, so the
DEFAULT protocol ran (diagnosed_when, current_treatment, is_controlled); the code was
then superseded by J45, whose REAL protocol was queued on top. She was asked when she
was diagnosed a second time, answered "हाँ" out of confusion, and every "हाँ" to a
date question tripped explain-then-reask.

Both complaints from that call - "it could not take 2015 as the answer for when asthma
happened" and "it is explaining diseases even when I didn't ask it to" - are that one
chain, and its first link was punctuation.

WHY THIS IS A SUITE AND NOT A DIAGNOSTIC. The rehearsal script says the LONG form,
"हाँ, मुझे अस्थमा है", which always resolved correctly - which is exactly why 33 clean
rehearsals did not catch this. A regression test has to use the words the caller
actually said.

    source config/env.sh && ./.venv/bin/python tests/test_asthma_danda_replay.py

Hits the real classifier and the real graph, so it needs the environment sourced.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "common"))

import _phones  # noqa: E402
import hvi_state as st  # noqa: E402
import orchestrator as orch  # noqa: E402
import tools as agent_tools  # noqa: E402

PHONE = _phones.reserve("test_asthma_danda_replay")
FORM_ID = "arogya-sanjeevani-v1"

# Verbatim from the live transcript, turn 39.
LIVE_ANSWER = "हाँ, अस्थमा।"

failures: list[str] = []
checks = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not ok:
        failures.append(f"{label}{': ' + detail if detail else ''}")
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          f"{'' if ok else '  <-- ' + detail}")


def test_classification() -> None:
    print("[1] the phrase the caller actually said")
    out = agent_tools.classify_condition(LIVE_ANSWER) or {}
    check(f"{LIVE_ANSWER!r} resolves to J45 Asthma, not J46 Status asthmaticus",
          out.get("icd10") == "J45",
          f"got {out.get('icd10')} ({out.get('label_en')}) "
          f"at {out.get('score')} via {out.get('source')}")
    # Corroboration is the difference between a recorded fact and a read-back
    # hypothesis. J45 is curated, so the graph must agree.
    check("the graph corroborates it, so it is not an uncorroborated guess",
          bool(out.get("corroborated")), f"source={out.get('source')}")
    check("the caller's verbatim words are preserved on the result",
          out.get("phrase") == LIVE_ANSWER, f"got {out.get('phrase')!r}")


def test_protocol_is_asked_once() -> None:
    """The diagnosis date must be asked ONCE, not once per protocol."""
    print("\n[2] the follow-up protocol that gets queued")
    cust = st.get_customer_by_phone(PHONE)
    session = st.start_session(PHONE, cust, FORM_ID)
    cid = session["callId"]
    st.set_language(cid, "hi", confirmed=True)

    turn = 1
    reply = orch.handle_turn(cid, "", turn, language_hint="hi")
    asked: list[str] = []
    said_answers = {
        "A010": "हाँ", "A020": "हाँ",
        "A030": _spoken_dob(cust.get("dateOfBirth") or ""),
        "A100": "165 सेंटीमीटर", "A110": "62 किलो",
    }
    reached_asthma = False
    for _ in range(60):
        if reply.get("isComplete") or reply.get("stopQuestionnaire"):
            break
        ref = reply.get("askedRef") or ""
        kind = reply.get("askedKind") or ""
        utt = reply.get("agentUtterance") or ""

        if reached_asthma:
            asked.append(f"{ref}/{kind}")
            # Stop once the asthma protocol has clearly moved past its date question
            # and reached the respiratory-specific ones, or a new screen begins.
            if ref.startswith("A2") and ref != "A230" or ref.startswith("A3"):
                break

        if ref == "A230":
            reached_asthma = True
            answer = LIVE_ANSWER
        elif kind == "confirmation":
            answer = "हाँ, सही है"
        elif ref in said_answers:
            answer = said_answers[ref]
        elif "कब" in utt or "when" in utt.lower():
            answer = "2015"
        elif "इलाज" in utt or "दवा" in utt:
            answer = "ज़रूरत पर साल्ब्यूटामोल इन्हेलर"
        elif "कितनी बार" in utt:
            answer = "हफ्ते में दो बार"
        elif "नियंत्रण" in utt:
            answer = "हाँ"
        else:
            answer = "नहीं"
        turn += 1
        reply = orch.handle_turn(cid, answer, turn, language_hint="hi")

    print(f"    asthma protocol asked: {asked}")
    date_qs = [a for a in asked
               if "DIAG_WHEN" in a or "diagnosed_when" in a]
    check("the diagnosis date is asked exactly once for the asthma disclosure",
          len(date_qs) == 1, f"asked {len(date_qs)} times: {date_qs}")

    sess = st.get_session(cid) or {}
    codes = [d.get("icd10") for d in sess.get("declaredConditions") or []]
    check("J45 is on the record", "J45" in codes, str(codes))
    check("J46 is NOT on the record", "J46" not in codes, str(codes))
    # The default protocol is the tell: if it ran, the code had no graph entry.
    sources = {(d.get("protocolSource") or "") for d in
               sess.get("pendingFollowUps") or []}
    check("no default protocol was applied (the code was governed)",
          not any("default-protocol" in s for s in sources), str(sources))


_HI_MONTHS = ("जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई",
              "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर")


def _spoken_dob(iso: str) -> str:
    y, m, d = (iso or "--").split("-")
    return f"{int(d)} {_HI_MONTHS[int(m) - 1]} {y}"


def main() -> int:
    if not os.environ.get("OPENSEARCH_ENDPOINT"):
        print("OPENSEARCH_ENDPOINT is not set. Run:\n"
              "  source config/env.sh && ./.venv/bin/python "
              "tests/test_asthma_danda_replay.py")
        return 2
    test_classification()
    test_protocol_is_asked_once()
    print()
    if failures:
        print(f"  {len(failures)} of {checks} checks FAILED")
        for f in failures:
            print(f"    - {f}")
        return 1
    print(f"  All {checks} checks passed.")
    print("  The short answer with a trailing danda resolves to governed J45, its")
    print("  protocol is asked once, and no default protocol runs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
