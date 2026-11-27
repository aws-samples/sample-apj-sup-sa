# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""r152 /code-review fixes, against the same moto table as test_dashboard_api.py."""
import boto3

from test_dashboard_api import api, call  # noqa: F401 - the fixture and the call helper


def test_a_hand_edited_boost_is_offered_for_removal_on_the_user_page(api):  # noqa: F811
    # End boost accepted a hand-edited boost, but the page offered no button for one.
    boto3.client("dynamodb").update_item(TableName="t", Key={"pk": {"S": "USER#alice"}, "sk": {"S": "BUDGET"}},
                                         UpdateExpression="SET boost_usd = :u", ExpressionAttributeValues={":u": {"S": "lots"}})
    d = call(api, "GET", "/api/v2/users/alice")[1]
    assert d["boost"] is None and d["boost_state"] == "unreadable"
    assert call(api, "GET", "/api/v2/users/bob")[1]["boost_state"] is None


def test_an_expired_boost_is_expired_not_unreadable(api):  # noqa: F811
    # r153 /code-review: every expired boost was reported as boost values that cannot be read.
    boto3.client("dynamodb").update_item(TableName="t", Key={"pk": {"S": "USER#alice"}, "sk": {"S": "BUDGET"}},
                                         UpdateExpression="SET boost_usd = :u, boost_until = :t, boost_period = :p",
                                         ExpressionAttributeValues={":u": {"N": "5"}, ":t": {"N": "1000"}, ":p": {"S": "Monthly"}})
    d = call(api, "GET", "/api/v2/users/alice")[1]
    assert d["boost"] is None and d["boost_state"] == "expired"


import pytest  # noqa: E402


@pytest.mark.parametrize("usd,until", [("-5", "1000"), ("5", "99999999999")])
def test_a_voided_boost_is_unreadable_not_expired(api, usd, until):  # noqa: F811
    # r154 /code-review: a negative amount or an end past BOOST_UNTIL_MAX (both voided by policy.limits) read as expired.
    boto3.client("dynamodb").update_item(TableName="t", Key={"pk": {"S": "USER#alice"}, "sk": {"S": "BUDGET"}},
                                         UpdateExpression="SET boost_usd = :u, boost_until = :t",
                                         ExpressionAttributeValues={":u": {"N": usd}, ":t": {"N": until}})
    assert call(api, "GET", "/api/v2/users/alice")[1]["boost_state"] == "unreadable"


def test_a_racing_second_delete_of_a_versionless_policy_is_a_404_with_one_audit_row(api, monkeypatch):  # noqa: F811
    # attribute_not_exists(version) also held once the item was gone, so a racing second delete succeeded and wrote
    # a second audit row.
    boto3.client("dynamodb").put_item(TableName="t", Item={"pk": {"S": "POLICY"}, "sk": {"S": "GROUP#hand"}, "monthly_usd": {"N": "5"}})
    raw = api.get("POLICY", "GROUP#hand", consistent=True)
    assert call(api, "DELETE", "/api/v2/policy/groups/hand", {})[0] == 200
    real = api.get
    # the racer read the item before the first delete landed
    stale = [raw]  # only the racer's first read is stale; its 409 re-read sees the item gone

    def get(pk, sk, **k):
        return stale.pop() if (pk, sk) == ("POLICY", "GROUP#hand") and stale else real(pk, sk, **k)
    monkeypatch.setattr(api, "get", get)
    assert call(api, "DELETE", "/api/v2/policy/groups/hand", {})[0] == 404  # nothing is left: 404, as for a stale client
    monkeypatch.setattr(api, "get", real)
    rows = call(api, "GET", "/api/v2/audit", q={"target": "group:hand"})[1]["rows"]
    assert [r["action"] for r in rows].count("delete_group_policy") == 1
