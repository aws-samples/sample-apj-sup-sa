"""Which KB cards a schema change rewrites and which it deletes."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import schema_sync_lambda as m  # noqa: E402


def test_dropped_column_keeps_and_rewrites_the_table_card():
    changes = [{"type": "column_dropped", "object": "bookings", "column": "promo_code"}]
    assert m.touched_objects(changes) == {"bookings"}
    assert m.removed_objects(changes) == []


def test_dropped_table_removes_its_card_only():
    changes = [{"type": "table_dropped", "object": "promotions"},
               {"type": "column_added", "object": "bookings", "column": "x"}]
    assert m.touched_objects(changes) == {"bookings"}
    assert m.removed_objects(changes) == ["promotions"]


def test_renamed_table_rewrites_new_name_and_removes_old():
    changes = [{"type": "table_renamed", "object": "reservations", "from": "bookings", "to": "reservations"}]
    assert m.touched_objects(changes) == {"reservations"}
    assert m.removed_objects(changes) == ["bookings"]
