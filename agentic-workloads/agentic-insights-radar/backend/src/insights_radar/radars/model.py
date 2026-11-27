# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Radar domain: a generic quadrant/ring chart for any purpose.

A radar has 1-8 sectors (slices), 1-6 rings (innermost first) and an optional set of up to six
markers (legend categories drawn as glyphs, e.g. "New" / "Moved in" or "Threat" / "Partner").
There are no built-in meanings: templates only seed names. Matching uploaded values to the
schema is tolerant (case, whitespace, Unicode form, smart quotes), as in the internal radar.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any

from insights_radar.core.errors import ValidationError
from insights_radar.core.html import HtmlSanitizer
from insights_radar.core.ids import Slug
from insights_radar.core.names import NameNormalizer
from insights_radar.core.urls import UrlGuard
from insights_radar.graph.model import node_type_of

MAX_SECTORS = 8
MAX_RINGS = 6
MAX_MARKERS = 6
MAX_ENTRIES = 300
DIM_NAME_MAX = 40
ENTRY_TITLE_MAX = 120
ENTRY_DESCRIPTION_MAX = 4000
SUB_SECTOR_MAX = 120
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
GLYPHS = ("circle", "triangle", "square", "diamond", "star", "ring")


class RadarMode(StrEnum):
    MANUAL = "manual"
    ASSIST = "assist"
    COPILOT = "copilot"
    AUTOPILOT = "autopilot"


@dataclass(frozen=True, slots=True)
class Dimension:
    name: str
    color: str | None = None
    glyph: str | None = None

    def to_public(self) -> dict[str, Any]:
        body: dict[str, Any] = {"name": self.name}
        if self.color:
            body["color"] = self.color
        if self.glyph:
            body["glyph"] = self.glyph
        return body

    @classmethod
    def from_public(cls, raw: Any) -> Dimension:
        if isinstance(raw, str):
            return cls(raw)
        if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
            raise ValidationError("each schema value needs a name")
        color, glyph = raw.get("color"), raw.get("glyph")
        for name, value in (("color", color), ("glyph", glyph)):
            if value is not None and not isinstance(value, str):
                raise ValidationError(f"schema value {name} must be a string")
        return cls(raw["name"], color, glyph)


@dataclass(frozen=True, slots=True)
class RadarSchema:
    sectors: tuple[Dimension, ...]
    rings: tuple[Dimension, ...]
    markers: tuple[Dimension, ...] = ()
    sector_label: str = "Sector"
    ring_label: str = "Ring"
    marker_label: str = "Marker"

    def to_public(self) -> dict[str, Any]:
        return {
            "sectors": [d.to_public() for d in self.sectors],
            "rings": [d.to_public() for d in self.rings],
            "markers": [d.to_public() for d in self.markers],
            "sector_label": self.sector_label,
            "ring_label": self.ring_label,
            "marker_label": self.marker_label,
        }

    FIELDS = frozenset({"sectors", "rings", "markers", "sector_label", "ring_label", "marker_label"})
    VALUE_FIELDS = frozenset({"name", "color", "glyph"})

    @classmethod
    def from_public(cls, raw: Any, strict: bool = False) -> RadarSchema:
        """``strict`` (a request body): an unknown key is an error, not ignored. "marker" for "markers" used
        to save an empty marker list with a 200 (CTR e27239c5). A stored schema is read leniently."""
        if not isinstance(raw, dict):
            raise ValidationError("schema must be an object")
        if strict:
            unknown = sorted(set(raw) - cls.FIELDS)
            for key in ("sectors", "rings", "markers"):
                for value in raw.get(key) or []:
                    if isinstance(value, dict):
                        unknown += sorted(f"{key}[].{k}" for k in set(value) - cls.VALUE_FIELDS)
            if unknown:
                raise ValidationError(f"unknown schema field(s): {', '.join(unknown)}", {"unknown": unknown})

        def dims(key: str) -> tuple[Dimension, ...]:
            values = raw.get(key) or []
            if not isinstance(values, list):
                raise ValidationError(f"{key} must be a list")
            return tuple(Dimension.from_public(v) for v in values)

        def label(key: str, default: str) -> str:
            value = raw.get(key)
            if value is None or value == "":
                return default
            if not isinstance(value, str):
                raise ValidationError(f"{key} must be a string")
            return value

        return cls.validate(
            cls(
                dims("sectors"),
                dims("rings"),
                dims("markers"),
                label("sector_label", "Sector"),
                label("ring_label", "Ring"),
                label("marker_label", "Marker"),
            )
        )

    def same_identity(self, other: RadarSchema) -> bool:
        """Same value names in the same order in every dimension (colours, glyphs, labels aside)."""

        def names(s: RadarSchema) -> tuple[tuple[str, ...], ...]:
            return tuple(tuple(d.name for d in dims) for dims in (s.sectors, s.rings, s.markers))

        return names(self) == names(other)

    @staticmethod
    def _check(
        dims: tuple[Dimension, ...], label: str, low: int, high: int, errors: list[str]
    ) -> tuple[Dimension, ...]:
        if not low <= len(dims) <= high:
            errors.append(f"{label} must have between {low} and {high} values")
        seen: set[str] = set()
        out = []
        for d in dims:
            name = NameNormalizer.display(d.name)
            if not 1 <= len(name) <= DIM_NAME_MAX:
                errors.append(f"{label} names must be 1-{DIM_NAME_MAX} characters")
                continue
            key = NameNormalizer.title_key(name)
            if key in seen:
                errors.append(f"duplicate {label} name {name!r}")
            seen.add(key)
            if d.color is not None and not _COLOR_RE.fullmatch(d.color):
                errors.append(f"{label} colour {d.color!r} must look like #1a2b3c")
            if d.glyph is not None and d.glyph not in GLYPHS:
                errors.append(f"glyph must be one of {', '.join(GLYPHS)}")
            out.append(replace(d, name=name))
        return tuple(out)

    @classmethod
    def validate(cls, s: RadarSchema) -> RadarSchema:
        errors: list[str] = []
        sectors = cls._check(s.sectors, "sectors", 1, MAX_SECTORS, errors)
        rings = cls._check(s.rings, "rings", 1, MAX_RINGS, errors)
        markers = cls._check(s.markers, "markers", 0, MAX_MARKERS, errors)
        labels = [s.sector_label.strip(), s.ring_label.strip(), s.marker_label.strip()]
        if any(not 1 <= len(lbl) <= 30 for lbl in labels):
            errors.append("dimension labels must be 1-30 characters")
        if errors:
            raise ValidationError("radar schema is invalid", {"errors": errors})
        return cls(sectors, rings, markers, *labels)

    def match(self, dim: str, value: str | None) -> str | None:
        """Return the canonical schema name for a tolerant match, else None."""
        if value is None or not str(value).strip():
            return None
        pool = {"sector": self.sectors, "ring": self.rings, "marker": self.markers}[dim]
        key = NameNormalizer.title_key(str(value))
        return next((d.name for d in pool if NameNormalizer.title_key(d.name) == key), None)

    def suggest(self, dim: str, value: str, renamed: dict[str, dict[str, str]] | None = None) -> str | None:
        """The closest declared name; first, what a schema edit renamed ``value`` to (CTR HA-044), so an
        old file with "Assess" suggests the ring it became, however different the new name is."""
        target = (renamed or {}).get(dim, {}).get(NameNormalizer.title_key(value))
        if target is not None and self.match(dim, target) is not None:
            return self.match(dim, target)
        pool = {"sector": self.sectors, "ring": self.rings, "marker": self.markers}[dim]
        best, best_d = None, 3
        for d in pool:
            dist = _levenshtein(NameNormalizer.title_key(value), NameNormalizer.title_key(d.name))
            if dist < best_d:
                best, best_d = d.name, dist
        return best


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _dims(names: list[str]) -> tuple[Dimension, ...]:
    return tuple(Dimension(n) for n in names)


TEMPLATES: dict[str, tuple[str, RadarSchema]] = {
    "technology_adoption": (
        "Which technologies to adopt, trial, assess or hold",
        RadarSchema(
            _dims(["Techniques", "Tools", "Platforms", "Materials & components"]),
            _dims(["Adopt", "Trial", "Assess", "Hold"]),
            _dims(["New", "Moved in", "Moved out"]),
            "Quadrant",
            "Ring",
            "Change",
        ),
    ),
    "competitor_landscape": (
        "Who competes with us, how closely, and how they are moving",
        RadarSchema(
            _dims(["Direct", "Adjacent", "Emerging", "Substitutes"]),
            _dims(["Leader", "Challenger", "Niche", "Watch"]),
            _dims(["Gaining", "Steady", "Losing"]),
            "Category",
            "Position",
            "Momentum",
        ),
    ),
    "vendor_map": (
        "Suppliers and other third parties by category and relationship",
        RadarSchema(
            _dims(["Components", "Materials", "Logistics", "Software & services"]),
            _dims(["Strategic", "Preferred", "Approved", "Exit"]),
            _dims(["New", "At risk"]),
            "Category",
            "Status",
            "Flag",
        ),
    ),
    "customer_segments": (
        "Customer segments by priority",
        RadarSchema(
            _dims(["Consumers", "Small business", "Enterprise", "Public sector"]),
            _dims(["Core", "Growth", "Explore", "Deprioritise"]),
            (),
            "Market",
            "Priority",
            "Marker",
        ),
    ),
    "blank": ("A general-purpose radar", RadarSchema(_dims(["General"]), _dims(["Now", "Next", "Later"]))),
}


@dataclass(frozen=True, slots=True)
class RadarEntry:
    key: str
    title: str
    sector: str
    ring: str
    marker: str | None = None
    description: str = ""
    node_id: str | None = None
    link: str | None = None
    # Free-text grouping inside a sector (the internal Tech Radar's sub-segment); not part of the schema.
    sub_sector: str | None = None

    def to_public(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "key": self.key,
            "title": self.title,
            "sector": self.sector,
            "ring": self.ring,
            "description": self.description,
        }
        for k in ("marker", "node_id", "link", "sub_sector"):
            v = getattr(self, k)
            if v:
                body[k] = v
        return body

    def same_content(self, other: RadarEntry) -> bool:
        return (
            self.title,
            self.sector,
            self.ring,
            self.marker,
            self.description,
            self.node_id,
            self.link,
            self.sub_sector,
        ) == (
            other.title,
            other.sector,
            other.ring,
            other.marker,
            other.description,
            other.node_id,
            other.link,
            other.sub_sector,
        )


@dataclass(frozen=True, slots=True)
class UnmatchedValue:
    row: int
    field: str
    value: str
    suggestion: str | None

    def to_public(self) -> dict[str, Any]:
        return {"row": self.row, "field": self.field, "value": self.value, "suggestion": self.suggestion}


# What the API's {key} path segment and the save route accept, so every entry stays addressable.
ENTRY_KEY_RE = re.compile(r"^[A-Za-z0-9._~:@-]{1,64}$")


class EntryValidator:
    @staticmethod
    def safe_key(key: str, row: int, used: set[str]) -> str:
        """``key`` if the API can address it (a repeated valid key is left for the duplicate-key
        check); otherwise a readable replacement (an imported ``Tech 12`` becomes ``Tech-12``) that
        collides with no key already used."""
        if ENTRY_KEY_RE.match(key):
            return key
        base = re.sub(r"[^A-Za-z0-9._~:@-]+", "-", key).strip("-")[:56] or f"entry-{row}"
        candidate, n = base, 2
        while candidate in used:
            candidate, n = f"{base}-{n}", n + 1
        return candidate

    @staticmethod
    def check(
        schema: RadarSchema, entries: list[RadarEntry], renamed: dict[str, dict[str, str]] | None = None
    ) -> tuple[list[RadarEntry], list[UnmatchedValue], list[int]]:
        """Return (canonicalised entries, unmatched values, duplicate-title row numbers)."""
        if len(entries) > MAX_ENTRIES:
            raise ValidationError(f"a radar can hold at most {MAX_ENTRIES} entries")
        clean: list[RadarEntry] = []
        unmatched: list[UnmatchedValue] = []
        duplicates: list[int] = []
        seen: dict[str, int] = {}
        # Every valid key in the batch is reserved first, so a replacement made for an earlier row (Tech 12
        # -> Tech-12) cannot take a key a later row already has.
        keys: set[str] = {e.key for e in entries if ENTRY_KEY_RE.match(e.key)}
        for row, e in enumerate(entries, 1):
            entry_key = EntryValidator.safe_key(e.key, row, keys)
            keys.add(entry_key)
            title = NameNormalizer.display(e.title)
            if not 1 <= len(title) <= ENTRY_TITLE_MAX:
                raise ValidationError(f"row {row}: title must be 1-{ENTRY_TITLE_MAX} characters")
            key = NameNormalizer.title_key(title)
            if key in seen:
                duplicates.append(row)
            seen[key] = row
            sector, ring = schema.match("sector", e.sector), schema.match("ring", e.ring)
            marker = schema.match("marker", e.marker) if e.marker else None
            if sector is None:
                unmatched.append(UnmatchedValue(row, "sector", e.sector, schema.suggest("sector", e.sector, renamed)))
            if ring is None:
                unmatched.append(UnmatchedValue(row, "ring", e.ring, schema.suggest("ring", e.ring, renamed)))
            if e.marker and marker is None:
                unmatched.append(UnmatchedValue(row, "marker", e.marker, schema.suggest("marker", e.marker, renamed)))
            description = HtmlSanitizer.sanitize(e.description)
            if len(description) > ENTRY_DESCRIPTION_MAX:
                raise ValidationError(f"row {row}: description must be at most {ENTRY_DESCRIPTION_MAX} characters")
            if e.link and not UrlGuard.is_safe(e.link):
                raise ValidationError(f"row {row}: link must be a public https URL")
            if e.node_id:
                try:
                    node_type_of(e.node_id)
                except ValidationError as err:
                    raise ValidationError(f"row {row}: node_id must be a graph node id") from err
            # Every write path (save, import, suggestions) passes here, so the limit holds everywhere.
            sub_sector = " ".join(unicodedata.normalize("NFC", e.sub_sector or "").split()) or None
            if sub_sector and len(sub_sector) > SUB_SECTOR_MAX:
                raise ValidationError(f"row {row}: sub-sector must be at most {SUB_SECTOR_MAX} characters")
            clean.append(
                replace(
                    e,
                    key=entry_key,
                    title=title,
                    sector=sector or e.sector,
                    ring=ring or e.ring,
                    marker=marker,
                    description=description,
                    sub_sector=sub_sector,
                )
            )
        return clean, unmatched, duplicates


@dataclass(frozen=True, slots=True)
class Radar:
    slug: str
    name: str
    schema: RadarSchema
    purpose: str = ""
    description: str = ""
    mode: RadarMode = RadarMode.MANUAL
    auto_apply_days: int = 7
    owner: str = ""
    owner_email: str = ""
    editors: tuple[str, ...] = ()
    current_version: int = 0
    entry_count: int = 0
    created_at: str = ""
    updated_at: str = ""
    updated_by: str = ""
    version_lock: int = 1
    # Autopilot applies agent (curator) proposals unattended only when an administrator opted in;
    # otherwise every agent proposal waits for a human, whatever the mode.
    auto_apply_agent: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    def to_public(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "name": self.name,
            "schema": self.schema.to_public(),
            "purpose": self.purpose,
            "description": self.description,
            "mode": self.mode.value,
            "auto_apply_days": self.auto_apply_days,
            "owner": self.owner,
            "owner_email": self.owner_email,
            "editors": list(self.editors),
            "current_version": self.current_version,
            "entry_count": self.entry_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
            "version_lock": self.version_lock,
            "auto_apply_agent": self.auto_apply_agent,
        }

    @staticmethod
    def validate(r: Radar) -> Radar:
        # Metadata edits arrive as raw JSON values: reject wrong types before measuring them.
        wrong = [f for f in ("name", "purpose", "description") if not isinstance(getattr(r, f), str)]
        if isinstance(r.auto_apply_days, bool) or not isinstance(r.auto_apply_days, int):
            wrong.append("auto_apply_days")
        if not isinstance(r.auto_apply_agent, bool):
            wrong.append("auto_apply_agent")
        if wrong:
            raise ValidationError("radar is invalid", {"errors": [f"{f} has the wrong type" for f in wrong]})
        errors: list[str] = []
        if not Slug.is_valid(r.slug):
            errors.append("slug must be 1-64 lower-case letters, digits or hyphens")
        elif Slug.is_reserved(r.slug, prefixes=False):  # a radar has no budget ledger to collide with
            errors.append(f"slug {r.slug!r} is reserved")
        name = r.name.strip()
        if not 1 <= len(name) <= 120:
            errors.append("name must be 1-120 characters")
        if len(r.purpose) > 300:
            errors.append("purpose must be at most 300 characters")
        if len(r.description) > 2000:
            errors.append("description must be at most 2000 characters")
        if not 1 <= r.auto_apply_days <= 90:
            errors.append("auto_apply_days must be between 1 and 90")
        if len(r.editors) > 50:
            errors.append("at most 50 editors")
        if errors:
            raise ValidationError("radar is invalid", {"errors": errors})
        return replace(
            r,
            name=name,
            purpose=r.purpose.strip(),
            description=r.description.strip(),
            editors=tuple(dict.fromkeys(e.strip().lower() for e in r.editors if e.strip())),
        )
