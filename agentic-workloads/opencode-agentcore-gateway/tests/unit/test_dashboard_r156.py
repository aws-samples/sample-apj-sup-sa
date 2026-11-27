# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""r156 /code-review fixes: a boost that is not whole is unreadable, and the limit rows use the page's clock."""
import sys
from pathlib import Path

import boto3
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lambda"))
import policy  # noqa: E402

from test_dashboard_api import api, call  # noqa: E402,F401 - the fixture and the call helper


@pytest.mark.parametrize("attrs", [
    {":u": {"N": "0"}, ":t": {"N": "1000"}},
    {":u": {"N": "5"}},
    {":t": {"N": "1000"}},
])
def test_a_boost_that_is_not_whole_is_unreadable(api, attrs):  # noqa: F811
    # boost_state called these "unreadable" while boost_voided called them clean: the two rules now agree.
    names = {":u": "boost_usd", ":t": "boost_until"}
    boto3.client("dynamodb").update_item(TableName="t", Key={"pk": {"S": "USER#alice"}, "sk": {"S": "BUDGET"}},
                                         UpdateExpression="SET " + ", ".join(f"{names[k]} = {k}" for k in attrs),
                                         ExpressionAttributeValues=attrs)
    assert call(api, "GET", "/api/v2/users/alice")[1]["boost_state"] == "unreadable"


def test_the_limit_rows_use_the_pages_clock(api, monkeypatch):  # noqa: F811
    # limits_for resolved the boost with its own time.time(), so a boost could be in the limit rows and not in "boost".
    seen = []
    real = policy.resolve
    monkeypatch.setattr(policy, "resolve", lambda *a, **k: seen.append(a[5] if len(a) > 5 else k.get("now")) or real(*a, **k))
    call(api, "GET", "/api/v2/users/alice")
    call(api, "GET", "/api/v2/users")
    assert seen and all(t is not None for t in seen)


def test_limits_for_reads_the_cleaned_budget_item(api):  # noqa: F811
    # r157 /code-review: limits_for took both the raw item and an optional cleaned copy; it now takes only the copy.
    rows, _ = api.limits_for(policy.limits({"budget_usd": 7.0}), [], {"Monthly": None, "Daily": None, "Hourly": None},
                             [75, 95], __import__("time").gmtime(), pols={})
    assert rows[0]["cap"] == 7.0
