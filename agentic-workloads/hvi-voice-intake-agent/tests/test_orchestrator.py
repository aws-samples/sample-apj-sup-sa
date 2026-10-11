#!/usr/bin/env python
"""
Drives full calls through the deterministic orchestrator.

The point of the rebuild was that protocol compliance should be a property of the
code, not of the model's mood. So these assertions are about guarantees:

  - every answer is checkpointed, because the code path writes it
  - a declared condition ALWAYS queues its Neptune-mandated follow-ups
  - those follow-ups are asked before the questionnaire moves on
  - a red-flag classification is never accepted without a read-back
  - a drop mid-protocol carries the outstanding follow-ups to the resumed call
  - a self-harm disclosure stops the questionnaire immediately
  - a mis-heard implausible value is rejected rather than recorded

Run:  ./.venv/bin/python tests/test_orchestrator.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "common"))

os.makedirs(os.path.join(ROOT, "agent", "common"), exist_ok=True)
_dst = os.path.join(ROOT, "agent", "common", "hvi_state.py")
import shutil  # noqa: E402
shutil.copy(os.path.join(ROOT, "common", "hvi_state.py"), _dst)

import hvi_state as st  # noqa: E402
import orchestrator as orch  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "tests"))
import _phones  # noqa: E402

# This suite's OWN number, not the shared fixture. It drops a call and then
# asserts the resumable session is that one, which is only true if nothing else
# is creating sessions on the same number - and eleven other suites were. See
# tests/_phones.py; reserve() also clears any leftover from a previous run.
PHONE = _phones.reserve("test_orchestrator")
FORM_ID = os.environ.get("FORM_ID", "arogya-sanjeevani-v1")

failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{'' if cond else '  <- ' + detail}")
    if not cond:
        failures.append(label)


class Call:
    """Small driver that keeps the turn counter honest."""

    def __init__(self, call_id: str, verbose: bool = True):
        self.call_id = call_id
        self.turn = 0
        self.verbose = verbose
        self.tools: list[str] = []
        self.notes: list[str] = []

    def say(self, utterance: str = "") -> dict:
        r = orch.handle_turn(self.call_id, utterance, self.turn, "hinglish")
        used = [t["tool"] for t in r.get("toolTrace", [])]
        self.tools.extend(used)
        self.notes.extend(r.get("notes") or [])
        if self.verbose:
            if utterance:
                print(f"    customer > {utterance}")
            print(f"    agent    > {r.get('agentUtterance')}")
            if used:
                print(f"    tools    : {used}")
            if r.get("notes"):
                print(f"    notes    : {r['notes']}")
        self.turn = r.get("turnIndex", self.turn) + 2
        return r


def fresh_call(language: str = "hinglish") -> Call:
    """A call whose language is already settled.

    Every call now opens by ASKING whether to continue in Hindi or English, so
    without this each test below would spend its first two turns on that exchange
    and assert nothing about the protocol.

    NOT YET COVERED ANYWHERE: tests/test_language_selection.py is still to be
    written, so short-circuiting the selection here currently leaves it untested -
    including whether it is asked at all. Do not read these passing as evidence that
    the language turn works.

    Kept at hinglish because these fixtures answer in Hinglish.
    """
    cust = st.get_customer_by_phone(PHONE)
    session = st.start_session(PHONE, cust, FORM_ID)
    st.set_language(session["callId"], language, confirmed=True)
    return Call(session["callId"])


def main() -> int:
    print("\n[1] Opening turn asks the first schema question, no LLM needed")
    c = fresh_call()
    r = c.say()
    check("greets and asks A010", "Priya" in (r.get("agentUtterance") or ""),
          r.get("agentUtterance", "")[:70])
    check("asked kind is a form item", r.get("askedKind") == "item", str(r.get("askedKind")))
    check("no model call on the opener", not c.tools, str(c.tools))

    print("\n[2] Every answer is checkpointed by the code path")
    r = c.say("Haan, Priya bol rahi hoon")
    check("save_answer was called", "save_answer" in c.tools, str(c.tools))
    session = st.get_session(c.call_id)
    check("A010 persisted", "A010" in (session.get("answers") or {}),
          str(session.get("answers")))

    r = c.say("Haan bilkul, poochhiye")
    session = st.get_session(c.call_id)
    check("A020 consent persisted", session["answers"].get("A020") is True,
          str(session["answers"].get("A020")))

    print("\n[3] A declared condition ALWAYS queues the Neptune protocol")
    r = c.say("Mera date of birth bais March unnees sau sattasi hai")
    r = c.say("Meri height paanch foot paanch inch hai")
    session = st.get_session(c.call_id)
    check("height coerced to cm", 160 <= float(session["answers"].get("A100", 0)) <= 170,
          str(session["answers"].get("A100")))

    r = c.say("Bawan kilo")
    # Now volunteer diabetes while answering an unrelated question.
    r = c.say("Nahi, lekin mujhe sugar hai das saal se")
    check("classify_condition ran", "classify_condition" in c.tools, str(c.tools))
    check("get_followup_questions ran", "get_followup_questions" in c.tools, str(c.tools))

    session = st.get_session(c.call_id)
    pending = session.get("pendingFollowUps") or []
    declared = session.get("declaredConditions") or []
    print(f"        queued follow-ups: {[p['questionId'] for p in pending]}")
    check("six follow-ups queued", len(pending) == 6, str(len(pending)))
    check("E11 recorded as declared", any(d["icd10"] == "E11" for d in declared),
          str(declared))
    check("protocol source captured",
          bool(pending and pending[0].get("protocolSource", "").startswith("neptune:")),
          str(pending[0].get("protocolSource") if pending else None))

    print("\n[4] Follow-ups are asked before the questionnaire continues")
    r = c.say("Two thousand fifteen se")
    check("agent is in follow-up mode", r.get("askedKind") == "followup",
          str(r.get("askedKind")))
    session = st.get_session(c.call_id)
    check("one follow-up consumed", len(session.get("pendingFollowUps") or []) == 5,
          str(len(session.get("pendingFollowUps") or [])))
    check("follow-up answer stored", len(session.get("followUpAnswers") or {}) == 1,
          str(session.get("followUpAnswers")))

    print("\n[5] Drop mid-protocol, then resume: obligations carry over")
    st.mark_status(c.call_id, st.STATUS_DROPPED, note="simulated drop mid-protocol")
    before = st.get_session(c.call_id)
    owed = len(before.get("pendingFollowUps") or [])

    resumable = st.find_resumable_session(PHONE)
    check("dropped call is resumable", resumable and resumable["callId"] == c.call_id)

    cust = st.get_customer_by_phone(PHONE)
    s2 = st.start_session(PHONE, cust, FORM_ID, resume_from=resumable)
    c2 = Call(s2["callId"])
    print(f"        resumed callId={s2['callId']}")
    check("answers carried over", len(s2.get("answers") or {}) == len(before["answers"]),
          f"{len(s2.get('answers') or {})} vs {len(before['answers'])}")
    check("outstanding follow-ups carried over",
          len(s2.get("pendingFollowUps") or []) == owed,
          f"{len(s2.get('pendingFollowUps') or [])} vs {owed}")
    check("answered follow-ups carried over",
          len(s2.get("followUpAnswers") or {}) == len(before.get("followUpAnswers") or {}))

    r = c2.say()
    check("resume is acknowledged", any("resumed" in n for n in c2.notes), str(c2.notes))

    # A RESUMED CALL RE-VERIFIES IDENTITY FIRST, and that is deliberate: the
    # previous leg's answers are about to be attributed to whoever picks up, so
    # confirming who that is comes before continuing a medical questionnaire. This
    # check used to demand askedKind == "followup" on the opening turn, which
    # predates that decision.
    #
    # What actually matters is asserted instead: identity comes first, the owed
    # follow-up is NOT lost, and the protocol resumes on it once identity clears.
    check("resumes by re-verifying identity, not by continuing blind",
          r.get("askedKind") == "item" and r.get("askedRef") == "A010",
          f"{r.get('askedKind')}/{r.get('askedRef')}")
    # In whichever language the call is running - this one is Hinglish. See
    # _identity_reconfirm_prompt_text; the point is that the caller is told the
    # call is a continuation rather than being re-greeted from the top.
    said = (r.get("agentUtterance") or "").lower()
    check("the caller is told why we are back",
          any(lead in said for lead in
              ("cut off", "beech mein ruk gaye", "बीच में रुक गए")),
          r.get("agentUtterance"))
    check("and is NOT re-greeted from the top",
          "medical information call" not in said and "namaste, main" not in said,
          r.get("agentUtterance"))
    check("no unrendered placeholder in the resumed opening",
          "{{" not in (r.get("agentUtterance") or ""), r.get("agentUtterance"))

    r = c2.say("Haan Priya bol rahi hoon")
    r = c2.say("Bais March unnees sau sattasi")
    check("once identity clears, the protocol resumes on the owed follow-up",
          r.get("askedKind") == "followup", str(r.get("askedKind")))
    still_owed = len(st.get_session(c2.call_id).get("pendingFollowUps") or [])
    check("the owed follow-ups survived the resume intact",
          still_owed == owed, f"{still_owed} vs {owed}")

    print("\n[6] Red-flag symptom is parked for confirmation, never auto-accepted")
    c3 = fresh_call()
    c3.say()
    c3.say("Haan Priya bol rahi hoon")
    c3.say("Haan poochhiye")
    c3.say("Bais March unnees sau sattasi")
    r = c3.say("Mujhe chalne par seene mein dard hota hai")
    session = st.get_session(c3.call_id)
    parked = session.get("awaitingConfirmation")
    print(f"        parked: {parked and parked.get('icd10')} redFlag={parked and parked.get('redFlag')}")
    check("classification parked, not accepted", parked is not None, str(parked))
    if parked:
        check("parked as I25", parked.get("icd10") == "I25", str(parked.get("icd10")))
        check("marked as a red flag", parked.get("redFlag") is True)
        check("no follow-ups queued yet",
              not (session.get("pendingFollowUps") or []),
              str(len(session.get("pendingFollowUps") or [])))
        check("agent asks for confirmation", r.get("askedKind") == "confirmation",
              str(r.get("askedKind")))

    print("        patient confirms it")
    r = c3.say("Haan, doctor ne heart ki problem boli thi")
    session = st.get_session(c3.call_id)
    check("confirmation cleared", session.get("awaitingConfirmation") is None)
    check("protocol queued after confirmation",
          len(session.get("pendingFollowUps") or []) > 0,
          str(len(session.get("pendingFollowUps") or [])))

    print("\n[7] Implausible value is rejected, not written to the record")
    c4 = fresh_call()
    c4.say()
    c4.say("Haan Priya bol rahi hoon")
    c4.say("Haan theek hai")
    c4.say("Bais March unnees sau sattasi")
    r = c4.say("Char sau centimetre")          # 400cm is not a height
    session = st.get_session(c4.call_id)
    check("implausible height not recorded", "A100" not in (session.get("answers") or {}),
          str(session.get("answers", {}).get("A100")))
    check("agent re-asked", any("rejected value" in n or "re-asked" in n for n in c4.notes),
          str(c4.notes))

    print("\n[8] Self-harm disclosure halts the questionnaire")
    c5 = fresh_call()
    c5.say()
    r = c5.say("Mujhe lagta hai main apni jaan de dunga, bahut pareshan hoon")
    check("escalated", r.get("escalated") is True)
    check("questionnaire stopped", r.get("stopQuestionnaire") is True)
    check("session marked escalated",
          st.get_session(c5.call_id)["status"] == "escalated",
          st.get_session(c5.call_id)["status"])
    check("response is supportive, not procedural",
          "doctor" in (r.get("agentUtterance") or "").lower(),
          r.get("agentUtterance", "")[:80])

    print("\n" + "=" * 70)
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("Protocol compliance is enforced by the orchestrator, not the model.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
