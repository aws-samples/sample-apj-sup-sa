#!/usr/bin/env python3
"""Every way a live caller has said a date of birth, and every guard that must hold.

Each accepted case below is a VERBATIM transcript from a real call that the
parser rejected at the time - the identity gate then terminated the call on a
caller who had answered correctly. Three scripts (Latin, Devanagari, Urdu),
garbled phonetic spellings, and fully spoken Hindi numbers all have to work.

Run:  ./.venv/bin/python tests/test_spoken_dates.py
"""
from __future__ import annotations

import datetime as dt
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agent"))

import extract  # noqa: E402

DOB = {"validation": {"minAgeYears": 18}}
THIS_YEAR = dt.date.today().year

ACCEPTED = [
    # --- live transcripts of one caller saying "5 September 2000" -----------
    ("5 September 2000", "2000-09-05", "English"),
    ("5 सितंबर 2000", "2000-09-05", "Hindi calendar name"),
    ("5 जन्वरी 2000", "2000-01-05", "halant-shifted January (live)"),
    ("पांच जनवरी दो हजार", "2000-01-05", "spoken Hindi January (live)"),
    ("5 सितम्बर 2000", "2000-09-05", "halant-shifted September"),
    ("5 फर्वरी 1990", "1990-02-05", "halant-shifted February"),
    ("5 सप्तेंबर 2000", "2000-09-05", "phonetic Devanagari (live)"),
    ("5 सप्टेंबर 2000", "2000-09-05", "phonetic Devanagari (live)"),
    ("5 सेप्टेंबर 2000", "2000-09-05", "phonetic Devanagari"),
    ("5 सतंबर 2000", "2000-09-05", "garbled Devanagari (live)"),
    ("5 सब्टेंबर 2000", "2000-09-05", "garbled Devanagari (live)"),
    ("5 सेटम्बर 2000", "2000-09-05", "garbled Devanagari (live)"),
    ("5 ستمبر 2000", "2000-09-05", "Urdu script (live)"),
    ("पाँच सितंबर दो हजार", "2000-09-05", "fully spoken Hindi (live)"),
    # --- other spoken forms -------------------------------------------------
    ("बाईस मार्च उन्नीस सौ सत्तासी", "1987-03-22", "spoken Hindi, 19xx year"),
    ("दो जनवरी दो हजार पाँच", "2005-01-02", "spoken Hindi, 200x year"),
    ("22 ऑक्टोबर 1987", "1987-10-22", "phonetic Devanagari October"),
    ("20 March 1987", "1987-03-20", "plain English"),
    ("1987-03-22", "1987-03-22", "ISO"),
    ("22/03/1987", "1987-03-22", "day/month/year"),
]

REJECTED = [
    ("5 ستمبر 2020", "under the 18-year floor"),
    ("5 रिटम्बर 2000", "garble equidistant between two months"),
    ("Past September 2000", "no day"),
    ("September 2000", "no day"),
    ("5 September or October 2000", "two months"),
    ("1950", "year only"),
    ("2000", "year only"),
    ("teen saal pehle", "relative, not a calendar date"),
    ("31 February 2000", "impossible calendar date"),
    ("30 September 3000", "future year"),
    ("5 sep 1500", "more than 120 years ago"),
    (f"1 January {THIS_YEAR + 1}", "future date"),
    ("नहीं, 5 सितंबर 2000 नहीं", "repudiated"),
    ("5 सितंबर 2000, सॉरी 6 सितंबर 2000", "corrected mid-answer"),
    ("22 March 1987 or 23rd", "alternative offered"),
]

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"  <- {detail}" if not ok and detail else ""))
    if not ok:
        failures.append(label)


print("[1] forms a caller has actually used must be accepted")
for said, want, why in ACCEPTED:
    got, err = extract.coerce(said, "date", DOB)
    check(f"{why}: {said!r} -> {want}", got == want,
          f"got {got!r} ({err})")

print("\n[2] guards that must keep rejecting")
for said, why in REJECTED:
    got, err = extract.coerce(said, "date", DOB)
    check(f"{why}: {said!r}", got is None, f"accepted as {got!r}")

print("\n[3] spoken-number conversion in isolation")
for said, want in (
    ("पाँच सितंबर दो हजार", "5 सितंबर 2000"),
    ("उन्नीस सौ सत्तासी", "1987"),
    ("दो हजार बीस", "2020"),
    ("इकतीस दिसंबर उन्नीस सौ निन्यानवे", "31 दिसंबर 1999"),
    ("मुझे तीन साल पहले", "मुझे 3 साल पहले"),
    ("no numbers here", "no numbers here"),
):
    got = extract._hindi_words_to_digits(said)
    check(f"{said!r} -> {want!r}", got == want, f"got {got!r}")

print()
if failures:
    print(f"FAILED: {len(failures)}")
    for failure in failures:
        print("  -", failure)
    raise SystemExit(1)
print(f"All {len(ACCEPTED)} spoken forms parse and all {len(REJECTED)} guards hold.")
