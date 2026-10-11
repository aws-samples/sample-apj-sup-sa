#!/usr/bin/env python3
"""Does the record say what the caller said?

THE GAP THIS CLOSES. Fifty-two suites were green when a real English call produced
a record with four wrong fields. That is not a contradiction, it is a description
of what the suites test. Six of them share one `ScriptedExtract` double which
*supplies* the extractor's output, so they verify - thoroughly - that a value
coming out of the extractor is routed, coerced and stored under the right item.
They cannot verify that the value is faithful to what the caller said, because the
test author writes the value.

Every defect on that call lived in exactly that blind spot:

    A520   said "2019, I told you already."   stored '2015'
    A410   said "Cigarette toys a week."      stored [..., 'duration not specified']
    A920   said "Skip."                       stored 'Skip'
    A990   said "Yes."                        stored 'not_captured'

So this suite compares the two columns. It is deliberately not a check on meaning -
no model, no similarity - only on things that are decidable from the pair:

    a four-digit year in the record that the caller did not say
    a list element that reports the model's uncertainty rather than an answer
    a question the caller answered that ended up not_captured
    a bare yes/no/skip stored as though it were content

WHY A FROZEN FIXTURE AS THE NEGATIVE CONTROL. An audit that has never caught
anything is indistinguishable from an audit that cannot. The real call's turns are
checked in - 4 known-bad and 26 known-good - so this proves the audit flags the
former and leaves the latter alone. The turn log itself has a 30-day TTL and would
have rotted.

Run:  ./.venv/bin/python tests/test_transcript_fidelity.py
      SKIP_LIVE_CALL=1 ./.venv/bin/python tests/test_transcript_fidelity.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "common"))
sys.path.insert(0, str(ROOT / "tools"))

FIXTURE = ROOT / "tests" / "fixtures" / "live_call_defects.json"

failures: list[str] = []
checks = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"  <- {detail[:200]}" if not ok and detail else ""))
    if not ok:
        failures.append(label)


# ---- the audit ------------------------------------------------------------- #

_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")

# Reported uncertainty, not an answer. Anchored with fullmatch so an element that
# merely contains the words ("duration not specified by the doctor") survives.
_PLACEHOLDER = re.compile(
    r"(?:duration|amount|quantity|frequency|dosage|years?|period)?\s*"
    r"(?:not\s+(?:specified|mentioned|stated|provided|given|available)"
    r"|unspecified|unknown\s+(?:duration|amount|quantity|period)"
    r"|none\s+specified|n/?a|not\s+applicable)\s*", re.I)

# A yes, a no, or a skip, kept as a string. Each of these is a decision the
# coercion layer was supposed to resolve into a typed value: True, False, or
# "declined". Finding the word itself in the record means nothing resolved it.
_BARE_DECISION = {
    "yes", "no", "yeah", "yep", "nope", "true", "false", "skip", "skipped",
    "haan", "nahi", "nahin", "ji", "correct", "right", "declined?",
    "\u0939\u093e\u0901", "\u0939\u093e\u0902", "\u0928\u0939\u0940\u0902",
    "\u0928\u0939\u0940", "\u091c\u0940", "\u0938\u094d\u0915\u093f\u092a",
}


def _years(text: str) -> list[int]:
    flat = str(text or "").translate(_DEVANAGARI_DIGITS)
    flat = re.sub(r"(?<=\d),(?=\d)", "", flat)
    this_year = time.gmtime().tm_year
    return [int(t) for t in re.findall(r"(?<!\d)(\d{4})(?!\d)", flat)
            if 1900 <= int(t) <= this_year]


def audit(said: str, stored, item_id: str = "") -> list[str]:
    """Every way this stored value fails to match what the caller said."""
    problems: list[str] = []
    spoken = str(said or "").strip()

    # 1. A YEAR NOBODY SAID. Only judged when the caller did give a year: an
    #    answer derived from "two years ago" legitimately has no spoken year.
    if isinstance(stored, str):
        said_years, kept_years = _years(spoken), _years(stored)
        if kept_years and said_years and not set(kept_years) <= set(said_years):
            problems.append(
                f"year {kept_years} recorded, caller said {said_years}")

    # 2. A LIST ELEMENT THAT IS NOT AN ANSWER.
    if isinstance(stored, list):
        invented = [e for e in stored
                    if isinstance(e, str) and _PLACEHOLDER.fullmatch(e.strip())]
        if invented:
            problems.append(f"placeholder element(s) {invented} recorded as content")

    # 3. ANSWERED, THEN LOST. The caller said something and the record says the
    #    question was never captured.
    if stored == "not_captured" and spoken:
        problems.append(f"caller answered {spoken[:40]!r} but nothing was captured")

    # 4. AN UNRESOLVED DECISION, KEPT AS PROSE.
    if isinstance(stored, str):
        flat = " ".join(stored.lower().split()).rstrip(".,;\u0964")
        if flat in _BARE_DECISION:
            problems.append(
                f"{stored!r} stored as text; a yes/no/skip should have resolved "
                "to a typed value")

    return [f"{item_id or '?'}: {p}" for p in problems]


# ---- the frozen call, as the negative control ------------------------------ #

# Exactly the turns that were wrong, established by reading the transcript.
KNOWN_BAD = {
    "A520": "the caller's spoken year was replaced with another year",
    "A410": "an element the model invented to report its own uncertainty",
    "A920": "a skip filed verbatim into the national health ID field",
    "A990": "a question the caller answered stored as not_captured",
}


def test_audit_has_teeth() -> None:
    print("[1] the audit must flag the real call's four defects, and only those")
    doc = json.loads(FIXTURE.read_text())
    turns = doc["turns"]
    check(f"fixture loaded ({len(turns)} answered turns)", len(turns) >= 25,
          str(len(turns)))

    flagged: dict[str, list[str]] = {}
    for t in turns:
        found = audit(t["said"], t["stored"], t["itemId"])
        if found:
            flagged.setdefault(t["itemId"], []).extend(found)

    for item_id, why in KNOWN_BAD.items():
        check(f"{item_id} is caught ({why})", item_id in flagged,
              f"NOT flagged - the audit would not have caught this: "
              f"{[t for t in turns if t['itemId'] == item_id]}")

    extra = sorted(set(flagged) - set(KNOWN_BAD))
    check("nothing else is flagged (no false positives on 26 good turns)",
          not extra, f"unexpectedly flagged {extra}: "
                     f"{[flagged[k] for k in extra]}")

    print("\n       what the audit reports:")
    for item_id in sorted(flagged):
        for line in flagged[item_id]:
            print(f"         {line}")


def test_audit_does_not_overreach() -> None:
    print("\n[2] answers that are correct must never be flagged")
    for said, stored, why in (
            # The good rows from the same call, plus the shapes most at risk.
            ("2015", "2015", "year repeated exactly"),
            ("In 2019.", "In 2019", "caller's preposition kept"),
            ("2019, I told you already.", "2019", "irritated but consistent"),
            ("about two years ago", "about two years ago", "no year at all"),
            ("Vodka every day for 10 years.", ["Vodka every day for 10 years"],
             "a two-digit duration is not a year"),
            ("Cigarettes, ten a day, fifteen years",
             ["Cigarettes", "ten a day", "fifteen years"], "three real elements"),
            ("Yes, I had Dengue in 2021.", True, "a boolean beside a spoken year"),
            ("Nothing. I recovered fully.", "Nothing. I recovered fully.",
             "prose that starts with a negative word"),
            ("Salbutamol inhaler when I need it.", "Salbutamol inhaler when I need it",
             "trailing full stop trimmed"),
            ("\u0939\u093e\u0901", True, "Devanagari yes, properly typed"),
            ("Skip.", "declined", "a skip that WAS resolved"),
            ("1234 5678 9012 34", "12345678901234", "separators stripped"),
            ("duration not specified by the doctor",
             ["duration not specified by the doctor"],
             "contains the words but is a real answer"),
            ("A liver function test in 2020, the enzymes were high",
             "A liver function test in 2020, the enzymes were high", "year echoed"),
    ):
        found = audit(said, stored)
        check(f"clean: {why}", not found, f"said={said!r} stored={stored!r} -> {found}")

    print("\n[2b] and the defects are caught in isolation too")
    for said, stored, why in (
            ("2019, I told you already.", "2015", "wrong year"),
            ("The operation was in 2019", "2015", "wrong year, demo phrasing"),
            ("Cigarette toys a week.",
             ["cigarette", "toys a week", "duration not specified"], "placeholder"),
            ("Skip.", "Skip", "unresolved skip"),
            ("Yes.", "not_captured", "answered but lost"),
            ("Yes.", "Yes", "unresolved yes"),
            ("\u0928\u0939\u0940\u0902", "\u0928\u0939\u0940\u0902",
             "unresolved Devanagari no"),
            ("2019 mein", "2015", "Hinglish wrong year"),
            ("\u0968\u0966\u0967\u0669 \u092e\u0947\u0902", "2015",
             "Devanagari digits, wrong year"),
    ):
        found = audit(said, stored)
        check(f"caught: {why}", bool(found), f"said={said!r} stored={stored!r} NOT flagged")


# ---- the same audit, over a call driven right now -------------------------- #

def test_live_call_is_faithful() -> None:
    print("\n[3] drive a real call and audit every answer against what was said")
    if os.environ.get("SKIP_LIVE_CALL"):
        print("       SKIP_LIVE_CALL set - skipping the end-to-end leg")
        return

    import boto3  # noqa: PLC0415
    from boto3.dynamodb.conditions import Key  # noqa: PLC0415

    import demo_call  # noqa: PLC0415
    import hvi_state as st  # noqa: PLC0415
    import orchestrator as orch  # noqa: PLC0415

    import _phones  # noqa: PLC0415

    lang = "en"
    # The form still comes from demo_call, but not the phone: demo_call.PHONE is
    # the shared demo number, and driving a full call on it left sessions that
    # other suites could then resume. See tests/_phones.py.
    phone = _phones.reserve("test_transcript_fidelity")
    cust = st.get_customer_by_phone(phone)
    if not cust:
        check("a seeded customer exists to call", False, phone)
        return
    email = cust.get("customerEmail") or "demo@example.com"
    session = st.start_session(phone, cust, demo_call.FORM_ID)
    call_id = session["callId"]
    st.set_language(call_id, lang, confirmed=True)
    print(f"       driving {call_id}")

    ctx: dict = {}
    turn = 1
    reply = orch.handle_turn(call_id, "", turn, language_hint=lang)
    for _ in range(95):
        if reply.get("isComplete") or reply.get("stopQuestionnaire"):
            break
        said = demo_call.reply_to(reply, lang, email, ctx)
        turn += 1
        reply = orch.handle_turn(call_id, said, turn, language_hint=lang)
    check("the call completed", bool(reply.get("isComplete")),
          f"stopped after {turn} turns")

    ddb = boto3.resource(
        "dynamodb", region_name=os.environ.get("DATA_REGION", "ap-south-1"))
    rows = ddb.Table("hvi-call-state").query(
        KeyConditionExpression=Key("callId").eq(call_id)).get("Items", [])
    answered = [r for r in rows
                if r.get("role") == "customer" and r.get("itemId")]
    check(f"the turn log has the answered turns ({len(answered)})",
          len(answered) >= 20, str(len(answered)))

    problems: list[str] = []
    for r in sorted(answered, key=lambda x: int(x.get("turnIndex", 0))):
        stored = r.get("extractedValue")
        if isinstance(stored, list):
            stored = [str(x) for x in stored]
        elif stored is not None and not isinstance(stored, (str, bool)):
            stored = str(stored)
        problems.extend(audit(str(r.get("text") or ""), stored, str(r["itemId"])))
    check("every answer on a live call is faithful to what was said",
          not problems, "; ".join(problems))

    # The specific fields that were wrong, named so a regression is unambiguous.
    sess = st.get_session(call_id) or {}
    answers = sess.get("answers") or {}
    said_a520 = demo_call.SAID[lang]["A520"]
    check(f"A520 holds the year the script said ({said_a520!r})",
          bool(answers.get("A520"))
          and set(_years(str(answers["A520"]))) <= set(_years(said_a520))
          and bool(_years(str(answers["A520"]))),
          f"A520={answers.get('A520')!r}")
    check("A990 holds an address, not a confirmation",
          "@" in str(answers.get("A990") or ""), f"A990={answers.get('A990')!r}")
    check("A920 holds an identifier, not the word skip",
          str(answers.get("A920") or "").strip().lower() not in _BARE_DECISION,
          f"A920={answers.get('A920')!r}")
    a410 = answers.get("A410") or []
    check("A410 contains no invented element",
          not [e for e in a410 if _PLACEHOLDER.fullmatch(str(e).strip())],
          f"A410={a410!r}")


def main() -> int:
    test_audit_has_teeth()
    test_audit_does_not_overreach()
    test_live_call_is_faithful()

    print()
    if failures:
        print(f"  {len(failures)} of {checks} checks FAILED")
        for f in failures:
            print(f"    - {f}")
        return 1
    print(f"  All {checks} transcript-fidelity checks passed.")
    print("  The audit catches all four defects on the frozen real call, leaves")
    print("  its 26 correct answers alone, and finds nothing on a call driven now.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
