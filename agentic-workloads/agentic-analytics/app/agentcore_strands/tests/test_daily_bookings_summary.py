"""get_daily_bookings_summary: per-day aggregates with group_by=day, date filters, capped row list."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import prebaked_sql_toolset_lambda as m  # noqa: E402


class FakeCursor:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def execute(self, sql, params=None):
        self.calls.append((sql, list(params or [])))

    def fetchall(self):
        return self.rows

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    def __init__(self, cur):
        self.cur = cur

    def cursor(self, **kw):
        return self.cur

    def close(self):
        pass


def _run(monkeypatch, args, rows):
    cur = FakeCursor(rows)
    monkeypatch.setattr(m, "get_db_connection", lambda rls: FakeConn(cur))
    return json.loads(m.get_daily_bookings_summary(args, {})["body"]), cur


def test_group_by_day_aggregates_and_filters(monkeypatch):
    rows = [{"day": "2025-12-01", "bookings": 19, "completed": 18, "rental_hours": 129.5},
            {"day": "2025-12-02", "bookings": 21, "completed": 21, "rental_hours": 171}]
    body, cur = _run(monkeypatch, {"start_date": "2025-12-01", "end_date": "2025-12-31", "group_by": "day"}, rows)
    sql, params = cur.calls[0]
    assert "GROUP BY 1" in sql and "COUNT(*) AS bookings" in sql
    assert params == ["2025-12-01", "2025-12-01", "2025-12-31", "2025-12-31"]
    assert body["days"] == 2 and body["total_bookings"] == 40 and body["per_day"] == rows
    assert body["total_completed"] == 39 and body["total_rental_hours"] == 300.5


def test_row_list_limit_is_capped(monkeypatch):
    body, cur = _run(monkeypatch, {"limit": 5000}, [])
    sql, params = cur.calls[0]
    assert "GROUP BY" not in sql
    assert params[-1] == m.MAX_ROWS
    assert body["count"] == 0


def test_get_bookings_one_day_range_is_inclusive(monkeypatch):
    cur = FakeCursor([])
    monkeypatch.setattr(m, "get_db_connection", lambda rls: FakeConn(cur))
    m.get_bookings({"start_date": "2027-01-21", "end_date": "2027-01-21", "limit": 999}, {})
    sql, params = cur.calls[0]
    assert "start_datetime::date >= %s" in sql and "start_datetime::date <= %s" in sql
    assert "end_datetime <=" not in sql
    assert params == ["2027-01-21", "2027-01-21", m.MAX_ROWS]
