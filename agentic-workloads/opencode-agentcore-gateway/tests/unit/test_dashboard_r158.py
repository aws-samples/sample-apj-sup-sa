# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""r158 /code-review fixes: a reason that is not text is a hand edit; End boost on a gone boost is a 404 first."""
import boto3

from test_dashboard_api import api, call  # noqa: F401 - the fixture and the call helper

FAR = 4102444800


def set_boost(**attrs):
    expr = "SET " + ", ".join(f"{k} = :{k}" for k in attrs)
    boto3.client("dynamodb").update_item(TableName="t", Key={"pk": {"S": "USER#alice"}, "sk": {"S": "BUDGET"}},
                                         UpdateExpression=expr, ExpressionAttributeValues={f":{k}": v for k, v in attrs.items()})


def test_a_boost_with_a_reason_that_is_not_text_is_unreadable(api):  # noqa: F811
    set_boost(boost_usd={"N": "5"}, boost_until={"N": str(FAR)}, boost_period={"S": "Monthly"}, boost_reason={"N": "7"})
    d = call(api, "GET", "/api/v2/users/alice")[1]
    assert d["boost"] is None and d["boost_state"] == "unreadable"
    assert all(b["user"] != "alice" for b in call(api, "GET", "/api/v2/policy")[1]["boosts"])


def test_ending_a_boost_that_is_gone_is_a_404_even_with_a_stale_version(api):  # noqa: F811
    s, r = call(api, "DELETE", "/api/v2/users/alice/boost", {"reason": "done", "version": "stale"})
    assert s == 404 and "no boost to end" in str(r)


def test_a_boost_cannot_lift_a_zero_cap(api):  # noqa: F811
    # r161 /code-review: a $0 cap is a deliberate block (typed confirmation); a boost must not undo it.
    v = call(api, "GET", "/api/v2/users/alice")[1]["version"]
    assert call(api, "PUT", "/api/v2/users/alice/limits", {"daily_usd": 0, "version": v, "reason": "block"})[0] == 200
    s, r = call(api, "POST", "/api/v2/users/alice/boost", {"usd": 5, "period": "Daily", "hours": 1, "reason": "x"})
    assert s == 400 and r.get("field") == "period" and "$0" in r.get("error", str(r))


def test_a_live_boost_does_not_lift_a_cap_set_to_zero_later():
    # r162 /code-review: the rule lives in policy.resolve, so the gateway keeps a $0 cap set under a live boost.
    import policy
    env = {"Monthly": None, "Daily": None, "Hourly": None, "rpm": None}
    b = {"daily_usd": 0.0, "boost_usd": 5.0, "boost_until": FAR, "boost_period": "Daily", "boost_reason": "r"}
    c = policy.resolve(b, {}, [], env, {}, now=1000.0)["caps"]["Daily"]  # a real clock value (0 reads as "now")
    assert c == {"cap": 0.0, "base": 0.0, "source": "user", "boost": 0.0}
    assert policy.at_cap(0.0, c["cap"])


def test_a_boost_cannot_lift_a_group_zero_cap(api):  # noqa: F811
    def group_version():
        return next((g["version"] for g in call(api, "GET", "/api/v2/groups")[1]["groups"] if g["group"] == "ow"), None)
    assert call(api, "PUT", "/api/v2/policy/groups/ow", {"daily_usd": 10, "version": group_version()})[0] == 200
    assert call(api, "POST", "/api/v2/users/alice/boost", {"usd": 5, "period": "Daily", "hours": 1, "reason": "x"})[0] == 200
    s, _ = call(api, "PUT", "/api/v2/policy/groups/ow", {"daily_usd": 0, "reason": "freeze", "version": group_version()})
    assert s == 200
    d = call(api, "GET", "/api/v2/users/alice")[1]
    assert d["boost"]["in_effect"] is False and next(x for x in d["limits"] if x["period"] == "Daily")["cap"] == 0
    s, r = call(api, "POST", "/api/v2/users/alice/boost", {"usd": 5, "period": "Daily", "hours": 1, "reason": "x"})
    assert s == 400 and r.get("field") == "period"


def test_a_boost_held_back_by_a_zero_cap_applies_again_when_the_cap_is_raised(api):  # noqa: F811
    # r164 /code-review: one rule for every source of a $0 cap: the boost is held back, not ended (End boost ends it).
    v = call(api, "GET", "/api/v2/users/alice")[1]["version"]
    assert call(api, "PUT", "/api/v2/users/alice/limits", {"daily_usd": 10, "version": v})[0] == 200
    assert call(api, "POST", "/api/v2/users/alice/boost", {"usd": 5, "period": "Daily", "hours": 2, "reason": "x"})[0] == 200
    assert call(api, "PUT", "/api/v2/users/alice/limits", {"daily_usd": 0, "reason": "block"})[0] == 200
    d = call(api, "GET", "/api/v2/users/alice")[1]
    day = next(x for x in d["limits"] if x["period"] == "Daily")
    assert day["cap"] == 0 and day["boost"] == 0 and d["boost"]["in_effect"] is False
    assert call(api, "PUT", "/api/v2/users/alice/limits", {"daily_usd": 10})[0] == 200
    day = next(x for x in call(api, "GET", "/api/v2/users/alice")[1]["limits"] if x["period"] == "Daily")
    assert day["cap"] == 15 and day["boost"] == 5


def test_a_zero_cap_on_one_period_leaves_a_boost_on_another_in_effect(api):  # noqa: F811
    v = call(api, "GET", "/api/v2/users/alice")[1]["version"]
    assert call(api, "PUT", "/api/v2/users/alice/limits", {"daily_usd": 10, "hourly_usd": 2, "version": v})[0] == 200
    assert call(api, "POST", "/api/v2/users/alice/boost", {"usd": 5, "period": "Daily", "hours": 2, "reason": "x"})[0] == 200
    assert call(api, "PUT", "/api/v2/users/alice/limits", {"hourly_usd": 0, "reason": "block"})[0] == 200
    d = call(api, "GET", "/api/v2/users/alice")[1]
    assert d["boost"]["in_effect"] is True and next(x for x in d["limits"] if x["period"] == "Daily")["cap"] == 15


def test_a_policy_save_naming_no_field_is_a_400(api):  # noqa: F811
    # r169 /code-review: put_policy wrote a new version and an audit row for a body with no field.
    v = call(api, "GET", "/api/v2/policy")[1]["org"]["version"]
    s, r = call(api, "PUT", "/api/v2/policy/org", {"version": v})
    assert s == 400 and "nothing to change" in str(r)
