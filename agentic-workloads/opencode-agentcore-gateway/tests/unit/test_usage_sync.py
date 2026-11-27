# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""usage_sync prices Mantle per-project tokens into MONTH, DAY and HOUR ledger items (model_spend_usd).

CloudWatch is a fake; DynamoDB is moto.
"""
import collections
import datetime as dt
import importlib
import json
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

MOTO_KEY = "x"  # moto accepts any key; not a credential

PRICES = {"moonshotai.kimi-k3": {"alias": "ow/kimi-k3", "in": 3.3, "out": 16.5},
          "zai.glm-5.3": {"alias": "ow/glm-5.3", "in": 1.848, "out": 5.808},
          "deepseek.v3.2": {"alias": "ow/deepseek-v3.2", "in": 0.62, "out": 1.85}}


class _CW:
    """Mantle metrics: {period: {model: {metric: total}}}, the same for every project; ListMetrics pages of `page`."""

    def __init__(self, by_period, projects=("proj_a",), models=("moonshotai.kimi-k3",), page=500):
        self.by_period, self.projects, self.models, self.page, self.calls = by_period, projects, models, page, collections.Counter()

    def list_metrics(self, Namespace, MetricName, NextToken=None):  # noqa: N803
        assert Namespace == "AWS/BedrockMantle"
        self.calls["list"] += 1
        ms = [{"MetricName": MetricName, "Dimensions": [{"Name": "Project", "Value": p}, {"Name": "Model", "Value": m}]}
              for p in self.projects for m in self.models]
        i = int(NextToken or 0)
        return {"Metrics": ms[i:i + self.page], **({"NextToken": str(i + self.page)} if i + self.page < len(ms) else {})}

    def get_metric_data(self, StartTime, EndTime, MetricDataQueries, NextToken=None):  # noqa: N803
        self.calls["data"] += 1
        assert len(MetricDataQueries) <= 500
        out = []
        for q in MetricDataQueries:
            st = q["MetricStat"]
            d = {x["Name"]: x["Value"] for x in st["Metric"]["Dimensions"]}
            v = self.by_period.get(st["Period"], {}).get(d["Model"], {}).get(st["Metric"]["MetricName"], 0)
            out.append({"Id": q["Id"], "Values": [v]})
        return {"MetricDataResults": out}


@pytest.fixture()
def sync(monkeypatch):
    for k, v in dict(TABLE="t", PROJECTS=json.dumps({"alice": "proj_a"}), PRICES=json.dumps(PRICES), SK_INDEX="",
                     AWS_DEFAULT_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY=MOTO_KEY).items():
        monkeypatch.setenv(k, v)
    with mock_aws():
        c = boto3.client("dynamodb")
        c.create_table(TableName="t", KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
                       AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}, {"AttributeName": "sk", "AttributeType": "S"}],
                       BillingMode="PAY_PER_REQUEST")
        import usage_sync
        importlib.reload(usage_sync)
        yield usage_sync, c


def _get(c, sk, user="alice"):
    return c.get_item(TableName="t", Key={"pk": {"S": f"USER#{user}"}, "sk": {"S": sk}}).get("Item", {})


def _n(item, k):
    return float(item.get(k, {}).get("N", 0))


def keys(now):
    return f"DAY#{now:%Y-%m-%d}", f"HOUR#{now:%Y-%m-%dT%H}", f"MONTH#{now:%Y-%m}"


# (input tokens, output tokens) -> USD for Kimi K3 at $3.30 / $16.50 per 1M
CASES = [(0, 0), (1e6, 0), (0, 1e6), (1e6, 1e5), (123456, 7890), (5e7, 2e6)]


@pytest.mark.parametrize("hour", CASES)
@pytest.mark.parametrize("day", CASES[:4])
def test_windows(sync, monkeypatch, day, hour):
    us, c = sync
    month = (day[0] * 3, day[1] * 3)
    cw = _CW({3600: {"moonshotai.kimi-k3": {"InputTokens": hour[0], "OutputTokens": hour[1]}},
              86400: {"moonshotai.kimi-k3": {"InputTokens": month[0], "OutputTokens": month[1]}}})
    monkeypatch.setattr(us, "cw", cw)
    now = dt.datetime(2026, 11, 10, 13, 30, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(us, "_now", lambda: now)
    us.lambda_handler()
    d, h, m = keys(now)
    usd = lambda i, o: i * 3.3 / 1e6 + o * 16.5 / 1e6  # noqa: E731
    assert _n(_get(c, h), "model_spend_usd") == pytest.approx(usd(*hour))
    # The day and month windows both use daily periods in the fake, so both read the month totals.
    assert _n(_get(c, m), "model_spend_usd") == pytest.approx(usd(*month))
    assert _n(_get(c, m), "model_input_tokens") == pytest.approx(month[0])
    assert _n(_get(c, m), "model_output_tokens") == pytest.approx(month[1])


@pytest.mark.parametrize("model,rate_in,rate_out", [("moonshotai.kimi-k3", 3.3, 16.5), ("zai.glm-5.3", 1.848, 5.808), ("deepseek.v3.2", 0.62, 1.85)])
def test_each_model_priced(sync, monkeypatch, model, rate_in, rate_out):
    us, c = sync
    monkeypatch.setattr(us, "cw", _CW({3600: {model: {"InputTokens": 2e6, "OutputTokens": 1e6}}, 86400: {model: {"InputTokens": 2e6, "OutputTokens": 1e6}}}, models=(model,)))
    us.lambda_handler()
    _, _, m = keys(dt.datetime.now(dt.timezone.utc))
    assert _n(_get(c, m), "model_spend_usd") == pytest.approx(2 * rate_in + rate_out)


def test_models_summed_and_unpriced_reported(sync, monkeypatch):
    us, c = sync
    per = {"moonshotai.kimi-k3": {"InputTokens": 1e6, "OutputTokens": 0}, "zai.glm-5.3": {"InputTokens": 1e6, "OutputTokens": 0},
           "mystery.model": {"InputTokens": 1e6, "OutputTokens": 1e6}}
    monkeypatch.setattr(us, "cw", _CW({3600: per, 86400: per}, models=tuple(per)))
    r = us.lambda_handler()
    assert r["alice"]["model_spend_usd"] == pytest.approx(3.3 + 1.848)
    assert r["alice"]["unpriced"] == ["mystery.model"]
    _, _, m = keys(dt.datetime.now(dt.timezone.utc))
    assert set(json.loads(_get(c, m)["model_by_model"]["S"])) == set(per)


def test_websearch_spend_not_overwritten(sync, monkeypatch):
    us, c = sync
    _, _, m = keys(dt.datetime.now(dt.timezone.utc))
    c.put_item(TableName="t", Item={"pk": {"S": "USER#alice"}, "sk": {"S": m}, "spend_usd": {"N": "0.021"}})
    monkeypatch.setattr(us, "cw", _CW({3600: {}, 86400: {"moonshotai.kimi-k3": {"InputTokens": 1e6, "OutputTokens": 0}}}))
    us.lambda_handler()
    it = _get(c, m)
    assert _n(it, "spend_usd") == pytest.approx(0.021) and _n(it, "model_spend_usd") == pytest.approx(3.3)


@pytest.mark.parametrize("bad", [{"N": "-3"}, {"S": "x"}])
def test_a_bad_websearch_spend_is_reported_as_zero_like_everywhere_else(sync, monkeypatch, bad):
    # r142 /code-review: the per-user result read spend_usd with num_attr, so -3 was reported (and lowered the logged
    # total) while the rollups and the dashboard counted it as 0 (policy.amount).
    us, c = sync
    _, _, m = keys(dt.datetime.now(dt.timezone.utc))
    c.put_item(TableName="t", Item={"pk": {"S": "USER#alice"}, "sk": {"S": m}, "spend_usd": bad})
    monkeypatch.setattr(us, "cw", _CW({3600: {}, 86400: {"moonshotai.kimi-k3": {"InputTokens": 1e6, "OutputTokens": 0}}}))
    assert us.lambda_handler()["alice"]["websearch_spend_usd"] == 0.0


def test_window_items_created_with_ttl(sync, monkeypatch):
    us, c = sync
    used = {"moonshotai.kimi-k3": {"InputTokens": 10, "OutputTokens": 10}}  # usage in the hour is usage in the day
    monkeypatch.setattr(us, "cw", _CW({3600: used, 86400: used}))
    us.lambda_handler()
    d, h, _ = keys(dt.datetime.now(dt.timezone.utc))
    assert "ttl" in _get(c, d) and "ttl" in _get(c, h)


def test_model_missing_from_listmetrics_keeps_month_tokens(sync, monkeypatch):
    us, c = sync
    _, _, m = keys(dt.datetime.now(dt.timezone.utc))
    c.put_item(TableName="t", Item={"pk": {"S": "USER#alice"}, "sk": {"S": m},
                                    "model_by_model": {"S": json.dumps({"zai.glm-5.3": {"in": 1e6, "out": 0}})}})
    monkeypatch.setattr(us, "cw", _CW({3600: {}, 86400: {"moonshotai.kimi-k3": {"InputTokens": 1e6, "OutputTokens": 0}}}))
    us.lambda_handler()
    assert _n(_get(c, m), "model_spend_usd") == pytest.approx(3.3 + 1.848)


def test_corrupt_stored_breakdown_ignored(sync, monkeypatch):
    us, c = sync
    _, _, m = keys(dt.datetime.now(dt.timezone.utc))
    c.put_item(TableName="t", Item={"pk": {"S": "USER#alice"}, "sk": {"S": m}, "model_by_model": {"S": "{not json"}})
    monkeypatch.setattr(us, "cw", _CW({3600: {}, 86400: {"moonshotai.kimi-k3": {"InputTokens": 1e6, "OutputTokens": 0}}}))
    us.lambda_handler()
    assert _n(_get(c, m), "model_spend_usd") == pytest.approx(3.3)


@pytest.mark.parametrize("n", [1, 60, 300])
def test_many_users_batched(sync, monkeypatch, n):
    us, c = sync
    projects = {f"u{i}": f"proj_{i}" for i in range(n)}
    monkeypatch.setattr(us, "PROJECTS", projects)
    # Mid-hour: in the first CLOSE_MINUTES of an hour (more at 00:xx) the closed windows add their own passes.
    monkeypatch.setattr(us, "_now", lambda: dt.datetime(2026, 11, 12, 10, 30, tzinfo=dt.timezone.utc))
    cw = _CW({3600: {"moonshotai.kimi-k3": {"InputTokens": 1e6, "OutputTokens": 0}}, 86400: {"moonshotai.kimi-k3": {"InputTokens": 1e6, "OutputTokens": 0}}},
             projects=tuple(projects.values()), page=100)
    monkeypatch.setattr(us, "cw", cw)
    r = us.lambda_handler()
    assert len([k for k in r if not k.startswith("_")]) == n
    assert cw.calls["list"] == 2 * max(1, -(-n // 100))  # two metric names, paged
    assert cw.calls["data"] <= 3 * max(1, -(-2 * n // 500)) + 3


def test_tags_rollup_and_stale_reset(sync, monkeypatch):
    us, c = sync
    _, _, m = keys(dt.datetime.now(dt.timezone.utc))
    c.put_item(TableName="t", Item={"pk": {"S": "USER#alice"}, "sk": {"S": "PROFILE"}, "tags": {"M": {"team": {"S": "red"}}}})
    monkeypatch.setattr(us, "cw", _CW({3600: {}, 86400: {"moonshotai.kimi-k3": {"InputTokens": 1e6, "OutputTokens": 0}}}))
    us.lambda_handler()
    red = c.get_item(TableName="t", Key={"pk": {"S": "TAG#team=red"}, "sk": {"S": m}})["Item"]
    assert _n(red, "model_spend_usd") == pytest.approx(3.3)
    assert _n(c.get_item(TableName="t", Key={"pk": {"S": "TAG#project=unassigned"}, "sk": {"S": m}})["Item"], "model_spend_usd") == pytest.approx(3.3)
    c.put_item(TableName="t", Item={"pk": {"S": "USER#alice"}, "sk": {"S": "PROFILE"}, "tags": {"M": {"team": {"S": "blue"}}}})
    us.lambda_handler()
    assert "Item" not in c.get_item(TableName="t", Key={"pk": {"S": "TAG#team=red"}, "sk": {"S": m}})  # stale value deleted
    blue = c.get_item(TableName="t", Key={"pk": {"S": "TAG#team=blue"}, "sk": {"S": m}})["Item"]
    assert _n(blue, "m|ow/kimi-k3|usd") == pytest.approx(3.3)  # per-model counters, so tag views can filter by model
    assert _n(c.get_item(TableName="t", Key={"pk": {"S": "TAG#team=blue"}, "sk": {"S": m}})["Item"], "model_spend_usd") == pytest.approx(3.3)


@pytest.mark.parametrize("hhmm,day,want", [((0, 5), 1, {"HOUR", "DAY", "MONTH"}), ((0, 5), 15, {"HOUR", "DAY"}), ((13, 5), 15, {"HOUR"}),
                                           ((13, 15), 15, {"HOUR"}), ((13, 25), 15, set())])
def test_closed_windows_resynced_after_rollover(sync, monkeypatch, hhmm, day, want):
    us, c = sync
    now = dt.datetime(2026, 11, day, hhmm[0], hhmm[1], tzinfo=dt.timezone.utc)
    monkeypatch.setattr(us, "_now", lambda: now)
    monkeypatch.setattr(us, "cw", _CW({3600: {"moonshotai.kimi-k3": {"InputTokens": 1e6, "OutputTokens": 0}},
                                       86400: {"moonshotai.kimi-k3": {"InputTokens": 1e6, "OutputTokens": 0}}}))
    us.lambda_handler()
    prev = {"HOUR": f"HOUR#{now - dt.timedelta(hours=1):%Y-%m-%dT%H}", "DAY": f"DAY#{now - dt.timedelta(days=1):%Y-%m-%d}",
            "MONTH": f"MONTH#{(now.replace(day=1) - dt.timedelta(days=1)):%Y-%m}"}
    got = {w for w, sk in prev.items() if _n(_get(c, sk), "model_spend_usd") > 0}
    assert got == want


def test_cost_function_direct():
    import usage_sync as us
    assert us.mantle_cost({}) == (0.0, 0.0, 0.0, [])


def test_group_and_model_rollups(sync, monkeypatch):
    us, c = sync
    monkeypatch.setattr(us, "PROJECTS", {"alice": "proj_a", "bob": "proj_b"})
    c.put_item(TableName="t", Item={"pk": {"S": "USER#alice"}, "sk": {"S": "BUDGET"}, "token_groups": {"L": [{"S": "ocgw-team-a"}]}})
    c.put_item(TableName="t", Item={"pk": {"S": "USER#bob"}, "sk": {"S": "BUDGET"}, "token_groups": {"L": [{"S": "ocgw-team-a"}, {"S": "ocgw-team-b"}]}})
    per = {"moonshotai.kimi-k3": {"InputTokens": 1e6, "OutputTokens": 0}, "zai.glm-5.3": {"InputTokens": 1e6, "OutputTokens": 1e6}}
    monkeypatch.setattr(us, "cw", _CW({3600: per, 86400: per}, projects=("proj_a", "proj_b"), models=tuple(per)))
    us.lambda_handler()
    now = dt.datetime.now(dt.timezone.utc)
    d, _, m = keys(now)
    get = lambda pk, sk: c.get_item(TableName="t", Key={"pk": {"S": pk}, "sk": {"S": sk}})["Item"]  # noqa: E731
    one = 3.3 + 1.848 + 5.808
    a = get("GROUP#ocgw-team-a", m)
    assert _n(a, "model_spend_usd") == pytest.approx(2 * one) and set(a["user_set"]["SS"]) == {"alice", "bob"}
    assert _n(get("GROUP#ocgw-team-b", m), "model_spend_usd") == pytest.approx(one)
    assert _n(get("GROUP#ocgw-team-a", d), "model_spend_usd") == pytest.approx(2 * one)
    assert _n(get("MODEL#ow/kimi-k3", m), "model_spend_usd") == pytest.approx(2 * 3.3)
    assert _n(get("MODEL#ow/glm-5.3", d), "model_spend_usd") == pytest.approx(2 * (1.848 + 5.808))
    u = _get(c, m)
    assert _n(u, "m|ow/kimi-k3|usd") == pytest.approx(3.3) and _n(u, "m|ow/glm-5.3|out") == 1e6


def test_user_without_groups_rolls_up_under_no_group(sync, monkeypatch):
    us, c = sync
    monkeypatch.setattr(us, "cw", _CW({3600: {}, 86400: {"moonshotai.kimi-k3": {"InputTokens": 1e6, "OutputTokens": 0}}}))
    us.lambda_handler()
    _, _, m = keys(dt.datetime.now(dt.timezone.utc))
    it = c.get_item(TableName="t", Key={"pk": {"S": "GROUP#(no group)"}, "sk": {"S": m}})["Item"]
    assert _n(it, "model_spend_usd") == pytest.approx(3.3)


def test_price_key_prefers_exact_then_longest(monkeypatch):
    import usage_sync as m
    prices = {"deepseek.v3.2": {"alias": "ow/deepseek-v3.2", "in": 0.62, "out": 1.85},
              "zai.glm-5": {"alias": "ow/glm-5", "in": 1.0, "out": 3.2},
              "zai.glm-5.3": {"alias": "ow/glm-5.3", "in": 1.848, "out": 5.808}}
    monkeypatch.setattr(m, "PRICES", prices)
    monkeypatch.setattr(m, "_PRICE_KEYS", {})  # the cache belongs to the PRICES it was built from
    assert m.price("zai.glm-5.3")["in"] == 1.848
    assert m.alias_of("zai.glm-5.3") == "ow/glm-5.3"
    assert m.alias_of("zai.glm-5") == "ow/glm-5"
    assert m.alias_of("us.zai.glm-5.3-v1") == "ow/glm-5.3"   # contained id: longest key wins
    assert m.price("unknown.model") is None and m.alias_of("unknown.model") == "unknown.model"


@pytest.mark.parametrize("model,key", [
    ("zai.glm-5", "zai.glm-5"), ("us.zai.glm-5", "zai.glm-5"), ("zai.glm-5:0", "zai.glm-5"), ("zai.glm-5-v1:0", "zai.glm-5"),
    ("zai.glm-5.3", None), ("us.zai.glm-5.3-v1", None), ("zai.glm-50", None), ("xzai.glm-5", None),
])
def test_a_model_that_only_contains_a_priced_id_is_unpriced(monkeypatch, model, key):
    import usage_sync as m
    monkeypatch.setattr(m, "PRICES", {"zai.glm-5": {"alias": "ow/glm-5", "in": 1.0, "out": 3.2}})
    monkeypatch.setattr(m, "_PRICE_KEYS", {})  # the cache belongs to the PRICES it was built from
    assert m.price_key(model) == key


def test_group_move_deletes_old_group_rollup(sync, monkeypatch):
    us, c = sync
    monkeypatch.setattr(us, "cw", _CW({3600: {}, 86400: {"moonshotai.kimi-k3": {"InputTokens": 1e6, "OutputTokens": 0}}}))
    budget = lambda g: c.put_item(TableName="t", Item={"pk": {"S": f"USER#{next(iter(us.PROJECTS))}"}, "sk": {"S": "BUDGET"},  # noqa: E731
                                                       "token_groups": {"L": [{"S": g}]}})
    budget("team-a")
    us.lambda_handler()
    _, _, m = keys(dt.datetime.now(dt.timezone.utc))
    assert "Item" in c.get_item(TableName="t", Key={"pk": {"S": "GROUP#team-a"}, "sk": {"S": m}})
    budget("team-b")
    us.lambda_handler()
    assert "Item" not in c.get_item(TableName="t", Key={"pk": {"S": "GROUP#team-a"}, "sk": {"S": m}})
    assert _n(c.get_item(TableName="t", Key={"pk": {"S": "GROUP#team-b"}, "sk": {"S": m}})["Item"], "model_spend_usd") == pytest.approx(3.3)


def test_closed_day_resyncs_group_and_model_rollups(sync, monkeypatch):
    us, c = sync
    now = dt.datetime(2026, 11, 15, 0, 5, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(us, "_now", lambda: now)
    monkeypatch.setattr(us, "cw", _CW({3600: {"moonshotai.kimi-k3": {"InputTokens": 1e6, "OutputTokens": 0}},
                                       86400: {"moonshotai.kimi-k3": {"InputTokens": 1e6, "OutputTokens": 0}}}))
    us.lambda_handler()
    yday = f"DAY#{now - dt.timedelta(days=1):%Y-%m-%d}"
    assert _n(c.get_item(TableName="t", Key={"pk": {"S": "MODEL#ow/kimi-k3"}, "sk": {"S": yday}})["Item"], "model_spend_usd") > 0
    assert _n(c.get_item(TableName="t", Key={"pk": {"S": "GROUP#(no group)"}, "sk": {"S": yday}})["Item"], "model_spend_usd") > 0


def test_requests_rolled_up_to_group_and_model(sync, monkeypatch):
    us, c = sync
    user = next(iter(us.PROJECTS))
    now = dt.datetime.now(dt.timezone.utc)
    d, _, m = keys(now)
    for sk in (m, d):
        c.put_item(TableName="t", Item={"pk": {"S": f"USER#{user}"}, "sk": {"S": sk}, "requests": {"N": "7"}, "m|ow/kimi-k3|req": {"N": "5"},
                                        "m|ow/glm-5.3|req": {"N": "2"}})
    c.put_item(TableName="t", Item={"pk": {"S": f"USER#{user}"}, "sk": {"S": "BUDGET"}, "token_groups": {"L": [{"S": "team-a"}]}})
    monkeypatch.setattr(us, "cw", _CW({3600: {}, 86400: {"moonshotai.kimi-k3": {"InputTokens": 1e6, "OutputTokens": 0}}}))
    us.lambda_handler()
    get = lambda pk, sk: c.get_item(TableName="t", Key={"pk": {"S": pk}, "sk": {"S": sk}})["Item"]  # noqa: E731
    for sk in (m, d):
        g = get("GROUP#team-a", sk)
        assert g["requests"]["N"] == "7" and g["m|ow/kimi-k3|req"]["N"] == "5"
        assert get("MODEL#ow/kimi-k3", sk)["requests"]["N"] == "5"
        assert get("MODEL#ow/glm-5.3", sk)["requests"]["N"] == "2"  # requests with no token metrics yet still show
    assert get(f"USER#{user}", m)["requests"]["N"] == "7"  # user counters untouched


def test_closed_day_and_month_keep_their_request_counts(sync, monkeypatch):
    us, c = sync
    user = next(iter(us.PROJECTS))
    now = dt.datetime(2026, 12, 1, 0, 5, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(us, "_now", lambda: now)
    yday, pmonth = "DAY#2026-11-30", "MONTH#2026-11"
    for sk in (yday, pmonth):
        c.put_item(TableName="t", Item={"pk": {"S": f"USER#{user}"}, "sk": {"S": sk}, "requests": {"N": "4"}, "m|ow/kimi-k3|req": {"N": "4"}})
    monkeypatch.setattr(us, "cw", _CW({3600: {"moonshotai.kimi-k3": {"InputTokens": 1e6, "OutputTokens": 0}},
                                       86400: {"moonshotai.kimi-k3": {"InputTokens": 1e6, "OutputTokens": 0}}}))
    us.lambda_handler()
    for sk in (yday, pmonth):
        assert c.get_item(TableName="t", Key={"pk": {"S": "MODEL#ow/kimi-k3"}, "sk": {"S": sk}})["Item"]["requests"]["N"] == "4"
        assert c.get_item(TableName="t", Key={"pk": {"S": "GROUP#(no group)"}, "sk": {"S": sk}})["Item"]["requests"]["N"] == "4"


def test_idle_users_do_not_create_group_rows(sync, monkeypatch):
    us, c = sync
    monkeypatch.setattr(us, "PROJECTS", {"alice": "proj_a", "bob": "proj_b"})
    c.put_item(TableName="t", Item={"pk": {"S": "USER#bob"}, "sk": {"S": "BUDGET"}, "token_groups": {"L": [{"S": "idle-team"}]}})
    per = {"moonshotai.kimi-k3": {"InputTokens": 1e6, "OutputTokens": 0}}
    monkeypatch.setattr(us, "cw", _CW({3600: {}, 86400: per}, projects=("proj_a",), models=tuple(per)))
    us.lambda_handler()
    _, _, m = keys(dt.datetime.now(dt.timezone.utc))
    assert "Item" not in c.get_item(TableName="t", Key={"pk": {"S": "GROUP#idle-team"}, "sk": {"S": m}})
    g = c.get_item(TableName="t", Key={"pk": {"S": "GROUP#(no group)"}, "sk": {"S": m}})["Item"]
    assert g["user_set"]["SS"] == ["alice"]


@pytest.mark.parametrize("n", [3, 250])
def test_per_user_items_read_in_batches_not_one_getitem_each(sync, monkeypatch, n):
    us, c = sync
    monkeypatch.setattr(us, "PROJECTS", {f"u{i}": f"proj_{i}" for i in range(n)})
    monkeypatch.setattr(us, "cw", _CW({3600: {}, 86400: {}}))
    gets, batches = [], []
    real_get, real_batch = us.ddb.get_item, us.ddb.batch_get_item
    monkeypatch.setattr(us.ddb, "get_item", lambda **kw: (gets.append(kw["Key"]["pk"]["S"]), real_get(**kw))[1])
    monkeypatch.setattr(us.ddb, "batch_get_item", lambda **kw: (batches.append(1), real_batch(**kw))[1])
    us.lambda_handler()
    assert not [g for g in gets if g.startswith("USER#")]  # no per-user GetItem
    assert len(batches) >= -(-4 * n // 100)


def test_batch_get_retries_unprocessed_then_fails_loudly(sync, monkeypatch):
    us, c = sync
    monkeypatch.setattr(us.policy.time, "sleep", lambda s: None)
    calls = []

    def flaky(RequestItems):  # noqa: N803
        calls.append(1)
        keys = RequestItems[us.TABLE]["Keys"]
        return {"Responses": {us.TABLE: []}, "UnprocessedKeys": {us.TABLE: {"Keys": keys}}}
    monkeypatch.setattr(us.ddb, "batch_get_item", flaky)
    with pytest.raises(RuntimeError, match="unprocessed"):
        us.batch_get([("USER#a", "BUDGET")])
    assert len(calls) == 8

    seq = iter([{"Responses": {us.TABLE: []}, "UnprocessedKeys": {us.TABLE: {"Keys": [{"pk": {"S": "USER#a"}, "sk": {"S": "BUDGET"}}]}}},
                {"Responses": {us.TABLE: [{"pk": {"S": "USER#a"}, "sk": {"S": "BUDGET"}, "rpm": {"N": "3"}}]}}])
    monkeypatch.setattr(us.ddb, "batch_get_item", lambda RequestItems: next(seq))  # noqa: N803
    assert us.batch_get([("USER#a", "BUDGET")])[("USER#a", "BUDGET")]["rpm"]["N"] == "3"


@pytest.mark.parametrize("at", [
    "2026-11-10T14:30:00",  # open windows only
    "2026-11-10T14:05:00",  # the hour that just closed is re-synced
    "2026-11-10T00:05:00",  # closed hour and closed day
    "2026-11-01T00:05:00",  # closed hour, day and previous month
])
def test_idle_users_get_no_zero_writes(sync, monkeypatch, at):
    us, c = sync
    monkeypatch.setattr(us, "_now", lambda: dt.datetime.fromisoformat(at).replace(tzinfo=dt.timezone.utc))
    monkeypatch.setattr(us, "PROJECTS", {f"idle{i}": f"proj_{i}" for i in range(5)})
    monkeypatch.setattr(us, "cw", _CW({3600: {}, 86400: {}}))
    us.lambda_handler()
    items = c.scan(TableName="t")["Items"]
    assert [it for it in items if it["pk"]["S"].startswith("USER#idle")] == []
    assert [it for it in items if it["pk"]["S"].startswith(("TAG#", "GROUP#", "MODEL#"))] == []


def test_closed_month_stale_tag_value_deleted_on_day_one(sync, monkeypatch):
    us, c = sync
    monkeypatch.setattr(us, "cw", _CW({3600: {}, 86400: {"moonshotai.kimi-k3": {"InputTokens": 1e6, "OutputTokens": 0}}}))
    c.put_item(TableName="t", Item={"pk": {"S": "USER#alice"}, "sk": {"S": "PROFILE"}, "tags": {"M": {"team": {"S": "a"}}}})
    monkeypatch.setattr(us, "_now", lambda: dt.datetime(2026, 10, 31, 23, 55, tzinfo=dt.timezone.utc))
    us.lambda_handler()  # last October run: team=a
    oct_key = lambda v: {"pk": {"S": f"TAG#team={v}"}, "sk": {"S": "MONTH#2026-10"}}  # noqa: E731
    assert "Item" in c.get_item(TableName="t", Key=oct_key("a"))
    c.put_item(TableName="t", Item={"pk": {"S": "USER#alice"}, "sk": {"S": "PROFILE"}, "tags": {"M": {"team": {"S": "b"}}}})
    monkeypatch.setattr(us, "_now", lambda: dt.datetime(2026, 11, 1, 0, 5, tzinfo=dt.timezone.utc))
    us.lambda_handler()  # day one re-sync of October with the tag now b
    assert "Item" in c.get_item(TableName="t", Key=oct_key("b"))
    assert "Item" not in c.get_item(TableName="t", Key=oct_key("a"))  # counted once, not under both values


def test_web_search_spend_reaches_group_and_tag_rollups(sync, monkeypatch):
    us, c = sync
    now = dt.datetime(2026, 11, 10, 14, 30, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(us, "_now", lambda: now)
    monkeypatch.setattr(us, "cw", _CW({3600: {}, 86400: {}}))  # no Mantle tokens: search-only usage
    c.put_item(TableName="t", Item={"pk": {"S": "USER#alice"}, "sk": {"S": "BUDGET"}, "token_groups": {"L": [{"S": "team-a"}]}})
    c.put_item(TableName="t", Item={"pk": {"S": "USER#alice"}, "sk": {"S": "PROFILE"}, "tags": {"M": {"team": {"S": "red"}}}})
    c.put_item(TableName="t", Item={"pk": {"S": "USER#alice"}, "sk": {"S": "MONTH#2026-11"}, "spend_usd": {"N": "7"}})
    c.put_item(TableName="t", Item={"pk": {"S": "USER#alice"}, "sk": {"S": "DAY#2026-11-10"}, "spend_usd": {"N": "2"}})
    us.lambda_handler()
    get = lambda pk, sk: c.get_item(TableName="t", Key={"pk": {"S": pk}, "sk": {"S": sk}}).get("Item", {})  # noqa: E731
    assert _n(get("GROUP#team-a", "MONTH#2026-11"), "spend_usd") == pytest.approx(7)
    assert _n(get("GROUP#team-a", "DAY#2026-11-10"), "spend_usd") == pytest.approx(2)
    assert _n(get("TAG#team=red", "MONTH#2026-11"), "spend_usd") == pytest.approx(7)


def test_month_merge_is_per_metric(sync, monkeypatch):
    us, c = sync
    now = dt.datetime(2026, 11, 20, 12, 30, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(us, "_now", lambda: now)
    stored = {"moonshotai.kimi-k3": {"in": 1_000_000, "out": 1_000_000}}
    c.put_item(TableName="t", Item={"pk": {"S": "USER#alice"}, "sk": {"S": "MONTH#2026-11"}, "model_by_model": {"S": json.dumps(stored)},
                                    "model_synced_at": {"S": "x"}})
    # InputTokens re-listed (more input this month); OutputTokens not listed yet, so it reads 0
    monkeypatch.setattr(us, "cw", _CW({3600: {}, 86400: {"moonshotai.kimi-k3": {"InputTokens": 2e6}}}))
    us.lambda_handler()
    got = json.loads(_get(c, "MONTH#2026-11")["model_by_model"]["S"])["moonshotai.kimi-k3"]
    assert got["in"] == 2e6 and got["out"] == 1_000_000  # output kept, not zeroed


def test_tag_rollups_carry_request_counts(sync, monkeypatch):
    us, c = sync
    monkeypatch.setattr(us, "_now", lambda: dt.datetime(2026, 11, 10, 14, 30, tzinfo=dt.timezone.utc))
    monkeypatch.setattr(us, "cw", _CW({3600: {}, 86400: {"moonshotai.kimi-k3": {"InputTokens": 1e6, "OutputTokens": 0}}}))
    c.put_item(TableName="t", Item={"pk": {"S": "USER#alice"}, "sk": {"S": "PROFILE"}, "tags": {"M": {"team": {"S": "red"}}}})
    c.put_item(TableName="t", Item={"pk": {"S": "USER#alice"}, "sk": {"S": "MONTH#2026-11"}, "requests": {"N": "7"}, "m|ow/kimi-k3|req": {"N": "7"}})
    us.lambda_handler()
    row = c.get_item(TableName="t", Key={"pk": {"S": "TAG#team=red"}, "sk": {"S": "MONTH#2026-11"}})["Item"]
    assert _n(row, "requests") == 7 and _n(row, "m|ow/kimi-k3|req") == 7


def test_deferred_writes_go_out_after_the_block(sync, monkeypatch):
    us, c = sync
    sent = []
    monkeypatch.setattr(us.ddb, "update_item", lambda **kw: sent.append(kw["Key"]["pk"]["S"]))
    now = dt.datetime(2026, 11, 10, 14, 30, tzinfo=dt.timezone.utc)
    with us.deferred_writes():
        for u in ("a", "b", "c"):
            us.set_rollup({"pk": {"S": f"USER#{u}"}, "sk": {"S": "DAY#2026-11-10"}}, {}, now)
        assert sent == []  # nothing sent inside the block
    assert sorted(sent) == ["USER#a", "USER#b", "USER#c"] and us._DEFERRED is None


def test_metrics_with_extra_dimensions_are_not_added_twice(monkeypatch, sync):
    us, c = sync
    class LM:
        def list_metrics(self, **kw):
            base = [{"Name": "Project", "Value": "proj_a"}, {"Name": "Model", "Value": "moonshotai.kimi-k3"}]
            return {"Metrics": [{"MetricName": kw["MetricName"], "Dimensions": base},
                                {"MetricName": kw["MetricName"], "Dimensions": base + [{"Name": "Operation", "Value": "x"}]}]}
    monkeypatch.setattr(us, "cw", LM())
    assert [k for k, _, _ in us.mantle_metrics()["proj_a"]] == ["in", "out"]


def test_rollups_drop_counters_of_models_no_member_uses(sync, monkeypatch):
    us, c = sync
    monkeypatch.setattr(us, "_now", lambda: dt.datetime(2026, 11, 10, 14, 30, tzinfo=dt.timezone.utc))
    c.put_item(TableName="t", Item={"pk": {"S": "GROUP#team-a"}, "sk": {"S": "MONTH#2026-11"}, "m|ow/glm-5.3|usd": {"N": "9"},
                                    "m|ow/glm-5.3|req": {"N": "4"}})
    c.put_item(TableName="t", Item={"pk": {"S": "USER#alice"}, "sk": {"S": "BUDGET"}, "token_groups": {"L": [{"S": "team-a"}]}})
    monkeypatch.setattr(us, "cw", _CW({3600: {}, 86400: {"moonshotai.kimi-k3": {"InputTokens": 1e6, "OutputTokens": 0}}}))
    us.lambda_handler()
    row = c.get_item(TableName="t", Key={"pk": {"S": "GROUP#team-a"}, "sk": {"S": "MONTH#2026-11"}})["Item"]
    assert "m|ow/kimi-k3|usd" in row and "m|ow/glm-5.3|usd" not in row and "m|ow/glm-5.3|req" not in row


def test_no_zero_hour_row_when_the_hour_is_idle(sync, monkeypatch):
    us, c = sync
    monkeypatch.setattr(us, "_now", lambda: dt.datetime(2026, 11, 10, 14, 30, tzinfo=dt.timezone.utc))
    monkeypatch.setattr(us, "cw", _CW({3600: {}, 86400: {"moonshotai.kimi-k3": {"InputTokens": 1e6, "OutputTokens": 0}}}))
    us.lambda_handler()  # tokens today, none this hour
    assert "Item" not in c.get_item(TableName="t", Key={"pk": {"S": "USER#alice"}, "sk": {"S": "HOUR#2026-11-10T14"}})
    assert "Item" in c.get_item(TableName="t", Key={"pk": {"S": "USER#alice"}, "sk": {"S": "DAY#2026-11-10"}})


def test_model_row_nobody_uses_is_deleted(sync, monkeypatch):
    us, c = sync
    monkeypatch.setattr(us, "_now", lambda: dt.datetime(2026, 11, 10, 14, 30, tzinfo=dt.timezone.utc))
    monkeypatch.setattr(us, "cw", _CW({3600: {}, 86400: {"moonshotai.kimi-k3": {"InputTokens": 1e6, "OutputTokens": 0}}}))
    us.lambda_handler()
    key = {"pk": {"S": "MODEL#ow/kimi-k3"}, "sk": {"S": "MONTH#2026-11"}}
    assert "Item" in c.get_item(TableName="t", Key=key)
    monkeypatch.setattr(us, "PROJECTS", {})  # the only user of the model is removed
    us.lambda_handler()
    assert "Item" not in c.get_item(TableName="t", Key=key)


def test_seeded_group_is_used_until_the_token_is_seen(sync, monkeypatch):
    us, c = sync
    monkeypatch.setattr(us, "_now", lambda: dt.datetime(2026, 11, 10, 14, 30, tzinfo=dt.timezone.utc))
    monkeypatch.setattr(us, "cw", _CW({3600: {}, 86400: {"moonshotai.kimi-k3": {"InputTokens": 1e6, "OutputTokens": 0}}}))
    c.put_item(TableName="t", Item={"pk": {"S": "USER#alice"}, "sk": {"S": "BUDGET"}, "group": {"S": "eng"}})
    us.lambda_handler()
    assert "Item" in c.get_item(TableName="t", Key={"pk": {"S": "GROUP#eng"}, "sk": {"S": "MONTH#2026-11"}})
    assert "Item" not in c.get_item(TableName="t", Key={"pk": {"S": "GROUP#(no group)"}, "sk": {"S": "MONTH#2026-11"}})


def test_models_with_no_tokens_in_the_window_are_dropped(sync, monkeypatch):
    us, c = sync
    monkeypatch.setattr(us, "cw", _CW({3600: {}, 86400: {"moonshotai.kimi-k3": {"InputTokens": 0, "OutputTokens": 0}}}))
    t = us.window_tokens(us.mantle_metrics(), ["proj_a"], dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc),
                         dt.datetime(2026, 11, 2, tzinfo=dt.timezone.utc), 86400)
    assert t == {"proj_a": {}}  # listed by ListMetrics, but no tokens in the window


def test_failed_stale_delete_keeps_the_index_so_the_next_run_retries(sync, monkeypatch):
    us, c = sync
    now = us.dt.datetime(2026, 11, 12, 6, 0, tzinfo=us.dt.timezone.utc)
    sk = "MONTH#2026-11"
    for g in ("A", "B"):
        c.put_item(TableName="t", Item={"pk": {"S": f"GROUP#{g}"}, "sk": {"S": sk}})
    c.put_item(TableName="t", Item={"pk": {"S": "ROLLUPINDEX"}, "sk": {"S": sk}, "keys": {"L": [{"S": "GROUP#A"}, {"S": "GROUP#B"}]}})
    real = us.ddb.delete_item

    def throttled(**kw):
        raise RuntimeError("ProvisionedThroughputExceededException")
    monkeypatch.setattr(us.ddb, "delete_item", throttled)
    with pytest.raises(RuntimeError):
        with us.deferred_writes():
            us.delete_stale("ROLLUPINDEX", sk, "GROUP#", {"GROUP#B"}, now, 86400)
    idx = c.get_item(TableName="t", Key={"pk": {"S": "ROLLUPINDEX"}, "sk": {"S": sk}})["Item"]
    assert {x["S"] for x in idx["keys"]["L"]} == {"GROUP#A", "GROUP#B"}  # A is still listed: the next run deletes it
    monkeypatch.setattr(us.ddb, "delete_item", real)
    with us.deferred_writes():
        us.delete_stale("ROLLUPINDEX", sk, "GROUP#", {"GROUP#B"}, now, 86400)
    assert "Item" not in c.get_item(TableName="t", Key={"pk": {"S": "GROUP#A"}, "sk": {"S": sk}})
    idx = c.get_item(TableName="t", Key={"pk": {"S": "ROLLUPINDEX"}, "sk": {"S": sk}})["Item"]
    assert [x["S"] for x in idx["keys"]["L"]] == ["GROUP#B"]


@pytest.mark.parametrize("sk,days", [("MONTH#2026-11", 400), ("DAY#2026-11-12", 40)])
def test_rollup_rows_and_their_index_expire_with_the_window(sync, sk, days):
    us, c = sync
    now = us.dt.datetime(2026, 11, 12, 6, 0, tzinfo=us.dt.timezone.utc)
    t = {"moonshotai.kimi-k3": {"in": 10, "out": 5}}
    with us.deferred_writes():
        us.write_group_model_rollups(sk, {"alice": t}, {"alice": ["eng"]}, now, per_user_req={"alice": (1, {}, 0.0)})
    want = int(now.timestamp()) + days * 86400
    for pk in ("GROUP#eng", "ROLLUPINDEX"):
        it = c.get_item(TableName="t", Key={"pk": {"S": pk}, "sk": {"S": sk}})["Item"]
        assert int(it["ttl"]["N"]) == want, pk


def _month_tokens(c, sk="MONTH#2026-11"):
    it = c.get_item(TableName="t", Key={"pk": {"S": "USER#alice"}, "sk": {"S": sk}})["Item"]
    return int(it["model_input_tokens"]["N"]), it


def test_a_new_project_mid_month_adds_to_the_old_one_instead_of_hiding_under_it(sync):
    us, c = sync
    now = us.dt.datetime(2026, 11, 12, 6, 0, tzinfo=us.dt.timezone.utc)
    k = "moonshotai.kimi-k3"
    us.sync_window("alice", "MONTH#2026-11", {k: {"in": 1000, "out": 10}}, now, project="proj_old")
    # alice is removed and re-added: a new project whose CloudWatch month starts at 0
    _, it = _month_tokens(c)
    us.sync_window("alice", "MONTH#2026-11", {k: {"in": 200, "out": 2}}, now, stored=it, project="proj_new")
    n, it = _month_tokens(c)
    assert n == 1200  # max() would have kept 1000 and hidden the new project's 200
    _, it = _month_tokens(c)
    us.sync_window("alice", "MONTH#2026-11", {k: {"in": 500, "out": 5}}, now, stored=it, project="proj_new")
    n, it = _month_tokens(c)
    assert n == 1500 and it["model_project"]["S"] == "proj_new"


@pytest.mark.parametrize("new_tokens", [0, 200_000])
def test_a_new_project_mid_day_keeps_the_old_projects_day_and_hour_spend(sync, monkeypatch, new_tokens):
    """Removed and re-added the same hour: the daily and hourly caps must still count the old project's spend."""
    us, c = sync
    now = dt.datetime(2026, 11, 10, 13, 30, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(us, "_now", lambda: now)
    k = "moonshotai.kimi-k3"
    monkeypatch.setattr(us, "cw", _CW({p: {k: {"InputTokens": 1e6, "OutputTokens": 0}} for p in (3600, 86400)}))
    us.lambda_handler()
    d, h, m = keys(now)
    before = {sk: _n(_get(c, sk), "model_spend_usd") for sk in (d, h, m)}
    assert all(v == pytest.approx(3.3) for v in before.values())
    monkeypatch.setattr(us, "PROJECTS", {"alice": "proj_new"})
    monkeypatch.setattr(us, "cw", _CW({p: {k: {"InputTokens": new_tokens, "OutputTokens": 0}} for p in (3600, 86400)}, projects=("proj_new",)))
    us.lambda_handler()
    for sk in (d, h, m):
        it = _get(c, sk)
        assert _n(it, "model_spend_usd") == pytest.approx(3.3 + new_tokens * 3.3 / 1e6), sk
        if new_tokens:
            assert it["model_project"]["S"] == "proj_new", sk


def test_closed_day_rollups_keep_the_earlier_projects_spend(sync, monkeypatch):
    """Re-added mid-day with no calls afterwards: the closed day's MODEL# row must still hold the old project's spend."""
    us, c = sync
    k = "moonshotai.kimi-k3"
    clock = [dt.datetime(2026, 11, 10, 13, 30, tzinfo=dt.timezone.utc)]
    monkeypatch.setattr(us, "_now", lambda: clock[0])
    monkeypatch.setattr(us, "cw", _CW({p: {k: {"InputTokens": 1e6, "OutputTokens": 0}} for p in (3600, 86400)}))
    us.lambda_handler()
    monkeypatch.setattr(us, "PROJECTS", {"alice": "proj_new"})
    monkeypatch.setattr(us, "cw", _CW({}, projects=("proj_new",)))
    clock[0] = dt.datetime(2026, 11, 10, 15, 0, tzinfo=dt.timezone.utc)
    us.lambda_handler()
    clock[0] = dt.datetime(2026, 11, 11, 0, 5, tzinfo=dt.timezone.utc)  # day D closes: full-span re-sync of D
    us.lambda_handler()
    rows = c.scan(TableName="t", FilterExpression="sk = :s AND begins_with(pk, :p)",
                  ExpressionAttributeValues={":s": {"S": "DAY#2026-11-10"}, ":p": {"S": "MODEL#"}})["Items"]
    assert rows and sum(_n(r, "model_spend_usd") for r in rows) == pytest.approx(3.3)
    assert _n(_get(c, "DAY#2026-11-10"), "model_spend_usd") == pytest.approx(3.3)


def test_same_project_still_merges_with_max(sync):
    us, c = sync
    now = us.dt.datetime(2026, 11, 12, 6, 0, tzinfo=us.dt.timezone.utc)
    k = "moonshotai.kimi-k3"
    us.sync_window("alice", "MONTH#2026-11", {k: {"in": 1000, "out": 10}}, now, project="proj_a")
    _, it = _month_tokens(c)
    us.sync_window("alice", "MONTH#2026-11", {k: {"in": 0, "out": 12}}, now, stored=it, project="proj_a")  # a lagging metric
    n, it = _month_tokens(c)
    assert n == 1000 and int(it["model_output_tokens"]["N"]) == 12


def test_a_row_created_in_a_failed_run_is_still_on_the_index(sync, monkeypatch):
    us, c = sync
    now = us.dt.datetime(2026, 11, 12, 6, 0, tzinfo=us.dt.timezone.utc)
    sk = "MONTH#2026-11"
    c.put_item(TableName="t", Item={"pk": {"S": "ROLLUPINDEX"}, "sk": {"S": sk}, "keys": {"L": [{"S": "GROUP#old"}]}})
    real = us.ddb.put_item

    def flaky(**kw):
        if kw["Item"]["pk"]["S"] == "GROUP#other":
            raise RuntimeError("ProvisionedThroughputExceededException")
        return real(**kw)
    monkeypatch.setattr(us.ddb, "put_item", flaky)
    with pytest.raises(RuntimeError):
        with us.deferred_writes():
            us._write({"put": {"pk": {"S": "GROUP#newteam"}, "sk": {"S": sk}}})
            us._write({"put": {"pk": {"S": "GROUP#other"}, "sk": {"S": sk}}})
            us.delete_stale("ROLLUPINDEX", sk, "GROUP#", {"GROUP#old", "GROUP#newteam", "GROUP#other"}, now, 86400)
    keys = {x["S"] for x in c.get_item(TableName="t", Key={"pk": {"S": "ROLLUPINDEX"}, "sk": {"S": sk}})["Item"]["keys"]["L"]}
    assert "GROUP#newteam" in keys  # so a later run that no longer writes it deletes it


@pytest.mark.parametrize("kind,tokens,synced,want", [
    ("MONTH", {}, False, False), ("MONTH", {}, True, True), ("DAY", {}, True, True), ("DAY", {}, False, False),
    ("HOUR", {}, True, False), ("HOUR", {"m": {"in": 1, "out": 0}}, False, True), ("DAY", {"m": {"in": 0, "out": 2}}, False, True),
])
def test_should_sync_is_the_one_idle_window_rule(sync, kind, tokens, synced, want):
    us, _ = sync
    assert us.should_sync(kind, tokens, {"model_synced_at": {"S": "x"}} if synced else {}) is want


def test_month_items_written_by_the_sync_get_a_ttl(sync, monkeypatch):
    us, c = sync
    now = dt.datetime(2026, 11, 10, 13, 30, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(us, "_now", lambda: now)
    monkeypatch.setattr(us, "cw", _CW({p: {"moonshotai.kimi-k3": {"InputTokens": 10, "OutputTokens": 1}} for p in (3600, 86400)}))
    us.lambda_handler()
    assert "ttl" in _get(c, "MONTH#2026-11")


def test_index_read_ahead_skips_tag_index_for_days_and_is_cleared(sync, monkeypatch):
    us, c = sync
    seen = []
    real = us.batch_get
    monkeypatch.setattr(us, "batch_get", lambda keys: (seen.extend(keys), real(keys))[1])
    us.prefetch_indexes(["MONTH#2026-11", "DAY#2026-11-10"])
    assert ("TAGINDEX", "MONTH#2026-11") in seen and ("TAGINDEX", "DAY#2026-11-10") not in seen
    monkeypatch.setattr(us, "_now", lambda: dt.datetime(2026, 11, 10, 13, 30, tzinfo=dt.timezone.utc))
    monkeypatch.setattr(us, "cw", _CW({}))
    us.lambda_handler()
    assert us._INDEX == {}  # nothing read ahead is left for a later call


def test_without_a_user_source_the_sync_stops_instead_of_syncing_nobody(sync, monkeypatch):
    us, _ = sync
    monkeypatch.delenv("PROJECTS", raising=False)
    monkeypatch.delenv("DIRECTORY_FROM_TABLE", raising=False)
    with pytest.raises(RuntimeError, match="DIRECTORY_FROM_TABLE"):
        us.lambda_handler()


def test_unchanged_skips_an_identical_synced_item_and_compares_numbers_by_value(monkeypatch):
    # r51: an idle user's synced MONTH/DAY item is not rewritten every run; DynamoDB's "0.1" equals our "0.10000000".
    import datetime as dt
    import usage_sync as us
    now = dt.datetime(2026, 11, 12, tzinfo=dt.timezone.utc)
    total = {}
    attrs = us.token_attrs(total, now)
    stored = {k: ({"N": str(float(v["N"]))} if "N" in v else v) for k, v in attrs.items()}
    stored["model_synced_at"] = {"S": "2026-11-12T00:00:00+00:00"}
    also = {"model_by_model": {"S": "{}"}}
    stored.update(also)
    assert us.unchanged(stored, attrs, also)
    assert not us.unchanged({**stored, "model_by_model": {"S": '{"x": {"in": 1}}'}}, attrs, also)
    assert not us.unchanged({k: v for k, v in stored.items() if k != "model_synced_at"}, attrs, also)  # never synced


def test_per_model_spend_a_window_no_longer_carries_is_removed(sync):
    # r87: SET-only writes left m|<old alias>|usd next to the new counters when a model's alias or price changed.
    us, c = sync
    now = us.dt.datetime(2026, 11, 12, 6, 0, tzinfo=us.dt.timezone.utc)
    key = {"pk": {"S": "USER#alice"}, "sk": {"S": "MONTH#2026-11"}}
    us.sync_window("alice", "MONTH#2026-11", {"moonshotai.kimi-k3": {"in": 1000, "out": 10}}, now, project="proj_a")
    c.update_item(TableName="t", Key=key, UpdateExpression="SET #u = :v, #r = :v",
                  ExpressionAttributeNames={"#u": "m|ow/old-alias|usd", "#r": "m|ow/old-alias|req"},
                  ExpressionAttributeValues={":v": {"N": "1"}})
    it = c.get_item(TableName="t", Key=key)["Item"]
    us.sync_window("alice", "MONTH#2026-11", {"moonshotai.kimi-k3": {"in": 1000, "out": 10}}, now, stored=it, project="proj_a")
    it = c.get_item(TableName="t", Key=key)["Item"]
    assert "m|ow/old-alias|usd" not in it and "m|ow/old-alias|req" in it  # req is the interceptor's, kept


@pytest.mark.parametrize("at", ["2026-11-12T11:59:30", "2026-11-12T23:59:30", "2026-11-30T23:59:30"])
def test_open_windows_end_at_their_own_end(sync, monkeypatch, at):
    us, c = sync
    now = dt.datetime.fromisoformat(at).replace(tzinfo=dt.timezone.utc)
    monkeypatch.setattr(us, "_now", lambda: now)
    monkeypatch.setattr(us, "cw", _CW({3600: {}, 86400: {}}))
    seen = []
    real = us.window_tokens
    monkeypatch.setattr(us, "window_tokens", lambda listing, projects, lo, hi, period: seen.append((lo, hi, period)) or real(listing, projects, lo, hi, period))
    us.lambda_handler()
    hour0 = now.replace(minute=0, second=0, microsecond=0)
    day0 = hour0.replace(hour=0)
    month_end = (day0.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
    assert (hour0, min(now + dt.timedelta(minutes=1), hour0 + dt.timedelta(hours=1)), 3600) in seen
    assert (day0, min(now + dt.timedelta(minutes=1), day0 + dt.timedelta(days=1)), 86400) in seen
    assert (day0.replace(day=1), min(now + dt.timedelta(minutes=1), month_end), 86400) in seen


def test_hand_edited_counts_and_tags_never_stop_the_sync(sync, monkeypatch):
    # r147 /code-review: a non-string PROFILE tag (KeyError) or a non-numeric count in model_by_model (TypeError)
    # aborted the whole run for every user.
    us, c = sync
    _, _, m = keys(dt.datetime.now(dt.timezone.utc))
    c.put_item(TableName="t", Item={"pk": {"S": "USER#alice"}, "sk": {"S": m},
                                    "model_by_model": {"S": json.dumps({"moonshotai.kimi-k3": {"in": "lots", "out": -5}})}})
    c.put_item(TableName="t", Item={"pk": {"S": "USER#alice"}, "sk": {"S": "PROFILE"}, "tags": {"M": {"team": {"N": "7"}, "project": {"S": "p1"}}}})
    monkeypatch.setattr(us, "cw", _CW({3600: {}, 86400: {"moonshotai.kimi-k3": {"InputTokens": 1e6, "OutputTokens": 0}}}))
    r = us.lambda_handler()
    assert r["alice"]["model_spend_usd"] == pytest.approx(3.3)


def test_the_role_may_write_only_the_sync_own_spend_attributes_on_user_items(sync):
    """r192: the usage sync's role could UpdateItem any attribute of a USER# item, BUDGET caps and boosts included.
    Its grant in terraform/policy.tf now names the attributes set_rollup writes (StringLike patterns); the budget,
    boost, rpm and token-group attributes the dashboard and interceptor own are outside it."""
    import re
    from fnmatch import fnmatchcase
    from pathlib import Path
    mod, _ = sync
    tf = (Path(__file__).resolve().parents[2] / "terraform" / "policy.tf").read_text()
    block = tf[tf.index('resource "aws_iam_role_policy" "usage_sync"'):]
    m = re.search(r'"dynamodb:LeadingKeys" = \["USER#\*"\]\s*"dynamodb:Attributes" = \[([^\]]*)\]', block)
    assert m, "usage_sync's USER# UpdateItem statement no longer carries a dynamodb:Attributes list"
    pats = re.findall(r'"([^"]+)"', m.group(1))
    ok = lambda name: any(fnmatchcase(name, p) for p in pats)  # noqa: E731  IAM StringLike: * matches any run
    now = dt.datetime(2026, 11, 23, tzinfo=dt.timezone.utc)
    written = set(mod.token_attrs({m: {"in": 10, "out": 5} for m in PRICES}, now))
    written |= {"pk", "sk", "ttl", "model_by_model", "model_base", "model_project"}
    assert any(a.startswith("m|") for a in written)
    assert all(ok(a) for a in written), sorted(a for a in written if not ok(a))
    for owned_by_others in ("budget_usd", "daily_usd", "hourly_usd", "rpm", "boost_usd", "boost_period", "boost_until",
                            "boost_reason", "token_groups", "groups_iat", "group", "requests", "spend_usd",
                            "websearch_requests", "m|ow/kimi-k3|req", "tags"):
        assert not ok(owned_by_others), owned_by_others


def test_input_is_priced_at_the_higher_of_the_input_and_cache_write_rates(sync, monkeypatch):
    """r196 threat model T-012: Mantle does not say which input tokens were cache writes, so a model whose cache
    writes cost more than its input is priced at the cache-write rate for all input, keeping spend an upper bound."""
    m, _ = sync
    monkeypatch.setattr(m, "PRICES", {"moonshotai.kimi-k3": {"alias": "ow/kimi-k3", "in": 3.3, "out": 16.5, "cache_write": 4.125},
                                      "zai.glm-5.3": {"alias": "ow/glm-5.3", "in": 1.848, "out": 5.808, "cache_write": 1.0},
                                      "deepseek.v3.2": {"alias": "ow/deepseek-v3.2", "in": 0.62, "out": 1.85}})
    monkeypatch.setattr(m, "_PRICE_KEYS", {})  # the cache belongs to the PRICES it was built from
    v = {"in": 1_000_000, "out": 1_000_000}
    assert m.token_cost("moonshotai.kimi-k3", v) == pytest.approx(4.125 + 16.5)  # cache write above input
    assert m.token_cost("zai.glm-5.3", v) == pytest.approx(1.848 + 5.808)  # below input: the input rate stays
    assert m.token_cost("deepseek.v3.2", v) == pytest.approx(0.62 + 1.85)  # none given
    tf = (Path(__file__).resolve().parents[2] / "terraform" / "policy.tf").read_text()
    assert "m.cache_write == null ? {} : { cache_write = m.cache_write }" in tf  # unset: PRICES as before
