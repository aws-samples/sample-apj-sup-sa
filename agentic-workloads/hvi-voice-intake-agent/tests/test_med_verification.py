#!/usr/bin/env python3
"""An unexpected medication is verified ONCE, then believed.

The flow under test (see orchestrator's pre-record consistency gate):
answering a treatment follow-up with a drug that does not fit the condition HOLDS
the answer and asks "just to be sure?" one time. Yes records it flagged; anything
else becomes the new answer, because the treatment question was never completed.
A revised answer that is ALSO unexpected is recorded flagged without re-asking -
medChecksAsked makes the probe one-shot, so nobody defends their prescription to
a machine twice.

Uses a real DynamoDB session with the state injected directly, because driving a
full call to a thyroid protocol costs ~15 LLM turns and this tests the gate, not
the journey.

Run:  ./.venv/bin/python tests/test_med_verification.py
"""
from __future__ import annotations

import os
import shutil
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))

_agent_common = os.path.join(ROOT, "agent", "common")
os.makedirs(_agent_common, exist_ok=True)
for _mod in ("hvi_state.py", "hvi_prompts.py"):
    shutil.copy(os.path.join(ROOT, "common", _mod), os.path.join(_agent_common, _mod))

import orchestrator as orch  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "common"))
import hvi_state as st  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"   {detail}" if detail else ""))
    if not ok:
        failures.append(label)


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _phones  # noqa: E402

# This suite's OWN number, still the seeded "Priya Sharma" record but cloned onto
# a phone nobody else uses. Twelve suites shared one number, and the unfinished
# sessions they leave behind leaked between them through find_resumable_session.
# See tests/_phones.py; reserve() also clears leftovers from a previous run.
PHONE = _phones.reserve("test_med_verification")
FORM_ID = os.environ.get("FORM_ID", "arogya-sanjeevani-v1")


def call_with_treatment_pending() -> str:
    """A session whose head-of-queue follow-up is a treatment question for E03."""
    cust = st.get_customer_by_phone(PHONE)
    s = st.start_session(PHONE, cust, FORM_ID)
    st.set_language(s["callId"], "en", confirmed=True)
    st.queue_followups(
        s["callId"], icd10="E03", condition_label="Hypothyroidism",
        protocol_source="test",
        questions=[{"questionId": "FU_TREATMENT", "mandatory": True,
                    "answerType": "text", "formField": None,
                    "prompt": "What treatment or medication are you taking for it?"}])
    return s["callId"]


def main() -> int:
    print("[1] An unexpected medicine is held and questioned, not recorded")
    cid = call_with_treatment_pending()
    r = orch.handle_turn(cid, "I take metformin for it", 2, language_hint="en")
    if any("re-asked" in n for n in (r.get("notes") or [])):
        # The extractor is a language model and occasionally misses a phrasing;
        # the agent re-asking is correct. Repeat once, as a caller would, rather
        # than making this suite flaky on a probabilistic step.
        r = orch.handle_turn(cid, "metformin", 4, language_hint="en")
    check("asks 'are you sure?'", r.get("askedKind") == "medVerify",
          f"{r.get('askedKind')}: {(r.get('agentUtterance') or '')[:70]}")
    sess = st.get_session(cid) or {}
    check("the answer is NOT yet recorded",
          not (sess.get("followUpAnswers") or {}),
          str(sess.get("followUpAnswers")))
    check("the hold carries the medicine",
          (sess.get("medCheckPending") or {}).get("med") == "metformin")

    print("\n[2] 'yes' records it, flagged, and moves on")
    r = orch.handle_turn(cid, "yes", 3, language_hint="en")
    sess = st.get_session(cid) or {}
    fua = sess.get("followUpAnswers") or {}
    check("recorded after confirmation", bool(fua), str(list(fua))[:60])
    check("flagged for the underwriter",
          any("UNDERWRITER ATTENTION" in n for n in (r.get("notes") or [])),
          str(r.get("notes"))[:90])
    check("probe is marked asked",
          "FU_TREATMENT" in (sess.get("medChecksAsked") or []))
    check("the call moved on", r.get("askedKind") != "medVerify",
          str(r.get("askedKind")))

    print("\n[3] A revision replaces the answer instead")
    cid2 = call_with_treatment_pending()
    orch.handle_turn(cid2, "I take metformin for it", 2, language_hint="en")
    r = orch.handle_turn(cid2, "sorry, I meant thyroxine", 3, language_hint="en")
    sess = st.get_session(cid2) or {}
    fua = sess.get("followUpAnswers") or {}
    vals = " ".join(str(v.get("value", "")).lower() for v in fua.values()
                    if isinstance(v, dict))
    check("the revised answer was recorded", "thyroxine" in vals, vals[:70])
    check("consistent revision corroborates, no flag",
          not any("UNDERWRITER ATTENTION" in n for n in (r.get("notes") or [])),
          str(r.get("notes"))[:90])

    for c in (cid, cid2):
        try:
            st.mark_status(c, st.STATUS_ABANDONED, note="test cleanup")
        except Exception as exc:  # noqa: BLE001
            print(f"  note: cleanup failed for {c}: {exc}")

    print()
    print("=" * 74)
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print("  -", f)
        return 1
    print("Unexpected medicines get one gentle check; the caller's final word "
          "stands, flagged where the underwriter should look twice.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
