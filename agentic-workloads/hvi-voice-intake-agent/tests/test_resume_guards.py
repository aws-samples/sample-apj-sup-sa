#!/usr/bin/env python3
"""Resume must not inherit answers from a different form, or from days ago.

THE CALL THIS COMES FROM. A caller was moved from the 27-item rehearsal form to
the 38-item production form. Their next call found the previous day's unfinished
session, resumed it, and skipped tobacco and alcohol - because the rehearsal form
had already collected them 19 hours earlier. The record came out complete with 29
answers and the interview never asked, which is the worst combination available:
it looks finished.

Nothing was broken in the questionnaire engine. The obligation ledger was
satisfied, every screen the form owed was either asked or already answered, and
the caller's own "Did you ask me about tobacco and alcohol?" was escalated for
human review rather than discarded. The fault was in what resume was ALLOWED to
carry across.

THREE GUARDS, and the reasoning differs.

Form: answers are keyed by item id, and an item id only means something inside one
questionnaire. Carrying them across forms inherits answers to questions this call
will never ask and skips questions it should ask. That is a correctness bug, not a
preference.

Age: "We were cut off earlier, so let me carry on from where we stopped" is a
claim about a dropped call. A caller hears it as minutes. Said 19 hours later it
is false, and it drops them into the middle of a protocol they do not remember.

Progress: a session holding no answers, no owed follow-up and no declared condition
has nothing to hand over, so resuming it is all cost. Measured on call-416a12ed: a
session THIRTEEN SECONDS old with zero answers was resumed - the same call
reconnecting, not a callback - so the caller was told "we were cut off earlier"
about a call she had never been on, and then made to re-verify her name and date of
birth before the questionnaire had started. Unlike the other two this is not a
window to tune; an empty session is worth nothing at any age.

Run:  ./.venv/bin/python tests/test_resume_guards.py
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "common"))

import hvi_state as st  # noqa: E402

failures: list[str] = []
checks = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"  <- {detail[:180]}" if not ok and detail else ""))
    if not ok:
        failures.append(label)


PHONE = "+910000000001"


def ago(**kw) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(**kw)).isoformat()


class FakeTable:
    """Just enough of the phone-index query to drive find_resumable_session."""

    def __init__(self, items: list[dict]):
        self.items = items

    def query(self, **kwargs):  # noqa: ANN001, ARG002
        return {"Items": self.items}


class FakeDdb:
    def __init__(self, items: list[dict]):
        self._table = FakeTable(items)

    def Table(self, name):  # noqa: ANN001, ARG002, N802
        return self._table


def resumable(items: list[dict], form_id: str | None = None):
    original = st._ddb  # noqa: SLF001
    st._ddb = FakeDdb(items)  # noqa: SLF001
    try:
        return st.find_resumable_session(PHONE, form_id)
    finally:
        st._ddb = original  # noqa: SLF001


def session(**over) -> dict:
    base = {
        "callId": "call-old", "phone": PHONE, "status": st.STATUS_IN_PROGRESS,
        "formId": "arogya-sanjeevani-v1", "updatedAt": ago(minutes=5),
        "answers": {"A010": True},
    }
    base.update(over)
    return base


def main() -> int:  # noqa: PLR0915
    print("[1] the ordinary case still resumes")
    got = resumable([session()], "arogya-sanjeevani-v1")
    check("a fresh dropped call on the same form is resumable",
          got is not None and got["callId"] == "call-old", repr(got))

    print("\n[2] a session from a DIFFERENT form is not inherited")
    # The exact shape of the live incident.
    got = resumable([session(callId="call-demo",
                             formId="arogya-sanjeevani-demo-v1")],
                    "arogya-sanjeevani-v1")
    check("rehearsal-form answers do not carry into the production form",
          got is None, f"resumed {got!r}")
    got = resumable([session(formId="arogya-sanjeevani-v1")],
                    "arogya-sanjeevani-demo-v1")
    check("and not the other way round either", got is None, repr(got))

    print("\n[3] the guard cannot be bypassed by simply not asking")
    # Callers that pass no form id keep the old behaviour, so this asserts every
    # production entry point passes one.
    for path, needle in (
            ("agent/app.py", "find_resumable_session(caller_phone, form_id)"),
            ("lambdas/outbound/handler.py", "find_resumable_session(phone, form_id)"),
    ):
        src = (ROOT / path).read_text()
        check(f"{path} passes the form id", needle in src,
              f"{path} still calls find_resumable_session without a form id")
    src = (ROOT / "lambdas" / "outbound" / "handler.py").read_text()
    check("and resolves form_id BEFORE the lookup",
          src.index("form_id = customer.get") < src.index("find_resumable_session"),
          "form_id is computed after the lookup, so the guard sees None")

    print("\n[4] a stale session is not resumed")
    check("19 hours old - the live incident - is not resumed",
          resumable([session(updatedAt=ago(hours=19))],
                    "arogya-sanjeevani-v1") is None)
    for hours, want in ((0.1, True), (1, True), (3.9, True),
                        (4.1, False), (24, False), (72, False)):
        got = resumable([session(updatedAt=ago(hours=hours))],
                        "arogya-sanjeevani-v1")
        check(f"{hours}h old -> {'resumable' if want else 'too old'}",
              (got is not None) == want, repr(got))

    print("\n[5] the window is configurable, not baked in")
    check("RESUME_MAX_AGE_SECS defaults to four hours",
          st.RESUME_MAX_AGE_SECS == 4 * 3600, str(st.RESUME_MAX_AGE_SECS))
    original = st.RESUME_MAX_AGE_SECS
    try:
        st.RESUME_MAX_AGE_SECS = 48 * 3600
        check("widening it lets an older session through",
              resumable([session(updatedAt=ago(hours=19))],
                        "arogya-sanjeevani-v1") is not None)
    finally:
        st.RESUME_MAX_AGE_SECS = original

    print("\n[6] a missing or unparseable timestamp does not silently block resume")
    # Failing closed here would break resume for any row written before updatedAt
    # existed, so an unknown age is treated as recent and the form guard still
    # applies.
    for stamp, why in ((None, "no timestamp"), ("not-a-date", "garbage"),
                       ("", "empty")):
        row = session(updatedAt=stamp)
        if stamp is None:
            row.pop("updatedAt")
            row.pop("createdAt", None)
        got = resumable([row], "arogya-sanjeevani-v1")
        check(f"{why} -> still resumable", got is not None, repr(got))

    print("\n[7] status still decides first")
    for status in (st.STATUS_COMPLETED, st.STATUS_ABANDONED, st.STATUS_ESCALATED):
        check(f"{status} is never resumable",
              resumable([session(status=status)], "arogya-sanjeevani-v1") is None)

    print("\n[8] the newest eligible session wins, skipping the disqualified")
    got = resumable([
        session(callId="call-wrong-form", formId="arogya-sanjeevani-demo-v1",
                updatedAt=ago(minutes=1)),
        session(callId="call-too-old", updatedAt=ago(hours=30)),
        session(callId="call-good", updatedAt=ago(minutes=30)),
    ], "arogya-sanjeevani-v1")
    check("skips the wrong form and the stale one, takes the valid one",
          got is not None and got["callId"] == "call-good", repr(got))

    print("\n[9] a session with NOTHING to resume is not resumed")
    # The live shape: call-416a12ed resumed a 13-second-old session that had
    # collected nothing, and the caller was apologised to for a drop that never
    # happened and then asked to re-confirm who she was.
    empty = session(callId="call-empty", answers={}, updatedAt=ago(seconds=13))
    check("13 seconds old with zero answers is not resumed",
          resumable([empty], "arogya-sanjeevani-v1") is None,
          repr(resumable([empty], "arogya-sanjeevani-v1")))
    check("and a missing answers key behaves the same as an empty one",
          resumable([session(callId="call-none", answers=None)],
                    "arogya-sanjeevani-v1") is None)

    # Each kind of durable obligation is progress in its own right - a call that
    # dropped while owing a follow-up must still come back to it, even though no
    # answer had been written yet.
    for key, value in (
            ("answers", {"A010": True}),
            ("pendingFollowUps", [{"questionId": "q1"}]),
            ("followUpAnswers", {"q1": "yes"}),
            ("declaredConditions", [{"icd10": "E11"}]),
            ("deferredMentions", ["aur mujhe sugar bhi hai"]),
            ("awaitingConfirmation", {"icd10": "E11"}),
            ("pendingConditionDetail", {"ref": "A200", "phrase": "cancer"}),
    ):
        row = session(callId=f"call-{key}", answers={})
        row[key] = value
        got = resumable([row], "arogya-sanjeevani-v1")
        check(f"{key} alone counts as progress", got is not None, repr(got))

    # A confirmed language is NOT progress. Inheriting it saves one short question;
    # resuming to get it costs the false "we were cut off earlier" plus two identity
    # re-verifications. A fresh call just asks which language again.
    check("a confirmed language alone is not enough to resume",
          resumable([session(callId="call-lang", answers={},
                             language="hi", languageConfirmed=True)],
                    "arogya-sanjeevani-v1") is None)

    print("\n[10] an empty newest session does not mask a real one behind it")
    got = resumable([
        session(callId="call-empty-newest", answers={}, updatedAt=ago(seconds=13)),
        session(callId="call-real", updatedAt=ago(minutes=20)),
    ], "arogya-sanjeevani-v1")
    check("skips the empty leg and resumes the one with answers",
          got is not None and got["callId"] == "call-real", repr(got))

    print()
    if failures:
        print(f"  {len(failures)} of {checks} checks FAILED")
        for f in failures:
            print(f"    - {f}")
        return 1
    print(f"  All {checks} resume-guard checks passed.")
    print("  A dropped call still resumes. A different questionnaire,")
    print("  yesterday's call, and a call that collected nothing, do not.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
