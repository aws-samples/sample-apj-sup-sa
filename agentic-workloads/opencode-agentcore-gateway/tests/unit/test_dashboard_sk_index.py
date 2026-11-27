# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Dashboard reads through the by_sk index give the same answers as the filtered scans, without scanning."""
import pytest

from test_dashboard_api import api, call  # noqa: F401 - fixture


@pytest.fixture()
def indexed(api, monkeypatch):  # noqa: F811
    api.ddb.update_table(TableName="t", AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}, {"AttributeName": "sk", "AttributeType": "S"}],
                         GlobalSecondaryIndexUpdates=[{"Create": {"IndexName": "by_sk", "KeySchema": [{"AttributeName": "sk", "KeyType": "HASH"},
                                                                                                         {"AttributeName": "pk", "KeyType": "RANGE"}],
                                                                  "Projection": {"ProjectionType": "ALL"}}}])
    return api


REQS = [("GET", "/api/v2/overview", {}), ("GET", "/api/v2/users", {}), ("GET", "/api/v2/users/alice", {}), ("GET", "/api/v2/groups", {}),
        ("GET", "/api/v2/usage", {"group_by": "model", "granularity": "month"}), ("GET", "/api/v2/usage", {"group_by": "user", "granularity": "day"}),
        ("GET", "/api/v2/usage", {"group_by": "group", "granularity": "month"}), ("GET", "/api/v2/usage", {"group_by": "model", "granularity": "day", "user": "alice"}),
        ("GET", "/api/v2/ratelimits", {})]


def strip(o):
    if isinstance(o, dict):
        return {k: strip(v) for k, v in o.items() if k not in ("generated_at",)}
    if isinstance(o, list):
        return [strip(x) for x in o]
    return o


@pytest.mark.parametrize("method,path,q", REQS)
def test_index_matches_scan(indexed, monkeypatch, method, path, q):
    import time
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now)  # overview's projected_usd moves with the clock between the two calls
    monkeypatch.setattr(indexed, "SK_INDEX", "")
    plain = call(indexed, method, path, q=q)
    monkeypatch.setattr(indexed, "SK_INDEX", "by_sk")
    scans = []
    real = indexed.ddb.scan
    monkeypatch.setattr(indexed.ddb, "scan", lambda **kw: (scans.append(kw), real(**kw))[1])
    via_index = call(indexed, method, path, q=q)
    assert strip(via_index) == strip(plain)
    assert scans == []  # every period read went to the index


def test_overview_reads_each_past_day_once_and_no_future_day(indexed, monkeypatch):
    import time
    monkeypatch.setattr(indexed, "SK_INDEX", "by_sk")
    seen, real = [], indexed.ddb.query
    monkeypatch.setattr(indexed.ddb, "query", lambda **kw: (seen.append(kw["ExpressionAttributeValues"].get(":s", {}).get("S")), real(**kw))[1])
    s, r = call(indexed, "GET", "/api/v2/overview")
    today = time.strftime("DAY#%Y-%m-%d", time.gmtime())
    days = [x for x in seen if x and x.startswith("DAY#")]
    assert s == 200 and days.count(today) == 1 and len(days) == len(set(days)) and all(d <= today for d in days)
    assert len(days) == int(today[-2:])  # the 1st to today, one Query each


def test_days_of_month(indexed):
    assert len(indexed._days_of("2028-02")) == 29 and indexed._days_of("2026-10")[-1] == "2026-10-31"
