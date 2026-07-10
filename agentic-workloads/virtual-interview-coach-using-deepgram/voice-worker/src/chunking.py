"""Sentence-level chunking (supports T021).

The decisive latency technique (R1): as soon as the reply generator streams the first clause,
hand it to TTS so first-audio overlaps the LLM still generating. This module accumulates a
token stream and emits speakable chunks at clause/sentence boundaries.

Pure, synchronous, and fully unit-testable — no I/O.
"""

from __future__ import annotations

# Characters that end a speakable clause/sentence.
_BOUNDARY = {".", "!", "?", ";", ":", ","}
# Don't emit a chunk shorter than this (avoids choppy one-word TTS calls).
_MIN_CHUNK_CHARS = 12


class SentenceChunker:
    """Feed tokens in; pull speakable chunks out as boundaries are crossed."""

    def __init__(self, min_chars: int = _MIN_CHUNK_CHARS) -> None:
        self._buf = ""
        self._min_chars = min_chars

    def add(self, token: str) -> list[str]:
        """Add a token/partial string; return zero or more ready-to-speak chunks.

        Emits at the first clause/sentence boundary that leaves a trimmed chunk of at least
        `min_chars` characters, so very early boundaries (e.g. "Hi,") are merged forward.
        """
        self._buf += token
        chunks: list[str] = []
        while True:
            cut = self._emit_index(self._buf)
            if cut is None:
                break
            chunk = self._buf[:cut].strip()
            self._buf = self._buf[cut:]
            if chunk:
                chunks.append(chunk)
        return chunks

    def flush(self) -> str | None:
        """Return any remaining buffered text at end-of-stream (the trailing clause)."""
        remainder = self._buf.strip()
        self._buf = ""
        return remainder or None

    def _emit_index(self, s: str) -> int | None:
        """Index (exclusive) at which to cut a speakable chunk, or None if not ready.

        Returns the position just after the first boundary character that occurs at or beyond
        `min_chars` worth of non-whitespace content.
        """
        for i, ch in enumerate(s):
            if ch in _BOUNDARY:
                # length of the trimmed candidate up to and including this boundary
                if len(s[: i + 1].strip()) >= self._min_chars:
                    return i + 1
        return None
