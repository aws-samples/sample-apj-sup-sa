# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Defang untrusted text before it reaches a model (ported from the internal radar, AGT-2).

Any text the platform did not author (crawled pages, search snippets, uploaded documents,
graph text written by an agent, an admin's source instruction) may carry instructions aimed
at the model. ``Defang.neutralize`` folds homoglyphs, removes zero-width/bidi characters,
substitutes an inert placeholder for the reserved context marker (substitution, not
deletion, so nested markers cannot re-form: the internal radar's F-22) and caps length.
``Defang.fence`` wraps a tool result in a tag the system prompt tells the model to treat as
data. Escapes are written as ``\\u`` sequences so the source stays pure ASCII.
"""

from __future__ import annotations

import re
import unicodedata

_ZERO_WIDTH = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")
_WS = re.compile(r"\s+")
_BLANKS = re.compile(r"[^\S\n]+")  # whitespace except newlines
CONTEXT_MARKER_RE = re.compile(r"\[\[?\s*context\s*:?", re.IGNORECASE)
FENCE_TAG = "untrusted-tool-output"
# Any dash-like separator: NFKC keeps U+2010-2015, U+2212 and friends, which read as "-" to a model.
_DASH = "[-_\u2010-\u2015\u2212\ufe58\ufe63\uff0d\\s]*"
_FENCE_RE = re.compile(rf"<\s*/?\s*untrusted{_DASH}tool{_DASH}output[^>]*>?", re.IGNORECASE)
MARKER_REDACTED = "\u27ea redacted-marker \u27eb"
# Compatibility characters that NFKC turns into a JSON or CSV delimiter (", \, comma): kept as they are,
# so folding text that is already serialised cannot open a string or a column (tests check every code point).
_DELIMITER_LOOKALIKES = re.compile("([\ufe10\ufe50\ufe68\uff02\uff0c\uff3c\U0001f101-\U0001f10a])")


def _nfkc(text: str) -> str:
    parts = _DELIMITER_LOOKALIKES.split(text)  # the kept characters sit at the odd indexes
    return "".join(p if i % 2 else unicodedata.normalize("NFKC", p) for i, p in enumerate(parts))


class Defang:
    @staticmethod
    def fold(text: str) -> str:
        """NFKC, then drop every invisible format character (Unicode category Cf: zero-width, bidi
        controls and isolates, soft hyphen, tag characters), so none can split a marker apart."""
        folded = _ZERO_WIDTH.sub("", _nfkc(str(text)))
        return "".join(c for c in folded if unicodedata.category(c) != "Cf")

    @classmethod
    def redact_markers(cls, text: str) -> str:
        folded = cls.fold(text)
        folded = _FENCE_RE.sub(MARKER_REDACTED, folded)
        return CONTEXT_MARKER_RE.sub(MARKER_REDACTED, folded)

    @classmethod
    def neutralize(cls, text: object, max_len: int = 600) -> str:
        s = str(text).strip()
        if not s:
            return ""
        s = _WS.sub(" ", cls.fold(s))
        s = cls.redact_markers(s)
        if len(s) > max_len:
            s = s[: max_len - 1] + "\u2026"
        return s

    @classmethod
    def neutralize_lines(cls, text: object, max_len: int = 600) -> str:
        """``neutralize`` that keeps line breaks: the same folding and marker substitution, with runs of
        blanks collapsed per line rather than across the whole text, so a structured brief (lists,
        headings) still reads as one when it reaches the model."""
        s = str(text).strip()
        if not s:
            return ""
        lines = [_BLANKS.sub(" ", cls.fold(line)).strip() for line in s.splitlines()]
        out = cls.redact_markers("\n".join(lines))
        if len(out) > max_len:
            out = out[: max_len - 1] + "\u2026"
        return out

    @classmethod
    def fence(cls, tool: str, payload: str, max_len: int = 100_000) -> str:
        """Wrap untrusted tool output. Markers inside are neutralised so it cannot close the fence."""
        body = cls.redact_markers(payload)
        if len(body) > max_len:
            body = body[:max_len] + "\n[truncated]"
        safe_tool = re.sub(r"[^A-Za-z0-9_.\-]", "_", tool)[:64]
        return f'<{FENCE_TAG} tool="{safe_tool}">\n{body}\n</{FENCE_TAG}>'
