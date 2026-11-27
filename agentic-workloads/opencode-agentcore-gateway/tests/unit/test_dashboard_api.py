# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Dashboard API v2 against a moto DynamoDB table: reads, writes with audit, version conflicts, roles, CSRF."""
import json
import os
import sys
import time
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

MOTO_KEY = "x"  # moto accepts any key; not a credential

os.environ.update(TABLE="t", AWS_DEFAULT_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY=MOTO_KEY,
                  GROUP_MODELS=json.dumps({"ow": ["ow/*"], "acme": ["acme-*"]}), DEFAULT_BUDGET_USD="50",
                  MODEL_CATALOG=json.dumps([{"id": "acme-chat-1", "display_name": "Acme Chat 1"}, {"id": "ow/kimi-k3", "display_name": "Kimi K3"}]))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lambda"))

ADMIN = {"email": "a@x", "sub": "s", "groups": ["adm"], "role": "admin"}
VIEWER = {**ADMIN, "role": "viewer"}


@pytest.fixture()
def api():
    os.environ["TABLE"] = "t"
    with mock_aws():
        c = boto3.client("dynamodb")
        c.create_table(TableName="t", KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
                       AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}, {"AttributeName": "sk", "AttributeType": "S"}],
                       BillingMode="PAY_PER_REQUEST")
        g = time.gmtime()
        m, d = time.strftime("%Y-%m", g), time.strftime("%Y-%m-%d", g)
        put = lambda pk, sk, **a: c.put_item(TableName="t", Item={"pk": {"S": pk}, "sk": {"S": sk}, **a})  # noqa: E731
        put("USER#alice", f"MONTH#{m}", spend_usd={"N": "40"}, requests={"N": "10"}, **{"m|kimi-k3|usd": {"N": "30"}, "m|kimi-k3|req": {"N": "6"},
            "m|acme-chat-1|usd": {"N": "10"}, "m|acme-chat-1|req": {"N": "4"}}, updated_at={"S": "2026-10-27T00:00:00Z"})
        put("USER#alice", f"DAY#{d}", spend_usd={"N": "4"}, model_spend_usd={"N": "3"}, requests={"N": "2"})  # $4 web search, $3 kimi-k3 (the MODEL# row)
        put("USER#bob", f"MONTH#{m}", spend_usd={"N": "1"}, requests={"N": "1"})
        put("GROUP#ow", f"MONTH#{m}", spend_usd={"N": "40"}, user_set={"SS": ["alice"]})
        put("USER#alice", "BUDGET", token_groups={"L": [{"S": "ow"}]})  # as the interceptor stores it on her first call
        put("USER#bob", "BUDGET", group={"S": "ow"})  # as Terraform seeds every user in var.users
        put("MODEL#kimi-k3", f"MONTH#{m}", spend_usd={"N": "30"}, requests={"N": "6"})
        put("MODEL#kimi-k3", f"DAY#{d}", spend_usd={"N": "3"}, requests={"N": "1"})
        import importlib
        import policy
        import dashboard_api
        importlib.reload(policy)
        importlib.reload(dashboard_api)
        yield dashboard_api


def seed(*users):
    """BUDGET items as Terraform writes them for users in var.users (writes refuse a user without one)."""
    for u in users:
        boto3.client("dynamodb").put_item(TableName="t", Item={"pk": {"S": f"USER#{u}"}, "sk": {"S": "BUDGET"}, "group": {"S": "ow"}})


def call(api, method, path, body=None, ident=ADMIN, q=None):
    try:
        return api.handle(method, path, q or {}, body or {}, ident)
    except api.ApiError as e:
        return e.status, e.body


def test_reads(api):
    s, u = call(api, "GET", "/api/v2/users")
    a = next(x for x in u["users"] if x["user"] == "alice")
    assert s == 200 and a["groups"] == ["ow"] and a["mtd"]["usd"] == 40
    mon = next(r for r in a["limits"] if r["period"] == "Monthly")
    assert mon["cap"] == 50 and mon["source"] == "default" and mon["state"] == "warn" and a["state"] == "warn"
    s, o = call(api, "GET", "/api/v2/overview")
    assert o["kpis"]["mtd_usd"] == 41 and o["model_mix"][0]["model"] == "kimi-k3" and o["daily"][0]["by_model"] == {"kimi-k3": 3, "(unattributed)": 4}  # alice's DAY spend_usd is web search
    s, us = call(api, "GET", "/api/v2/usage", q={"group_by": "model", "granularity": "month"})
    # bob's $1 has no per-model counters: kept under (unattributed) instead of vanishing from the model view
    assert {r["key"]: r["usd"] for r in us["table"]} == {"kimi-k3": 30, "acme-chat-1": 10, "(unattributed)": 1}
    s, det = call(api, "GET", "/api/v2/users/alice")
    assert det["by_model_mtd"][0] == {"model": "kimi-k3", "usd": 30, "input": 0, "output": 0, "requests": 6}
    assert call(api, "GET", "/api/v2/users/nobody")[0] == 404


def test_group_policy_write_conflict_and_audit(api):
    s, r = call(api, "PUT", "/api/v2/policy/groups/ow", {"daily_usd": 5, "models": ["ow/kimi-k3"], "version": None})
    assert s == 200 and r["item"]["daily_usd"] == 5 and r["audit"]["action"] == "set_group_policy"
    v = r["item"]["version"]
    assert call(api, "PUT", "/api/v2/policy/groups/ow", {"daily_usd": 6, "version": "stale"})[0] == 409
    s, r = call(api, "PUT", "/api/v2/policy/groups/ow", {"daily_usd": 6, "version": v})
    assert s == 200 and r["item"]["models"] == ["ow/kimi-k3"]  # unspecified fields kept
    s, u = call(api, "GET", "/api/v2/users")
    a = next(x for x in u["users"] if x["user"] == "alice")
    assert next(x for x in a["limits"] if x["period"] == "Daily") ["source"] == "group:ow"
    s, au = call(api, "GET", "/api/v2/audit")
    assert [x["action"] for x in au["rows"]] == ["set_group_policy", "set_group_policy"]
    assert call(api, "PUT", "/api/v2/policy/groups/ow", {"models": ["gpt-9"], "version": r["item"]["version"]})[0] == 400


def test_user_limits_boost_and_validation(api):
    assert call(api, "PUT", "/api/v2/users/alice/limits", {"daily_usd": 0})[0] == 400  # reason required for 0
    s, r = call(api, "PUT", "/api/v2/users/alice/limits", {"daily_usd": 0, "reason": "paused"})
    assert s == 200 and r["item"] == {"daily_usd": 0}
    # A $0 cap is a block: a boost does not lift it (r161), raising the cap does.
    assert call(api, "POST", "/api/v2/users/alice/boost", {"usd": 10, "period": "Daily", "hours": 2, "reason": "demo"})[0] == 400
    assert call(api, "PUT", "/api/v2/users/alice/limits", {"daily_usd": 1})[0] == 200
    s, r = call(api, "POST", "/api/v2/users/alice/boost", {"usd": 10, "period": "Daily", "hours": 2, "reason": "demo"})
    assert s == 200
    s, d = call(api, "GET", "/api/v2/users/alice")
    day = next(x for x in d["limits"] if x["period"] == "Daily")
    assert day["cap"] == 11 and day["boost"] == 10 and d["boost"]["reason"] == "demo"
    assert call(api, "DELETE", "/api/v2/users/alice/boost", {})[0] == 200
    assert call(api, "PUT", "/api/v2/users/alice/limits", {"daily_usd": -1})[0] == 400
    assert call(api, "PUT", "/api/v2/users/al!ce/limits", {"daily_usd": 1})[0] == 400


def test_viewer_cannot_write(api):
    assert call(api, "PUT", "/api/v2/policy/org", {"monthly_usd": 1}, ident=VIEWER)[0] == 403
    assert call(api, "GET", "/api/v2/policy", ident=VIEWER)[0] == 200


def test_csrf_and_origin_enforced_in_handler(api, monkeypatch):
    import dashboard
    monkeypatch.setattr(dashboard, "PRIVATE_ROLE", "admin")  # dashboard_private_role = "admin"
    ev = lambda h: {"path": "/api/v2/policy/org", "httpMethod": "PUT", "headers": h, "body": '{"rpm": 50}'}  # noqa: E731
    base = {"host": "dash.example", "content-type": "application/json"}
    assert dashboard.lambda_handler(ev(base), None)["statusCode"] == 403
    assert dashboard.lambda_handler(ev({**base, "x-ocgw-csrf": "1", "origin": "https://evil.example"}), None)["statusCode"] == 403
    assert dashboard.lambda_handler(ev({**base, "x-ocgw-csrf": "1", "origin": "https://dash.example"}), None)["statusCode"] == 200
    monkeypatch.setattr(dashboard, "PRIVATE_ROLE", "viewer")  # the default: an IAM caller cannot write
    assert dashboard.lambda_handler(ev({**base, "x-ocgw-csrf": "1", "origin": "https://dash.example"}), None)["statusCode"] == 403


@pytest.mark.parametrize("role", ["admin", "viewer"])
def test_head_on_the_api_is_405_not_a_csrf_refusal(api, monkeypatch, role):
    """r127 review: HEAD is not a write, so a health check gets 405 rather than 403 'missing CSRF header'."""
    import dashboard
    monkeypatch.setattr(dashboard, "PRIVATE_ROLE", role)
    r = dashboard.lambda_handler({"path": "/api/v2/overview", "httpMethod": "HEAD", "headers": {"host": "dash.example"}}, None)
    assert r["statusCode"] == 405 and "CSRF" not in (r.get("body") or "")


@pytest.mark.parametrize("sent", [{"models": ["ow/kimi-k3"]}, {"rpm": 40}, {"daily_usd": 9}, {"models": None}])
def test_group_policy_partial_save_keeps_other_fields(api, sent):
    s, r = call(api, "PUT", "/api/v2/policy/groups/ow", {"monthly_usd": 100, "rpm": 30, "hourly_usd": 2, "version": None})
    assert s == 200
    v = r["item"]["version"]
    s, r = call(api, "PUT", "/api/v2/policy/groups/ow", {**sent, "version": v})
    assert s == 200
    item = r["item"]
    for k, old in {"monthly_usd": 100, "rpm": 30, "hourly_usd": 2}.items():
        assert item.get(k) == sent.get(k, old)


def test_group_policy_explicit_null_clears_field(api):
    v = call(api, "PUT", "/api/v2/policy/groups/ow", {"monthly_usd": 100, "rpm": 30, "version": None})[1]["item"]["version"]
    item = call(api, "PUT", "/api/v2/policy/groups/ow", {"rpm": None, "version": v})[1]["item"]
    assert "rpm" not in item and item["monthly_usd"] == 100


class _FakeGw:
    """The gateway's rate limits as the service holds them: one limit per set of dimension keys (a second create on the
    same keys is refused, as the live API does), entries updated in place."""

    def __init__(self, limits=()):
        self.calls, self.gets, self.limits = [], [], [dict(d) for d in limits]

    def list_gateway_rate_limits(self, **kw):
        return {"rateLimits": [{"rateLimitId": d["rateLimitId"], "description": d.get("description", "")} for d in self.limits]}

    def get_gateway_rate_limit(self, **kw):
        self.gets.append(kw["rateLimitId"])
        return next(d for d in self.limits if d["rateLimitId"] == kw["rateLimitId"])


def _entry(api, value, rpm=None, tpm=None):
    return {"dimensions": {"$.context.jwt.username": value},
            **({"requests": [{"rate": rpm, "period": "minute"}]} if rpm else {}), **({"tokens": [{"rate": tpm, "period": "minute"}]} if tpm else {})}


def _gw(api, monkeypatch, **kw):
    """The one limit Terraform makes: "*" plus five users' rpm_overrides entries."""
    entries = [_entry(api, "*", 60, 200000)] + [_entry(api, f"user{i}", 9) for i in range(5)]
    gw = _FakeGw(limits=[{"rateLimitId": "rl-shared", "description": "ocgw per user #abc", "status": "ACTIVE",
                          "dimensionKeys": ["$.context.jwt.username"], "entries": entries}], **kw)
    monkeypatch.setattr(api, "GATEWAY_ID", "gw-1")
    monkeypatch.setattr(api, "acc", lambda: gw)
    return gw


def _values(gw):
    return sorted(next(iter(e["dimensions"].values())) for e in gw.limits[0]["entries"])


def _audits(api):
    return boto3.client("dynamodb").query(TableName="t", KeyConditionExpression="pk = :p", ExpressionAttributeValues={":p": {"S": "AUDIT"}})["Items"]


@pytest.mark.parametrize("q", [{}, {"group_by": "user"}, {"group_by": "group"}, {"group_by": "tag"}, {"granularity": "month"},
                               {"user": "alice"}, {"model": "kimi-k3"}, {"group": "ow"}])
def test_usage_single_scan_matches_items(api, monkeypatch, q):
    scans = []
    real = api._paginate
    monkeypatch.setattr(api, "_paginate", lambda op, **kw: scans.append(op) or real(op, **kw))
    s, r = call(api, "GET", "/api/v2/usage", q=q)
    # One table scan per request, whatever the range (it used to be one per day bucket); output was checked equal to
    # the per-bucket version on 20 filter combinations when this changed. A filter on one user or group is a single
    # Query of that pk instead.
    single = "user" in q or "group" in q
    assert s == 200 and scans.count("scan") == (0 if single else 1) and (not single or scans.count("query") == 1), (s, r, scans)
    assert r["total"]["usd"] == pytest.approx(sum(row["usd"] for row in r["table"]))


def test_usage_from_after_to_is_rejected(api):
    s, _ = call(api, "GET", "/api/v2/usage", q={"from": "2026-10-20", "to": "2026-10-01"})
    assert s == 400


@pytest.mark.parametrize("raw,ok", [("dev%40example.com", True), ("a%2Bb", True), ("dev@example.com", True),
                                    ("a%2Fb", False), ("..%2F..", False), ("%00", False), ("x%20y", False)])
def test_encoded_user_names_are_decoded_per_segment(api, raw, ok):
    seed("dev@example.com", "a+b")
    s, _ = call(api, "PUT", f"/api/v2/users/{raw}/limits", {"daily_usd": 5, "reason": "t"})
    assert (s == 200) == ok, s


@pytest.mark.parametrize("where", ["token", "token_empty", "budget_seed", "rollup_no_budget", "none"])
def test_user_groups_one_rule(api, where):
    c = boto3.client("dynamodb")
    msk = time.strftime("MONTH#%Y-%m", time.gmtime())
    if where == "token":
        c.put_item(TableName="t", Item={"pk": {"S": "USER#dave"}, "sk": {"S": "BUDGET"}, "group": {"S": "g3"}, "token_groups": {"L": [{"S": "g1"}]}})
    elif where == "token_empty":
        c.put_item(TableName="t", Item={"pk": {"S": "USER#dave"}, "sk": {"S": "BUDGET"}, "group": {"S": "g3"}, "token_groups": {"L": []}})
    elif where == "budget_seed":
        c.put_item(TableName="t", Item={"pk": {"S": "USER#dave"}, "sk": {"S": "BUDGET"}, "group": {"S": "g3"}})
    elif where == "rollup_no_budget":
        c.put_item(TableName="t", Item={"pk": {"S": "GROUP#g2"}, "sk": {"S": msk}, "user_set": {"SS": ["dave"]}})
    want = {"token": ["g1"], "token_empty": None, "budget_seed": ["g3"], "rollup_no_budget": None, "none": None}[where]
    assert api.user_groups().get("dave", []) == (want or [])
    # The list and the user page agree for users with a BUDGET item.
    if where in ("token", "token_empty", "budget_seed"):
        assert api.user_groups(only="dave")["dave"] == (want or [])


@pytest.mark.parametrize("dim,want", [(None, 100.0), ("team", 100.0), ("project", 100.0), ("environment", 100.0), ("bogus", None)])
def test_usage_by_tag_counts_one_dimension(api, dim, want):
    c = boto3.client("dynamodb")
    m = time.strftime("MONTH#%Y-%m", time.gmtime())
    for k, v in (("team", "a"), ("team", "b"), ("project", "p"), ("environment", "prod")):
        usd = 50 if k == "team" else 100
        c.put_item(TableName="t", Item={"pk": {"S": f"TAG#{k}={v}"}, "sk": {"S": m}, "spend_usd": {"N": str(usd)}})
    q = {"group_by": "tag", "granularity": "month", **({"dim": dim} if dim else {})}
    s, r = call(api, "GET", "/api/v2/usage", q=q)
    if want is None:
        assert s == 400
    else:
        assert s == 200 and r["total"]["usd"] == pytest.approx(want), r["table"]
        assert all(row["key"].startswith((dim or "team") + "=") for row in r["table"])


def test_group_models_store_alias_targets(api):
    api.MODEL_ALIASES = {"alias-ow-kimi-k3": "ow/kimi-k3"}
    s, r = call(api, "PUT", "/api/v2/policy/groups/ow", {"models": ["alias-ow-kimi-k3", "acme-chat-1"], "reason": "t"})
    assert s == 200 and sorted(r["item"]["models"]) == ["acme-chat-1", "ow/kimi-k3"]
    s, _ = call(api, "PUT", "/api/v2/policy/groups/ow", {"models": ["not-a-model"], "reason": "t", "version": r["item"]["version"]})
    assert s == 400


@pytest.mark.parametrize("q,ok", [
    ({"group_by": "user", "group": "ow"}, False), ({"group_by": "user", "tag": "team=a"}, False),
    ({"group_by": "group", "user": "alice"}, False), ({"group_by": "tag", "user": "alice"}, False),
    ({"group_by": "tag", "group": "ow"}, False), ({"group_by": "group", "tag": "team=a"}, False),
    ({"group_by": "user", "user": "alice"}, True), ({"group_by": "group", "group": "ow"}, True),
    ({"group_by": "tag", "tag": "team=a"}, True), ({"group_by": "model", "group": "ow"}, True),
    ({"group_by": "model", "user": "alice"}, True), ({"group_by": "model", "tag": "team=a"}, True)])
def test_usage_rejects_unanswerable_filter_combinations(api, q, ok):
    s, r = call(api, "GET", "/api/v2/usage", q=q)
    assert (s == 200) == ok, (s, r)


@pytest.mark.parametrize("period,ok", [("Monthly", True), ("Daily", False), ("Hourly", False)])
def test_boost_needs_a_cap_on_that_period(api, period, ok):
    # bob has the env default monthly cap and no daily or hourly cap
    s, r = call(api, "POST", "/api/v2/users/bob/boost", {"usd": 10, "period": period, "hours": 2, "reason": "t"})
    assert (s == 200) == ok, r
    if not ok:
        assert r.get("field") == "period"


def test_boost_whose_cap_is_removed_later_is_shown_as_having_no_effect(api):
    v = call(api, "GET", "/api/v2/users/bob")[1]["version"]
    assert call(api, "PUT", "/api/v2/users/bob/limits", {"daily_usd": 10, "version": v})[0] == 200
    assert call(api, "POST", "/api/v2/users/bob/boost", {"usd": 20, "period": "Daily", "hours": 2, "reason": "t"})[0] == 200
    s, d = call(api, "GET", "/api/v2/users/bob")
    daily = next(x for x in d["limits"] if x["period"] == "Daily")
    assert d["boost"]["in_effect"] is True and daily["cap"] == 30 and daily["boost"] == 20
    assert call(api, "PUT", "/api/v2/users/bob/limits", {"daily_usd": None, "version": d["version"]})[0] == 200
    s, d = call(api, "GET", "/api/v2/users/bob")
    daily = next(x for x in d["limits"] if x["period"] == "Daily")
    assert d["boost"]["in_effect"] is False and daily["cap"] is None and daily["boost"] == 0
    listed = [b for b in call(api, "GET", "/api/v2/policy")[1]["boosts"] if b["user"] == "bob"]
    assert listed and listed[0]["in_effect"] is False


def test_tag_usage_at_day_granularity_is_shown_by_month_with_a_note(api):
    s, r = call(api, "GET", "/api/v2/usage", q={"group_by": "tag", "granularity": "day"})
    assert s == 200 and any("per month" in n for n in r.get("notes", []))


def test_user_groups_single_unseen_user_does_no_scans(monkeypatch):
    import dashboard_api as d
    monkeypatch.setattr(d, "scan_sk", lambda *a, **k: (_ for _ in ()).throw(AssertionError("scanned")))
    item = {"pk": {"S": "USER#new"}, "sk": {"S": "BUDGET"}, "group": {"S": "eng"}}
    assert d.user_groups(only="new", budget_item=item) == {"new": ["eng"]}
    assert d.user_groups(only="new", budget_item={}) == {"new": []}


@pytest.mark.parametrize("q,status", [({"group_by": "tag", "granularity": "month"}, 400), ({"tag": "team=x", "granularity": "month"}, 400),
                                      ({"dim": "team", "granularity": "month"}, 400), ({"group_by": "user", "granularity": "month"}, 200)])
def test_tag_views_say_tags_are_off_when_there_are_no_dimensions(api, monkeypatch, q, status):
    monkeypatch.setattr(api, "TAG_DIMS", [])  # var.tag_dimensions = [] -> TAG_DIMENSIONS="" -> usage_sync writes no TAG#
    s, r = call(api, "GET", "/api/v2/usage", q=q)
    assert s == status and (s == 200 or "tags are off" in r["error"])


def test_tag_dims_parse_is_shared_and_empty_means_off():
    import policy  # noqa: PLC0415
    assert policy.tag_dims({}) == ["team", "project", "environment"]
    assert policy.tag_dims({"TAG_DIMENSIONS": ""}) == [] and policy.tag_dims({"TAG_DIMENSIONS": " cc , env "}) == ["cc", "env"]


def test_overview_daily_includes_web_search_spend(api):
    import time as _t
    day = _t.strftime("DAY#%Y-%m-%d", _t.gmtime())
    boto3.client("dynamodb").put_item(TableName="t", Item={"pk": {"S": "USER#carol"}, "sk": {"S": day}, "spend_usd": {"N": "2.5"}})
    s, o = call(api, "GET", "/api/v2/overview")
    today = next(d for d in o["daily"] if d["date"] == day[4:])
    assert s == 200 and today["by_model"]["(unattributed)"] == 2.5 + 4  # carol's search plus alice's from the fixture


def test_no_group_is_listed_read_only_and_cannot_be_edited(api):
    boto3.client("dynamodb").put_item(TableName="t", Item={"pk": {"S": "GROUP#(no group)"}, "sk": {"S": api.time.strftime("MONTH#%Y-%m", api.time.gmtime())},
                                                           "user_set": {"SS": ["eve"]}})
    s, r = call(api, "GET", "/api/v2/groups")
    ng = next(g for g in r["groups"] if g["group"] == "(no group)")
    assert ng["editable"] is False and all(g["editable"] for g in r["groups"] if g["group"] != "(no group)")
    s, r = call(api, "PUT", "/api/v2/policy/groups/(no group)", {"monthly_usd": 5, "version": None})
    assert s == 400 and "org policy" in r["error"]


def test_hand_written_policy_without_version_can_be_saved(api):
    boto3.client("dynamodb").put_item(TableName="t", Item={"pk": {"S": "POLICY"}, "sk": {"S": "ORG"}, "monthly_usd": {"N": "100"}})
    s, r = call(api, "PUT", "/api/v2/policy/org", {"monthly_usd": 120, "version": None})
    assert s == 200 and r["item"]["monthly_usd"] == 120 and r["item"]["version"]
    assert call(api, "PUT", "/api/v2/policy/org", {"monthly_usd": 130, "version": None})[0] == 409  # now versioned


def test_audit_full_target_is_exact_and_substring_still_works(api):
    seed("alice2")
    for u in ("alice", "alice2"):
        call(api, "PUT", f"/api/v2/users/{u}/limits", {"monthly_usd": 5, "reason": "r"})
    s, r = call(api, "GET", "/api/v2/audit", q={"target": "user:alice"})
    assert s == 200 and {x["target"] for x in r["rows"]} == {"user:alice"}
    s, r = call(api, "GET", "/api/v2/audit", q={"target": "alice"})
    assert {x["target"] for x in r["rows"]} == {"user:alice", "user:alice2"}


def test_org_warn_pct_null_resets_to_default(api):
    s, r = call(api, "PUT", "/api/v2/policy/org", {"warn_pct": [50, 80], "version": None})
    assert s == 200 and r["item"]["warn_pct"] == [50, 80]
    s, r = call(api, "PUT", "/api/v2/policy/org", {"warn_pct": None, "version": r["item"]["version"]})
    assert s == 200 and "warn_pct" not in r["item"]
    assert call(api, "GET", "/api/v2/policy")[1]["org"]["warn_pct"] == [75, 95]


def test_seen_user_with_no_token_groups_is_not_given_the_seeded_group(api):
    item = {"pk": {"S": "USER#zed"}, "sk": {"S": "BUDGET"}, "group": {"S": "eng"}, "token_groups": {"L": []}}
    assert api.user_groups(only="zed", budget_item=item) == {"zed": []}
    assert api.user_groups(only="zed", budget_item={k: v for k, v in item.items() if k != "token_groups"}) == {"zed": ["eng"]}


def test_hand_written_null_and_map_attributes_survive_a_save(api):
    boto3.client("dynamodb").put_item(TableName="t", Item={"pk": {"S": "POLICY"}, "sk": {"S": "ORG"}, "note": {"NULL": True},
                                                           "meta": {"M": {"owner": {"S": "ops"}}}})
    assert call(api, "PUT", "/api/v2/policy/org", {"monthly_usd": 9, "version": None})[0] == 200
    it = boto3.client("dynamodb").get_item(TableName="t", Key={"pk": {"S": "POLICY"}, "sk": {"S": "ORG"}})["Item"]
    assert it["note"] == {"NULL": True} and it["meta"] == {"M": {"owner": {"S": "ops"}}}


def test_a_save_beside_a_hand_written_binary_replies_as_json_and_audits_both_sides_alike(api):
    # r179 /code-review: before was the cleaned item and after the stored one, so untouched attributes looked changed.
    import dashboard
    boto3.client("dynamodb").put_item(TableName="t", Item={"pk": {"S": "POLICY"}, "sk": {"S": "ORG"}, "note": {"B": b"\x00"},
                                                           "rpm": {"N": "2.5"}})
    s, r = call(api, "PUT", "/api/v2/policy/org", {"monthly_usd": 9, "version": None})
    assert s == 200 and json.loads(dashboard.resp(s, r)["body"])["item"]["note"] == "<B>"
    a = r["audit"]
    assert a["before"]["note"] == a["after"]["note"] == "<B>" and a["before"]["rpm"] == a["after"]["rpm"] == 2.5


def test_a_hand_written_binary_version_can_be_sent_back_as_shown(api):
    # r180 /code-review: the reply showed the version as "<B>", and a save sending that back always got 409.
    boto3.client("dynamodb").put_item(TableName="t", Item={"pk": {"S": "POLICY"}, "sk": {"S": "ORG"}, "version": {"B": b"\x01"}})
    assert call(api, "PUT", "/api/v2/policy/org", {"monthly_usd": 9, "version": "stale"})[0] == 409
    s, r = call(api, "PUT", "/api/v2/policy/org", {"monthly_usd": 9, "version": "<B>"})
    assert s == 200 and r["item"]["version"] != "<B>"


def test_a_key_only_policy_item_can_still_be_removed(api):
    # r182 /code-review: dropping pk/sk before the existence check made an item with only its key a 404.
    boto3.client("dynamodb").put_item(TableName="t", Item={"pk": {"S": "POLICY"}, "sk": {"S": "GROUP#ow"}})
    s, _ = call(api, "DELETE", "/api/v2/policy/groups/ow", {"version": None})
    assert s == 200


def test_a_stale_save_gets_the_policy_without_its_key_as_current(api):
    # r183 /code-review: the 409 reply's current is the policy item (API.md), not pk and sk with it.
    boto3.client("dynamodb").put_item(TableName="t", Item={"pk": {"S": "POLICY"}, "sk": {"S": "GROUP#kk"},
                                                           "rpm": {"N": "3"}, "version": {"S": "v1"}})
    s, r = call(api, "PUT", "/api/v2/policy/groups/kk", {"rpm": 4, "version": "old"})
    assert s == 409 and "pk" not in r["current"] and "sk" not in r["current"] and r["current"]["version"] == "v1"


def test_a_hand_written_large_number_version_can_be_sent_back_as_a_json_client_holds_it(api):
    # r183 /code-review: the reply holds the version as a double; a JSON client sends that double back.
    boto3.client("dynamodb").put_item(TableName="t", Item={"pk": {"S": "POLICY"}, "sk": {"S": "ORG"},
                                                           "version": {"N": "12345678901234567890"}})
    s, r = call(api, "PUT", "/api/v2/policy/org", {"monthly_usd": 9, "version": 12345678901234567000})
    assert s == 200


def test_group_totals_note_double_counting(api):
    s, r = call(api, "GET", "/api/v2/usage", q={"group_by": "group", "granularity": "month"})
    assert s == 200 and any("several groups" in n for n in r["notes"])
    s, r = call(api, "GET", "/api/v2/usage", q={"group_by": "user", "granularity": "month"})
    assert not any("several groups" in n for n in r["notes"])


def test_audit_list_is_bounded(api, monkeypatch):
    monkeypatch.setattr(api, "AUDIT_PAGE", 3)
    seed(*(f"u{i}" for i in range(5)))
    for i in range(5):
        call(api, "PUT", f"/api/v2/users/u{i}/limits", {"monthly_usd": 5, "reason": "r"})
    s, r = call(api, "GET", "/api/v2/audit")
    assert s == 200 and len(r["rows"]) == 3


@pytest.mark.parametrize("q,limited", [
    ({}, True), ({"target": "user:u1"}, True), ({"from": "2000-01-01"}, True),
    ({"actor": "admin"}, False), ({"target": "u1"}, False),
])
def test_audit_without_a_paging_filter_lets_dynamodb_stop_at_the_page(api, monkeypatch, q, limited):
    seed("u1")
    call(api, "PUT", "/api/v2/users/u1/limits", {"monthly_usd": 5, "reason": "r"})
    real, seen = api.ddb.query, []
    monkeypatch.setattr(api.ddb, "query", lambda **kw: (seen.append(kw), real(**kw))[1])
    s, r = call(api, "GET", "/api/v2/audit", q=q)
    audit = [kw for kw in seen if kw["ExpressionAttributeValues"][":p"]["S"].startswith("AUDIT")]
    assert s == 200 and audit and all(("Limit" in kw) is limited for kw in audit)
    if limited:
        assert audit[0]["Limit"] == api.AUDIT_PAGE


def test_group_detail_reads_one_group_only(api, monkeypatch):
    orig, seen = api.scan_sk, []
    monkeypatch.setattr(api, "scan_sk", lambda sk, *a, **k: (seen.append(sk), orig(sk, *a, **k))[1])
    s, r = call(api, "GET", "/api/v2/groups/ow")
    assert s == 200 and r["group"] == "ow" and r["members_seen"] == ["alice"] and seen == []
    # A group with no rollup, policy or GROUP_MODELS entry is looked up in the users' BUDGET groups: one filtered,
    # projected read, not every user's whole item.
    real, reads = api._paginate, []
    monkeypatch.setattr(api, "_paginate", lambda op, **kw: (reads.append(kw), real(op, **kw))[1])
    assert call(api, "GET", "/api/v2/groups/nope")[0] == 404 and seen == []
    assert len(reads) == 1 and "ProjectionExpression" in reads[0] and "contains(token_groups, :g)" in reads[0]["FilterExpression"]
    boto3.client("dynamodb").put_item(TableName="t", Item={"pk": {"S": "USER#dana"}, "sk": {"S": "BUDGET"}, "group": {"S": "nope"}})
    assert call(api, "GET", "/api/v2/groups/nope")[0] == 200  # a seeded group with no spend yet is found


def test_audit_filters_apply_before_the_cap(api, monkeypatch):
    monkeypatch.setattr(api, "AUDIT_PAGE", 2)
    seed("old", *(f"n{i}" for i in range(4)))
    call(api, "PUT", "/api/v2/users/old/limits", {"monthly_usd": 5, "reason": "r"}, ident={**ADMIN, "email": "carol@example.com"})
    for i in range(4):
        call(api, "PUT", f"/api/v2/users/n{i}/limits", {"monthly_usd": 5, "reason": "r"})
    s, r = call(api, "GET", "/api/v2/audit", q={"actor": "carol"})
    assert s == 200 and [x["target"] for x in r["rows"]] == ["user:old"]  # older than the 2 newest, still found


@pytest.mark.parametrize("method,path,body", [
    ("PUT", "/api/v2/policy/org", {"monthly_usd": 9, "version": None}),
    ("PUT", "/api/v2/users/alice/limits", {"daily_usd": 3, "reason": "r"}),
])
def test_cancelled_save_for_other_reasons_is_503_not_409(api, monkeypatch, method, path, body):
    from botocore.exceptions import ClientError
    exc = api.ddb.exceptions.TransactionCanceledException(
        {"Error": {"Code": "TransactionCanceledException", "Message": "x"},
         "CancellationReasons": [{"Code": "None"}, {"Code": "ThrottlingError"}, {"Code": "None"}]}, "TransactWriteItems")
    assert isinstance(exc, ClientError)
    monkeypatch.setattr(api.ddb, "transact_write_items", lambda **kw: (_ for _ in ()).throw(exc))
    s, r = call(api, method, path, body)
    assert s == 503 and "ThrottlingError" in r["error"]  # the same message for a policy and a user-limit save


def test_concurrent_user_limit_edit_is_409(api, monkeypatch):
    assert call(api, "PUT", "/api/v2/users/alice/limits", {"monthly_usd": 10, "reason": "r"})[0] == 200
    real_get = api.get
    # The first admin read the item before the second admin's save landed: simulate by returning the stale item.
    stale = real_get("USER#alice", "BUDGET")
    assert call(api, "PUT", "/api/v2/users/alice/limits", {"monthly_usd": 20, "reason": "r"})[0] == 200
    monkeypatch.setattr(api, "get", lambda pk, sk, **k: stale if (pk, sk) == ("USER#alice", "BUDGET") else real_get(pk, sk, **k))
    s, r = call(api, "PUT", "/api/v2/users/alice/limits", {"monthly_usd": 30, "reason": "r"})
    assert s == 409


def test_month_range_over_13_months_is_refused_not_cut(api):
    s, r = call(api, "GET", "/api/v2/usage", q={"granularity": "month", "from": "2025-01-01", "to": "2026-02-01"})
    assert s == 400 and "13 months" in r["error"]
    assert call(api, "GET", "/api/v2/usage", q={"granularity": "month", "from": "2025-02-01", "to": "2026-02-01"})[0] == 200


@pytest.mark.parametrize("to,ok", [("2026-10-10", True), ("2026-10-11", False)])  # 40 and 41 days from 2026-09-01
def test_day_range_is_at_most_40_days_inclusive(api, to, ok):
    s, _ = call(api, "GET", "/api/v2/usage", q={"granularity": "day", "from": "2026-09-01", "to": to})
    assert (s == 200) is ok


def test_cap_already_zero_needs_no_reason_on_later_saves(api):
    s, r = call(api, "PUT", "/api/v2/policy/org", {"monthly_usd": 0, "reason": "freeze", "version": None})
    assert s == 200
    s, r = call(api, "PUT", "/api/v2/policy/org", {"monthly_usd": 0, "daily_usd": 5, "version": r["item"]["version"]})
    assert s == 200  # unchanged 0, no reason needed
    s, _ = call(api, "PUT", "/api/v2/policy/org", {"daily_usd": 0, "version": r["item"]["version"]})
    assert s == 400  # a new 0 still needs one


@pytest.mark.parametrize("q,field", [
    ({"from": "2026-12-01", "to": "2026-01-01"}, "from"),
    ({"from": "yesterday"}, "from"),
    ({"to": "2026-13"}, "to"),
    ({"to": "2026-01-01; DROP"}, "to"),
    ({"from": "2026-01-01T10Z"}, "from"),
    ({"from": "2026-01-01T10:00Z"}, "from"),
    ({"to": "2026-01-01T10:00:00"}, "to"),
    ({"to": "2026-12-31T23:59:59.123Z"}, "to"),
])
def test_audit_bad_range_is_400_naming_the_field(api, q, field):
    s, r = call(api, "GET", "/api/v2/audit", q=q)
    assert s == 400 and r["field"] == field


@pytest.mark.parametrize("q", [{"from": "2026-01-01", "to": "2026-01-01"}, {"from": "2026-01-01T00:00:00Z"},
                               {"to": "2026-12-31T23:59:59Z"}, {"from": "", "to": ""}])
def test_audit_good_range_is_200(api, q):
    s, r = call(api, "GET", "/api/v2/audit", q=q)
    assert s == 200 and "rows" in r


def test_audit_partial_full_target_matches_nothing_and_bare_text_is_substring(api):
    call(api, "PUT", "/api/v2/users/alice/limits", {"daily_usd": 3, "reason": "r"})
    assert call(api, "GET", "/api/v2/audit", q={"target": "user:al"})[1]["rows"] == []
    assert any(r["target"] == "user:alice" for r in call(api, "GET", "/api/v2/audit", q={"target": "al"})[1]["rows"])


def test_a_group_known_only_from_a_users_token_is_listed_and_editable(api):
    import boto3  # noqa: PLC0415
    boto3.client("dynamodb").put_item(TableName="t", Item={"pk": {"S": "USER#dave"}, "sk": {"S": "BUDGET"},
                                                             "token_groups": {"L": [{"S": "Engineering"}]}})
    s, r = call(api, "GET", "/api/v2/groups")
    row = next(g for g in r["groups"] if g["group"] == "Engineering")
    assert s == 200 and row["editable"] and row["models_effective"] == []
    assert call(api, "GET", "/api/v2/groups/Engineering")[0] == 200
    assert call(api, "GET", "/api/v2/groups/Nobody")[0] == 404


def test_no_cache_figures_anywhere(api):
    # Mantle reports no cache split, so the API carries no cache counters, KPI or prices that could only ever be 0.
    s, r = call(api, "GET", "/api/v2/overview")
    assert s == 200 and "cache_read_share" not in r["kpis"]
    text = json.dumps([r, call(api, "GET", "/api/v2/users/alice")[1], call(api, "GET", "/api/v2/models")[1],
                       call(api, "GET", "/api/v2/usage", q={"group_by": "user", "granularity": "month"})[1]])
    assert "cache_read" not in text and "cache_write" not in text


@pytest.mark.parametrize("legacy", ["unknown", '{"sub":"x"}'])
def test_overview_total_is_the_sum_of_the_users_page(api, legacy):
    m = time.strftime("MONTH#%Y-%m", time.gmtime())
    boto3.client("dynamodb").put_item(TableName="t", Item={"pk": {"S": f"USER#{legacy}"}, "sk": {"S": m}, "spend_usd": {"N": "3"}})
    users = call(api, "GET", "/api/v2/users")[1]["users"]
    s, ov = call(api, "GET", "/api/v2/overview")
    assert s == 200 and legacy not in {u["user"] for u in users}
    assert ov["kpis"]["mtd_usd"] == pytest.approx(sum(u["mtd"]["usd"] for u in users))


@pytest.mark.parametrize("method,path,body", [
    ("PUT", "/api/v2/users/alcie/limits", {"monthly_usd": 10, "reason": "r"}),
    ("POST", "/api/v2/users/alcie/boost", {"usd": 5, "period": "Monthly", "hours": 2, "reason": "r"}),
    ("DELETE", "/api/v2/users/alcie/boost", {}),
])
def test_a_typo_user_is_404_and_creates_nothing(api, method, path, body):
    s, r = call(api, method, path, body)
    assert s == 404 and "not a user of this gateway" in r["error"]
    c = boto3.client("dynamodb")
    assert "Item" not in c.get_item(TableName="t", Key={"pk": {"S": "USER#alcie"}, "sk": {"S": "BUDGET"}})
    assert all(x["target"] != "user:alcie" for x in call(api, "GET", "/api/v2/audit")[1]["rows"])


@pytest.mark.parametrize("spent,cap,state", [(0.01999, 0.02, "critical"), (0.02, 0.02, "blocked"), (0.0199999995, 0.02, "blocked"),
                                             (49.9996, 50, "critical"), (50, 50, "blocked"), (0, 0, "blocked"), (1, None, "none")])
def test_blocked_on_the_dashboard_is_exactly_what_the_gateway_refuses(api, monkeypatch, spent, cap, state):
    import time as t  # noqa: PLC0415
    caps = {p: {"cap": cap, "base": cap, "source": "org", "boost": 0.0} for p in ("Monthly", "Daily", "Hourly")}
    monkeypatch.setattr(api.policy, "resolve", lambda *a, **k: {"caps": caps})
    item = lambda usd: {"spend_usd": {"N": repr(usd)}}  # noqa: E731 - raw window items, as the gate reads them
    rows, _ = api.limits_for({}, [], {"Monthly": item(spent), "Daily": None, "Hourly": None}, [75, 95], t.gmtime(), pols={})
    assert rows[0]["state"] == state and (state == "blocked") == api.policy.at_cap(spent, cap)


@pytest.mark.parametrize("cap,state", [(50, "blocked"), (None, "warn")])  # r143: an uncapped one warns, never "none"
def test_an_unreadable_window_is_flagged_everywhere_and_blocked_where_it_has_a_cap(api, monkeypatch, cap, state):
    # r141 /code-review: the gate refuses an unreadable record only on a capped window; the row says why.
    # r142 /code-review: on an uncapped window it was invisible until an admin set a cap: it is flagged there too.
    import time as t  # noqa: PLC0415
    caps = {p: {"cap": cap, "base": cap, "source": "org", "boost": 0.0} for p in ("Monthly", "Daily", "Hourly")}
    monkeypatch.setattr(api.policy, "resolve", lambda *a, **k: {"caps": caps})
    rows, _ = api.limits_for({}, [], {"Monthly": {"spend_usd": {"S": "x"}}, "Daily": None, "Hourly": None}, [75, 95],
                             t.gmtime(), pols={})
    assert rows[0]["state"] == state and rows[0]["unreadable"] is True
    assert rows[1]["unreadable"] is False  # an absent window item is readable (nothing spent)


def test_usage_answers_which_tag_dimensions_exist_and_which_it_used(api, monkeypatch):
    monkeypatch.setattr(api, "TAG_DIMS", ["team", "project"])
    s, r = call(api, "GET", "/api/v2/usage", q={"group_by": "tag", "granularity": "month", "dim": "project"})
    assert s == 200 and r["tag_dims"] == ["team", "project"] and r["dim"] == "project"
    assert call(api, "GET", "/api/v2/usage", q={"group_by": "tag", "granularity": "month"})[1]["dim"] == "team"


@pytest.mark.parametrize("dims", [["team", "project"], []])
def test_me_tells_the_ui_which_tag_dimensions_exist(api, monkeypatch, dims):
    monkeypatch.setattr(api, "TAG_DIMS", dims)
    s, r = call(api, "GET", "/api/v2/me")
    assert s == 200 and r["tag_dims"] == dims and r["role"] == "admin"


def test_clearing_group_limits_keeps_its_model_allow_list(api):
    s, r = call(api, "PUT", "/api/v2/policy/groups/ow", {"models": ["ow/kimi-k3"], "monthly_usd": 9, "version": None})
    assert s == 200
    s, r = call(api, "PUT", "/api/v2/policy/groups/ow", {"monthly_usd": None, "daily_usd": None, "hourly_usd": None, "rpm": None,
                                                         "version": r["item"]["version"], "reason": "r"})
    assert s == 200 and r["item"].get("models") == ["ow/kimi-k3"] and "monthly_usd" not in r["item"]


def test_a_save_from_a_stale_user_page_is_409_not_a_silent_overwrite(api):
    v = call(api, "GET", "/api/v2/users/alice")[1]["version"]                       # admin A opens alice
    s, _ = call(api, "PUT", "/api/v2/users/alice/limits", {"monthly_usd": 20, "reason": "B", "version": v})  # admin B saves first
    assert s == 200
    s, r = call(api, "PUT", "/api/v2/users/alice/limits", {"monthly_usd": 15, "reason": "A", "version": v})  # A saves from the old page
    assert s == 409 and r["current"]["budget_usd"] == 20
    s, r = call(api, "POST", "/api/v2/users/alice/boost", {"usd": 5, "period": "Monthly", "hours": 2, "reason": "A", "version": v})
    assert s == 409
    fresh = call(api, "GET", "/api/v2/users/alice")[1]["version"]
    assert call(api, "PUT", "/api/v2/users/alice/limits", {"monthly_usd": 15, "reason": "A", "version": fresh})[0] == 200


def test_the_interceptor_recording_groups_does_not_change_the_version(api):
    v = call(api, "GET", "/api/v2/users/alice")[1]["version"]
    boto3.client("dynamodb").update_item(TableName="t", Key={"pk": {"S": "USER#alice"}, "sk": {"S": "BUDGET"}},
                                         UpdateExpression="SET token_groups = :g",
                                         ExpressionAttributeValues={":g": {"L": [{"S": "eng"}]}})
    assert call(api, "PUT", "/api/v2/users/alice/limits", {"monthly_usd": 7, "reason": "r", "version": v})[0] == 200


def test_deleting_a_group_policy_that_is_gone_is_404_even_with_a_version(api):
    s, r = call(api, "DELETE", "/api/v2/policy/groups/eng", {"version": "abc"})
    assert s == 404 and "no policy" in r["error"]


def test_overview_takes_every_time_from_one_clock_read(api, monkeypatch):
    """Month end: a second clock read would put generated_at (and the projection) in the next month."""
    import time as t
    ticks = iter([1793491199.9] + [1793491200.5] * 1000)  # 2026-10-31T23:59:59.9Z, then 2026-11-01
    real = t.time
    monkeypatch.setattr(t, "time", lambda: next(ticks, real()))
    s, r = call(api, "GET", "/api/v2/overview")
    assert s == 200 and r["generated_at"].startswith("2026-10-31T23:59:59")


@pytest.mark.parametrize("method,path,body", [
    ("POST", "/api/v2/users/alice/boost", {"usd": 5, "hours": 1, "period": "Monthly", "reason": 123}),
    ("PUT", "/api/v2/policy/org", {"monthly_usd": 9, "reason": ["x"]}),
    ("PUT", "/api/v2/users/alice/limits", {"monthly_usd": 0, "reason": {"a": 1}}),
    ("PUT", "/api/v2/users/alice/limits", {"monthly_usd": 5, "reason": "x" * 1001}),
])
def test_a_reason_that_is_not_short_text_is_400_naming_the_field(api, method, path, body):
    s, r = call(api, method, path, body)
    assert s == 400 and r.get("field") == "reason", (s, r)


def test_removed_users_stay_listed_but_never_need_attention(api):
    boto3.client("dynamodb").put_item(TableName="t", Item={"pk": {"S": "USER#gone"}, "sk": {"S": f"MONTH#{time.strftime('%Y-%m', time.gmtime())}"},
                                                         "spend_usd": {"N": "49"}})
    users = {u["user"]: u for u in call(api, "GET", "/api/v2/users")[1]["users"]}
    assert users["gone"]["removed"] is True and users["alice"]["removed"] is False
    ov = call(api, "GET", "/api/v2/overview")[1]
    assert all(a["user"] != "gone" for a in ov["attention"])


def test_an_unreadable_record_needs_attention_first_even_without_a_cap(api):
    # r142 /code-review: a bad record on a window with no cap showed nowhere, and blocked the user the moment an
    # admin set that cap. It is listed first in the attention list, flagged, whatever its state.
    boto3.client("dynamodb").put_item(TableName="t", Item={"pk": {"S": "USER#alice"}, "sk": {"S": f"HOUR#{time.strftime('%Y-%m-%dT%H', time.gmtime())}"},
                                                         "spend_usd": {"S": "x"}})
    ov = call(api, "GET", "/api/v2/overview")[1]
    assert ov["attention"][0]["user"] == "alice" and ov["attention"][0]["period"] == "Hourly" and ov["attention"][0]["unreadable"] is True
    hourly = next(r for r in next(u for u in call(api, "GET", "/api/v2/users")[1]["users"] if u["user"] == "alice")["limits"]
                  if r["period"] == "Hourly")
    assert hourly["unreadable"] is True and hourly["state"] == ("blocked" if hourly["cap"] is not None else "warn")


def test_user_page_says_when_the_user_was_removed(api):
    boto3.client("dynamodb").put_item(TableName="t", Item={"pk": {"S": "USER#gone"}, "sk": {"S": f"MONTH#{time.strftime('%Y-%m', time.gmtime())}"},
                                                         "spend_usd": {"N": "3"}})
    assert call(api, "GET", "/api/v2/users/gone")[1]["removed"] is True
    assert call(api, "GET", "/api/v2/users/alice")[1]["removed"] is False


def test_overview_daily_bars_add_up_to_the_month_to_date(api):
    c, day = boto3.client("dynamodb"), time.strftime("%Y-%m-%d", time.gmtime())
    # spend with no MODEL# row behind it: a removed user's model spend and an unpriced model
    c.put_item(TableName="t", Item={"pk": {"S": "USER#gone"}, "sk": {"S": f"DAY#{day}"}, "model_spend_usd": {"N": "7"}})
    c.put_item(TableName="t", Item={"pk": {"S": "USER#gone"}, "sk": {"S": f"MONTH#{day[:7]}"}, "model_spend_usd": {"N": "7"}})
    s, r = call(api, "GET", "/api/v2/overview")
    assert s == 200
    today = next(d["by_model"] for d in r["daily"] if d["date"] == day)
    # today's bar is every listed user's DAY spend, the same rows today_usd sums (the fixture's month has no other days)
    assert sum(today.values()) == pytest.approx(r["kpis"]["today_usd"]) and today[api.UNATTRIBUTED] == pytest.approx(7 + 4)


def test_usage_refuses_a_group_filter_with_a_tag_filter(api, monkeypatch):
    monkeypatch.setattr(api, "TAG_DIMS", ["team"])
    s, r = call(api, "GET", "/api/v2/usage", q={"group_by": "model", "group": "ow", "tag": "team=a"})
    assert s == 400 and r["field"] == "group" and "tag filter" in r["error"]  # names the real conflict, not group_by


def test_a_user_removed_during_a_save_is_404_not_409(api, monkeypatch):
    seed("dave")
    real = api._write_guarded

    def removed_first(items, current):
        boto3.client("dynamodb").delete_item(TableName="t", Key={"pk": {"S": "USER#dave"}, "sk": {"S": "BUDGET"}})
        return real(items, current)
    monkeypatch.setattr(api, "_write_guarded", removed_first)
    s, r = call(api, "PUT", "/api/v2/users/dave/limits", {"monthly_usd": 5})
    assert s == 404, r


def test_blocked_uses_the_unrounded_spend_like_the_interceptor(api):
    v = call(api, "GET", "/api/v2/users/bob")[1]["version"]
    assert call(api, "PUT", "/api/v2/users/bob/limits", {"monthly_usd": 10, "version": v})[0] == 200
    boto3.client("dynamodb").put_item(TableName="t", Item={"pk": {"S": "USER#bob"}, "sk": {"S": f"MONTH#{time.strftime('%Y-%m', time.gmtime())}"},
                                                         "model_spend_usd": {"N": "9.9999986"}})
    lim = next(x for x in call(api, "GET", "/api/v2/users/bob")[1]["limits"] if x["period"] == "Monthly")
    assert lim["state"] != "blocked"  # 9.9999986 < 10 - 1e-6: the gateway still allows calls


def test_rounding_drift_is_not_shown_as_unattributed(api):
    c, day = boto3.client("dynamodb"), time.strftime("%Y-%m-%d", time.gmtime())
    c.put_item(TableName="t", Item={"pk": {"S": "USER#carol"}, "sk": {"S": f"DAY#{day}"}, "model_spend_usd": {"N": "0.0000049"}})
    # carol's spend is drift-sized; on a day with nothing else unattributed it must not become a slice of its own
    c.put_item(TableName="t", Item={"pk": {"S": "USER#alice"}, "sk": {"S": f"DAY#{day}"}, "model_spend_usd": {"N": "3"}})
    today = next(d["by_model"] for d in call(api, "GET", "/api/v2/overview")[1]["daily"] if d["date"] == day)
    assert api.UNATTRIBUTED not in today, today


def test_ratelimits_show_the_deployment_default_rpm(api, monkeypatch):
    monkeypatch.setitem(api.ENV_LIMITS, "rpm", 30)
    monkeypatch.setattr(api, "gateway_limits", lambda: [])
    s, r = call(api, "GET", "/api/v2/ratelimits")
    assert s == 200 and r["interceptor"]["default"] == 30


def test_a_sub_tenth_of_a_cent_web_search_still_shows_in_the_model_view(api):
    c, m = boto3.client("dynamodb"), time.strftime("%Y-%m", time.gmtime())
    c.put_item(TableName="t", Item={"pk": {"S": "USER#carol"}, "sk": {"S": f"MONTH#{m}"}, "spend_usd": {"N": "0.0005"}})
    by_model = {r["key"]: r["usd"] for r in call(api, "GET", "/api/v2/usage", q={"group_by": "model", "granularity": "month"})[1]["table"]}
    by_user = {r["key"]: r["usd"] for r in call(api, "GET", "/api/v2/usage", q={"group_by": "user", "granularity": "month"})[1]["table"]}
    assert sum(by_model.values()) == pytest.approx(sum(by_user.values()))


def test_per_model_rounding_is_not_reported_as_unattributed(api):
    c, m = boto3.client("dynamodb"), time.strftime("%Y-%m", time.gmtime())
    item = {"pk": {"S": "USER#carol"}, "sk": {"S": f"MONTH#{m}"}, "model_spend_usd": {"N": "0.0000056"}}
    for i in range(4):
        item[f"m|ow/x{i}|usd"] = {"N": "0.0000014"}
    c.put_item(TableName="t", Item=item)
    rows = call(api, "GET", "/api/v2/usage", q={"group_by": "model", "granularity": "month", "user": "carol"})[1]["table"]
    assert api.UNATTRIBUTED not in {r["key"] for r in rows}, rows


def test_user_page_model_table_adds_up_to_the_months_spend(api):
    d = call(api, "GET", "/api/v2/users/alice")[1]
    monthly = next(x for x in d["limits"] if x["period"] == "Monthly")["spent"]
    assert sum(r["usd"] for r in d["by_model_mtd"]) == pytest.approx(monthly)


@pytest.mark.parametrize("v,ok", [(0, True), (0.01, True), (0.005, False), (1e-7, False)])
def test_a_cap_is_zero_or_at_least_a_cent(api, v, ok):
    s, r = call(api, "PUT", "/api/v2/users/alice/limits", {"daily_usd": v, "reason": "r"})
    assert (s == 200) == ok and (ok or r["field"] == "daily_usd"), (s, r)


def test_unfiltered_audit_pages_past_a_short_first_page(api, monkeypatch):
    seed(*(f"q{i}" for i in range(6)))
    for i in range(6):
        call(api, "PUT", f"/api/v2/users/q{i}/limits", {"monthly_usd": 5, "reason": "r"})
    monkeypatch.setattr(api, "AUDIT_PAGE", 5)
    real = api.ddb.query

    def short(**kw):  # DynamoDB ends a page at 1 MB: hand back 2 rows a page
        r = real(**{**kw, "Limit": min(kw.get("Limit", 2), 2)})
        return r
    monkeypatch.setattr(api.ddb, "query", short)
    s, r = call(api, "GET", "/api/v2/audit")
    assert s == 200 and len(r["rows"]) == 5


def test_me_offers_sign_out_only_behind_the_alb(api, monkeypatch):
    monkeypatch.delenv("ALB_ARN", raising=False)
    assert call(api, "GET", "/api/v2/me")[1]["sign_out"] is False
    monkeypatch.setenv("ALB_ARN", "arn:aws:elasticloadbalancing:us-east-1:1:loadbalancer/app/x/y")
    assert call(api, "GET", "/api/v2/me")[1]["sign_out"] is True


@pytest.mark.parametrize("w", [80, [80], ["a", "b"], [95, 75], [0, 50], True])
def test_a_hand_written_warn_pct_never_breaks_the_views(api, w):
    put = boto3.client("dynamodb").put_item
    item = {"pk": {"S": "POLICY"}, "sk": {"S": "ORG"}}
    item["warn_pct"] = {"N": "80"} if isinstance(w, int) and not isinstance(w, bool) else (
        {"BOOL": True} if isinstance(w, bool) else {"L": [{"N": str(x)} if isinstance(x, int) else {"S": x} for x in w]})
    put(TableName="t", Item=item)
    for path in ("/api/v2/users", "/api/v2/overview", "/api/v2/users/alice", "/api/v2/policy"):
        assert call(api, "GET", path)[0] == 200, path
    assert call(api, "GET", "/api/v2/policy")[1]["org"]["warn_pct"] == [75, 95]


def test_end_boost_from_a_stale_list_is_a_409(api):
    v = call(api, "GET", "/api/v2/users/bob")[1]["version"]
    assert call(api, "POST", "/api/v2/users/bob/boost", {"usd": 5, "period": "Monthly", "hours": 2, "reason": "a", "version": v})[0] == 200
    listed = next(b for b in call(api, "GET", "/api/v2/policy")[1]["boosts"] if b["user"] == "bob")
    v2 = call(api, "GET", "/api/v2/users/bob")[1]["version"]
    assert call(api, "POST", "/api/v2/users/bob/boost", {"usd": 200, "period": "Monthly", "hours": 2, "reason": "incident", "version": v2})[0] == 200
    assert call(api, "DELETE", "/api/v2/users/bob/boost", {"version": listed["version"]})[0] == 409  # the new boost stays
    assert call(api, "GET", "/api/v2/users/bob")[1]["boost"]["usd"] == 200


def test_a_filtered_audit_search_stops_after_the_scan_cap(api, monkeypatch):
    seed(*(f"w{i}" for i in range(4)))
    for i in range(4):
        call(api, "PUT", f"/api/v2/users/w{i}/limits", {"monthly_usd": 5, "reason": "r"})
    monkeypatch.setattr(api, "AUDIT_SCAN_MAX", 2)
    real = api.ddb.query
    monkeypatch.setattr(api.ddb, "query", lambda **kw: real(**{**kw, "Limit": 1}))  # one row a page
    s, r = call(api, "GET", "/api/v2/audit", q={"actor": "nobody-matches"})
    assert s == 200 and r["rows"] == [] and r["complete"] is False


@pytest.mark.parametrize("q,field", [("from=2026-06-01&to=2026-03-01", "to"), ("from=2026-13-01&to=2026-12-01", "from"),
                                     ("from=2026-01-01&to=2026-02-30", "to")])
def test_month_usage_ranges_are_checked_like_day_ranges(api, q, field):
    # r48 asked whether month granularity skips the date checks: it does not (400 naming the field).
    rng = dict(x.split("=") for x in q.split("&"))
    for extra in ({"granularity": "month"}, {"group_by": "tag"}):
        s, r = call(api, "GET", "/api/v2/usage", q={**rng, **extra})
        assert s == 400 and r["field"] == field, r


def test_a_boost_whose_cap_was_removed_meanwhile_is_a_409(api):
    # r48: admin A read bob with a daily cap; admin B removes it; A's Daily boost must not be stored.
    v = call(api, "GET", "/api/v2/users/bob")[1]["version"]
    assert call(api, "PUT", "/api/v2/users/bob/limits", {"daily_usd": 5, "version": v})[0] == 200
    real = api._known_budget
    snapshot = real("bob")  # what A read: the cap is there
    assert call(api, "PUT", "/api/v2/users/bob/limits", {"daily_usd": None})[0] == 200  # B removes it
    api._known_budget = lambda u: snapshot if u == "bob" else real(u)
    try:
        s, r = call(api, "POST", "/api/v2/users/bob/boost", {"usd": 5, "period": "Daily", "hours": 2, "reason": "x"})
    finally:
        api._known_budget = real
    assert s == 409, r


def test_a_hand_written_text_boost_until_does_not_break_the_budget_pages(api):
    # r53 reproduced: boost_until stored as S "4000000000" made boost_view raise, so Budgets and the user page 500ed.
    boto3.client("dynamodb").update_item(TableName="t", Key={"pk": {"S": "USER#bob"}, "sk": {"S": "BUDGET"}},
                                         UpdateExpression="SET boost_usd = :u, boost_until = :t, boost_period = :p, boost_reason = :r",
                                         ExpressionAttributeValues={":u": {"S": "5"}, ":t": {"S": "4000000000"}, ":p": {"S": "Monthly"}, ":r": {"S": "x"}})
    s, r = call(api, "GET", "/api/v2/policy")
    assert s == 200, r
    b = next(x for x in r["boosts"] if x["user"] == "bob")
    assert b["usd"] == 5.0 and isinstance(b["usd"], float) and b["until"].startswith("2096")
    assert call(api, "GET", "/api/v2/users/bob")[0] == 200


@pytest.mark.parametrize("until,period", [({"N": "1e20"}, {"S": "Monthly"}), ({"S": "1e300"}, {"S": "Monthly"}),
                                          ({"N": "4000000000"}, {"NULL": True}), ({"N": "4000000000"}, {"S": "Weekly"})])
def test_hand_written_boost_until_and_period_never_break_the_pages(api, until, period):
    # r54: boost_until 1e20 overflowed time.gmtime (500); a NULL period reached the UI as null and blanked the app.
    boto3.client("dynamodb").update_item(TableName="t", Key={"pk": {"S": "USER#bob"}, "sk": {"S": "BUDGET"}},
                                         UpdateExpression="SET boost_usd = :u, boost_until = :t, boost_period = :p, boost_reason = :r",
                                         ExpressionAttributeValues={":u": {"N": "10"}, ":t": until, ":p": period, ":r": {"S": "x"}})
    s, r = call(api, "GET", "/api/v2/policy")
    assert s == 200, r
    for b in r["boosts"]:
        assert b["period"] in ("Monthly", "Daily", "Hourly")
    s, r = call(api, "GET", "/api/v2/users/bob")
    assert s == 200 and (r["boost"] is None or r["boost"]["period"] in ("Monthly", "Daily", "Hourly")), r


@pytest.mark.parametrize("pat,ok", [("ow/kimi*", True), ("ow/*", True), ("kimi*", False), ("*", False), ("o*", False)])
def test_group_model_patterns_must_carry_the_ow_prefix(api, pat, ok):
    # r55: 'kimi*' saved and then matched no model (every call refused, no error to explain it).
    s, r = call(api, "PUT", "/api/v2/policy/groups/ow", {"models": [pat], "version": None})
    assert (s == 200) is ok, r


def test_rate_limit_gets_slide_and_one_slow_get_does_not_hold_the_others(monkeypatch):
    # r60: lock-step batches of 8 waited for each batch's slowest Get.
    import threading
    import time as _t
    import dashboard_api as d
    live, peak, lock = [0], [0], threading.Lock()

    class Ctl:
        def get_gateway_rate_limit(self, gatewayIdentifier, rateLimitId):  # noqa: N803
            with lock:
                live[0] += 1
                peak[0] = max(peak[0], live[0])
            _t.sleep(0.6 if rateLimitId == "0" else 0.05)
            with lock:
                live[0] -= 1
            return {"rateLimitId": rateLimitId}
    monkeypatch.setattr(d, "acc", lambda: Ctl())
    t0 = _t.monotonic()
    out = d.rate_limit_details([str(i) for i in range(40)])
    assert [o["rateLimitId"] for o in out] == [str(i) for i in range(40)]
    assert peak[0] <= d.RL_GETS and _t.monotonic() - t0 < 0.6 + 0.3  # the other 39 ran beside the slow one


def test_a_failed_rate_limit_get_starts_no_more_and_waits_for_those_in_flight(monkeypatch):
    import time as _t
    import dashboard_api as d
    started, finished = [], []

    class Ctl:
        def get_gateway_rate_limit(self, gatewayIdentifier, rateLimitId):  # noqa: N803
            started.append(rateLimitId)
            if rateLimitId == "0":
                raise RuntimeError("throttled")
            _t.sleep(0.1)
            finished.append(rateLimitId)
            return {"rateLimitId": rateLimitId}
    monkeypatch.setattr(d, "acc", lambda: Ctl())
    with pytest.raises(RuntimeError, match="throttled"):
        try:
            d.rate_limit_details([str(i) for i in range(40)])
        finally:
            d._POOL.drain()  # what handle() does on the way out
    assert len(started) <= d.RL_GETS and sorted(finished) == sorted(x for x in started if x != "0")


def test_a_failed_gateway_read_waits_for_the_views_other_reads(monkeypatch):
    # r61: ratelimits_view raised while its POLICY and BUDGET reads still ran on the pool.
    import time as _t
    import dashboard_api as d
    done = []
    monkeypatch.setattr(d.policy, "policy_items", lambda *a: _t.sleep(0.3) or done.append("pol") or {})
    monkeypatch.setattr(d, "scan_sk", lambda sk, kind=None: _t.sleep(0.3) or done.append("bud") or [])
    monkeypatch.setattr(d, "gateway_limits", lambda: (_ for _ in ()).throw(RuntimeError("throttled")))
    with pytest.raises(RuntimeError, match="throttled"):
        d.handle("GET", "/api/v2/ratelimits", {}, None, {"email": "a@example.com", "role": "admin"})
    assert sorted(done) == ["bud", "pol"]


def test_a_failed_user_detail_read_leaves_nothing_running(monkeypatch):
    # r62: a failing GetItem in user_detail's map raised while its day, audit and policy reads still ran.
    import time as _t
    import dashboard_api as d
    done = []
    monkeypatch.setattr(d.policy, "policy_items", lambda *a: _t.sleep(0.3) or done.append("pol") or {})
    monkeypatch.setattr(d, "query", lambda *a, **k: _t.sleep(0.3) or done.append("days") or [])
    monkeypatch.setattr(d, "audit_rows", lambda **k: _t.sleep(0.3) or done.append("audit") or [])
    monkeypatch.setattr(d.policy, "batch_get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("throttled")))  # r147: one BatchGetItem
    with pytest.raises(RuntimeError, match="throttled"):
        d.handle("GET", "/api/v2/users/alice@example.com", {}, None, {"email": "a@example.com", "role": "admin"})
    assert sorted(done) == ["audit", "days", "pol"]


def test_a_failed_pool_map_drops_the_calls_not_started():
    # r65: on an error the queued calls of a failed view still ran, and drain() waited for all of them.
    import threading
    import time as _t
    import dashboard_api as d
    ran = []
    lock = threading.Lock()

    def call(i):
        if i == 0:
            raise RuntimeError("throttled")
        _t.sleep(0.05)
        with lock:
            ran.append(i)
    with pytest.raises(RuntimeError, match="throttled"):
        list(d._POOL.map(call, range(200)))  # as the views consume it
    d._POOL.drain()
    assert len(ran) < 200


def test_a_rate_limit_deleted_between_list_and_get_is_left_out(monkeypatch):
    # r64: a ResourceNotFound from a limit deleted by another admin's save failed the page with a 500.
    import botocore.exceptions
    import dashboard_api as d

    class Ctl:
        def get_gateway_rate_limit(self, gatewayIdentifier, rateLimitId):  # noqa: N803
            if rateLimitId == "gone":
                raise botocore.exceptions.ClientError({"Error": {"Code": "ResourceNotFoundException"}}, "GetGatewayRateLimit")
            return {"rateLimitId": rateLimitId}
    monkeypatch.setattr(d, "acc", lambda: Ctl())
    assert [x["rateLimitId"] for x in d.rate_limit_details(["a", "gone", "b"])] == ["a", "b"]


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
def test_gateway_rate_limits_cannot_be_written_from_the_dashboard(api, monkeypatch, method):
    # r72: gateway rate limits are Terraform's alone (AgentCore allows one limit per key set; two writers kept
    # overwriting each other). The per-user cap admins change is the interceptor's rpm.
    gw = _gw(api, monkeypatch)
    s, _ = call(api, method, "/api/v2/ratelimits/gateway/alice", {"requests_per_minute": 5})
    assert s == 404 and gw.calls == []


def test_the_rate_limits_view_names_each_entrys_user(api, monkeypatch):
    gw = _gw(api, monkeypatch)
    gw.limits[0]["entries"].append(_entry(api, "00uBOB", 3))
    monkeypatch.setenv("DIRECTORY_FROM_TABLE", "1")
    monkeypatch.setattr(api.policy, "directory", lambda *a, **k: ({}, {"00uBOB": "bob"}))
    rows = call(api, "GET", "/api/v2/ratelimits")[1]["gateway"]
    assert next(r for r in rows if r["value"] == "00uBOB")["user"] == "bob"
    assert next(r for r in rows if r["value"] == "*")["user"] is None


def test_rate_limit_rows_tolerate_null_lists_and_any_period_case(api):
    # r77/r78: "MINUTE" showed as unset, and a null tokens list made the whole Rate limits page a 500.
    assert api._per_minute([{"rate": 60.0, "period": "MINUTE"}]) == 60.0
    assert api._per_minute(None) is None
    assert api._per_minute([{"period": "minute"}]) is None


def test_a_rate_limit_with_a_null_description_shows_an_empty_string(api, monkeypatch):
    # r81: description null reached the UI (typed string) as null.
    monkeypatch.setattr(api, "_identity_map", lambda: {})
    monkeypatch.setattr(api, "_limit_details", lambda: [{"rateLimitId": "a", "description": None, "status": "ACTIVE"}])
    assert [r["description"] for r in api.gateway_limits()] == [""]


def test_a_boost_ending_now_is_left_out_of_the_budgets_list(api, monkeypatch):
    # r157: the list reads the clock once, so a boost is either in force at that instant (listed) or not (left out).
    v = call(api, "GET", "/api/v2/users/bob")[1]["version"]
    assert call(api, "PUT", "/api/v2/users/bob/limits", {"daily_usd": 10, "version": v})[0] == 200
    assert call(api, "POST", "/api/v2/users/bob/boost", {"usd": 20, "period": "Daily", "hours": 2, "reason": "t"})[0] == 200
    monkeypatch.setattr(time, "time", lambda: 4102444800.0)  # long after the boost ends
    s, d = call(api, "GET", "/api/v2/policy")
    assert s == 200 and not [b for b in d["boosts"] if b["user"] == "bob"]


def _hour_sk():
    return f"HOUR#{time.strftime('%Y-%m-%dT%H', time.gmtime())}"


@pytest.mark.parametrize("bad", [{"S": "x"}, {"N": "-3"}])
def test_an_admin_repairs_an_unreadable_record_and_it_is_audited(api, bad):
    # r143 /code-review: an unreadable record was flagged everywhere but nothing could fix it except a hand edit.
    c = boto3.client("dynamodb")
    c.put_item(TableName="t", Item={"pk": {"S": "USER#alice"}, "sk": {"S": _hour_sk()}, "spend_usd": bad, "model_spend_usd": {"N": "0.5"},
                                    "requests": {"N": "3"}})
    s, r = call(api, "POST", "/api/v2/users/alice/repair", {"period": "Hourly", "reason": "hand edit"})
    assert s == 200 and r["item"]["removed"] == ["spend_usd"] and r["audit"]["action"] == "repair_usage_record"
    it = c.get_item(TableName="t", Key={"pk": {"S": "USER#alice"}, "sk": {"S": _hour_sk()}})["Item"]
    assert "spend_usd" not in it and it["model_spend_usd"] == {"N": "0.5"} and it["requests"] == {"N": "3"}  # only the bad one
    hourly = next(x for x in next(u for u in call(api, "GET", "/api/v2/users")[1]["users"] if u["user"] == "alice")["limits"]
                  if x["period"] == "Hourly")
    assert hourly["unreadable"] is False and hourly["spent"] == 0.5
    assert any(a["action"] == "repair_usage_record" for a in call(api, "GET", "/api/v2/audit", q={"target": "user:alice"})[1]["rows"])


@pytest.mark.parametrize("body,ident,status,field", [
    ({"period": "Hourly", "reason": "r"}, VIEWER, 403, None),
    ({"period": "Weekly", "reason": "r"}, ADMIN, 400, "period"),
    ({"period": "Hourly"}, ADMIN, 400, "reason"),
    ({"period": "Hourly", "reason": "r"}, ADMIN, 409, None),  # readable: nothing to repair
])
def test_a_repair_is_admin_only_named_reasoned_and_only_for_a_bad_record(api, body, ident, status, field):
    s, r = call(api, "POST", "/api/v2/users/alice/repair", body, ident=ident)
    assert s == status and (field is None or r.get("field") == field)


def test_a_repair_for_someone_who_is_not_a_user_is_404(api):
    assert call(api, "POST", "/api/v2/users/nobody/repair", {"period": "Hourly", "reason": "r"})[0] == 404


def test_an_unreadable_record_has_no_amount_and_marks_the_user(api, monkeypatch):
    # r143 /code-review: an unreadable row showed the partial sum (a share next to "blocked"), and an uncapped one
    # left the user "ok".
    boto3.client("dynamodb").put_item(TableName="t", Item={"pk": {"S": "USER#alice"}, "sk": {"S": _hour_sk()},
                                                         "spend_usd": {"S": "x"}, "model_spend_usd": {"N": "6"}})
    a = next(u for u in call(api, "GET", "/api/v2/users")[1]["users"] if u["user"] == "alice")
    hourly = next(x for x in a["limits"] if x["period"] == "Hourly")
    assert hourly["spent"] is None and hourly["pct"] is None and hourly["unreadable"] is True
    assert hourly["state"] == ("blocked" if hourly["cap"] is not None else "warn") and a["state"] != "ok"


def test_a_binary_hand_edit_is_repaired_and_audited_as_text(api):
    # r144 /code-review: the audit copied the raw value, and bytes made json.dumps raise (a 500).
    boto3.client("dynamodb").put_item(TableName="t", Item={"pk": {"S": "USER#alice"}, "sk": {"S": _hour_sk()}, "spend_usd": {"B": b"\x00x"}})
    s, r = call(api, "POST", "/api/v2/users/alice/repair", {"period": "Hourly", "reason": "binary hand edit"})
    assert s == 200 and r["audit"]["before"]["spend_usd"] == "<B>"


def test_a_hand_edited_boost_can_still_be_ended(api):
    # r146 /code-review: a non-numeric boost_usd cleaned to 0, so End boost answered 404 with the attributes still there.
    boto3.client("dynamodb").update_item(TableName="t", Key={"pk": {"S": "USER#alice"}, "sk": {"S": "BUDGET"}},
                                         UpdateExpression="SET boost_usd = :u, boost_period = :p",
                                         ExpressionAttributeValues={":u": {"S": "lots"}, ":p": {"L": [{"S": "Monthly"}]}})
    assert call(api, "GET", "/api/v2/users/alice")[0] == 200  # a List boost_period no longer raises (policy.limits)
    s, r = call(api, "DELETE", "/api/v2/users/alice/boost", {"reason": "clean up"})
    assert s == 200 and r["audit"]["before"]["boost_usd"] == '<S> "lots"'
    it = boto3.client("dynamodb").get_item(TableName="t", Key={"pk": {"S": "USER#alice"}, "sk": {"S": "BUDGET"}})["Item"]
    assert "boost_usd" not in it and "boost_period" not in it


def test_a_policy_save_keeps_hand_written_attributes_exactly(api):
    # r147 /code-review: unedited attributes were round-tripped through plain() and _av(), so a binary or number
    # set came back as a string or a list.
    c = boto3.client("dynamodb")
    c.put_item(TableName="t", Item={"pk": {"S": "POLICY"}, "sk": {"S": "ORG"}, "note_bin": {"B": b"\x01\x02"}, "codes": {"NS": ["1", "2"]}})
    s, _ = call(api, "PUT", "/api/v2/policy/org", {"monthly_usd": 9})
    it = c.get_item(TableName="t", Key={"pk": {"S": "POLICY"}, "sk": {"S": "ORG"}})["Item"]
    assert s == 200 and it["note_bin"] == {"B": b"\x01\x02"} and sorted(it["codes"]["NS"]) == ["1", "2"] and it["monthly_usd"] == {"N": "9.0"}


def test_a_grant_over_a_hand_edited_boost_keeps_what_was_there_in_the_audit(api):
    boto3.client("dynamodb").update_item(TableName="t", Key={"pk": {"S": "USER#alice"}, "sk": {"S": "BUDGET"}},
                                         UpdateExpression="SET boost_usd = :u", ExpressionAttributeValues={":u": {"S": "lots"}})
    s, r = call(api, "POST", "/api/v2/users/alice/boost", {"usd": 5, "hours": 1, "period": "Monthly", "reason": "release"})
    assert s == 200 and r["audit"]["before"] == {"boost_usd": '<S> "lots"'}


def test_a_hand_written_numeric_policy_version_is_saved_over_not_a_500(api):
    # r148 /code-review: the lock condition sent the version as {"S": <number>}, which botocore refused (a 500).
    boto3.client("dynamodb").put_item(TableName="t", Item={"pk": {"S": "POLICY"}, "sk": {"S": "ORG"}, "version": {"N": "3"}})
    s, r = call(api, "PUT", "/api/v2/policy/org", {"monthly_usd": 9, "version": 3.0})
    assert s == 200, r


def test_two_hand_edits_that_clean_alike_are_two_versions(api):
    # r148 /code-review: the version hashed cleaned values, so "x" and "y" (both cleaned to a block) were one version.
    a = api.budget_version({"budget_usd": {"S": "x"}})
    assert a != api.budget_version({"budget_usd": {"S": "y"}}) and a == api.budget_version({"budget_usd": {"S": "x"}})


@pytest.mark.parametrize("stored,sent", [({"N": "0"}, 0.0), ({"S": ""}, "")])
def test_a_falsy_hand_written_version_is_a_version(api, stored, sent):
    # r149 /code-review: a version of 0 or "" counted as absent, so the save asked attribute_not_exists and always failed.
    boto3.client("dynamodb").put_item(TableName="t", Item={"pk": {"S": "POLICY"}, "sk": {"S": "ORG"}, "version": stored})
    s, r = call(api, "PUT", "/api/v2/policy/org", {"monthly_usd": 9, "version": sent})
    assert s == 200, r


def test_a_boost_granted_between_load_and_save_makes_the_limits_save_a_409(api, monkeypatch):
    # r150 /code-review: the limits write guarded only the limit attributes, though the version it checked covers the
    # boost too. A boost granted after the page loaded now fails the save (409) instead of being silently kept.
    v0 = call(api, "GET", "/api/v2/users/alice")[1]["version"]
    raw = api._known_budget("alice")  # what the save will read before another admin's boost lands
    monkeypatch.setattr(api, "_known_budget", lambda u: raw)
    monkeypatch.setattr(api, "_check_version", lambda *a: None)  # the read-time check passed; only the write condition is left
    boto3.client("dynamodb").update_item(TableName="t", Key={"pk": {"S": "USER#alice"}, "sk": {"S": "BUDGET"}},
                                         UpdateExpression="SET boost_usd = :u", ExpressionAttributeValues={":u": {"N": "5"}})
    assert call(api, "PUT", "/api/v2/users/alice/limits", {"monthly_usd": 7, "version": v0})[0] == 409

def test_a_limit_changed_between_load_and_boost_makes_the_boost_a_409(api, monkeypatch):
    # r151 /code-review: the boost write guarded only the boost attributes and one cap, though its version covers
    # every limit. A daily cap set after the page loaded now fails the boost (409).
    seed("alice")
    raw = api._known_budget("alice")
    monkeypatch.setattr(api, "_known_budget", lambda u: raw)
    monkeypatch.setattr(api, "_check_version", lambda *a: None)
    boto3.client("dynamodb").update_item(TableName="t", Key={"pk": {"S": "USER#alice"}, "sk": {"S": "BUDGET"}},
                                         UpdateExpression="SET daily_usd = :d", ExpressionAttributeValues={":d": {"N": "3"}})
    s, _ = call(api, "POST", "/api/v2/users/alice/boost", {"usd": 5, "hours": 1, "period": "Monthly", "reason": "release"})
    assert s == 409


def test_an_audit_row_can_only_be_added_never_replaced(api):
    """r192: the dashboard role's PutItem on AUDIT partitions is the append-only control; each audit Put now also
    carries attribute_not_exists, so a write that reused an existing key would fail rather than rewrite history."""
    aud = api._audit_item("arn:test", "set_org_policy", "policy:org", {}, {"rpm": 1}, "")
    puts = api._audit_puts(aud)
    assert [p["Put"].get("ConditionExpression") for p in puts] == ["attribute_not_exists(sk)"] * 2
    api.ddb.transact_write_items(TransactItems=puts)
    with pytest.raises(api.ddb.exceptions.TransactionCanceledException):
        api.ddb.transact_write_items(TransactItems=api._audit_puts({**aud, "reason": {"S": "rewritten"}}))


def test_an_audit_row_names_the_idp_subject_as_well_as_the_email(api):
    """r194: the email is user-editable in some IdPs; each row now also carries the immutable sub."""
    seed("alice")
    call(api, "PUT", "/api/v2/users/alice/limits", {"monthly_usd": 5, "reason": "r"},
         ident={**ADMIN, "email": "carol@example.com", "sub": "00u-carol"})
    s, r = call(api, "GET", "/api/v2/audit", q={"actor": "00u-carol"})
    assert s == 200 and [x["actor"] for x in r["rows"]] == ["carol@example.com (00u-carol)"]


@pytest.mark.parametrize("bm", ["\x1b[2J", "line\nbreak", "\u202eevil", "\x9b2J", "hid\u200bden", "\ufeffbom"])
def test_a_block_message_with_control_or_direction_characters_is_refused(api, monkeypatch, bm):
    """r196 threat model T-069: opencode shows the refusal text as is, so the org message must not carry terminal
    control sequences, line breaks or direction overrides into every capped developer's session."""
    writes = []
    monkeypatch.setattr(api.ddb, "transact_write_items", lambda **kw: writes.append(kw))
    s, r = call(api, "PUT", "/api/v2/policy/org", {"block_message": bm, "version": None})
    assert s == 400 and r["field"] == "block_message" and "control or direction" in r["error"] and writes == []
    monkeypatch.undo()
    s, r = call(api, "PUT", "/api/v2/policy/org", {"block_message": "Ask #ai-help for a boost.", "version": None})
    assert s == 200 and r["item"]["block_message"] == "Ask #ai-help for a boost."
