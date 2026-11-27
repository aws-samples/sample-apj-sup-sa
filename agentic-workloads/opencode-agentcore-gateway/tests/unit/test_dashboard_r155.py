# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""r155 /code-review fixes: one rule for a hand-edited (voided) boost, one clock per user page."""
import sys
from pathlib import Path

import boto3
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lambda"))
import policy  # noqa: E402

from test_dashboard_api import api, call  # noqa: E402,F401 - the fixture and the call helper


@pytest.mark.parametrize("raw,voided", [
    ({"boost_usd": {"N": "5"}, "boost_until": {"N": "1000"}}, False),
    ({"boost_usd": {"S": "5"}, "boost_until": {"S": "1000"}}, False),   # numeric strings: limits reads them as numbers
    ({"boost_usd": {"N": "-5"}, "boost_until": {"N": "1000"}}, True),
    ({"boost_usd": {"N": "5"}, "boost_until": {"N": "99999999999"}}, True),
    ({"boost_usd": {"S": "lots"}}, True),
    ({"boost_period": {"S": "Weekly"}}, True),
    ({"boost_period": {"S": "Daily"}, "boost_usd": {"N": "5"}, "boost_until": {"N": "1000"}}, False),
    ({"boost_period": {"S": "Daily"}}, True),                            # r156: a period alone is not a whole boost
    ({"boost_usd": {"N": "0"}, "boost_until": {"N": "1000"}}, True),     # r156: the dashboard never writes a 0 amount
    ({"boost_usd": {"N": "5"}}, True),                                   # r156: an amount with no end
    ({"boost_until": {"N": "1000"}}, True),
    ({}, False),
    ({"boost_usd": {"N": "5"}, "boost_until": {"N": "1000"}, "boost_reason": {"N": "7"}}, True),  # r158: reason not text
])
def test_limits_is_the_one_hand_edit_rule(raw, voided):
    # The dashboard's own classifier, long after any end: a hand edit is unreadable, a whole boost expired.
    import dashboard_api
    state = dashboard_api.boost_state(policy.plain(raw), now=10 ** 12)
    assert state == (None if raw == {} else "unreadable" if voided else "expired")


def set_boost(**attrs):
    expr = "SET " + ", ".join(f"{k} = :{k}" for k in attrs)
    boto3.client("dynamodb").update_item(TableName="t", Key={"pk": {"S": "USER#alice"}, "sk": {"S": "BUDGET"}},
                                         UpdateExpression=expr, ExpressionAttributeValues={f":{k}": v for k, v in attrs.items()})


def test_a_string_typed_boost_that_ended_is_expired_not_unreadable(api):  # noqa: F811
    # policy.limits reads a numeric string as its number, so the gateway honoured it while live; once ended it is expired.
    set_boost(boost_usd={"S": "5"}, boost_until={"S": "1000"}, boost_period={"S": "Monthly"})
    assert call(api, "GET", "/api/v2/users/alice")[1]["boost_state"] == "expired"


def test_ending_a_voided_boost_audits_the_stored_text(api):  # noqa: F811
    # An end past BOOST_UNTIL_MAX is voided; the audit keeps what was stored, not the cleaned 0.0.
    set_boost(boost_usd={"N": "5"}, boost_until={"N": "99999999999"})
    s, r = call(api, "DELETE", "/api/v2/users/alice/boost", {"reason": "clean up"})
    assert s == 200 and r["audit"]["before"]["boost_until"] == '<N> "99999999999"'
