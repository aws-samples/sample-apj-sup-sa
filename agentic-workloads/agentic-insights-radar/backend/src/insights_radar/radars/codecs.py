# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""CSV and JSON codecs for radar entries. ``parse(serialize(x)) == x`` for both formats.

Header aliases accept files exported from other radar tools: ``name``->``title``,
``quadrant``/``segment``/``category``->``sector``, ``status``/``phase``->``marker``,
``isNew``-style columns are ignored. CSV formula injection is neutralised on export
(cells starting with ``= + - @`` get a leading apostrophe), so an exported file opened in a
spreadsheet cannot execute.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from insights_radar.core.errors import PayloadTooLargeError, ValidationError
from insights_radar.radars.model import RadarEntry

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
COLUMNS = ("key", "title", "sector", "ring", "marker", "description", "node_id", "link", "sub_sector")
ALIASES = {
    "name": "title",
    "quadrant": "sector",
    "segment": "sector",
    "category": "sector",
    "status": "marker",
    "phase": "marker",
    "flag": "marker",
    "moved": "marker",
    "node": "node_id",
    "url": "link",
    "sub_segment": "sub_sector",
    "subsegment": "sub_sector",
    "subsector": "sub_sector",
    "subcategory": "sub_sector",
    "id": "key",
}
_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _canon_header(h: str) -> str:
    key = h.strip().lower().replace(" ", "_").replace("-", "_")
    return ALIASES.get(key, key)


def _needs_guard(cell: str) -> bool:
    """A formula start, or quotes in front of one: a literal "'=" must survive the round trip too."""
    stripped = cell.lstrip("'")
    return bool(stripped) and stripped[0] in _FORMULA


def _guard(cell: str) -> str:
    return "'" + cell if _needs_guard(cell) else cell


def _unguard(cell: str) -> str:
    return cell[1:] if cell.startswith("'") and _needs_guard(cell[1:]) else cell


class EntryCodec:
    @staticmethod
    def _check_size(text: str) -> None:
        if len(text.encode("utf-8")) > MAX_UPLOAD_BYTES:
            raise PayloadTooLargeError("files must be at most 5 MiB")

    @staticmethod
    def _cell(value: Any, row: int) -> str:
        """One imported cell. Objects and lists used to be coerced with ``str()``, so a JSON import
        stored Python reprs ("{'x': 1}", "['General']") as the entry's title and sector."""
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, str | int | float):
            return str(value)
        raise ValidationError(f"row {row}: cell values must be text, numbers or null")

    @classmethod
    def from_rows(cls, rows: list[dict[str, Any]], new_key: Any) -> list[RadarEntry]:
        out: list[RadarEntry] = []
        for n, raw in enumerate(rows, 1):
            if not isinstance(raw, dict):
                raise ValidationError(f"row {n} must be an object")
            row = {_canon_header(str(k)): cls._cell(v, n) for k, v in raw.items()}
            if not row.get("title", "").strip():
                raise ValidationError(f"row {n} has no title")
            out.append(
                RadarEntry(
                    key=row.get("key", "").strip() or str(new_key()),
                    title=row["title"],
                    sector=row.get("sector", ""),
                    ring=row.get("ring", ""),
                    marker=row.get("marker", "").strip() or None,
                    description=row.get("description", ""),
                    node_id=row.get("node_id", "").strip() or None,
                    link=row.get("link", "").strip() or None,
                    sub_sector=row.get("sub_sector", "").strip() or None,
                )
            )
        return out

    @classmethod
    def parse_csv(cls, text: str, new_key: Any) -> list[RadarEntry]:
        cls._check_size(text)
        reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
        if not reader.fieldnames or "title" not in {_canon_header(h) for h in reader.fieldnames}:
            raise ValidationError("CSV needs a header row with at least a title (or name) column")
        try:
            rows = [{k: _unguard(v or "") for k, v in r.items() if k is not None} for r in reader]
        except csv.Error as err:  # a field past the csv module's limit, or malformed quoting
            raise ValidationError(f"invalid CSV at line {reader.line_num}: {err}") from err
        return cls.from_rows(rows, new_key)

    @classmethod
    def parse_json(cls, text: str, new_key: Any) -> list[RadarEntry]:
        cls._check_size(text)
        try:
            # A BOM, as parse_csv allows: a file saved by Excel or Notepad sent straight to the API (CTR QA-106).
            data = json.loads(text.lstrip("\ufeff"))
        except json.JSONDecodeError as err:
            raise ValidationError(f"invalid JSON: {err.msg} at line {err.lineno}") from err
        except (ValueError, RecursionError) as err:  # too deeply nested, or an over-long integer
            raise ValidationError("invalid JSON: too deeply nested or a number is too long") from err
        if isinstance(data, dict):
            data = data.get("entries")
        if not isinstance(data, list):
            raise ValidationError("JSON must be a list of entries or an object with an entries list")
        return cls.from_rows(data, new_key)

    @staticmethod
    def to_csv(entries: list[RadarEntry]) -> str:
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=list(COLUMNS), lineterminator="\n")
        w.writeheader()
        for e in entries:
            pub = e.to_public()
            w.writerow({c: _guard(str(pub.get(c, ""))) for c in COLUMNS})
        return buf.getvalue()

    @staticmethod
    def to_json(entries: list[RadarEntry]) -> str:
        return json.dumps({"entries": [e.to_public() for e in entries]}, indent=2, ensure_ascii=False)


class RadarDiff:
    """What changed between two versions (keyed by entry key, falling back to title)."""

    @staticmethod
    def compare(before: list[RadarEntry], after: list[RadarEntry]) -> dict[str, list[dict[str, Any]]]:
        old = {e.key: e for e in before}
        new = {e.key: e for e in after}
        out: dict[str, list[dict[str, Any]]] = {
            "added": [],
            "removed": [],
            "moved": [],
            "recategorised": [],
            "marker_changed": [],
            "edited": [],
        }
        for key, e in new.items():
            prev = old.get(key)
            if prev is None:
                out["added"].append({"key": key, "title": e.title, "ring": e.ring, "sector": e.sector})
                continue
            if prev.ring != e.ring:
                out["moved"].append({"key": key, "title": e.title, "from": prev.ring, "to": e.ring})
            if prev.sector != e.sector:
                out["recategorised"].append({"key": key, "title": e.title, "from": prev.sector, "to": e.sector})
            if prev.marker != e.marker:
                out["marker_changed"].append({"key": key, "title": e.title, "from": prev.marker, "to": e.marker})
            if (prev.title, prev.description, prev.node_id, prev.link, prev.sub_sector) != (
                e.title,
                e.description,
                e.node_id,
                e.link,
                e.sub_sector,
            ):
                out["edited"].append({"key": key, "title": e.title, "previous_title": prev.title})
        for key, e in old.items():
            if key not in new:
                out["removed"].append({"key": key, "title": e.title})
        return out
