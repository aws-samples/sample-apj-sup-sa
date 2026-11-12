"""Unit tests for schema_diff (run: uv run --no-project --with pytest python -m pytest -q app/schema_sync)."""
import copy

from schema_diff import classify


def snap():
    return {"objects": {
        "bookings": {"kind": "table", "rls": True, "has_policy": True, "comment": "",
                     "columns": [{"name": "booking_id", "type": "uuid"}, {"name": "account_id", "type": "uuid"},
                                 {"name": "total_cost", "type": "numeric"}]},
        "unicorns": {"kind": "table", "rls": True, "has_policy": True, "comment": "",
                     "columns": [{"name": "unicorn_id", "type": "uuid"}, {"name": "account_id", "type": "uuid"},
                                 {"name": "name", "type": "varchar"}]},
        "revenue_view": {"kind": "view", "comment": "", "sql": "select 1",
                         "columns": [{"name": "revenue", "type": "numeric"}]},
    }}


def test_no_change():
    r = classify(snap(), snap())
    assert r["severity"] == "none" and r["changes"] == []


def test_add_column_is_additive():
    new = snap()
    new["objects"]["bookings"]["columns"].append({"name": "promo_code", "type": "varchar"})
    r = classify(snap(), new)
    assert r["severity"] == "additive"
    assert r["changes"] == [{"type": "column_added", "object": "bookings", "column": "promo_code"}]


def test_add_view_is_additive():
    new = snap()
    new["objects"]["weekly_view"] = {"kind": "view", "sql": "select 2", "columns": [{"name": "w", "type": "int"}]}
    assert classify(snap(), new)["severity"] == "additive"


def test_rename_column_detected_and_breaking():
    new = snap()
    new["objects"]["bookings"]["columns"][2]["name"] = "booking_total"
    r = classify(snap(), new)
    assert r["severity"] == "breaking"
    assert r["changes"][0]["type"] == "column_renamed"
    assert (r["changes"][0]["from"], r["changes"][0]["to"]) == ("total_cost", "booking_total")


def test_rename_table_detected():
    new = snap()
    new["objects"]["reservations"] = new["objects"].pop("bookings")
    r = classify(snap(), new)
    assert r["severity"] == "breaking"
    assert [c["type"] for c in r["changes"]] == ["table_renamed"]
    assert r["changes"][0]["from"] == "bookings" and r["changes"][0]["to"] == "reservations"


def test_retype_and_drop_are_breaking():
    new = snap()
    new["objects"]["bookings"]["columns"][2]["type"] = "integer"
    r = classify(snap(), new)
    assert r["severity"] == "breaking" and r["changes"][0]["type"] == "column_retyped"
    new = snap()
    del new["objects"]["revenue_view"]
    assert classify(snap(), new)["changes"] == [{"type": "view_dropped", "object": "revenue_view"}]


def test_new_tenant_table_without_rls_is_blocked():
    new = snap()
    new["objects"]["promotions"] = {"kind": "table", "rls": False, "has_policy": False,
                                    "columns": [{"name": "promo_id", "type": "uuid"},
                                                {"name": "account_id", "type": "uuid"}]}
    r = classify(snap(), new)
    assert r["severity"] == "blocked" and r["rls_gaps"] == ["promotions"]


def test_new_tenant_table_with_rls_is_additive():
    new = snap()
    new["objects"]["promotions"] = {"kind": "table", "rls": True, "has_policy": True,
                                    "columns": [{"name": "promo_id", "type": "uuid"},
                                                {"name": "account_id", "type": "uuid"}]}
    assert classify(snap(), new)["severity"] == "additive"


def test_rls_disabled_on_touched_table_is_blocked():
    new = copy.deepcopy(snap())
    new["objects"]["unicorns"]["rls"] = False
    new["objects"]["unicorns"]["columns"].append({"name": "stable", "type": "varchar"})
    assert classify(snap(), new)["severity"] == "blocked"


def test_comment_only_change_is_none():
    new = snap()
    new["objects"]["bookings"]["comment"] = "One row per rental"
    r = classify(snap(), new)
    assert r["severity"] == "none" and r["changes"][0]["type"] == "comment_changed"


def test_disabling_rls_on_tenant_table_blocks():
    new = snap()
    new["objects"]["bookings"]["rls"] = False
    r = classify(snap(), new)
    assert r["severity"] == "blocked" and r["rls_gaps"] == ["bookings"]


def test_dropping_policy_on_tenant_table_blocks():
    new = snap()
    new["objects"]["bookings"]["has_policy"] = False
    assert classify(snap(), new)["severity"] == "blocked"


def test_enabling_rls_is_not_breaking():
    old = snap()
    old["objects"]["bookings"]["has_policy"] = False
    r = classify(old, snap())
    assert r["severity"] == "none" and r["changes"][0]["type"] == "rls_changed"
