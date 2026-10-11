#!/usr/bin/env python3
"""A caller who says YES must be asked the follow-up, and it must be recorded.

WHY THIS EXISTS

Every conversational suite in this repo answers the questionnaire with "no". The
walk loops are literally written that way - `said = answers.get(ref, "no")` - so
a screen that is only interesting when the answer is yes was driven exactly
once, on the path where nothing happens. Counted across tests/, eleven items were
referenced by no suite at all:

    A410 A420 A430 A600 A610 A620 A630 A640 A650 A900 A910

Those are not edge cases, they are the underwriting content: what you smoke and
for how long, what you drink, which test was abnormal, who in the family has
what, what else you have had. The form has carried them the whole time behind a
`showIf` gate that no conversation had ever opened.

This is the suite that opens them. It is a caller with things to disclose:
asthma, ten cigarettes a day, beer, a hospitalisation with medication still
running, an abnormal liver panel, and a family history - and it asserts on both
halves of each branch, because they fail differently:

  ASKED    - the agent has to put the follow-up question to the caller. A gate
             that evaluates correctly but never reaches the caller is the bug
             the customer actually reported ("it did not ask whether I smoke or
             drink, it just talked about asthma").
  RECORDED - the free-text answer has to land in the session. Asking and then
             dropping the reply is the same as not asking, to the underwriter.

A550 is the one that matters most: it is gated on A540, which is itself gated on
A500, so it is the only two-level branch in the form and the only item that
cannot be reached without getting two consecutive booleans right.

There is a negative control inside the same walk rather than in a second call.
A640 (has an insurer ever declined you) is answered NO on purpose, so A650 must
never be asked and must never appear in the record. Without it this suite would
pass just as well against an agent that asked every follow-up unconditionally.

tests/test_resume_flow.py [8] already proves show_item() gates these correctly as
pure form logic, and this suite deliberately does not repeat that. The gap was
never the predicate, it was whether a conversation ever reaches it.

Run:  ./.venv/bin/python tests/test_positive_disclosures.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "agent"))

# Stage the shared modules the agent imports flat, exactly as
# scripts/40-agentcore.sh does, and unconditionally for the same reason the
# other suites do it: an "only if missing" guard leaves a stale copy behind and
# tests code that is no longer the code being shipped.
_agent_common = os.path.join(ROOT, "agent", "common")
os.makedirs(_agent_common, exist_ok=True)
for _mod in ("hvi_state.py", "hvi_prompts.py"):
    shutil.copy(os.path.join(ROOT, "common", _mod), os.path.join(_agent_common, _mod))

import orchestrator as orch  # noqa: E402
import tools  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "common"))
import hvi_state as st  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"   {detail}" if detail else ""))
    if not ok:
        failures.append(label)


# Same fixture as tests/test_orchestrator.py: a customer who exists in the seeded
# CRM, so identity passes on a real record rather than an unidentified caller.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _phones  # noqa: E402

# This suite's OWN number, still the seeded "Priya Sharma" record but cloned onto
# a phone nobody else uses. Twelve suites shared one number, and the unfinished
# sessions they leave behind leaked between them through find_resumable_session.
# See tests/_phones.py; reserve() also clears leftovers from a previous run.
PHONE = _phones.reserve("test_positive_disclosures")
FORM_ID = os.environ.get("FORM_ID", "arogya-sanjeevani-v1")

# What this caller discloses. Anything not named here is answered "no", so the
# yes answers below are the whole point of the suite.
SAID = {
    # identity and biometrics - DOB must match the seeded record or identity
    # fails closed and the call never reaches the questionnaire
    "A010": "Yes, speaking",
    "A020": "Yes, that is fine, go ahead",
    "A030": "22 March 1987",
    "A100": "165 centimetres",
    "A110": "62 kilograms",

    # one condition screen, named in the same breath so the disclosure goes
    # through the screening path. A bare "yes" here is a different route and
    # tests/test_condition_screen_followup.py already owns it.
    "A230": "Yes, I have asthma",

    # lifestyle: both screens yes, so both detail items must open
    "A400": "Yes, I smoke",
    "A410": "Cigarettes, about ten a day, for fifteen years",
    "A420": "Yes, I drink sometimes",
    "A430": "Beer, around four bottles a week, for about ten years",

    # hospitalisation: the two-level branch. A540 must be yes to reach A550.
    "A500": "Yes, I was hospitalised once",
    "A510": "Gallstone surgery",
    "A520": "In 2019",
    "A530": "Yes, it is fully resolved now",
    "A540": "Yes, I am still taking medication for it",
    "A550": "Pantoprazole, forty milligrams once a day",

    # investigations and family history
    "A600": "Yes, one test came back abnormal",
    "A610": "A liver function test in 2020, the enzymes were high",
    "A620": "Yes, there is something in the family",
    "A630": "My father has type 2 diabetes",

    # NEGATIVE CONTROL: this one is no, so A650 must never be asked
    "A640": "No, never",

    # closing. A bare "Yes" on purpose: "Yes, one more thing" does not survive
    # boolean coercion ("not a yes/no value"), so it cost three attempts and then
    # the item was skipped. Boolean phrasing is the stress suites' job; this one
    # is about whether the branch opens, so it is not confounded with that.
    "A900": "Yes",
    "A910": "I had dengue fever in 2021 and recovered fully",
    "A920": "No, I do not have an ABHA number",
}

# The screens whose follow-up must open, paired with the child it gates. Read as
# "answering the parent yes obliges the agent to ask the child".
OWED = {
    "A400": "A410",   # tobacco  -> which form, how much, how long
    "A420": "A430",   # alcohol  -> what, how much, how long
    "A500": "A510",   # hospital -> reason
    "A540": "A550",   # still medicated -> which medications   (two levels deep)
    "A600": "A610",   # abnormal test -> which test
    "A620": "A630",   # family history -> which condition, who
    "A900": "A910",   # anything else -> tell me about it
}

# THE ONLY ILLNESS THIS CALLER HAS. Everything else they say is a lifestyle
# habit, the name of a test, a past operation, or a relative's diagnosis - none of
# which are their own conditions.
#
# This list is what makes the read-back reply honest, and it has to be honest or
# the suite is worthless. A blanket "yes, that is right" to every read-back was
# the first version, and it quietly agreed to Cirrhosis of Liver when the agent
# offered it - so the record came out with K74 on it and every check still
# passed. A caller who is asked "is that Cirrhosis of Liver?" after mentioning a
# liver function test says no, and so does this one.
CLAIMS = ("asthma",)

# Replies to the agent's own interjections. These are not form items; they are
# the protocol turns (read-backs, medication checks, the closing summary) and a
# walk that cannot answer them stalls before finishing the form.
PROTOCOL = {
    "medVerify": "Yes, that is the medicine",
    "summaryConfirm": "Yes, that is all correct",
    "distressCheck": "No, I am alright",
    "refusalConfirm": "No, I am happy to answer",
    "explanationCheck": "Yes, that is what I meant",
    "explanationClarify": "Yes, that is what I meant",
}

_ITEM = re.compile(r"^A\d{3}$")


def reply_to(reply: dict, email: str) -> str:
    """What this caller says next, given what was just asked."""
    ref = reply.get("askedRef") or ""
    kind = reply.get("askedKind") or ""
    utt = (reply.get("agentUtterance") or "").lower()

    # A read-back is agreed to only if it names something the caller claimed.
    if kind == "confirmation":
        if any(c in utt for c in CLAIMS):
            return "Yes, that is right"
        return "No, that is not something I have"

    # "Which condition was it?" - answered from what is actually being discussed,
    # never with a stock illness, or the probe teaches the record a condition the
    # caller never mentioned.
    if kind == "conditionDetail":
        if "liver" in utt or "test" in utt:
            return "It was only a blood test, I have no liver disease"
        if "hospital" in utt or "surgery" in utt or "operation" in utt:
            return "It was a gallbladder operation, nothing else"
        if "smok" in utt or "tobacco" in utt or "cigarette" in utt:
            return "I only smoke, I have no lung disease"
        return "Asthma"

    if kind in PROTOCOL:
        return PROTOCOL[kind]
    if ref in SAID:
        return SAID[ref]
    if "email" in utt or "@" in utt:
        return email

    # A follow-up sourced from Neptune rather than the form: its ref is a label
    # like FU_DIAG_WHEN, not an Axxx item. Answered off the prompt so the walk
    # survives the medical team changing the protocol without a redeploy.
    if ref and not _ITEM.match(ref):
        if "how often" in utt or "how many times" in utt:
            return "About twice a week"
        if "diagnos" in utt or "when" in utt or "how long" in utt:
            return "Around 2015"
        if "inhaler" in utt or "pump" in utt:
            return "About twice a week"
        if "treatment" in utt or "medicat" in utt or "medicine" in utt:
            return "A salbutamol inhaler when I need it"
        if "control" in utt:
            return "Yes, it is well controlled"
        if "hospital" in utt or "admitted" in utt:
            return "No, never admitted for it"
        if "smok" in utt or "tobacco" in utt:
            return "Yes, ten cigarettes a day"
        if "alcohol" in utt or "drink" in utt:
            return "Beer, about four bottles a week"
        return "No"
    return "no"


def main() -> int:
    cust = st.get_customer_by_phone(PHONE)
    if not cust:
        print(f"FAIL: no seeded customer for {PHONE}")
        return 1
    email = cust.get("customerEmail") or "test@example.com"

    print(f"[1] Walk the whole form as a caller with things to disclose "
          f"({FORM_ID})")
    session = st.start_session(PHONE, cust, FORM_ID)
    call_id = session["callId"]
    st.set_language(call_id, "en", confirmed=True)

    turn = 1
    reply = orch.handle_turn(call_id, "", turn, language_hint="en")
    asked: list[str] = []          # every ref the agent put to the caller
    trail: list[str] = []          # ref(kind) pairs, for diagnosing a stall

    for _ in range(90):
        ref = reply.get("askedRef") or ""
        kind = reply.get("askedKind") or ""
        if ref:
            asked.append(ref)
        trail.append(f"{ref}({kind})" if kind else ref or "?")
        if reply.get("isComplete") or reply.get("stopQuestionnaire"):
            break
        said = reply_to(reply, email)
        turn += 1
        reply = orch.handle_turn(call_id, said, turn, language_hint="en")

    sess = st.get_session(call_id) or {}
    answers = sess.get("answers") or {}
    print(f"    {len(asked)} questions asked over {turn} turns, "
          f"{len(answers)} answers recorded")
    check("the form ran to completion",
          bool(reply.get("isComplete")),
          f"last={trail[-4:]}")

    print("\n[2] Every yes obliges its follow-up - asked AND recorded")
    for parent, child in sorted(OWED.items()):
        check(f"{parent} yes -> {child} was asked",
              child in asked,
              f"asked={[a for a in asked if a.startswith(parent[:2])][:8]}")
        check(f"{parent} yes -> {child} was recorded",
              child in answers and answers[child] not in (None, "", []),
              f"{child}={answers.get(child)!r}")

    print("\n[3] The two-level branch: A550 sits behind A540 behind A500")
    check("A500 recorded as yes", answers.get("A500") is True,
          repr(answers.get("A500")))
    check("A540 recorded as yes", answers.get("A540") is True,
          repr(answers.get("A540")))
    check("A550 was reached, which needs both booleans right",
          "A550" in answers, repr(answers.get("A550")))
    check("the medication itself was captured, not just a yes",
          "pantoprazole" in str(answers.get("A550", "")).lower(),
          repr(answers.get("A550")))

    print("\n[4] Negative control: A640 was no, so A650 must not exist")
    check("A640 recorded as no", answers.get("A640") is False,
          repr(answers.get("A640")))
    check("A650 was never asked", "A650" not in asked,
          f"asked={asked}")
    check("A650 was never recorded", "A650" not in answers,
          repr(answers.get("A650")))

    print("\n[5] The detail is the caller's, not a placeholder")
    check("the tobacco detail carries the quantity",
          "ten" in str(answers.get("A410", "")).lower()
          or "10" in str(answers.get("A410", "")),
          repr(answers.get("A410")))
    check("the alcohol detail carries the drink",
          "beer" in str(answers.get("A430", "")).lower(),
          repr(answers.get("A430")))
    check("the abnormal test is identified",
          "liver" in str(answers.get("A610", "")).lower(),
          repr(answers.get("A610")))
    check("the family history names the relative",
          "father" in str(answers.get("A630", "")).lower(),
          repr(answers.get("A630")))

    print("\n[6] The condition screen still opened its protocol")
    conditions = sess.get("declaredConditions") or []
    declared = [d.get("icd10") for d in conditions]
    check("A230 recorded as yes", answers.get("A230") is True,
          repr(answers.get("A230")))
    check("asthma was declared from the screen",
          "J45" in declared, str(declared))

    # The point of saying yes to lifestyle and history questions is that they are
    # NOT disclosures, and the record has to know the difference. This section is
    # the regression guard for a real defect this suite found on its first run.
    # Measured then, each of these was a confident disease match on its own:
    #
    #   "smoke"                      -> J44 COPD          0.8141
    #   "liver function test"        -> K74 Cirrhosis     0.7948
    #   "gallstone surgery"          -> N20 Kidney Stones 0.7738
    #   "My father has ... diabetes" -> E11 Diabetes      0.8197
    #
    # The COPD one was the dangerous one: it came back corroborated with
    # needsConfirmation false, so it took the path that writes without a
    # read-back. Answering "yes" to "do you use tobacco?" put Chronic Obstructive
    # Pulmonary Disease on the record with nobody ever asked.
    #
    # Fixed by adding the bare habit words and test/procedure names to the null
    # class in seed/distractors.json, which is what that file is for. They now
    # retrieve a distractor instead - "smoke" nulls at 0.886 while "smoker's
    # lung", a genuine disclosure, still resolves to J44 at 1.0.
    #
    # The read-back reply above refuses anything the caller did not claim, and it
    # has to: an agreeable caller papers straight over all of this. The first
    # version of this suite answered "yes, that is right" to everything, agreed to
    # Cirrhosis of Liver, and passed with K74 on the record.
    print("\n[7] A habit, a test name and a relative's illness are not diagnoses")
    print(f"    declared: {[(d.get('icd10'), d.get('patientPhrase')) for d in conditions]}")
    for icd, label, why in (
        ("J44", "COPD", "the caller only said they smoke"),
        ("K74", "Cirrhosis of Liver", "the caller only had a liver function test"),
        ("N20", "Kidney Stones", "the caller had gallbladder, not kidney, surgery"),
    ):
        offending = [d for d in conditions if d.get("icd10") == icd]
        check(f"{label} is not on the record - {why}",
              not offending,
              f"declared from {offending[0].get('patientPhrase')!r} "
              f"at {offending[0].get('confidence')}" if offending else "")
    father = [d for d in conditions
              if d.get("icd10") == "E11"
              and "father" in str(d.get("patientPhrase", "")).lower()]
    check("a relative's diabetes is not recorded as the caller's",
          not father, str(father))

    # Collecting the detail is only half of it. generate_summary compiled
    # session["answers"] alone, and answers holds form items - so a screen that
    # opened a protocol was summarised as the screen and nothing else:
    #
    #     Q: Asthma, tuberculosis, COPD, ... or any other lung condition?
    #     A: Yes
    #
    # J45 and all five follow-up answers were in the session the whole time, under
    # declaredConditions and followUpAnswers, which are not form items. To anyone
    # reading the summary that is indistinguishable from never having asked, which
    # is exactly how it was reported. The flag disagreed too: it counted True
    # booleans in two sections while crm_writeback counts declared conditions and
    # unresolved mentions, so a VOLUNTEERED condition read "clear" here and "refer"
    # in Salesforce.
    print("\n[8] The summary carries the protocol, not just the screen answer")
    summary = tools.generate_summary(call_id)
    blob = json.dumps(summary)
    summary_icds = [c.get("icd10") for c in (summary.get("conditions") or [])]
    check("the declared condition reaches the summary",
          "J45" in summary_icds, str(summary_icds))
    j45 = next((c for c in (summary.get("conditions") or [])
                if c.get("icd10") == "J45"), {})
    check("with the caller's own words attached",
          bool(j45.get("patientPhrase")), repr(j45.get("patientPhrase")))
    check("and the protocol that mandated the follow-ups",
          "neptune:" in str(j45.get("protocolSource") or ""),
          repr(j45.get("protocolSource")))
    check("the follow-up answers travel with it",
          len(j45.get("followUps") or {}) >= 3,
          f"{sorted((j45.get('followUps') or {}))}")
    check("every follow-up carries its question, not a bare id",
          all(f.get("question") for f in (j45.get("followUps") or {}).values()),
          str(j45.get("followUps"))[:160])
    check("unresolved mentions are surfaced for a human",
          "needsHumanReview" in summary, sorted(summary))
    # The writeback's rule: escalation, then unresolved mentions, then any
    # declared condition. Anything else would let the machine mark its own
    # blind spot as needing no review.
    check("the flag agrees with what Salesforce will say",
          summary.get("underwritingFlag") == "refer",
          f"{summary.get('underwritingFlag')} with {len(summary_icds)} condition(s) "
          f"and {len(summary.get('needsHumanReview') or [])} unresolved")
    check("nothing declared is dropped between session and summary",
          {c.get("icd10") for c in conditions} <= set(summary_icds),
          f"session={[c.get('icd10') for c in conditions]} summary={summary_icds}")
    del blob

    print()
    if failures:
        print(f"{len(failures)} check(s) failed:")
        for f in failures:
            print("  -", f)
        print(f"\npath taken: {' -> '.join(trail)}")
        return 1
    print("=" * 74)
    print("A caller with things to disclose is asked every follow-up their "
          "answers oblige,\nincluding the two-level medication branch, and the "
          "one screen they denied stays shut.\nWhat the protocol collected "
          "reaches the summary, so a screen answer is never\nthe whole record - "
          "and a habit is not filed as a disease.")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
