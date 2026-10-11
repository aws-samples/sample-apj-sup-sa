#!/usr/bin/env python3
"""A correctly-recorded year must not be flagged as invented.

THE LIVE EVIDENCE
-----------------
Two notes, one turn, on the deployed agent, flatly contradicting each other:

    recorded the spoken year 'Two thousand fifteen se' as 2015
    UNDERWRITER ATTENTION: year 2015 was not spoken by the caller

Both came from extract.py and only one could be true. normalise_spoken_year
understands word-form years and correctly produced 2015. ground_spoken_year then
checked it against _spoken_years, which finds only FOUR-DIGIT tokens, found none,
and declared the year un-spoken.

The record was right and the flag was wrong, which is the worse direction:
UNDERWRITER ATTENTION exists to route a doubtful answer to a human, and it is
worth nothing if it fires on correct ones.

Runs the two functions in the SAME ORDER the orchestrator does - normalise then
ground - because the bug only appears in that composition. AWS-free.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "agent" / "common"))

import extract  # noqa: E402

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"   {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


def pipeline(model_value: str, utterance: str) -> tuple[str, list[str]]:
    """What the orchestrator does to a formField=="since" answer."""
    notes: list[str] = []
    value, words_note = extract.normalise_spoken_year(model_value)
    if words_note:
        notes.append(words_note)
    grounded, year_note = extract.ground_spoken_year(value, utterance)
    if year_note:
        notes.append(year_note if grounded != value
                     else f"UNDERWRITER ATTENTION: {year_note}")
    return grounded, notes


def main() -> int:
    print("[1] THE REPORTED CASE: a word-form year said and recorded correctly")
    value, notes = pipeline("Two thousand fifteen se", "Two thousand fifteen se")
    print(f"      value={value!r}")
    for n in notes:
        print(f"      note: {n}")
    check("the year is recorded as digits", value == "2015", repr(value))
    check("NO underwriter attention on a correct record",
          not any("ATTENTION" in n for n in notes), str(notes))
    check("the conversion is still reported for the audit trail",
          any("recorded the spoken year" in n for n in notes), str(notes))

    print("\n[2] the other word forms callers actually use")
    for utterance, want in (
            ("Twenty fifteen.", "2015"),
            ("Two thousand and five", "2005"),
            ("Nineteen eighty seven", "1987"),
            ("two thousand nineteen", "2019"),
    ):
        value, notes = pipeline(utterance, utterance)
        flagged = any("ATTENTION" in n for n in notes)
        check(f"{utterance!r} -> {value!r}, unflagged",
              value == want and not flagged, f"{value!r} notes={notes}")

    print("\n[3] digits still behave exactly as before")
    value, notes = pipeline("2019", "2019, I told you already")
    check("a digit year is kept and unflagged",
          value == "2019" and not any("ATTENTION" in n for n in notes),
          f"{value!r} {notes}")

    print("\n[4] THE BUG THIS FUNCTION EXISTS FOR is still caught")
    # The original defect: the model reported one year, the caller said another.
    value, notes = pipeline("2015", "2019, I told you already")
    check("a mis-heard digit year is corrected to what was said",
          value == "2019", repr(value))
    check("and the correction is reported",
          any("corrected year" in n for n in notes), str(notes))

    print("\n[5] NEW CAPABILITY: correcting to a WORD-FORM year")
    # Digits-only grounding could not do this - it saw no year in the utterance
    # and could only complain.
    value, notes = pipeline("2015", "No, two thousand nineteen")
    check("a mis-heard year is corrected to a spoken word-form year",
          value == "2019", repr(value))

    print("\n[6] a genuinely invented year is STILL flagged")
    # The caller named no year at all. This must keep firing.
    value, notes = pipeline("2015", "I don't remember exactly")
    print(f"      notes: {notes}")
    check("attention fires when no year was spoken",
          any("ATTENTION" in n for n in notes), str(notes))
    check("and the value is left alone rather than invented over",
          value == "2015", repr(value))

    print("\n[7] a relative answer carries no year, so nothing to check")
    for utterance in ("पांच साल पहले", "five years ago", "Five years ago"):
        value, notes = pipeline(utterance, utterance)
        check(f"{utterance!r} -> no attention",
              not any("ATTENTION" in n for n in notes), f"{value!r} {notes}")

    print("\n[8] ambiguity is reported, not guessed at")
    # The model picked one of the two years the caller offered. That IS grounded -
    # the caller said it - so there is nothing to correct and nothing to flag.
    value, notes = pipeline("2015", "2015 or 2016, I'm not sure")
    check("a year the caller did say is accepted, even amid two",
          value == "2015" and not notes, f"{value!r} {notes}")
    # The model picked a year the caller never said, and the caller offered more
    # than one, so there is no single answer to substitute. Report, do not guess.
    value, notes = pipeline("2020", "2015 or 2016, I'm not sure")
    check("a year the caller did NOT say is left alone", value == "2020", repr(value))
    check("and is reported as differing from what was spoken",
          any("differs from the years spoken" in n for n in notes), str(notes))
    check("as an underwriter flag, since nothing was corrected",
          any("ATTENTION" in n for n in notes), str(notes))

    print("\n[9] years_said directly: digits win, words are the fallback")
    check("digits only", extract.years_said("in 2019") == [2019],
          str(extract.years_said("in 2019")))
    check("words only", extract.years_said("twenty fifteen") == [2015],
          str(extract.years_said("twenty fifteen")))
    check("digits are NOT overridden by a word form in the same sentence",
          extract.years_said("2019, not twenty fifteen") == [2019],
          str(extract.years_said("2019, not twenty fifteen")))
    check("no year at all", extract.years_said("i forget") == [],
          str(extract.years_said("i forget")))
    check("a dosage is not a year", extract.years_said("metformin 500 mg") == [],
          str(extract.years_said("metformin 500 mg")))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("A year said in words counts as spoken: correct records are not flagged, "
          "and a genuinely invented year still is.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
