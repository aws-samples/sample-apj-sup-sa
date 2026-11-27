# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Name normalisation for entity de-duplication (ported from the internal radar sec. 6.4).

``Acme Motors``, ``acme-motors`` and ``ACME_MOTORS`` collapse to one key; smart quotes fold
to ASCII; Unicode is NFC-normalised. Keys additionally fold compatibility forms (NFKC, so a
full-width ``\uff2fctank`` is the same name) and drop invisible format characters, because a
lookalike that displays identically must not become a second entity. Fuzzy matching is
deliberately out of scope: the agent is told to search aliases before creating, the key only
catches exact re-creations.
"""

from __future__ import annotations

import re
import unicodedata

from insights_radar.core.errors import ValidationError

_SMART = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2032": "'"})
_STRIP = re.compile(r"[\s\-_]+")
_WS = re.compile(r"\s+")


def _fold(title: str) -> str:
    """NFKC, invisible characters dropped, smart quotes made plain, runs of space made one: the shared part of
    ``title_key`` and ``lookalike_key``, which differ only in how they fold case (scan cycle 32)."""
    folded = _visible(unicodedata.normalize("NFKC", title)).translate(_SMART)
    return _WS.sub(" ", folded).strip()


def _visible(text: str) -> str:
    """``text`` without the invisible format characters (Unicode category Cf: zero-width spaces and
    joiners, bidi controls, soft hyphen, tag characters). A prompt-injected agent used to create
    ``Oct\u200bank`` next to the human-created ``Octank``; both display identically."""
    return "".join(c for c in text if unicodedata.category(c) != "Cf")


class NameNormalizer:
    @staticmethod
    def key(name: str) -> str:
        # NFKC, not NFC: compatibility forms (full-width letters, ligatures, enclosed characters)
        # read as the ASCII name to a person, so they must share its key. Normalised again after the
        # separators come out, because NFKC can decompose a character into one (U+00B4 becomes space
        # plus a combining accent), which would otherwise leave the key not idempotent.
        folded = _visible(unicodedata.normalize("NFKC", name)).translate(_SMART)
        key = unicodedata.normalize("NFKC", _STRIP.sub("", folded).casefold())
        if not key:
            raise ValidationError("name has no characters left after normalisation")
        return key

    @staticmethod
    def has_key(name: str) -> bool:
        """Whether ``name`` normalises to a non-empty key."""
        return NameNormalizer.key_or_none(name) is not None

    @staticmethod
    def key_or_none(name: str) -> str | None:
        """``key(name)``, or ``None`` for a name with no characters left after normalisation."""
        try:
            return NameNormalizer.key(name)
        except ValidationError:
            return None

    @staticmethod
    def display(name: str) -> str:
        """Trimmed, single-spaced, NFC display form, without invisible format characters."""
        return _WS.sub(" ", _visible(unicodedata.normalize("NFC", name))).strip()

    @staticmethod
    def legacy_title_key(title: str) -> str:
        """The title key before compatibility folding (NFC, invisible characters kept). Only to tell
        whether two stored titles were distinct when they were saved (CR2-11)."""
        return _WS.sub(" ", unicodedata.normalize("NFC", title).translate(_SMART)).strip().casefold()

    @staticmethod
    def lookalike_key(title: str) -> str:
        """Whether two names read the same: like ``title_key`` but with ``lower()``, so names only full case
        folding makes equal (``Stra\u00dfe`` and ``Strasse``, final and medial sigma) stay distinct (scan cycle 31)."""
        return _fold(title).lower()

    @staticmethod
    def title_key(title: str) -> str:
        """Case-insensitive uniqueness key for titles (keeps inner separators)."""
        return _fold(title).casefold()
