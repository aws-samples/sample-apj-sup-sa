"""Worker DB layer: loader and registry persistence against a fake Data API client."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
os.environ.setdefault("CLUSTER_ARN", "arn:aws:rds:us-east-1:111122223333:cluster:c")
os.environ.setdefault("WRITER_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:111122223333:secret:w")

from bedrock_bench.registry import load_registry  # noqa: E402

from worker import db  # noqa: E402

SAMPLE = Path(__file__).parents[2] / "docs" / "sample-report" / "summary.json"


class FakeRds:
    def __init__(self):
        self.sql: list[tuple[str, list]] = []
        self.committed = self.rolled_back = 0

    def execute_statement(self, **kw):
        self.sql.append((kw["sql"], kw["parameters"]))
        return {"formattedRecords": "[]"}

    def begin_transaction(self, **kw):
        return {"transactionId": "tx"}

    def commit_transaction(self, **kw):
        self.committed += 1

    def rollback_transaction(self, **kw):
        self.rolled_back += 1


@pytest.fixture
def rds(monkeypatch):
    fake = FakeRds()
    monkeypatch.setattr(db, "_rds", fake)
    return fake


def test_load_summary_writes_run_cells_and_comparisons(rds):
    summary = json.loads(SAMPLE.read_text())
    n = db.load_summary(summary)
    assert n == len(summary["cells"])
    tables = [s.split()[2] for s, _ in rds.sql]
    assert tables.count("runs") == 1 and tables.count("cells") == n
    assert tables.count("comparisons") == len(summary["comparisons"])
    # every value is a bind parameter: the run id never appears in SQL text
    assert all(summary["meta"]["run_id"] not in s for s, _ in rds.sql)


def test_upsert_refuses_unverified_and_is_transactional(rds):
    spec = load_registry()[0]
    with pytest.raises(ValueError):
        db.upsert_spec(replace(spec, verified_at=None))
    db.upsert_spec(replace(spec, verified_at="2026-10-08"))
    assert rds.committed == 1 and rds.rolled_back == 0
    assert sum("INSERT INTO offerings" in s for s, _ in rds.sql) == len(spec.offerings)
