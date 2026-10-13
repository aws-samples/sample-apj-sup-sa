"""Evidence-quote validation (FR-305 / SC-002).

Every competency score and per-question score the model produces may carry a candidate evidence quote.
A quote is trustworthy ONLY if the student actually said it. This module validates each candidate quote
against the session's student transcript by a normalized substring match, and the worker DROPS any
quote that fails (marking that competency not-assessed) — it never fabricates or paraphrases evidence.

This makes SC-002 mechanical: a hallucinated quote cannot survive into the report.
"""

from __future__ import annotations

import re

# A short quote (a few words) can be a coincidental substring; require a minimum length so validation
# is meaningful. Below this, treat as not-anchored.
_MIN_QUOTE_CHARS = 12


def _normalize(text: str) -> str:
    """Lowercase, collapse whitespace, strip surrounding quotes/punctuation noise so that a quote which
    differs from the transcript only in casing/spacing/quote-marks still validates as the same span.
    Does NOT alter word content — a quote with different WORDS will (correctly) fail to match."""
    t = text.lower().strip()
    t = t.strip("\"'“”‘’ \t\n")
    t = re.sub(r"\s+", " ", t)
    return t


def build_student_corpus(turns: list[dict]) -> str:
    """Concatenate all student turn transcripts into one normalized corpus for substring checks.

    `turns` is the ordered conversation_turn list; only speaker=='student' turns are the student's own
    words (coach questions are not the student's evidence)."""
    student = [t.get("transcript", "") for t in turns if t.get("speaker") == "student"]
    return _normalize(" ".join(student))


def is_present(quote: str | None, corpus: str) -> bool:
    """True iff `quote` is a real (normalized) substring of the student's words and long enough to be
    meaningful. Empty/short/None quotes are not present (FR-305)."""
    if not quote:
        return False
    nq = _normalize(quote)
    if len(nq) < _MIN_QUOTE_CHARS:
        return False
    return nq in corpus


def validate_quote(quote: str | None, corpus: str) -> str | None:
    """Return the quote if it is present in the student corpus, else None (drop it). The caller marks
    the competency/question not-assessed when this returns None, rather than inventing a quote."""
    return quote if is_present(quote, corpus) else None
