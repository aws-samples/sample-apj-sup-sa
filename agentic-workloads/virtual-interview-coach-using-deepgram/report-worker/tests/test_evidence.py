"""Evidence-validation unit tests (FR-305 / SC-002) — no model, no DB."""

from __future__ import annotations

from src.evidence import build_student_corpus, is_present, validate_quote

TURNS = [
    {"speaker": "coach", "transcript": "Walk me through a production bug."},
    {"speaker": "student", "transcript": "I traced it to a TIMEZONE bug in the batch window and fixed it."},
    {"speaker": "coach", "transcript": "And the outcome?"},
    {"speaker": "student", "transcript": "Reconciliation matched to the cent after the fix."},
]


def test_present_verbatim_quote():
    corpus = build_student_corpus(TURNS)
    assert is_present("traced it to a timezone bug in the batch window", corpus)


def test_case_and_whitespace_insensitive():
    corpus = build_student_corpus(TURNS)
    assert is_present('"Traced it to a   Timezone bug"', corpus)  # casing/spacing/quotes normalized


def test_fabricated_quote_rejected():
    corpus = build_student_corpus(TURNS)
    assert not is_present("I managed a team of twelve engineers", corpus)
    assert validate_quote("I managed a team of twelve engineers", corpus) is None


def test_too_short_quote_rejected():
    corpus = build_student_corpus(TURNS)
    assert not is_present("the fix", corpus)  # below minimum meaningful length


def test_coach_words_are_not_student_evidence():
    corpus = build_student_corpus(TURNS)
    # a phrase only the coach said must not validate as the student's evidence
    assert not is_present("walk me through a production bug", corpus)
