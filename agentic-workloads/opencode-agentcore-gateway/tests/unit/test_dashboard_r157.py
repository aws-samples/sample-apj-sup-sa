# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""r157 /code-review fixes: a hand-edited boost is no boost at the gate too, one clock on Budgets, End boost races."""
import sys
from pathlib import Path

import boto3
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lambda"))
import policy  # noqa: E402

from test_dashboard_api import api, call  # noqa: E402,F401 - the fixture and the call helper

FAR = 4102444800  # 2100-01-01


def set_boost(**attrs):
    expr = "SET " + ", ".join(f"{k} = :{k}" for k in attrs)
    boto3.client("dynamodb").update_item(TableName="t", Key={"pk": {"S": "USER#alice"}, "sk": {"S": "BUDGET"}},
                                         UpdateExpression=expr, ExpressionAttributeValues={f":{k}": v for k, v in attrs.items()})


@pytest.mark.parametrize("item", [
    {"budget_usd": 10.0, "boost_usd": 5.0, "boost_until": FAR, "boost_period": "Weekly"},
    {"budget_usd": 10.0, "boost_usd": 5.0, "boost_until": FAR, "boost_period": 3.0},
])
def test_a_hand_edited_boost_lifts_no_cap_at_the_gate(item):
    # A bad period used to count as Monthly at the gate while the dashboard called the boost a hand edit.
    assert not policy.boost_live(policy.limits(item), 0)
    caps = policy.resolve(policy.limits(item), {}, [], {"Monthly": None, "Daily": None, "Hourly": None, "rpm": None}, "", 0)["caps"]
    assert caps["Monthly"]["cap"] == 10.0 and not caps["Monthly"]["boost"]


def test_limits_stays_idempotent_on_a_voided_boost():
    item = {"boost_usd": 5.0, "boost_until": FAR, "boost_period": "Weekly"}
    once = policy.limits(item)
    assert policy.limits(once) == once and once["boost_usd"] == 0.0


def test_a_live_looking_hand_edited_boost_is_unreadable_not_live(api):  # noqa: F811
    set_boost(boost_usd={"N": "5"}, boost_until={"N": str(FAR)}, boost_period={"S": "Weekly"})
    d = call(api, "GET", "/api/v2/users/alice")[1]
    assert d["boost"] is None and d["boost_state"] == "unreadable"
    assert all(b["user"] != "alice" for b in call(api, "GET", "/api/v2/policy")[1]["boosts"])


def test_a_reason_alone_is_unreadable(api):  # noqa: F811
    set_boost(boost_reason={"S": "left over"})
    assert call(api, "GET", "/api/v2/users/alice")[1]["boost_state"] == "unreadable"


def test_the_budgets_list_reads_the_clock_once(api, monkeypatch):  # noqa: F811
    seen = []
    real = policy.resolve
    monkeypatch.setattr(policy, "resolve", lambda *a, **k: seen.append(a[5] if len(a) > 5 else k.get("now")) or real(*a, **k))
    set_boost(boost_usd={"N": "5"}, boost_until={"N": str(FAR)}, boost_period={"S": "Monthly"})
    call(api, "GET", "/api/v2/policy")
    assert seen and all(t is not None for t in seen)


def test_losing_an_end_boost_race_is_a_404(api, monkeypatch):  # noqa: F811
    set_boost(boost_usd={"N": "5"}, boost_until={"N": str(FAR)}, boost_period={"S": "Monthly"}, boost_reason={"S": "r"})
    v = call(api, "GET", "/api/v2/users/alice")[1]["version"]
    real = api.ddb.transact_write_items

    def racer(**kw):  # another admin's End boost lands first
        boto3.client("dynamodb").update_item(TableName="t", Key={"pk": {"S": "USER#alice"}, "sk": {"S": "BUDGET"}},
                                             UpdateExpression="REMOVE boost_usd, boost_period, boost_until, boost_reason")
        return real(**kw)
    monkeypatch.setattr(api.ddb, "transact_write_items", racer)
    s, r = call(api, "DELETE", "/api/v2/users/alice/boost", {"reason": "done", "version": v})
    assert s == 404 and "no boost to end" in str(r)
