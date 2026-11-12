"""Adaptive schema context in text_to_sql_tool: full schema when small, table index when large."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

import custom_sql_toolset_lambda as m  # noqa: E402


def _catalog(n_tables, n_cols=8):
    return {f"t{i}": {"description": f"table {i}", "columns": [{"name": f"c{j}", "type": "int", "description": ""}
                                                                for j in range(n_cols)]}
            for i in range(n_tables)}


def _run(monkeypatch, catalog, args):
    def fake_schema(a, rls):
        wanted = a.get("tables") or list(catalog)
        return {"statusCode": 200, "body": json.dumps({"success": True,
                                                        "schemas": {t: catalog[t] for t in wanted if t in catalog}})}
    monkeypatch.setattr(m, "get_schema_context", fake_schema)
    monkeypatch.setattr(m, "rag_retrieve", lambda q: [{"content": "glossary", "score": 1.0}])
    return json.loads(m.text_to_sql_with_rag(args, {})["body"])


def test_small_schema_is_sent_whole(monkeypatch):
    body = _run(monkeypatch, _catalog(10), {"question": "q"})
    assert body["schema_mode"] == "full"
    assert "Columns: c0 (int)" in body["context"]


def test_large_schema_returns_index(monkeypatch):
    monkeypatch.setattr(m, "SCHEMA_FULL_MAX_CHARS", 2000)
    body = _run(monkeypatch, _catalog(200), {"question": "q"})
    assert body["schema_mode"] == "index"
    assert "- t199: table 199 (8 columns)" in body["context"]
    assert "Columns:" not in body["context"]
    assert "call text_to_sql_tool again" in body["context"]


def test_selected_tables_get_full_columns(monkeypatch):
    monkeypatch.setattr(m, "SCHEMA_FULL_MAX_CHARS", 2000)
    body = _run(monkeypatch, _catalog(200), {"question": "q", "tables": ["t3", "t7"]})
    assert body["schema_mode"] == "selected"
    assert body["tables_count"] == 2
    assert "Table t3: table 3" in body["context"] and "Columns: c0 (int)" in body["context"]
