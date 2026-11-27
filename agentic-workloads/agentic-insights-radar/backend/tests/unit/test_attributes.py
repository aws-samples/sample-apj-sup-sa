# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

from typing import Any

import pytest

from insights_radar.core.errors import ValidationError
from insights_radar.modules.attributes import AttributeSchema, FieldType, parse_schema

URGENCY = {
    "key": "urgency",
    "label": "Urgency",
    "type": "enum",
    "required": True,
    "filter": True,
    "sort": True,
    "help": "How soon to act.",
    "values": [
        {"value": "act_now", "label": "Act now", "rank": 1},
        {"value": "plan", "label": "Plan", "rank": 2},
        {"value": "watch", "label": "Watch", "rank": 3},
    ],
}
COUNTRIES = {
    "key": "countries",
    "label": "Countries",
    "type": "string_list",
    "filter": True,
    "max_items": 3,
    "values": [{"value": c} for c in ("ID", "IN", "VN", "PH")],
}
TAGS = {"key": "tags", "label": "Tags", "type": "string_list"}
EFFECTIVE = {"key": "effective_date", "label": "Effective date", "type": "date", "sort": True}
SOURCE = {"key": "source_url", "label": "Primary source", "type": "url", "required": True}
ACTION = {"key": "suggested_action", "label": "Suggested action", "type": "text", "max_len": 20}
SCORE = {"key": "score", "label": "Score", "type": "number", "sort": True}


@pytest.fixture
def schema() -> AttributeSchema:
    return parse_schema([URGENCY, COUNTRIES, TAGS, EFFECTIVE, SOURCE, ACTION, SCORE])


def _errors(fn: Any, *args: Any) -> list[str]:
    with pytest.raises(ValidationError) as e:
        fn(*args)
    return list(e.value.details.get("errors", [])) or [e.value.message]


def test_none_is_an_empty_schema() -> None:
    s = parse_schema(None)
    assert not s and s.to_public() == [] and s.prompt_section() == ""
    assert s.check({"anything": 1}) == {"anything": 1}


def test_schema_round_trips_and_answers_lookups(schema: AttributeSchema) -> None:
    assert bool(schema)
    pub = schema.to_public()
    assert [f["key"] for f in pub] == [
        "urgency",
        "countries",
        "tags",
        "effective_date",
        "source_url",
        "suggested_action",
        "score",
    ]
    assert pub[0]["values"][0] == {"value": "act_now", "label": "Act now", "rank": 1}
    assert pub[1]["max_items"] == 3 and pub[5]["max_len"] == 20 and "values" not in pub[3]
    assert pub[1]["values"][0]["label"] == "ID"  # a label defaults to the value
    assert [f.key for f in schema.filterable()] == ["urgency", "countries"]
    assert [f.key for f in schema.sortable()] == ["urgency", "effective_date", "score"]
    urgency = schema.field("urgency")
    assert urgency is not None and urgency.type is FieldType.ENUM
    assert urgency.rank_of("plan") == 2 and urgency.rank_of("nope") == 0
    assert schema.field("missing") is None


def test_check_normalizes_valid_attributes(schema: AttributeSchema) -> None:
    out = schema.check(
        {
            "urgency": " plan ",
            "countries": ["ID", " IN", "ID", ""],
            "tags": ["a"],
            "effective_date": "2026-11-01",
            "source_url": " https://example.com/notice ",
            "suggested_action": " Check HS 8517 ",
            "score": 2.5,
            "extra": "kept",
            "tags_empty": [],
        }
    )
    assert out["urgency"] == "plan" and out["countries"] == ["ID", "IN"]
    assert out["source_url"] == "https://example.com/notice" and out["suggested_action"] == "Check HS 8517"
    assert out["extra"] == "kept"


def test_empty_optional_values_are_dropped(schema: AttributeSchema) -> None:
    out = schema.check({"urgency": "watch", "source_url": "https://e.example.com", "effective_date": "", "tags": []})
    assert "effective_date" not in out and "tags" not in out


def test_every_problem_is_reported_at_once(schema: AttributeSchema) -> None:
    errors = _errors(
        schema.check,
        {
            "countries": ["XX", "ID"],
            "effective_date": "2026-02-30",
            "suggested_action": "x" * 21,
            "score": True,
            "source_url": None,
        },
    )
    joined = "\n".join(errors)
    assert "urgency (Urgency) is required" in joined
    assert "source_url (Primary source) is required" in joined
    assert "not allowed: XX" in joined
    assert "is not a real date" in joined
    assert "at most 20 characters" in joined
    assert "must be a number" in joined


@pytest.mark.parametrize(
    ("attrs", "fragment"),
    [
        ({"urgency": "soon"}, "must be one of"),
        ({"urgency": 3}, "must be one of"),
        ({"countries": "ID"}, "must be a list of strings"),
        ({"countries": ["ID", 2]}, "must be a list of strings"),
        ({"countries": ["ID", "IN", "VN", "PH"]}, "at most 3 items"),
        ({"tags": ["x" * 61]}, "at most 60 characters"),
        ({"effective_date": "1 Nov 2026"}, "YYYY-MM-DD"),
        ({"effective_date": 20261101}, "YYYY-MM-DD"),
        ({"source_url": "http://e.example.com"}, "https URL"),
        ({"source_url": "https://127.0.0.1/x"}, "not a usable URL"),
        ({"suggested_action": 5}, "must be text"),
        ({"score": "high"}, "must be a number"),
    ],
)
def test_bad_values_are_refused(schema: AttributeSchema, attrs: dict[str, Any], fragment: str) -> None:
    base = {"urgency": "plan", "source_url": "https://e.example.com"}
    assert any(fragment in e for e in _errors(schema.check, {**base, **attrs})), _errors(
        schema.check, {**base, **attrs}
    )


def test_prompt_section_describes_every_field(schema: AttributeSchema) -> None:
    text = schema.prompt_section()
    assert "`urgency` (Urgency, required, one of the allowed values): How soon to act." in text
    assert "`act_now` (Act now)" in text
    assert "a list of at most 3 allowed values" in text and "a list of at most 50 short strings" in text
    assert "a date as YYYY-MM-DD" in text and "an https URL" in text
    assert "text of at most 20 characters" in text and "a number" in text
    assert "never invent a value" in text


def test_prompt_section_summarizes_long_value_lists() -> None:
    s = parse_schema([{"key": "country", "type": "enum", "values": [{"value": f"C{i:02d}"} for i in range(60)]}])
    text = s.prompt_section()
    assert "60 codes, for example `C00`" in text and "`C59`" not in text


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        ({"a": 1}, "must be a list of fields"),
    ],
)
def test_schema_must_be_a_list(raw: Any, fragment: str) -> None:
    assert fragment in _errors(parse_schema, raw)[0]


@pytest.mark.parametrize(
    ("field", "fragment"),
    [
        ("x", "must be an object"),
        ({"key": "a", "type": "text"}, "snake_case"),
        ({"key": "title", "type": "text"}, "reserved"),
        ({"key": "ok_key", "type": "colour"}, "type must be one of"),
        ({"key": "ok_key", "type": "text", "colour": 1}, "unknown fields: colour"),
        ({"key": "ok_key", "type": "text", "label": ""}, "label must be text"),
        ({"key": "ok_key", "type": "text", "help": 4}, "help must be text"),
        ({"key": "ok_key", "type": "text", "required": "yes"}, "required must be true or false"),
        ({"key": "ok_key", "type": "text", "filter": True}, "only enum and string_list fields can be filters"),
        ({"key": "ok_key", "type": "url", "sort": True}, "only enum, date and number"),
        ({"key": "ok_key", "type": "date", "values": []}, "only enum and string_list fields take values"),
        ({"key": "ok_key", "type": "enum"}, "an enum field needs values"),
        ({"key": "ok_key", "type": "enum", "values": "a"}, "must be a list of at most"),
        ({"key": "ok_key", "type": "enum", "values": ["a"]}, "must be an object with value"),
        ({"key": "ok_key", "type": "enum", "values": [{"value": "a", "x": 1}]}, "must be an object with value"),
        ({"key": "ok_key", "type": "enum", "values": [{"value": "has space"}]}, "must be a short code"),
        ({"key": "ok_key", "type": "enum", "values": [{"value": "a"}, {"value": "a"}]}, "twice"),
        ({"key": "ok_key", "type": "enum", "values": [{"value": "a", "rank": 100}]}, "rank must be"),
        ({"key": "ok_key", "type": "enum", "values": [{"value": "a", "rank": True}]}, "rank must be"),
        ({"key": "ok_key", "type": "string_list", "max_items": 0}, "max_items must be"),
        ({"key": "ok_key", "type": "string_list", "max_items": True}, "max_items must be"),
        ({"key": "ok_key", "type": "text", "max_len": 5000}, "max_len must be"),
    ],
)
def test_invalid_fields_are_refused(field: Any, fragment: str) -> None:
    errors = _errors(parse_schema, [field])
    assert any(fragment in e for e in errors), errors


def test_duplicate_keys_and_too_many_fields_are_refused() -> None:
    assert any("used twice" in e for e in _errors(parse_schema, [TAGS, TAGS]))
    many = [{"key": f"f{i:02d}", "type": "text"} for i in range(21)]
    assert any("at most 20 fields" in e for e in _errors(parse_schema, many))


def test_an_enum_without_ranks_sorts_in_the_order_its_values_are_listed() -> None:
    from insights_radar.modules.attributes import parse_schema

    f = parse_schema(
        [{"key": "stage", "label": "Stage", "type": "enum", "sort": True, "values": [{"value": "b"}, {"value": "a"}]}]
    ).fields[0]
    assert [f.rank_of("b"), f.rank_of("a"), f.rank_of("x")] == [1, 2, 0]
