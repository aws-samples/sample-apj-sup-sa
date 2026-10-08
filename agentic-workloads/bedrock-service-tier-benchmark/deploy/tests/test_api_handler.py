"""API Lambda: routing and SQL-injection safety (no AWS calls)."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("CLUSTER_ARN", "arn:aws:rds:us-east-1:111122223333:cluster:c")
os.environ.setdefault("READER_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:111122223333:secret:s")
os.environ.setdefault("DB_NAME", "bench")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")

_spec = importlib.util.spec_from_file_location(
    "api_handler", Path(__file__).parents[1] / "lambdas" / "api" / "handler.py"
)
api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api)


class FakeRds:
    def __init__(self):
        self.calls = []

    def execute_statement(self, **kw):
        self.calls.append(kw)
        return {"formattedRecords": json.dumps([{"v": "x"}])}


@pytest.fixture
def rds(monkeypatch):
    fake = FakeRds()
    monkeypatch.setattr(api, "rds", fake)
    return fake


def _event(path, qs=None, method="GET"):
    return {
        "rawPath": path,
        "queryStringParameters": qs,
        "requestContext": {"http": {"method": method}},
    }


def test_filters_are_bind_parameters(rds):
    r = api.handler(_event("/comparisons", {"model": "zai.glm-5.3", "tier": "flex", "evil": "x"}), None)
    assert r["statusCode"] == 200
    sql = rds.calls[0]["sql"]
    assert "zai.glm-5.3" not in sql and ":model" in sql and "evil" not in sql
    assert {p["name"] for p in rds.calls[0]["parameters"]} == {"model", "tier"}


@pytest.mark.parametrize("value", ["x' OR '1'='1", "a;DROP TABLE runs", "a b", "x" * 200])
def test_rejects_unsafe_values(rds, value):
    r = api.handler(_event("/cells", {"model": value}), None)
    assert r["statusCode"] == 400 and not rds.calls


def test_unknown_route_and_method(rds):
    assert api.handler(_event("/admin"), None)["statusCode"] == 404
    assert api.handler(_event("/models", method="POST"), None)["statusCode"] == 404
