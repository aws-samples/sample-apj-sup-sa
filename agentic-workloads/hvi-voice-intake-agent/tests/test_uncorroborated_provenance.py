#!/usr/bin/env python3
"""A code the graph never corroborated must reach a human, even when accepted.

THE FAILURE THIS SUITE EXISTS FOR, taken from a live browser call:

  caller: "पांच साल पहले ब्रॉन्काइटिस"      (five years ago, bronchitis)
  agent:  is that COPD?                     -> no
  agent:  is that chronic kidney disease?   -> no
  agent:  is that Chronic Viral Hepatitis?  -> yes

B18 was written onto the underwriting record at 0.7959 with four hepatitis
follow-ups behind it. The stored row was:

  {"icd10": "B18", "label": "Chronic Viral Hepatitis",
   "patientPhrase": "पांच साल पहले ब्रॉन्काइटिस।", "confidence": 0.7959855}

Nothing in it says the clinical graph never agreed, nothing says two other codes
were rejected first, and unclassifiedMentions was empty - so the file was stamped
as needing no second look.

Read-back is a genuine guard and it caught the first two guesses. The point here
is that it is defeated by a caller who is tiring, agreeable, or simply unsure,
so caller agreement cannot be the only evidence that survives into the record.
The existing escape hatch fires on REJECTION; this suite covers ACCEPTANCE.

What is asserted:
  - an uncorroborated match, accepted by the caller, is stored corroborated=False
  - it appears in needsHumanReview with the caller's own words
  - a graph-corroborated match is stored corroborated=True and is NOT flagged
  - a substituted candidate cannot inherit the original's corroboration
  - rows written before the field existed are not retroactively flagged
  - generate_summary and crm_writeback never disagree about any of it

Run:  ./.venv/bin/python tests/test_uncorroborated_provenance.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_dialogue_state_model as harness  # noqa: E402

orch = harness.orch

failures: list[str] = []
checks = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"  <- {detail[:220]}" if not ok and detail else ""))
    if not ok:
        failures.append(label)


def _declared(session: dict, code: str) -> dict:
    return next((row for row in session.get("declaredConditions") or []
                 if row.get("icd10") == code), {})


def main() -> int:  # noqa: PLR0915
    original = (orch.st, orch.tools, orch.extract)
    state = harness.MemoryState()
    tools = harness.FakeTools(state)
    extract = harness.ScriptedExtract()
    orch.st, orch.tools, orch.extract = state, tools, extract

    # A condition deliberately OUTSIDE the curated graph, reached by similarity
    # alone. J44 stands in for the real B18: what matters is not which wrong code
    # was offered but that the graph did not corroborate whichever one was.
    def uncorroborated_classify(phrase: str) -> dict:
        tools.calls.append("classify_condition")
        return {
            "phrase": phrase, "icd10": "J44",
            "label_en": "Chronic Obstructive Pulmonary Disease",
            "label_hi": "\u0938\u0940\u0913\u092a\u0940\u0921\u0940",
            "category": "respiratory", "severityTier": 3,
            "score": 0.7959, "source": "opensearch_knn",
            "confident": True, "ambiguous": False,
            "corroborated": False, "uncorroborated": True,
            "redFlag": False, "needsConfirmation": True,
            "confirmReason": "similarity match not corroborated by the clinical graph",
            "candidates": [
                {"icd10": "J44", "label_en": "Chronic Obstructive Pulmonary Disease",
                 "category": "respiratory", "score": 0.7959},
                {"icd10": "J45", "label_en": "Asthma",
                 "category": "respiratory", "score": 0.7801},
            ],
        }

    try:
        item = next(row for row in harness.ITEMS if row["itemId"] == "A100")
        identity = {"A010": True, "A020": True, "A030": "1987-03-22"}

        print("[1] an uncorroborated match the caller ACCEPTS is still flagged")
        call_id, session = state.reset([item], answers=dict(identity))
        tools.classify_condition = uncorroborated_classify  # type: ignore[method-assign]
        extract.push(harness.reading(
            170, mentionsCondition=True, conditionPhrase="bronchitis five years ago",
            conditionMentions=[{"phrase": "bronchitis five years ago",
                                "scope": "self_current"}]))
        orch.handle_turn(call_id, "170 and bronchitis five years ago", 1, "en")
        parked = session.get("awaitingConfirmation") or {}
        check("the interpretation is parked for read-back",
              parked.get("icd10") == "J44", str(parked)[:200])
        check("the parked payload carries the corroboration verdict",
              parked.get("corroborated") is False, str(parked)[:200])

        # The caller agrees, exactly as she did on the live call.
        extract.push(harness.reading(True))
        orch.handle_turn(call_id, "yes", 2, "en")
        row = _declared(session, "J44")
        check("the condition IS recorded (the disclosure is not discarded)",
              bool(row), str(session.get("declaredConditions"))[:200])
        check("recorded with corroborated=False",
              row.get("corroborated") is False, str(row)[:200])
        check("recorded as confirmed by the patient",
              row.get("confirmedByPatient") is True, str(row)[:200])

        print("[2] the REAL generate_summary routes it to a human")
        # original[1] is the genuine agent.tools module, saved before the fake was
        # installed. Pointing its state module at the in-memory session exercises
        # the shipped rule rather than FakeTools' stub summary - the stub returns a
        # skeleton with no needsHumanReview key at all, so asserting against it
        # would have proved nothing.
        real_tools = original[1]
        saved_st = real_tools.st
        real_tools.st = state
        try:
            summary = real_tools.generate_summary(call_id)
        finally:
            real_tools.st = saved_st
        flagged = [r for r in (summary.get("needsHumanReview") or [])
                   if r.get("resolvedTo") == "J44"]
        check("J44 appears in needsHumanReview", bool(flagged),
              str(summary.get("needsHumanReview"))[:220])
        check("the flag reason names the missing corroboration",
              bool(flagged) and "did not corroborate" in (flagged[0]["reason"] or ""),
              str(flagged)[:220])
        check("it keeps the caller's own words for the reviewer",
              bool(flagged) and "bronchitis" in (flagged[0]["patientPhrase"] or ""),
              str(flagged)[:220])
        check("the condition still appears in conditions, carrying provenance",
              any(c.get("icd10") == "J44" and c.get("corroborated") is False
                  for c in (summary.get("conditions") or [])),
              str(summary.get("conditions"))[:220])
        check("the underwriting flag is not 'clear'",
              summary.get("underwritingFlag") != "clear",
              str(summary.get("underwritingFlag")))

        print("[3] a corroborated match is NOT flagged")
        call_id2, session2 = state.reset([item], answers=dict(identity))
        # Restore the harness classifier: it returns neptune_exact_synonym with
        # corroborated=True, which is the graph fast path.
        tools.classify_condition = harness.FakeTools.classify_condition.__get__(
            tools, harness.FakeTools)
        extract.push(harness.reading(
            170, mentionsCondition=True, conditionPhrase="diabetes",
            conditionMentions=[{"phrase": "diabetes", "scope": "self_current"}]))
        orch.handle_turn(call_id2, "170 and I have diabetes", 1, "en")
        row2 = _declared(session2, "E11")
        check("diabetes recorded straight through, no read-back needed",
              bool(row2), str(session2.get("declaredConditions"))[:200])
        check("recorded with corroborated=True",
              row2.get("corroborated") is True, str(row2)[:200])
        check("not marked as patient-confirmed (it did not need to be)",
              row2.get("confirmedByPatient") is False, str(row2)[:200])

        print("[4] a substituted candidate cannot inherit corroboration")
        call_id3, session3 = state.reset(
            [], answers={"A010": True, "A020": True, "A030": "1987-03-22"},
            awaitingConfirmation={
                "icd10": "J44", "label": "Chronic Obstructive Pulmonary Disease",
                "question": "is that COPD?", "phrase": "bronchitis five years ago",
                "score": 0.7959, "redFlag": False, "corroborated": False,
                "alternatives": [
                    {"icd10": "J45", "label_en": "Asthma",
                     "category": "respiratory", "score": 0.7801}],
            })
        extract.push({"understood": True, "value": False, "confidence": 0.95,
                      "detectedLanguage": "en", "acknowledgement": ""})
        orch.handle_turn(call_id3, "no", 1, "en")
        parked3 = session3.get("awaitingConfirmation") or {}
        check("the next candidate is offered",
              parked3.get("icd10") == "J45", str(parked3)[:200])
        check("and it is marked uncorroborated, not endorsed by the graph",
              parked3.get("corroborated") is False, str(parked3)[:200])
        check("the refused code is blacklisted",
              "J44" in (session3.get("rejectedCodes") or []),
              str(session3.get("rejectedCodes")))

        print("[5] the two report builders agree, including on legacy rows")
        # Legacy row: written before provenance existed, so corroborated is absent.
        # It must NOT be retroactively flagged - unknown provenance is not the same
        # claim as known-bad provenance.
        legacy = {"icd10": "I10", "label": "Hypertension",
                  "patientPhrase": "BP hai", "confidence": 0.91,
                  "protocolSource": "neptune:test", "at": "2026-01-01T00:00:00Z"}
        bad = {"icd10": "B18", "label": "Chronic Viral Hepatitis",
               "patientPhrase": "\u092c\u094d\u0930\u0949\u0928\u094d\u0915\u093e\u0907\u091f\u093f\u0938",
               "confidence": 0.7959855, "corroborated": False,
               "confirmedByPatient": True,
               "protocolSource": "neptune:test", "at": "2026-01-02T00:00:00Z"}
        good = {"icd10": "E11", "label": "Type 2 Diabetes", "patientPhrase": "sugar hai",
                "confidence": 0.94, "corroborated": True, "confirmedByPatient": False,
                "protocolSource": "neptune:test", "at": "2026-01-03T00:00:00Z"}
        fake_session = {"declaredConditions": [legacy, bad, good],
                        "unclassifiedMentions": []}

        # Exercise the SHIPPED shared rule, not a reimplementation of it.
        rows = state.provenance_review_rows(fake_session) \
            if hasattr(state, "provenance_review_rows") else None
        if rows is None:
            import hvi_state as real_state  # noqa: PLC0415
            rows = real_state.provenance_review_rows(fake_session)
        flagged_codes = [r["resolvedTo"] for r in rows]
        check("the shared rule flags ONLY the uncorroborated row",
              flagged_codes == ["B18"], str(flagged_codes))
        check("it leaves the LEGACY row unflagged (unknown is not known-bad)",
              "I10" not in flagged_codes, str(flagged_codes))
        check("it leaves the corroborated row unflagged",
              "E11" not in flagged_codes, str(flagged_codes))
        check("the flag carries the caller's words, not just the code",
              rows and rows[0]["patientPhrase"], str(rows)[:200])

        ok, why = _shared_rule_is_shared()
        check("both builders DELEGATE to the shared rule (cannot drift)", ok, why)

        print("[6] classifiedBy stops overstating the evidence")
        import hvi_state as real_state  # noqa: PLC0415
        labels = {row["icd10"]: real_state.classified_by(row)
                  for row in fake_session["declaredConditions"]}
        check("uncorroborated row does not claim the graph",
              "neptune" not in (labels.get("B18") or "").lower(),
              str(labels))
        check("corroborated row does claim both methods",
              labels.get("E11") == "neptune-graph+opensearch-knn", str(labels))
        check("legacy row is marked as provenance-not-recorded",
              "not recorded" in (labels.get("I10") or ""), str(labels))
        check("the writeback uses the shared classified_by, not a local copy",
              "st.classified_by(d)" in _WRITEBACK.read_text(),
              "crm_writeback stopped delegating classifiedBy")
    finally:
        orch.st, orch.tools, orch.extract = original

    print()
    if failures:
        print(f"  {len(failures)} of {checks} checks FAILED")
        for f in failures:
            print(f"    - {f}")
        return 1
    print(f"  All {checks} provenance checks passed.")
    print("  An accepted guess is now distinguishable from a corroborated match,")
    print("  and only the guess reaches a human.")
    return 0


# --------------------------------------------------------------------------- #
# The two report builders, exercised as the real modules rather than reimplemented.
# Reimplementing the rule in the test would assert the test's opinion, not the
# system's - and the hazard being guarded against is precisely the two builders
# drifting apart.
# --------------------------------------------------------------------------- #
def _load_writeback():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_wb_provenance",
        str(Path(__file__).resolve().parent.parent
            / "lambdas" / "crm_writeback" / "handler.py"))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_ROOT = Path(__file__).resolve().parent.parent
_WRITEBACK = _ROOT / "lambdas" / "crm_writeback" / "handler.py"
_SUMMARY = _ROOT / "agent" / "tools.py"


def _shared_rule_is_shared() -> tuple[bool, str]:
    """Both report builders must DELEGATE the rule, not carry their own copy.

    This is the structural version of the invariant. The rule now lives once, in
    hvi_state.provenance_review_rows, and crm_writeback is packaged with that
    exact file - so the only way the two verdicts can diverge is if one builder
    stops calling the shared function. That is what this asserts, and it is
    strictly stronger than comparing two copies for equality.
    """
    wb, sm = _WRITEBACK.read_text(), _SUMMARY.read_text()
    problems = []
    if "st.provenance_review_rows(session)" not in wb:
        problems.append("crm_writeback no longer calls st.provenance_review_rows")
    if "st.provenance_review_rows(session)" not in sm:
        problems.append("generate_summary no longer calls st.provenance_review_rows")
    # And neither may have grown a private copy of the guard.
    for name, src in (("crm_writeback", wb), ("generate_summary", sm)):
        if 'corroborated") is not False' in src:
            problems.append(f"{name} has reintroduced its own copy of the guard")
    return (not problems), "; ".join(problems)


if __name__ == "__main__":
    raise SystemExit(main())
