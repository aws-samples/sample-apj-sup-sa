"""Publish rewrites the Glue column list from the Postgres snapshot."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import schema_sync_lambda as m  # noqa: E402

EXISTING = [{"Name": "booking_id", "Type": "string"}, {"Name": "price", "Type": "decimal(10,2)"},
            {"Name": "promo_code", "Type": "string"}]


def _obj(*cols):
    return {"kind": "table", "columns": [{"name": n, "type": t, "comment": c} for n, t, c in cols]}


def test_renamed_column_takes_the_new_name_and_keeps_the_old_type():
    obj = _obj(("booking_id", "uuid", ""), ("price", "numeric(10,2)", ""),
               ("promotion_code", "character varying(20)", "Promo code"))
    changes = [{"type": "column_renamed", "object": "bookings", "from": "promo_code", "to": "promotion_code"}]
    cols = m.glue_columns("bookings", obj, EXISTING, changes)
    assert [c["Name"] for c in cols] == ["booking_id", "price", "promotion_code"]
    assert cols[2] == {"Name": "promotion_code", "Type": "string", "Comment": "Promo code"}


def test_added_column_gets_a_mapped_type_and_dropped_column_disappears():
    obj = _obj(("booking_id", "uuid", ""), ("discount_pct", "numeric(5,2)", ""),
               ("created_at", "timestamp with time zone", ""), ("seats", "integer", ""))
    cols = m.glue_columns("bookings", obj, EXISTING, [])
    assert {c["Name"]: c["Type"] for c in cols} == {
        "booking_id": "string", "discount_pct": "decimal(5,2)", "created_at": "timestamp", "seats": "int"}


def test_rename_on_another_table_is_ignored():
    obj = _obj(("promotion_code", "text", ""))
    changes = [{"type": "column_renamed", "object": "payments", "from": "promo_code", "to": "promotion_code"}]
    assert m.glue_columns("bookings", obj, EXISTING, changes) == [{"Name": "promotion_code", "Type": "string"}]


class _FakeGlue:
    class exceptions:
        class EntityNotFoundException(Exception):
            pass

    def __init__(self, tables):
        self.tables, self.calls = dict(tables), []

    def get_table(self, DatabaseName, Name):
        if Name not in self.tables:
            raise self.exceptions.EntityNotFoundException(Name)
        return {"Table": dict(self.tables[Name], Name=Name, DatabaseName=DatabaseName, CreateTime="t")}

    def create_table(self, DatabaseName, TableInput):
        self.calls.append(("create", TableInput["Name"])); self.tables[TableInput["Name"]] = TableInput

    def update_table(self, DatabaseName, TableInput):
        self.calls.append(("update", TableInput["Name"]))

    def delete_table(self, DatabaseName, Name):
        self.calls.append(("delete", Name)); self.tables.pop(Name, None)


def test_renamed_table_moves_its_glue_entry(monkeypatch):
    old = f"{m.AURORA_DATABASE}_public_bookings"
    new = f"{m.AURORA_DATABASE}_public_rentals"
    fake = _FakeGlue({old: {"StorageDescriptor": {"Columns": EXISTING}}})
    monkeypatch.setattr(m, "GLUE", fake)
    monkeypatch.setattr(m, "GLUE_DATABASE", "timely_unicorn")
    changes = [{"type": "table_renamed", "object": "rentals", "from": "bookings"}]
    obj = _obj(("booking_id", "uuid", ""))
    assert m._update_glue_table("rentals", obj, "Rentals", changes)
    for name in m.removed_objects(changes):
        m._delete_glue_table(name)
    assert fake.calls == [("create", new), ("delete", old)]
    assert "CreateTime" not in fake.tables[new]


def test_dropping_a_table_that_was_never_crawled_is_a_no_op(monkeypatch):
    fake = _FakeGlue({})
    monkeypatch.setattr(m, "GLUE", fake)
    m._delete_glue_table("ghost")
    assert fake.calls == [("delete", f"{m.AURORA_DATABASE}_public_ghost")]


def test_older_snapshot_is_not_published_over_a_newer_one():
    old = {"taken_at": "2026-10-25T10:00:00+00:00"}
    new = {"taken_at": "2026-10-25T11:00:00+00:00"}
    assert m.is_stale(old, new) and not m.is_stale(new, old) and not m.is_stale(new, None)
