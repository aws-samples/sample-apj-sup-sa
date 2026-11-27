# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""List exports: CSV a spreadsheet will not execute, Markdown a report will not mangle."""

from __future__ import annotations

import csv
import io
from dataclasses import replace

import pytest

from insights_radar.graph.model import Node
from insights_radar.graph.registry import NodeType
from insights_radar.insights import export
from insights_radar.modules.attributes import parse_schema

SCHEMA = parse_schema(
    [
        {"key": "urgency", "label": "Urgency", "type": "enum", "values": [{"value": "act_now", "label": "Act now"}]},
        {
            "key": "countries",
            "label": "Countries",
            "type": "string_list",
            "values": [{"value": "ID", "label": "Indonesia"}],
        },
        {"key": "score", "label": "Score", "type": "number"},
        {"key": "note", "label": "Note", "type": "text"},
    ]
)


def _node(title: str = "Indonesia: rule", **attrs: object) -> Node:
    return Node(
        id="signal/abc",
        type=NodeType.SIGNAL,
        title=title,
        summary="What it does.",
        attributes=attrs,  # type: ignore[arg-type]
        confidence=0.8,
        created_at="2026-10-01T00:00:00Z",
        updated_at="2026-10-02T00:00:00Z",
    )


def test_rows_use_labels_and_link_back_to_the_app() -> None:
    header, body = export.rows(
        [_node(urgency="act_now", countries=["ID", "VN"], score=3, note=True)], SCHEMA, "https://app.example.com/"
    )
    assert header == [
        "Title",
        "Summary",
        "Urgency",
        "Countries",
        "Score",
        "Note",
        "Confidence",
        "Link",
        "Created",
        "Updated",
    ]
    assert body[0][2:8] == [
        "Act now",
        "Indonesia; VN",
        "3",
        "yes",
        "0.80",
        "https://app.example.com/insights/nodes/signal/abc",
    ]
    _, plain = export.rows([_node(urgency="other", note=False)], SCHEMA, "")
    # An unknown value keeps its code, a missing field is empty, no app address leaves the id.
    assert plain[0][2:8] == ["other", "", "", "no", "0.80", "signal/abc"]
    unscored = replace(_node(), confidence=None)
    assert export.rows([unscored], SCHEMA, "")[1][0][6] == ""


@pytest.mark.parametrize("start", ["=", "+", "-", "@", "\t", "\r"])
def test_csv_cells_never_run_as_formulas(start: str) -> None:
    text = export.to_csv(["Title"], [[f'{start}HYPERLINK("https://x.example")']])
    (_, (cell,)) = list(csv.reader(io.StringIO(text)))
    assert cell.startswith("'" + start)


def test_csv_quotes_and_caps_cells() -> None:
    text = export.to_csv(["A", "B"], [['say "hi", then go', "x" * 5000]])
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[1][0] == 'say "hi", then go' and len(rows[1][1]) == export.CELL_MAX
    assert text.endswith("\r\n")


def test_markdown_cells_cannot_break_the_table_or_add_markup() -> None:
    md = export.to_markdown(
        "Changes | all", ["Title", "Note"], [["a | b\nc", "<script>*x*</script> [l](u)"]], note="Radar, 2026-10-23."
    )
    lines = md.splitlines()
    assert lines[0] == "# Changes \\| all"
    assert lines[2] == "Radar, 2026-10-23."
    assert lines[4] == "| Title | Note |" and lines[5] == "|---|---|"
    assert lines[6] == "| a \\| b c | \\<script\\>\\*x\\*\\</script\\> \\[l\\](u) |"
    assert "Radar" not in export.to_markdown("T", ["A"], [])  # no note line without a note


def test_links_encode_each_part_of_the_id() -> None:
    odd = replace(_node(), id="signal/a/b?c#d")
    link = export.rows([odd], SCHEMA, "https://app.example.com")[1][0][7]
    assert link == "https://app.example.com/insights/nodes/signal/a%2Fb%3Fc%23d"
