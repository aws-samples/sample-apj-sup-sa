"""Unit tests for the read-only SQL guard in custom_sql_toolset_lambda.

Run: python -m pytest app/agentcore_strands/tests/test_custom_sql_guard.py
The Lambda's AWS/DB dependencies are stubbed, so no AWS access is needed.
"""
import importlib.util
import json
import pathlib
import sys
import types
from unittest import mock

import pytest

# Stub the Lambda's imports only while loading it, so other test modules in the
# same run still get the real boto3 and PyJWT.
_stubs = {name: types.ModuleType(name) for name in ("boto3", "psycopg2", "psycopg2.extras", "jwt")}
_stubs["boto3"].client = lambda *a, **k: None
_stubs["psycopg2"].extras = _stubs["psycopg2.extras"]
_stubs["psycopg2.extras"].RealDictCursor = object
_stubs["jwt"].PyJWKClient = lambda *a, **k: None

_PATH = pathlib.Path(__file__).resolve().parents[1] / "tools" / "custom_sql_toolset_lambda.py"
_spec = importlib.util.spec_from_file_location("custom_sql_toolset_lambda", _PATH)
mod = importlib.util.module_from_spec(_spec)
with mock.patch.dict(sys.modules, _stubs):
    _spec.loader.exec_module(mod)
validate = mod.validate_readonly_sql


@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "select * from bookings limit 5;",
    "WITH r AS (SELECT unicorn_id, SUM(price) s FROM bookings GROUP BY 1) SELECT * FROM r",
    "SELECT updated_at, deleted_flag, created_at FROM unicorns",
    "SELECT 'DROP TABLE x; DELETE' AS note",
    "SELECT \"Update\" FROM t -- trailing comment; DROP TABLE t",
    "SELECT $$; set_config$$ AS s",
    "/* leading comment */ SELECT 1",
    "SELECT current_setting('app.current_account_id')",
    "SELECT 1 AS x$q$, 2 AS y$q$",
    "SELECT name FROM t WHERE type = 'e'",
    "SELECT CASE WHEN a THEN 1 ELSE'x' END FROM t",
])
def test_allows_read_only(sql):
    assert validate(sql) is None


@pytest.mark.parametrize("sql,needle", [
    ("", "required"),
    ("   ", "required"),
    ("DELETE FROM bookings", "Only SELECT"),
    ("UPDATE unicorns SET name='x'", "Only SELECT"),
    ("SET app.current_account_id = 'other'", "Only SELECT"),
    ("SELECT 1; DROP TABLE bookings", "one SQL statement"),
    ("SELECT 1; COMMIT; DELETE FROM bookings", "one SQL statement"),
    ("SELECT set_config('app.current_account_id', 'other', false)", "set_config"),
    ("SELECT \"set_config\"('app.current_user_role', 'platform_admin', false)", "set_config"),
    ("SELECT SET_CONFIG ('app.current_account_id','x',true)", "set_config"),
    ("SELECT pg_read_file('/etc/passwd')", "pg_read_file"),
    ("SELECT pg_sleep(600)", "pg_sleep"),
    ("SELECT U&\"set\\005fconfig\"('app.current_account_id', 'other', false)", "Unicode-escaped"),
    ("SELECT u&\"pg_sl\\0065ep\"(600)", "Unicode-escaped"),
    ("SELECT U&\"set!005fconfig\" UESCAPE '!' ('app.current_account_id','x',false)", "Unicode-escaped"),
    ("SELECT U&'d\\0061ta'", "Unicode-escaped"),
    ("SELECT \"set\\_config\"('a','b',false)", "Unicode-escaped"),
    ("SELECT E'\\'', set_config('app.current_user_role','saas_admin',false), * FROM bookings -- '", "Escape string"),
    ("SELECT e'x'", "Escape string"),
    ("SELECT 1 AS x$q$, set_config('app.current_account_id','o',false) AS s, 1 AS y$q$", "set_config"),
    ("SELECT 'unterminated", "Unterminated"),
    ("SELECT 1 /* open comment", "Unterminated"),
])
def test_rejects(sql, needle):
    err = validate(sql)
    assert err is not None and needle in err


def test_execute_sql_rejects_before_connecting(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not connect for rejected SQL")
    monkeypatch.setattr(mod, "get_db_connection", boom)
    res = mod.execute_sql({"sql": "SELECT 1; DROP TABLE x"}, {})
    assert res["statusCode"] == 400


def test_execute_sql_closes_connection_on_error(monkeypatch):
    class Cur:
        description = None
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, *a, **k): raise RuntimeError("db down")

    class Conn:
        closed = False
        def cursor(self, **k): return Cur()
        def rollback(self): pass
        def close(self): self.closed = True

    conn = Conn()
    monkeypatch.setattr(mod, "get_db_connection", lambda ctx: conn)
    res = mod.execute_sql({"sql": "SELECT 1"}, {})
    assert res["statusCode"] == 500 and conn.closed


def test_execute_sql_runs_read_only_with_timeout(monkeypatch):
    seen = []

    class Cur:
        description = [("n",)]
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, q, params=None): seen.append(q)
        def fetchmany(self, n): return [{"n": i} for i in range(min(n, 150))]
        def fetchone(self): return {"a": None, "r": None}

    class Conn:
        def cursor(self, **k): return Cur()
        def rollback(self): seen.append("ROLLBACK")
        def close(self): seen.append("CLOSE")

    monkeypatch.setattr(mod, "get_db_connection", lambda ctx: Conn())
    res = mod.execute_sql({"sql": "SELECT n FROM generate_series(1,150) n"}, {})
    body = json.loads(res["body"])
    assert seen[0] == "SET TRANSACTION READ ONLY"
    assert seen[1].startswith("SET LOCAL statement_timeout")
    assert seen[2] == "SET LOCAL standard_conforming_strings = on"
    assert body["row_count"] == 100 and body["truncated"] is True
    assert seen[-2:] == ["ROLLBACK", "CLOSE"]


def test_execute_sql_drops_rows_when_session_changed(monkeypatch):
    class Cur:
        description = [("n",)]
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, q, params=None): pass
        def fetchmany(self, n): return [{"n": 1}]
        def fetchone(self): return {"a": "other-tenant", "r": "staff"}

    class Conn:
        def cursor(self, **k): return Cur()
        def rollback(self): pass
        def close(self): pass

    monkeypatch.setattr(mod, "get_db_connection", lambda ctx: Conn())
    res = mod.execute_sql({"sql": "SELECT 1"}, {"account_id": "mine", "role": "staff"})
    body = json.loads(res["body"])
    assert res["statusCode"] == 400 and "data" not in body
