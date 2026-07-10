"""Unit tests for sentence-level chunking (supports T021).

Chunking is the decisive latency technique (R1): the first clause must be emitted for TTS
before the full reply finishes. These tests confirm chunks emit at boundaries, short leading
fragments merge forward, and the tail is flushed.
"""

from __future__ import annotations

from src.chunking import SentenceChunker


def _feed_words(chunker: SentenceChunker, text: str) -> list[str]:
    out: list[str] = []
    for word in text.split(" "):
        out.extend(chunker.add(word + " "))
    tail = chunker.flush()
    if tail:
        out.append(tail)
    return out


def test_emits_first_clause_before_end():
    c = SentenceChunker()
    chunks = []
    chunks += c.add("Tell me about ")
    chunks += c.add("yourself, ")  # boundary at comma, but chunk long enough
    assert chunks, "first clause should emit as soon as a long-enough boundary is crossed"
    assert chunks[0].endswith(",")


def test_full_sentence_split():
    c = SentenceChunker()
    out = _feed_words(c, "What is your greatest strength? Tell me why it matters.")
    assert len(out) >= 2
    assert out[0].endswith("?")
    assert out[-1].endswith(".")


def test_short_leading_fragment_merges_forward():
    c = SentenceChunker()
    # "Hi," is shorter than min chars, so it should merge with the following clause.
    out = _feed_words(c, "Hi, tell me about a challenge you faced.")
    assert out[0].startswith("Hi,")
    assert len(out[0]) >= 12


def test_flush_returns_trailing_text_without_boundary():
    c = SentenceChunker()
    chunks = c.add("no terminal punctuation here")
    assert chunks == []
    assert c.flush() == "no terminal punctuation here"


def test_no_empty_chunks():
    c = SentenceChunker()
    out = _feed_words(c, "Okay... Next question, please.")
    assert all(chunk.strip() for chunk in out)
