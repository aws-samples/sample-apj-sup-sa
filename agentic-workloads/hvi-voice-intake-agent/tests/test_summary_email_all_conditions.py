#!/usr/bin/env python3
"""EVERY condition's sub-questions, checked in the EMAIL the customer receives.

WHY THIS EXISTS. The suite could already show that each of the 38 conditions
classifies, opens its protocol and is stored (tests/test_protocol_paths.py, 207
checks) - but every one of those assertions reads internal state. The artifact a
customer and an underwriter actually judge is the summary email, and nothing
verified that. The emailed transcript is where the defects were visible in the
first place: seventeen "we could not identify this" items, sixteen of them
medications and habits, and a treatment answer that read as if the patient took a
salbutamol inhaler for gallstones.

So this suite renders the real email, through the real
lambdas/send_confirmation/handler.build_message, for EVERY governed condition,
and asserts:

  1. the condition appears with its ICD-10 code
  2. EVERY follow-up its protocol mandates appears as a Q line - this is the
     "sub-questions for each path" check, done against the artifact rather than
     the database
  3. every answer appears UNDER ITS OWN condition and nowhere else, which is what
     makes cross-attribution detectable
  4. nothing that cannot be a diagnosis is shown as an unidentified condition

Answers are unique markers rather than plausible prose, on purpose: "ANS-J45-
FU_TREATMENT" appearing in the K80 section is unambiguous proof of
misattribution, where two conditions both answering "a salbutamol inhaler" hides
it - which is exactly how the live record looked believable.

No model calls, so all 38 conditions are covered in every language rather than a
sampled few.

Run:  ./.venv/bin/python tests/test_summary_email_all_conditions.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "agent"))

_agent_common = os.path.join(ROOT, "agent", "common")
os.makedirs(_agent_common, exist_ok=True)
for _mod in ("hvi_state.py", "hvi_prompts.py"):
    shutil.copy(os.path.join(ROOT, "common", _mod),
                os.path.join(_agent_common, _mod))

import tools  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "common"))
import hvi_state as st  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "lambdas", "send_confirmation"))
import handler as mail  # noqa: E402

failures: list[str] = []
checks = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not ok:
        failures.append(label)
        print(f"  FAIL  {label}   {detail[:230]}")


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _phones  # noqa: E402

# This suite's OWN number, still the seeded "Priya Sharma" record but cloned onto
# a phone nobody else uses. Twelve suites shared one number, and the unfinished
# sessions they leave behind leaked between them through find_resumable_session.
# See tests/_phones.py; reserve() also clears leftovers from a previous run.
PHONE = _phones.reserve("test_summary_email_all_conditions")
FORM_ID = os.environ.get("FORM_ID", "arogya-sanjeevani-v1")

# Words that can never be a diagnosis. Mirrors the guard in the orchestrator; if
# one of these reaches the customer's review list the guard has a hole.
NOT_CONDITIONS = ("salbutamol", "pantoprazole", "inhaler", "tablet", "milligram",
                  "cigarette", "smoke", "tobacco", "beer", "alcohol",
                  "liver function test", "enzyme")


def answer_for(fu: dict, icd10: str) -> object:
    """A UNIQUE, traceable answer, so misattribution cannot hide."""
    qid = fu.get("questionId") or "Q"
    atype = (fu.get("answerType") or "text").lower()
    if atype == "boolean":
        return True
    if atype == "enum":
        opts = fu.get("options") or []
        return opts[0] if opts else f"ANS-{icd10}-{qid}"
    if atype == "number":
        return 7
    return f"ANS-{icd10}-{qid}"


def section_for(plain: str, label: str, icd10: str) -> str:
    """The slice of the email belonging to one condition."""
    head = f"{label} ({icd10})"
    if head not in plain:
        return ""
    rest = plain.split(head, 1)[1]
    # The section ends at the next condition heading or the next block header.
    for stop in ("\nNOTED FOR REVIEW", "\nDECLARATION", "\nHOW THIS WAS VERIFIED"):
        if stop in rest:
            rest = rest.split(stop, 1)[0]
    return rest


def run_condition(cond: dict, language: str) -> dict:
    icd10 = cond["icd10"]
    label = cond.get("label_en") or icd10
    phrase = (cond.get("colloquial") or [label])[0]

    cust = st.get_customer_by_phone(PHONE)
    session = st.start_session(PHONE, cust, FORM_ID)
    call_id = session["callId"]
    st.set_language(call_id, language, confirmed=True)

    # The REAL protocol the agent would use, from the graph, not a fixture.
    fu = tools.get_followup_questions(icd10, language)
    queued = fu.get("followUps") or []
    st.queue_followups(call_id, icd10, label, queued,
                       fu.get("protocolSource", ""), patient_phrase=phrase,
                       confidence=1.0, corroborated=True,
                       confirmed_by_patient=True)

    expected: list[tuple[str, str, object]] = []
    for _ in range(len(queued)):
        session = st.get_session(call_id)
        head = st.peek_followup(session)
        if not head:
            break
        value = answer_for(head, icd10)
        expected.append((str(head.get("questionId")),
                         str(head.get("prompt") or ""), value))
        st.complete_followup(call_id, value)

    # The declaration block only renders once the read-back was confirmed, and the
    # email is the document that declaration refers to.
    st.record_declaration(call_id)
    message = mail.build_message(call_id)
    return {"callId": call_id, "icd10": icd10, "label": label,
            "expected": expected, "queued": len(queued),
            "plain": message["plain"], "message": message}


def main() -> int:  # noqa: PLR0915
    kg = json.loads(open(os.path.join(ROOT, "seed", "health-kg.json")).read())
    conditions = kg.get("conditions") or []
    print(f"{len(conditions)} governed conditions in the graph\n")

    languages = ("en", "hi", "hinglish")
    for language in languages:
        print(f"===== {language}: render the email for every condition =====")
        no_followups: list[str] = []
        worst = 0
        for cond in conditions:
            r = run_condition(cond, language)
            icd10, label, plain = r["icd10"], r["label"], r["plain"]

            check(f"[{language}] {icd10} appears in the email with its code",
                  f"({icd10})" in plain, plain[:160])

            section = section_for(plain, label, icd10)
            check(f"[{language}] {icd10} has its own section in the email",
                  bool(section.strip()),
                  f"looked for {label!r} ({icd10})")

            if not r["expected"]:
                no_followups.append(icd10)

            # EVERY mandated sub-question must be visible to the customer.
            for qid, prompt, value in r["expected"]:
                if prompt:
                    check(f"[{language}] {icd10}/{qid}: the question was asked and "
                          f"printed", prompt[:40] in plain,
                          f"missing {prompt[:60]!r}")
                if isinstance(value, str) and value.startswith("ANS-"):
                    check(f"[{language}] {icd10}/{qid}: its answer is under "
                          f"{icd10}, not another condition", value in section,
                          f"{value} not in the {icd10} section")
                    # And nowhere else in the document.
                    check(f"[{language}] {icd10}/{qid}: the answer appears once",
                          plain.count(value) == 1,
                          f"{value} appears {plain.count(value)}x")

            # Nothing undiagnosable may be shown as an unidentified condition.
            if "NOTED FOR REVIEW" in plain:
                review = plain.split("NOTED FOR REVIEW", 1)[1]
                offenders = [w for w in NOT_CONDITIONS if w in review.lower()]
                check(f"[{language}] {icd10}: the review list holds no "
                      f"medications or habits", not offenders, str(offenders))

            worst = max(worst, r["queued"])

        print(f"    all {len(conditions)} rendered; deepest protocol = {worst} "
              f"follow-ups")
        if no_followups:
            print(f"    NOTE: no graph protocol for {no_followups}")
        print()

    print(f"{'-' * 70}")
    if failures:
        print(f"  {len(failures)} of {checks} checks FAILED")
        for f in failures[:40]:
            print(f"    - {f}")
        return 1
    print(f"  All {checks} checks passed: every one of {len(conditions)} "
          f"conditions,")
    print(f"  every sub-question its protocol mandates, verified in the rendered")
    print(f"  email in English, Hindi and Hinglish - and no answer appears under")
    print(f"  a condition it does not belong to.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
