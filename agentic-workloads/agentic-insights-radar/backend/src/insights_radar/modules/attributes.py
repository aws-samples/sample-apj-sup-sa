# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Typed attributes for a module's outputs: an attribute schema.

A module may declare the fields every output it writes must (or may) carry, for example an urgency
from a fixed list, the countries a change applies to, or the date it takes effect. The agent's write
tool checks each output against the schema and answers with errors it can fix, the list API filters and
sorts by the fields marked for it, and the UI renders them by label. A module without a schema behaves
as before: free-form attributes, nothing indexed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any

from insights_radar.core.errors import ValidationError
from insights_radar.core.urls import UrlGuard

MAX_FIELDS = 20
MAX_VALUES = 300
KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")
LABEL_MAX = 40
HELP_MAX = 300
VALUE_MAX = 60
TEXT_MAX = 4000
LIST_MAX = 50
# Keys a schema may not take: they are node fields, or index fields filters are built on.
RESERVED_KEYS = frozenset(
    {
        "title",
        "summary",
        "description",
        "type",
        "id",
        "module",
        "confidence",
        "insight_kind",
        "private",
        "created_at",
        "updated_at",
        "published_at",
        "version",
        "sort",
        "order",
        "cursor",
        "limit",
        "query",
    }
)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_VALUE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,59}$")


class FieldType(StrEnum):
    ENUM = "enum"
    STRING_LIST = "string_list"
    DATE = "date"
    URL = "url"
    TEXT = "text"
    NUMBER = "number"


FILTERABLE = frozenset({FieldType.ENUM, FieldType.STRING_LIST})
SORTABLE = frozenset({FieldType.ENUM, FieldType.DATE, FieldType.NUMBER})


@dataclass(frozen=True, slots=True)
class Choice:
    value: str
    label: str
    rank: int = 0

    def to_public(self) -> dict[str, Any]:
        return {"value": self.value, "label": self.label, "rank": self.rank}


@dataclass(frozen=True, slots=True)
class AttributeField:
    key: str
    label: str
    type: FieldType
    required: bool = False
    filter: bool = False
    sort: bool = False
    help: str = ""
    values: tuple[Choice, ...] = ()
    max_items: int = LIST_MAX
    max_len: int = TEXT_MAX

    def to_public(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "key": self.key,
            "label": self.label,
            "type": self.type.value,
            "required": self.required,
            "filter": self.filter,
            "sort": self.sort,
            "help": self.help,
        }
        if self.values:
            out["values"] = [c.to_public() for c in self.values]
        if self.type is FieldType.STRING_LIST:
            out["max_items"] = self.max_items
        if self.type is FieldType.TEXT:
            out["max_len"] = self.max_len
        return out

    def allowed(self) -> frozenset[str]:
        return frozenset(c.value for c in self.values)

    def rank_of(self, value: str) -> int:
        """The value's sort position: its rank, or, when no choice declares one, the order it is listed in
        (with every rank 0, a sort on the field would not order anything)."""
        ranked = any(c.rank for c in self.values)
        for i, c in enumerate(self.values):
            if c.value == value:
                return c.rank if ranked else i + 1
        return 0


@dataclass(frozen=True, slots=True)
class AttributeSchema:
    fields: tuple[AttributeField, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.fields)

    def to_public(self) -> list[dict[str, Any]]:
        return [f.to_public() for f in self.fields]

    def field(self, key: str) -> AttributeField | None:
        return next((f for f in self.fields if f.key == key), None)

    def filterable(self) -> tuple[AttributeField, ...]:
        return tuple(f for f in self.fields if f.filter)

    def sortable(self) -> tuple[AttributeField, ...]:
        return tuple(f for f in self.fields if f.sort)

    def check(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Validate an output's attributes; returns them with schema values normalized (trimmed text,
        de-duplicated lists, upper-cased nothing: values are kept as declared). Keys the schema does
        not name pass through untouched. Every problem is reported at once, so one retry can fix all."""
        errors: list[str] = []
        out = dict(attrs)
        for f in self.fields:
            if f.key not in attrs or attrs[f.key] in (None, "", []):
                out.pop(f.key, None)
                if f.required:
                    errors.append(f"attributes.{f.key} ({f.label}) is required")
                continue
            value, problem = _coerce(f, attrs[f.key])
            if problem:
                errors.append(f"attributes.{f.key} ({f.label}) {problem}")
            else:
                out[f.key] = value
        if errors:
            raise ValidationError("the output's attributes do not match this module's fields", {"errors": errors})
        return out

    def prompt_section(self) -> str:
        """The "fields to fill" section added to a module's prompt, generated from the schema."""
        if not self.fields:
            return ""
        lines = ["Every output you save must carry these attributes (in `attributes`, by key):"]
        for f in self.fields:
            need = "required" if f.required else "optional"
            line = f"- `{f.key}` ({f.label}, {need}, {_type_words(f)})"
            if f.help:
                line += f": {f.help}"
            lines.append(line)
            if f.values and len(f.values) <= 40:
                lines.append("  allowed values: " + ", ".join(f"`{c.value}` ({c.label})" for c in f.values))
            elif f.values:
                lines.append(
                    f"  allowed values: {len(f.values)} codes, for example "
                    + ", ".join(f"`{c.value}`" for c in f.values[:12])
                )
        lines.append("Leave an optional attribute out rather than guess; never invent a value that is not allowed.")
        return "\n".join(lines)


def _type_words(f: AttributeField) -> str:
    return {
        FieldType.ENUM: "one of the allowed values",
        FieldType.STRING_LIST: f"a list of at most {f.max_items} "
        + ("allowed values" if f.values else "short strings"),
        FieldType.DATE: "a date as YYYY-MM-DD",
        FieldType.URL: "an https URL",
        FieldType.TEXT: f"text of at most {f.max_len} characters",
        FieldType.NUMBER: "a number",
    }[f.type]


def _coerce(f: AttributeField, raw: Any) -> tuple[Any, str | None]:
    t = f.type
    if t is FieldType.ENUM:
        if not isinstance(raw, str) or raw.strip() not in f.allowed():
            return None, f"must be one of: {', '.join(sorted(f.allowed()))[:600]}"
        return raw.strip(), None
    if t is FieldType.STRING_LIST:
        if not isinstance(raw, list) or not all(isinstance(v, str) for v in raw):
            return None, "must be a list of strings"
        items = list(dict.fromkeys(v.strip() for v in raw if v.strip()))
        if len(items) > f.max_items:
            return None, f"may hold at most {f.max_items} items"
        if f.values:
            bad = [v for v in items if v not in f.allowed()]
            if bad:
                return None, f"has values that are not allowed: {', '.join(bad[:10])}"
        elif any(len(v) > VALUE_MAX for v in items):
            return None, f"items must be at most {VALUE_MAX} characters"
        return items, None
    if t is FieldType.DATE:
        if not isinstance(raw, str) or not _DATE_RE.fullmatch(raw.strip()):
            return None, "must be a date as YYYY-MM-DD"
        try:
            date.fromisoformat(raw.strip())
        except ValueError:
            return None, "is not a real date"
        return raw.strip(), None
    if t is FieldType.URL:
        if not isinstance(raw, str) or not raw.strip().startswith("https://"):
            return None, "must be an https URL"
        reason = UrlGuard.reason(raw.strip())
        if reason:
            return None, f"is not a usable URL: {reason}"
        return raw.strip(), None
    if t is FieldType.TEXT:
        if not isinstance(raw, str):
            return None, "must be text"
        if len(raw.strip()) > f.max_len:
            return None, f"must be at most {f.max_len} characters"
        return raw.strip(), None
    # NUMBER
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None, "must be a number"
    return raw, None


def _text(raw: Any, name: str, lo: int, hi: int, errors: list[str]) -> str:
    if not isinstance(raw, str) or not lo <= len(raw.strip()) <= hi:
        errors.append(f"{name} must be text of {lo}-{hi} characters")
        return ""
    return raw.strip()


def _choices(raw: Any, where: str, errors: list[str]) -> tuple[Choice, ...]:
    if not isinstance(raw, list) or len(raw) > MAX_VALUES:
        errors.append(f"{where}.values must be a list of at most {MAX_VALUES} choices")
        return ()
    out: list[Choice] = []
    seen: set[str] = set()
    for i, c in enumerate(raw):
        if not isinstance(c, dict) or set(c) - {"value", "label", "rank"}:
            errors.append(f"{where}.values[{i}] must be an object with value, label and an optional rank")
            continue
        value = c.get("value")
        if not isinstance(value, str) or not _VALUE_RE.fullmatch(value):
            errors.append(f"{where}.values[{i}].value must be a short code (letters, digits, _ . : -)")
            continue
        if value in seen:
            errors.append(f"{where}.values has {value!r} twice")
            continue
        seen.add(value)
        label = _text(c.get("label", value), f"{where}.values[{i}].label", 1, LABEL_MAX, errors)
        rank = c.get("rank", 0)
        if isinstance(rank, bool) or not isinstance(rank, int) or not 0 <= rank <= 99:
            errors.append(f"{where}.values[{i}].rank must be a whole number 0-99")
            rank = 0
        out.append(Choice(value, label, rank))
    return tuple(out)


def parse_schema(raw: Any) -> AttributeSchema:
    """Validate a schema document (a list of field objects)."""
    if raw is None:
        return AttributeSchema()
    if not isinstance(raw, list):
        raise ValidationError("attribute_schema must be a list of fields")
    errors: list[str] = []
    if len(raw) > MAX_FIELDS:
        errors.append(f"attribute_schema may hold at most {MAX_FIELDS} fields")
    fields: list[AttributeField] = []
    keys: set[str] = set()
    allowed = {"key", "label", "type", "required", "filter", "sort", "help", "values", "max_items", "max_len"}
    for i, f in enumerate(raw[:MAX_FIELDS]):
        where = f"attribute_schema[{i}]"
        if not isinstance(f, dict):
            errors.append(f"{where} must be an object")
            continue
        unknown = set(f) - allowed
        if unknown:
            errors.append(f"{where} has unknown fields: {', '.join(sorted(unknown))}")
        key = f.get("key")
        if not isinstance(key, str) or not KEY_RE.fullmatch(key) or key in RESERVED_KEYS:
            errors.append(f"{where}.key must be snake_case (2-40 characters) and not a reserved name")
            continue
        if key in keys:
            errors.append(f"{where}.key {key!r} is used twice")
            continue
        keys.add(key)
        try:
            ftype = FieldType(str(f.get("type")))
        except ValueError:
            errors.append(f"{where}.type must be one of: {', '.join(t.value for t in FieldType)}")
            continue
        label = _text(f.get("label", key), f"{where}.label", 1, LABEL_MAX, errors)
        help_ = f.get("help", "")
        if not isinstance(help_, str) or len(help_) > HELP_MAX:
            errors.append(f"{where}.help must be text of at most {HELP_MAX} characters")
            help_ = ""
        flags = {}
        for name in ("required", "filter", "sort"):
            v = f.get(name, False)
            if not isinstance(v, bool):
                errors.append(f"{where}.{name} must be true or false")
                v = False
            flags[name] = v
        if flags["filter"] and ftype not in FILTERABLE:
            errors.append(f"{where}: only enum and string_list fields can be filters")
        if flags["sort"] and ftype not in SORTABLE:
            errors.append(f"{where}: only enum, date and number fields can be sorted on")
        values: tuple[Choice, ...] = ()
        if "values" in f:
            if ftype not in FILTERABLE:
                errors.append(f"{where}: only enum and string_list fields take values")
            else:
                values = _choices(f["values"], where, errors)
        elif ftype is FieldType.ENUM:
            errors.append(f"{where}: an enum field needs values")
        max_items = f.get("max_items", LIST_MAX)
        if isinstance(max_items, bool) or not isinstance(max_items, int) or not 1 <= max_items <= LIST_MAX:
            errors.append(f"{where}.max_items must be a whole number 1-{LIST_MAX}")
            max_items = LIST_MAX
        max_len = f.get("max_len", TEXT_MAX)
        if isinstance(max_len, bool) or not isinstance(max_len, int) or not 1 <= max_len <= TEXT_MAX:
            errors.append(f"{where}.max_len must be a whole number 1-{TEXT_MAX}")
            max_len = TEXT_MAX
        fields.append(
            AttributeField(
                key=key,
                label=label,
                type=ftype,
                required=flags["required"],
                filter=flags["filter"],
                sort=flags["sort"],
                help=help_.strip(),
                values=values,
                max_items=max_items,
                max_len=max_len,
            )
        )
    if errors:
        raise ValidationError("attribute_schema is not valid", {"errors": errors})
    return AttributeSchema(tuple(fields))


__all__ = [
    "AttributeField",
    "AttributeSchema",
    "Choice",
    "FieldType",
    "parse_schema",
]
