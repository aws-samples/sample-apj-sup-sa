# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""A list as a file to take elsewhere: CSV for a spreadsheet, Markdown for a report.

Columns are the record's title and summary, then the list's schema fields by label (enum values by their
label, lists joined with "; "), then its confidence, link and dates. The text comes from agents and from
the pages they read, so both formats are written to be inert where they land: a CSV cell that a
spreadsheet would run as a formula starts with a quote mark, and Markdown table cells have their pipes,
line breaks and markup characters escaped.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any
from urllib.parse import quote

from insights_radar.graph.model import Node
from insights_radar.modules.attributes import AttributeField, AttributeSchema, FieldType

FORMATS = ("csv", "md")
EXPORT_MAX = 2000
CELL_MAX = 2000
# A cell starting with one of these runs as a formula in Excel, LibreOffice and Google Sheets.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]<>|#!~])")


def _value(f: AttributeField, raw: Any) -> str:
    if raw is None:
        return ""
    if f.type is FieldType.ENUM and isinstance(raw, str):
        return next((c.label for c in f.values if c.value == raw), raw)
    if isinstance(raw, list):
        labels = {c.value: c.label for c in f.values}
        return "; ".join(labels.get(str(v), str(v)) for v in raw)
    if isinstance(raw, bool):
        return "yes" if raw else "no"
    return str(raw)


def rows(nodes: list[Node], schema: AttributeSchema, app_url: str) -> tuple[list[str], list[list[str]]]:
    header = ["Title", "Summary", *(f.label for f in schema.fields), "Confidence", "Link", "Created", "Updated"]
    body: list[list[str]] = []
    base = app_url.rstrip("/")
    for n in nodes:
        kind, _, uid = n.id.partition("/")
        # Each part encoded on its own, as the web app's links are: an id may hold "/", "?" or "#".
        link = f"{base}/insights/nodes/{quote(kind, safe='')}/{quote(uid, safe='')}" if base else n.id
        conf = "" if n.confidence is None else f"{n.confidence:.2f}"
        body.append(
            [
                n.title,
                n.summary,
                *(_value(f, n.attributes.get(f.key)) for f in schema.fields),
                conf,
                link,
                n.created_at,
                n.updated_at,
            ]
        )
    return header, body


def _csv_cell(text: str) -> str:
    text = text[:CELL_MAX]
    return f"'{text}" if text.startswith(_FORMULA_START) else text


def to_csv(header: list[str], body: list[list[str]]) -> str:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow([_csv_cell(h) for h in header])
    for r in body:
        w.writerow([_csv_cell(c) for c in r])
    return out.getvalue()


def _md_cell(text: str) -> str:
    text = " ".join(text[:CELL_MAX].split())  # one line: a line break would end the table row
    return _MD_SPECIAL.sub(r"\\\1", text)


def to_markdown(title: str, header: list[str], body: list[list[str]], note: str = "") -> str:
    lines = [f"# {_md_cell(title)}", ""]
    if note:
        lines += [_md_cell(note), ""]
    lines.append("| " + " | ".join(_md_cell(h) for h in header) + " |")
    lines.append("|" + "---|" * len(header))
    lines += ["| " + " | ".join(_md_cell(c) for c in r) + " |" for r in body]
    return "\n".join(lines) + "\n"


__all__ = ["EXPORT_MAX", "FORMATS", "rows", "to_csv", "to_markdown"]
