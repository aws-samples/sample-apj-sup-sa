# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Policy resolution (lambda/policy.py): user override > most generous group > org > env; boosts; model union."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lambda"))
import policy as p  # noqa: E402

ENV = {"Monthly": 50.0, "Daily": None, "Hourly": None, "rpm": None}
GM = {"dev": ["acme-*"], "ow": ["ow/*"]}


def caps(r):
    return {k: (v["cap"], v["source"]) for k, v in r["caps"].items()}


def test_env_only_is_unchanged_behaviour():
    r = p.resolve({}, {}, ["dev"], ENV, GM)
    assert caps(r) == {"Monthly": (50.0, "default"), "Daily": (None, None), "Hourly": (None, None)}
    assert r["models"] == ["acme-*"] and r["rpm"]["value"] is None


def test_order_user_group_org():
    pol = {"ORG": {"monthly_usd": 20.0, "daily_usd": 5.0, "rpm": 30.0},
           "GROUP#dev": {"daily_usd": 8.0}, "GROUP#ow": {"daily_usd": 12.0, "hourly_usd": 1.0}}
    r = p.resolve({"budget_usd": 100.0}, pol, ["dev", "ow"], ENV, GM)
    assert caps(r) == {"Monthly": (100.0, "user"), "Daily": (12.0, "group:ow"), "Hourly": (1.0, "group:ow")}
    assert r["rpm"] == {"value": 30, "source": "org"}


def test_boost_only_while_live():
    b = {"daily_usd": 2.0, "boost_usd": 3.0, "boost_period": "Daily", "boost_until": 2000.0}
    assert p.resolve(b, {}, [], ENV, GM, now=1000)["caps"]["Daily"]["cap"] == 5.0
    assert p.resolve(b, {}, [], ENV, GM, now=3000)["caps"]["Daily"]["cap"] == 2.0


def test_models_dynamodb_overrides_env_per_group():
    pol = {"GROUP#ow": {"models": ["ow/kimi-k3"]}}
    r = p.resolve({}, pol, ["dev", "ow"], ENV, GM)
    assert r["models"] == ["acme-*", "ow/kimi-k3"] and r["models_source"] == {"dev": "env", "ow": "dynamodb"}
    assert p.model_allowed("ow/kimi-k3", r["models"]) and not p.model_allowed("ow/glm-5.3", r["models"])


def test_plain_converts_attribute_maps():
    assert p.plain({"a": {"N": "1.5"}, "b": {"L": [{"S": "x"}]}, "c": {"M": {"d": {"BOOL": True}}}, "e": {"NULL": True}}) == {
        "a": 1.5, "b": ["x"], "c": {"d": True}, "e": None}


def test_env_limits_one_parser_for_both_lambdas():
    import policy
    assert policy.env_limits({}) == {"Monthly": 50.0, "Daily": None, "Hourly": None, "rpm": None}
    assert policy.env_limits({"DEFAULT_BUDGET_USD": "20", "DEFAULT_DAILY_USD": "3", "DEFAULT_RPM": "7"}) == \
        {"Monthly": 20.0, "Daily": 3.0, "Hourly": None, "rpm": 7}


def test_paginate_follows_every_page_and_tolerates_a_missing_items_key():
    pages = [{"Items": [1, 2], "LastEvaluatedKey": {"k": 1}}, {"LastEvaluatedKey": {"k": 2}}, {"Items": [3]}]
    seen = []

    class Ddb:
        def query(self, **kw):
            seen.append(kw.get("ExclusiveStartKey"))
            return pages[len(seen) - 1]
    assert list(p.paginate(Ddb(), "query", TableName="t")) == [1, 2, 3] and seen == [None, {"k": 1}, {"k": 2}]


@pytest.mark.parametrize("index,prefix,op,cond", [
    ("by_sk", None, "query", "sk = :s"),
    ("by_sk", "TAG#", "query", "sk = :s AND begins_with(pk, :p)"),
    ("", None, "scan", "sk = :s"),
    ("", "GROUP#", "scan", "sk = :s AND begins_with(pk, :p)"),
])
def test_by_sk_queries_the_index_or_scans_with_the_same_condition(index, prefix, op, cond):
    calls = []

    class Ddb:
        def query(self, **kw):
            calls.append(("query", kw))
            return {"Items": ["q"]}

        def scan(self, **kw):
            calls.append(("scan", kw))
            return {"Items": ["s"]}
    assert list(p.by_sk(Ddb(), "t", index, "MONTH#2026-11", prefix)) == [op[0]]
    (got, kw), = calls
    key = "KeyConditionExpression" if op == "query" else "FilterExpression"
    assert got == op and kw[key] == cond and kw["TableName"] == "t" and kw["ExpressionAttributeValues"][":s"] == {"S": "MONTH#2026-11"}
    assert ("IndexName" in kw) is (op == "query") and (":p" in kw["ExpressionAttributeValues"]) is bool(prefix)


@pytest.mark.parametrize("item,groups", [
    (None, []),
    ({}, []),
    ({"group": {"S": "ow"}}, ["ow"]),
    ({"group": {"S": ""}}, []),
    ({"token_groups": {"L": []}, "group": {"S": "ow"}}, []),          # the token's (empty) groups win over the seed
    ({"token_groups": {"L": [{"S": "b"}, {"S": "a"}, {"S": "a"}, {"S": ""}]}, "group": {"S": "ow"}}, ["a", "b"]),
])
def test_budget_groups_is_the_one_groups_rule(item, groups):
    assert p.budget_groups(item) == groups


@pytest.mark.parametrize("ts", [0, 1793491199, 1793491200, 1794300712, 1709251199])
def test_window_keys_agree_for_datetime_and_gmtime(ts):
    import datetime as dt
    import time
    d, g = dt.datetime.fromtimestamp(ts, dt.timezone.utc), time.gmtime(ts)
    for k in ("MONTH", "DAY", "HOUR"):
        assert p.window_sk(k, d) == p.window_sk(k, g) and p.window_sk(k, g).startswith(k + "#")
    assert p.window_sk("HOUR", g) == time.strftime("HOUR#%Y-%m-%dT%H", g)


def test_batch_get_reads_every_chunk_concurrently_and_fails_closed():
    import threading
    seen, lock = [], threading.Lock()

    class Ddb:
        def batch_get_item(self, RequestItems):  # noqa: N803
            ks = RequestItems["t"]["Keys"]
            with lock:
                seen.append(len(ks))
            return {"Responses": {"t": [{**k, "v": {"S": "x"}} for k in ks]}}
    keys = [(f"USER#u{i}", "BUDGET") for i in range(250)] + [("USER#u0", "BUDGET")]
    out = p.batch_get(Ddb(), "t", keys)
    assert len(out) == 250 and sorted(seen) == [50, 100, 100]

    class Stuck:
        def batch_get_item(self, RequestItems):  # noqa: N803
            return {"UnprocessedKeys": RequestItems}
    with pytest.raises(RuntimeError):
        p.batch_get(Stuck(), "t", keys, attempts=2, base=0)


@pytest.mark.parametrize("item,want", [(None, 0.0), ({}, 0.0), ({"spend_usd": {"N": "1.5"}}, 1.5),
                                       ({"spend_usd": {"N": "1"}, "model_spend_usd": {"N": "2.25"}, "x": {"N": "9"}}, 3.25)])
def test_window_spend_is_model_plus_web_search(item, want):
    assert p.window_spend(item) == want


def test_a_read_started_before_clear_cache_never_fills_the_cache():
    """A pooled read still running when a request ended (Lambda freezes it) must not serve the next request."""
    p.clear_cache()

    class Ddb:
        def __init__(self, v, hook=None):
            self.v, self.hook = v, hook

        def query(self, **kw):
            if self.hook:
                self.hook()
            return {"Items": [{"pk": {"S": "POLICY"}, "sk": {"S": "ORG"}, "v": {"S": self.v}}]}
    got = p.policy_items(Ddb("old", hook=p.clear_cache), "t", now=1000.0)  # the next request clears while it runs
    assert got["ORG"]["v"] == "old"  # its own caller still gets an answer
    assert p.policy_items(Ddb("new"), "t", now=1001.0)["ORG"]["v"] == "new"  # but it was never cached
    assert p.policy_items(Ddb("ignored"), "t", now=1002.0)["ORG"]["v"] == "new"  # a normal read is cached


@pytest.mark.parametrize("w,ok", [([75, 95], True), ([1, 100], True), ([True, 50], False), ([95, 75], False), ([0, 50], False),
                                  ([50], False), (80, False), (None, False), ([50, 50], False)])
def test_one_warn_pct_rule_for_saving_and_reading(w, ok):
    assert p.valid_warn_pct(w) is ok and (p.warn_pct({"warn_pct": w}) == ([float(x) for x in w] if ok else [75, 95]))


def test_directory_comes_from_env_unless_the_table_flag_is_set():
    assert p.directory(None, "t", {"PROJECTS": '{"a": "p1"}', "IDENTITY_MAP": '{"sub1": "a"}'}) == ({"a": "p1"}, {"sub1": "a"})
    with pytest.raises(RuntimeError, match="no user directory"):
        p.directory(None, "t", {})  # neither source: refuse rather than treat everyone as unknown


def test_directory_from_the_ledger_item_is_cached_and_fails_closed_when_missing():
    p._directory.update(at=0.0, tried=0.0, val=None)
    calls = []

    class Ddb:
        def __init__(self, item):
            self.item = item

        def get_item(self, TableName, Key):  # noqa: N803
            calls.append(Key)
            return {"Item": self.item} if self.item else {}
    item = {"projects": {"S": '{"alice": "proj_a"}'}, "identity_map": {"S": '{"00u1": "alice"}'}}
    env = {"DIRECTORY_FROM_TABLE": "1"}
    assert p.directory(Ddb(item), "t", env, now=1000.0) == ({"alice": "proj_a"}, {"00u1": "alice"})
    assert p.directory(Ddb(None), "t", env, now=1010.0) == ({"alice": "proj_a"}, {"00u1": "alice"})  # cached
    assert len(calls) == 1 and calls[0] == p.DIRECTORY_KEY
    p._directory.update(at=0.0, tried=0.0, val=None)
    with pytest.raises(RuntimeError):
        p.directory(Ddb(None), "t", env, now=2000.0)
    p._directory.update(at=0.0, tried=0.0, val=None)


def test_directory_serves_a_recent_copy_while_the_ledger_cannot_be_read():
    p._directory.update(at=0.0, tried=0.0, val=None)

    class Ok:
        def get_item(self, **kw):
            return {"Item": {"projects": {"S": '{"a": "p"}'}, "identity_map": {"S": "{}"}}}

    class Down:
        def get_item(self, **kw):
            raise RuntimeError("throttled")
    env = {"DIRECTORY_FROM_TABLE": "1"}
    p.directory(Ok(), "t", env, now=1000.0)
    assert p.directory(Down(), "t", env, now=1100.0) == ({"a": "p"}, {})  # past the 60 s cache, within STALE_SECONDS
    with pytest.raises(RuntimeError):
        p.directory(Down(), "t", env, now=1000.0 + p.STALE_SECONDS + 1)
    p._directory.update(at=0.0, tried=0.0, val=None)


def test_a_failed_directory_read_is_backed_off():
    # r48: after a failed read the next one waits CACHE_SECONDS (no GetItem on every call). (r103: the unused fresh=
    # path is gone.)
    p._directory.update(at=0.0, tried=0.0, val=None)
    calls = []

    class Ok:
        def get_item(self, **kw):
            return {"Item": {"projects": {"S": '{"a": "p"}'}, "identity_map": {"S": "{}"}}}

    class Down:
        def get_item(self, **kw):
            calls.append(1)
            raise RuntimeError("throttled")
    env = {"DIRECTORY_FROM_TABLE": "1"}
    p.directory(Ok(), "t", env, now=1000.0)
    assert p.directory(Down(), "t", env, now=1100.0) == ({"a": "p"}, {}) and len(calls) == 1
    assert p.directory(Down(), "t", env, now=1130.0) == ({"a": "p"}, {}) and len(calls) == 1  # backed off
    assert p.directory(Down(), "t", env, now=1170.0) == ({"a": "p"}, {}) and len(calls) == 2  # retried after 60 s
    p._directory.update(at=0.0, tried=0.0, val=None)


def test_a_container_that_never_read_the_directory_also_backs_off():
    p._directory.update(at=0.0, tried=0.0, val=None)
    calls = []

    class Down:
        def get_item(self, **kw):
            calls.append(1)
            raise RuntimeError("throttled")
    env = {"DIRECTORY_FROM_TABLE": "1"}
    # r111: with no copy the wait is FIRST_RETRY_SECONDS (a throttled table is still not asked on every call, and a
    # fresh container recovers in seconds, not a whole CACHE_SECONDS).
    for t in (1000.0, 1001.0, 1001.5):
        with pytest.raises(RuntimeError):
            p.directory(Down(), "t", env, now=t)
    assert len(calls) == 1  # one GetItem; the next two refused without asking the table
    with pytest.raises(RuntimeError):
        p.directory(Down(), "t", env, now=1000.0 + p.FIRST_RETRY_SECONDS + 0.5)
    assert len(calls) == 2
    p._directory.update(at=0.0, tried=0.0, val=None)


@pytest.mark.parametrize("models,allowed", [("ow/kimi*", {"ow/kimi-k3"}), ("*", {"ow/kimi-k3", "ow/glm-5"}),
                                            (["ow/glm-5", 7, None], {"ow/glm-5"}), ({"x": 1}, set()), (42, set())])
def test_hand_written_group_models_never_open_up_by_character(models, allowed):
    # r50: a string was read one character at a time, so 'ow/kimi*' (or anything with a '*') allowed every model.
    r = p.resolve({}, {"GROUP#eng": {"models": models}}, ["eng"], {}, {})
    got = {m for m in ("ow/kimi-k3", "ow/glm-5") if p.model_allowed(m, r["models"])}
    assert got == allowed


@pytest.mark.parametrize("cap,want", [("10", 10.0), ("ten", 0.0), (["5"], 0.0), (True, 0.0), (12.5, 12.5),
                                     ("nan", 0.0), ("inf", 0.0), ("-1", 0.0), (float("nan"), 0.0)])
def test_hand_written_caps_are_numbers_or_block(cap, want):
    r = p.resolve({}, {"GROUP#eng": {"monthly_usd": cap}}, ["eng"], {}, {})
    assert r["caps"]["Monthly"]["cap"] == want


@pytest.mark.parametrize("name,want", [("m|ow/kimi-k3|req", ("ow/kimi-k3", "req")), ("m|a|b|usd", ("a|b", "usd")),
                                       ("m||req", None), ("m|x", None), ("requests", None)])
def test_model_attr_round_trip(name, want):
    assert p.parse_model_attr(name) == want
    if want:
        assert p.model_attr(*want) == name


@pytest.mark.parametrize("rpm", ["inf", "nan", float("inf")])
def test_a_non_finite_rpm_blocks_instead_of_crashing(rpm):
    r = p.resolve({}, {"GROUP#eng": {"rpm": rpm}}, ["eng"], {}, {})
    assert r["rpm"]["value"] == 0


@pytest.mark.parametrize("usd,until", [("inf", 4e9), ("nan", 4e9), ("lots", 4e9), (5, "1999999999x"), (5, "4000000000")])
def test_hand_written_boosts_never_lift_a_cap_or_crash(usd, until):
    # r52: a boost of "inf" made the cap infinite; a string boost_until raised TypeError on every page.
    b = {"budget_usd": 10, "boost_usd": usd, "boost_until": until, "boost_period": "Monthly"}
    cap = p.resolve(b, {}, [], {}, {}, now=1e9)["caps"]["Monthly"]["cap"]
    assert cap is not None and cap < float("inf") and cap == cap  # finite, not NaN
    assert cap == (15.0 if (usd, until) == (5, "4000000000") else 10.0)


def test_a_hand_written_non_text_block_message_is_ignored():
    assert p.resolve({}, {"ORG": {"block_message": 42}}, [], {}, {})["block_message"] is None


def test_paginate_token_follows_tokens_takes_null_as_empty_and_stops_on_a_repeat():
    pages = {None: {"Metrics": [1], "NextToken": "a"}, "a": {"Metrics": None, "NextToken": "b"}, "b": {"Metrics": [2]}}
    assert list(p.paginate_token(lambda NextToken=None, **kw: pages[NextToken], "NextToken", ("Metrics",))) == [1, 2]
    with pytest.raises(RuntimeError, match="repeated NextToken"):
        list(p.paginate_token(lambda **kw: {"Metrics": [], "NextToken": "x"}, "NextToken", ("Metrics",)))


def test_paginate_token_falls_back_to_the_next_list_key():
    assert list(p.paginate_token(lambda **kw: {"rateLimits": None, "items": [3]}, "nextToken", ("rateLimits", "items"))) == [3]


def test_group_models_matches_resolve():
    pols = {"GROUP#a": {"models": ["ow/x", "ow/y"]}, "GROUP#b": {}}
    env = {"b": ["ow/y", "ow/z"]}
    gp = {g: p.limits(pols.get(f"GROUP#{g}", {})) for g in ("a", "b")}
    assert p.group_models(gp, ["a", "b"], env) == (["ow/x", "ow/y", "ow/z"], {"a": "dynamodb", "b": "env"})
    r = p.resolve({}, pols, ["a", "b"], {}, env)
    assert (r["models"], r["models_source"]) == p.group_models(gp, ["a", "b"], env)


def test_directory_serves_a_recent_copy_while_the_item_is_missing_or_malformed():
    # r109: a missing or unparsable CONFIG/DIRECTORY item takes the same backoff and stale-copy path as a failed read.
    p._directory.update(at=0.0, tried=0.0, val=None)

    class Ok:
        def get_item(self, **kw):
            return {"Item": {"projects": {"S": '{"a": "p"}'}, "identity_map": {"S": "{}"}}}

    class Gone:
        calls = 0

        def get_item(self, **kw):
            Gone.calls += 1
            return {}

    class Bad:
        def get_item(self, **kw):
            return {"Item": {"projects": {"S": "{not json"}, "identity_map": {"S": "{}"}}}
    env = {"DIRECTORY_FROM_TABLE": "1"}
    p.directory(Ok(), "t", env, now=1000.0)
    assert p.directory(Gone(), "t", env, now=1100.0) == ({"a": "p"}, {})
    assert p.directory(Gone(), "t", env, now=1101.0) == ({"a": "p"}, {}) and Gone.calls == 1  # backed off
    p._directory.update(at=1000.0, tried=0.0)
    assert p.directory(Bad(), "t", env, now=1100.0) == ({"a": "p"}, {})
    p._directory.update(at=0.0, tried=0.0, val=None)
    with pytest.raises(RuntimeError, match="missing"):
        p.directory(Gone(), "t", env, now=5000.0)
    p._directory.update(at=0.0, tried=0.0, val=None)


def test_directory_with_no_copy_retries_after_a_short_wait():
    # r111: a fresh container's first failed read must not fail every request for a whole CACHE_SECONDS.
    p._directory.update(at=0.0, tried=0.0, val=None)
    state = {"down": True}

    class Flaky:
        def get_item(self, **kw):
            if state["down"]:
                raise RuntimeError("throttled")
            return {"Item": {"projects": {"S": '{"a": "p"}'}, "identity_map": {"S": "{}"}}}
    env = {"DIRECTORY_FROM_TABLE": "1"}
    with pytest.raises(RuntimeError):
        p.directory(Flaky(), "t", env, now=1000.0)
    state["down"] = False
    with pytest.raises(RuntimeError, match="FIRST_RETRY_SECONDS"):
        p.directory(Flaky(), "t", env, now=1001.0)  # inside the short wait: no new read
    assert p.directory(Flaky(), "t", env, now=1000.0 + p.FIRST_RETRY_SECONDS + 0.5) == ({"a": "p"}, {})
    p._directory.update(at=0.0, tried=0.0, val=None)


def test_directory_of_the_wrong_shape_keeps_the_good_copy():
    # r112: identity_map "[]" is valid JSON but not a map: treated as malformed, the earlier copy keeps serving.
    p._directory.update(at=0.0, tried=0.0, val=None)

    class Ok:
        def get_item(self, **kw):
            return {"Item": {"projects": {"S": '{"a": "p"}'}, "identity_map": {"S": "{}"}}}

    class Wrong:
        def get_item(self, **kw):
            return {"Item": {"projects": {"S": '{"a": "p"}'}, "identity_map": {"S": "[]"}}}
    env = {"DIRECTORY_FROM_TABLE": "1"}
    p.directory(Ok(), "t", env, now=1000.0)
    assert p.directory(Wrong(), "t", env, now=1100.0) == ({"a": "p"}, {})
    p._directory.update(at=0.0, tried=0.0, val=None)


def test_a_copy_too_old_to_serve_retries_soon():
    # r112: past STALE_SECONDS the copy cannot serve, so the next read comes after FIRST_RETRY_SECONDS.
    p._directory.update(at=0.0, tried=0.0, val=None)
    calls = []

    class Ok:
        def get_item(self, **kw):
            calls.append(1)
            return {"Item": {"projects": {"S": '{"a": "p"}'}, "identity_map": {"S": "{}"}}}
    env = {"DIRECTORY_FROM_TABLE": "1"}
    p.directory(Ok(), "t", env, now=1000.0)
    p._directory.update(tried=1000.0 + p.STALE_SECONDS - 50)  # the last failed read, shortly before the copy expired
    assert p.directory(Ok(), "t", env, now=1000.0 + p.STALE_SECONDS + 1) == ({"a": "p"}, {})
    assert len(calls) == 2
    p._directory.update(at=0.0, tried=0.0, val=None)


@pytest.mark.parametrize("item,want", [
    (None, None), ({}, None), ({"token_groups": {"L": []}}, []),
    ({"token_groups": {"L": [{"S": "a"}, {"S": ""}, {"NULL": True}, "x", {"S": "b"}]}}, ["a", "b"]),
    ({"token_groups": {"L": None}}, []), ({"token_groups": "junk"}, None), ({"token_groups": {"S": "a"}}, []),
])
def test_token_groups_is_the_one_parse_of_the_recorded_groups(item, want):
    # r133 /code-review: the interceptor and budget_groups each parsed token_groups; both now call this.
    assert p.token_groups(item) == want
    if want is not None:
        assert p.budget_groups({**item, "group": {"S": "seeded"}}) == sorted(want)


def test_window_spend_survives_a_malformed_attribute():
    # r137 /code-review: a hand-edited spend_usd stored as a string crashed the Users page and the gate.
    # r140: sums stay finite (an unreadable or negative attribute adds 0), and window_read is the one rule the
    # gate refuses on and the dashboard shows as blocked; a negative edit never lifts a cap.
    assert p.window_spend({"spend_usd": {"S": "0.5"}, "model_spend_usd": {"N": "1.25"}}) == 1.25
    assert p.window_spend({"spend_usd": {"N": "-1000"}, "model_spend_usd": {"N": "1.25"}}) == 1.25
    assert p.window_spend({"spend_usd": {"N": "0.5"}, "model_spend_usd": {"N": "1.25"}}) == 1.75
    assert p.window_spend(None) == 0
    for bad in ({"S": "x"}, {"N": "-1"}, {"N": "nan"}, {"N": "inf"}, "junk"):
        assert p.window_read({"spend_usd": bad, "model_spend_usd": {"N": "2"}}) == (2.0, False)
        assert p.amount({"spend_usd": bad}, "spend_usd") == (0.0, False)
    assert p.window_read(None) == (0.0, True) and p.window_read({"spend_usd": {"N": "0"}, "model_spend_usd": {"N": "2"}}) == (2.0, True)


@pytest.mark.parametrize("readable,spent,cap,want", [(False, 0.0, 5.0, True), (False, 0.0, None, False), (True, 5.0, 5.0, True),
                                                     (True, 4.0, 5.0, False), (True, 99.0, None, False), (True, 0.0, 0.0, True)])
def test_refused_is_the_one_rule_the_gate_and_the_dashboard_share(readable, spent, cap, want):
    # r143 /code-review: "unreadable and capped" was written in both the interceptor and the dashboard.
    assert p.refused(readable, spent, cap) is want


@pytest.mark.parametrize("item,bad", [(None, []), ({}, []), ({"spend_usd": {"N": "1"}}, []), ({"spend_usd": {"S": "x"}}, ["spend_usd"]),
                                      ({"spend_usd": {"N": "-1"}, "model_spend_usd": {"N": "2"}}, ["spend_usd"]),
                                      ({"spend_usd": {"NULL": True}, "model_spend_usd": {"S": "y"}}, ["model_spend_usd", "spend_usd"])])
def test_bad_attrs_names_exactly_what_the_repair_removes(item, bad):
    assert p.bad_attrs(item) == bad


@pytest.mark.parametrize("bad", [["Monthly"], {"x": 1}, {"Monthly"}, 3.0, None])
def test_a_boost_period_of_any_other_type_counts_as_monthly(bad):
    # r146 /code-review: a List, Map or set boost_period raised TypeError (unhashable) in limits().
    assert p.limits({"boost_period": bad})["boost_period"] == "Monthly"


def test_a_fractional_rpm_rounds_down_but_never_to_a_block():
    # r175 /code-review: a hand-edited 0.5 was read as 0, which blocks every call.
    import policy
    assert [policy.rpm_whole(v) for v in (None, 0, -1, 0.5, 1, 2.9, 60)] == [None, 0, 0, 1, 1, 2, 60]
    eff = policy.resolve({"rpm": 0.5}, {}, [], {"rpm": None}, {})
    assert eff["rpm"] == {"value": 1, "source": "user"}


def test_every_reader_sees_the_enforced_whole_rpm_and_a_bad_default_never_crashes():
    # r176 /code-review: limits() rounds rpm for every view; env_limits reads a bad DEFAULT_RPM as a block, no crash.
    import policy
    assert policy.limits({"rpm": 2.7})["rpm"] == 2 and policy.limits({"rpm": 0.5})["rpm"] == 1
    assert policy.env_limits({"DEFAULT_RPM": "0.5"})["rpm"] == 1
    assert policy.env_limits({"DEFAULT_RPM": "inf"})["rpm"] == 0 and policy.env_limits({"DEFAULT_RPM": "nan"})["rpm"] == 0


def test_a_hand_written_binary_reads_as_bytes_so_text_checks_still_refuse_it():
    # r179 /code-review: as base64 text, a binary boost_reason passed "reason must be text" and a binary block_message
    # was appended to refusals. plain() keeps bytes; replies and audit rows render them as "<B>" (policy.shown).
    import policy
    got = policy.plain({"note": {"B": b"\x00\xff"}, "tags": {"BS": [b"a", b"b"]}, "rpm": {"N": "3"}})
    assert got == {"note": b"\x00\xff", "tags": [b"a", b"b"], "rpm": 3.0}
    org = policy.read_item({"block_message": {"B": b"stop"}})
    assert policy.resolve({}, {"ORG": org}, [], {}, {})["block_message"] is None
    b = policy.read_item({"budget_usd": {"N": "10"}, "boost_usd": {"N": "5"}, "boost_until": {"N": "4000000000"},
                          "boost_period": {"S": "Monthly"}, "boost_reason": {"B": b"x"}})
    assert policy.resolve(b, {}, [], {}, {}, now=1e9)["caps"]["Monthly"]["cap"] == 10.0


def test_version_matches_compares_as_the_client_was_shown():
    # r183 /code-review: the check must accept what a client was really shown, at any depth: the stored value through
    # plain() and a JSON reply, back through a JSON client (whose numbers are doubles) and JSON again.
    import json
    import policy

    def round_trip(av):  # reply -> JSON client -> request; json.loads(float repr) is what a JS client sends back
        return json.loads(json.dumps(json.loads(json.dumps(policy.plain({"v": av})["v"], default=policy.shown))))
    for av in ({"N": "12345678901234567890"}, {"N": "2"}, {"N": "0.1"}, {"B": b"\x01"}, {"BS": [b"a", b"b"]},
               {"S": "v1"}, {"BOOL": True}, {"NULL": True}, {"SS": ["a"]},
               {"M": {"b": {"B": b"x"}, "n": {"N": "98765432109876543210"}}}, {"L": [{"B": b"x"}, {"N": "1e30"}]}):
        assert policy.version_matches(av, round_trip(av)), av
    assert policy.version_matches({"N": "12345678901234567890"}, 12345678901234567000)  # as JSON.stringify sends it
    assert policy.version_matches({"N": "2"}, 2) and not policy.version_matches({"N": "1"}, True)
    assert not policy.version_matches({"BOOL": True}, 1) and not policy.version_matches({"S": "1"}, 1)
    assert not policy.version_matches({"S": "v1"}, "v2") and not policy.version_matches({"N": "1"}, 2)
    assert not policy.version_matches({"M": {"b": {"B": b"x"}}}, {"b": b"x"})
    # r184 /code-review: an int no double can hold is a 409, not an OverflowError (a 500)
    assert not policy.version_matches({"N": "1"}, 10 ** 400) and not policy.version_matches({"L": [{"N": "1"}]}, [10 ** 400])


def test_shown_raises_for_other_types_and_shown_text_never_does():
    import pytest
    import policy
    assert policy.shown(b"x") == "<B>" and policy.shown_text(b"x") == "<B>" and policy.shown_text({1}) == "{1}"
    with pytest.raises(TypeError):
        policy.shown({1})
