# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Unit tests for lambda/interceptor.py (opencode routes), with an in-memory DynamoDB stand-in.

Live end-to-end tests against a deployed gateway are in tests/e2e; these cover the decision logic in isolation.
"""
import base64
import concurrent.futures
import calendar
import importlib
import itertools
import json
import sys
import time
from pathlib import Path

import pytest

LAMBDA = Path(__file__).resolve().parents[2] / "lambda"
GROUP_MODELS = {"ocgw-team-a": ["ow/kimi-k3", "ow/glm-5.3"], "ocgw-team-b": ["ow/*"]}
MANTLE = {"ow/kimi-k3": "moonshotai.kimi-k3", "ow/glm-5.3": "zai.glm-5.3", "ow/deepseek-v3.2": "deepseek.v3.2", "ow/glm-5": "zai.glm-5"}


class CondFail(Exception):
    pass


class FakeDDB:
    """batch_get_item / update_item / get_item / query over a dict, enough for interceptor and policy."""

    class exceptions:  # noqa: N801 - mirrors boto3
        ConditionalCheckFailedException = CondFail

    def __init__(self):
        self.items = {}

    def batch_get_item(self, RequestItems):  # noqa: N803
        (table, spec), = RequestItems.items()
        out = [self.items[(k["pk"]["S"], k["sk"]["S"])] for k in spec["Keys"] if (k["pk"]["S"], k["sk"]["S"]) in self.items]
        return {"Responses": {table: out}}

    def get_item(self, TableName, Key, **_):  # noqa: N803
        it = self.items.get((Key["pk"]["S"], Key["sk"]["S"]))
        return {"Item": it} if it else {}

    def query(self, **kw):
        pk = kw.get("ExpressionAttributeValues", {}).get(":pk", {}).get("S")
        return {"Items": [v for (p, _), v in self.items.items() if p == pk]}

    def update_item(self, TableName, Key, UpdateExpression, ConditionExpression=None, ExpressionAttributeValues=None, **_):  # noqa: N803
        k = (Key["pk"]["S"], Key["sk"]["S"])
        vals = ExpressionAttributeValues or {}
        if ConditionExpression and "groups_iat" in ConditionExpression:
            self._groups_condition(ConditionExpression, self.items.get(k), vals)
        it = self.items.setdefault(k, {"pk": Key["pk"], "sk": Key["sk"]})
        if ConditionExpression and "requests < :lim" in ConditionExpression and "requests" in it:
            if float(it["requests"]["N"]) >= float(vals[":lim"]["N"]):
                raise CondFail()
        if "ADD requests" in UpdateExpression:
            it["requests"] = {"N": str(int(float(it.get("requests", {"N": "0"})["N"])) + 1)}
        if "last_seen_at = :seen" in UpdateExpression:
            it["last_seen_at"] = vals[":seen"]
        if "#mr :one" in UpdateExpression:
            mr = _["ExpressionAttributeNames"]["#mr"]
            it[mr] = {"N": str(int(float(it.get(mr, {"N": "0"})["N"])) + 1)}
        if "ADD spend_usd" in UpdateExpression:
            it["spend_usd"] = {"N": str(float(it.get("spend_usd", {"N": "0"})["N"]) + float(vals[":c"]["N"]))}
        if "token_groups" in UpdateExpression:
            it["token_groups"] = vals[":g"]
        if "groups_iat = :i" in UpdateExpression:
            it["groups_iat"] = vals[":i"]

    @staticmethod
    def _groups_condition(cond, old, vals):
        """Evaluates record_groups' condition as DynamoDB would, so a test sees what the write really does:
        attribute_exists(pk) AND (NOT attribute_type(groups_iat, :n) OR groups_iat <= :i)."""
        assert cond == "attribute_exists(pk) AND (NOT attribute_type(groups_iat, :n) OR groups_iat <= :i)", cond
        held = (old or {}).get("groups_iat")
        is_n = isinstance(held, dict) and set(held) == {vals[":n"]["S"]}  # attribute_type(groups_iat, "N")
        if not old or (is_n and float(held["N"]) > float(vals[":i"]["N"])):
            err = CondFail()
            err.response = {"Item": old} if old else {}
            raise err


@pytest.fixture()
def ic(monkeypatch):
    env = dict(GROUP_CLAIM="groups", USER_CLAIM="email", GROUP_MODELS=json.dumps(GROUP_MODELS), MANTLE_IDS=json.dumps(MANTLE),
               MAX_OUT=json.dumps({"ow/kimi-k3": 32768, "ow/deepseek-v3.2": 16384}), TABLE="t", DEFAULT_BUDGET_USD="50",
               PROJECTS=json.dumps({"alice@example.com": "proj_a", "bob@example.com": "proj_b", "carol@example.com": "proj_c"}),
               MODEL_ALIASES=json.dumps({"kimi": "ow/kimi-k3"}), DEFAULT_RPM="5",
               MODEL_CATALOG=json.dumps([{"id": k, "display_name": k[3:]} for k in MANTLE]),
               WEBSEARCH_MCP_URL="https://websearch.example.com/mcp")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.syspath_prepend(str(LAMBDA))
    sys.modules.pop("interceptor", None)
    sys.modules.pop("policy", None)
    import boto3
    fake = FakeDDB()
    monkeypatch.setattr(boto3, "client", lambda *_a, **_k: fake)
    mod = importlib.import_module("interceptor")
    mod.ddb = fake
    mod.fake = fake
    monkeypatch.setattr(mod.policy, "policy_items", lambda *_a, **_k: {})
    monkeypatch.setattr(mod, "_BG", _Inline())  # run the display-only background writes inline, so tests can read them
    return mod


class _Inline:
    """Stands in for interceptor._BG: runs each submitted call at once (errors swallowed, as a fire-and-forget would)."""
    def submit(self, fn, *a, **kw):
        f = concurrent.futures.Future()
        try:
            f.set_result(fn(*a, **kw))
        except Exception as e:  # noqa: BLE001 - kept on the future, as a real pool would
            f.set_exception(e)
        return f


def tok(user="alice@example.com", groups=("ocgw-team-a",), **extra):
    claims = {"email": user, "groups": list(groups), **extra}
    if user is None:
        claims.pop("email")
    p = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"Bearer e30.{p}.sig"


def call(ic, body=None, path="/inference/v1/chat/completions", method="POST", headers=None, raw=None):
    if raw is None:
        raw = base64.b64encode(json.dumps(body).encode()).decode() if body is not None else ""
    h = {"Authorization": tok()} if headers is None else headers
    return ic.lambda_handler({"http": {"gatewayRequest": {"path": path, "httpMethod": method, "headers": h, "body": raw}}}, None)


def status(r):
    h = r["http"]
    return h["transformedGatewayResponse"]["statusCode"] if "transformedGatewayResponse" in h else "allow"


def resp(r):
    return json.loads(base64.b64decode(r["http"]["transformedGatewayResponse"]["body"]))


def fwd(r):
    t = r["http"]["transformedGatewayRequest"]
    return t["headers"], json.loads(base64.b64decode(t["body"]))


CHAT = {"model": "ow/kimi-k3", "messages": [{"role": "user", "content": "hi"}]}


# ---------------- routing ----------------

@pytest.mark.parametrize("path,method,expect", [
    ("/inference/v1/chat/completions", "POST", "allow"), ("/v1/chat/completions", "POST", "allow"),
    ("/inference/v1/responses", "POST", "allow"), ("/inference/v1/chat/completions/", "POST", "allow"),
    ("/inference/v1/chat/completions", "GET", "allow"),  # method is not trusted (live: POST for every call)
    ("/inference/v1/messages", "POST", 404),
    ("/model/ow/kimi-k3/invoke", "POST", 404), ("/inference/v1/chat%2Fcompletions", "POST", 404),
    ("/inference/v1/chat%252Fcompletions", "POST", 404), ("/x/inference/v1/chat/completions", "POST", 404),
    ("/inference//v1/chat/completions", "POST", 404), ("/inference/v1/../v1/chat/completions", "POST", 404),
    ("/inference/v1/embeddings", "POST", 404), ("", "POST", 404), ("/inference/v1/chat/completions?x=1", "POST", "allow"),
])
def test_routes(ic, path, method, expect):
    assert status(call(ic, CHAT, path=path, method=method)) == expect


# ---------------- identity ----------------

@pytest.mark.parametrize("headers,expect", [
    ({}, 403), ({"Authorization": "Basic abc"}, 403), ({"Authorization": "Bearer"}, 403),
    ({"Authorization": "Bearer a.b.c extra"}, 403), ({"Authorization": "Bearer not-a-jwt"}, 403),
    ({"Authorization": tok(user=None)}, 403), ({"Authorization": tok(), "authorization": tok()}, 403),
    ({"Authorization": tok(user=["x"])}, 403), ({"Authorization": tok(user="")}, 403), ({"Authorization": tok()}, "allow"),
    ({"authorization": tok()}, "allow"), ({"AUTHORIZATION": tok()}, "allow"),
])
def test_identity(ic, headers, expect):
    assert status(call(ic, CHAT, headers=headers)) == expect


def test_groups_string_comma_only(ic):
    assert ic.groups_of({"groups": "ocgw-team-a, x"}) == ["ocgw-team-a", "x"]
    assert ic.groups_of({"groups": "former ocgw-team-b"}) == ["former ocgw-team-b"]
    assert ic.groups_of({"groups": 5}) == []
    assert ic.groups_of({"groups": ["a", 3, None, "a"]}) == ["a"]


# ---------------- allow-list grid ----------------

USERS = {"a": ("alice@example.com", ["ocgw-team-a"]), "b": ("bob@example.com", ["ocgw-team-b"]),
         "ab": ("alice@example.com", ["ocgw-team-a", "ocgw-team-b"]), "none": ("alice@example.com", []),
         "other": ("alice@example.com", ["nobody"]), "case": ("alice@example.com", ["OCGW-TEAM-A"])}
MODELS = list(MANTLE) + ["kimi", "ow/unknown", "gpt-4o", "OW/kimi-k3", " ow/kimi-k3", "ow/kimi-k3\n"]


def expected(model, groups):
    real = {"kimi": "ow/kimi-k3"}.get(model, model)
    if real not in MANTLE:
        return 404
    pats = [p for g in groups for p in GROUP_MODELS.get(g, [])]
    return "allow" if any(real == p or (p.endswith("*") and real.startswith(p[:-1])) for p in pats) else 403


@pytest.mark.parametrize("who,model", list(itertools.product(USERS, MODELS)))
def test_allow_list_grid(ic, who, model):
    user, groups = USERS[who]
    r = call(ic, {**CHAT, "model": model}, headers={"Authorization": tok(user, groups)})
    assert status(r) == expected(model, groups)
    if status(r) == "allow":
        h, b = fwd(r)
        assert b["model"] == MANTLE[{"kimi": "ow/kimi-k3"}.get(model, model)]
        assert h["OpenAI-Project"] == {"alice@example.com": "proj_a", "bob@example.com": "proj_b"}[user]


@pytest.mark.parametrize("spoof", ["OpenAI-Project", "openai-project", "OPENAI-PROJECT", "x-ocgw-user", "OpenAI-Organization"])
def test_client_cannot_set_project(ic, spoof):
    h, _ = fwd(call(ic, CHAT, headers={"Authorization": tok(), spoof: "proj_c"}))
    assert [k for k in h if k.lower() == spoof.lower() and h[k] == "proj_c"] == []
    assert h["OpenAI-Project"] == "proj_a"


def test_user_without_project_refused(ic):
    r = call(ic, CHAT, headers={"Authorization": tok("dave@example.com", ["ocgw-team-b"])})
    assert status(r) == 403 and "usage project" in resp(r)["error"]["message"]


# ---------------- bodies ----------------

@pytest.mark.parametrize("raw,expect", [
    (base64.b64encode(b"not json").decode(), 400), (base64.b64encode(b"[1,2]").decode(), 400),
    (base64.b64encode(b'"s"').decode(), 400), (base64.b64encode(b'{"model": NaN}').decode(), 400),
    (base64.b64encode(b'{"model": "ow/kimi-k3", "temperature": Infinity}').decode(), 400),
    (base64.b64encode(b'{"model": "ow/kimi-k3", "temperature": 1e999}').decode(), 400),
    (base64.b64encode(("[" * 5000 + "]" * 5000).encode()).decode(), 400), ("!!notbase64!!", 400),
    (base64.b64encode(b'{"model": 5}').decode(), 400), (base64.b64encode(b'{"model": null}').decode(), 400),
    (base64.b64encode(b'{"model": ["ow/kimi-k3"]}').decode(), 400), (base64.b64encode(b'{"model": ""}').decode(), 400),
    (base64.b64encode(b'{}').decode(), 400), ("", 400),
    (base64.b64encode(b'{"model": "ow/glm-5", "model": "ow/kimi-k3"}').decode(), "allow"),
])
def test_bad_bodies(ic, raw, expect):
    assert status(call(ic, raw=raw)) == expect


def test_duplicate_key_forwards_parsed_model(ic):
    raw = base64.b64encode(b'{"model": "ow/deepseek-v3.2", "model": "ow/kimi-k3", "messages": []}').decode()
    _, b = fwd(call(ic, raw=raw))
    assert b["model"] == "moonshotai.kimi-k3"


def test_error_shape_is_openai(ic):
    e = resp(call(ic, {**CHAT, "model": "ow/glm-5"}))["error"]
    assert set(e) == {"message", "type", "code", "param"} and e["type"] == "permission_error"


# ---------------- normalisation ----------------

def test_empty_assistant_content_with_tool_calls(ic):
    msgs = [{"role": "user", "content": "x"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "type": "function", "function": {"name": "f", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "1", "content": "ok"}, {"role": "assistant", "content": ""}]
    _, b = fwd(call(ic, {**CHAT, "messages": msgs}))
    assert b["messages"][1]["content"] is None and b["messages"][3]["content"] == ""


@pytest.mark.parametrize("so,expect", [(None, True), ({}, True), ({"include_usage": False}, True), ({"include_usage": True}, True), ("bad", True)])
def test_stream_include_usage(ic, so, expect):
    body = {**CHAT, "stream": True, **({"stream_options": so} if so is not None else {})}
    _, b = fwd(call(ic, body))
    assert b["stream_options"]["include_usage"] is expect


def test_non_stream_untouched(ic):
    _, b = fwd(call(ic, CHAT))
    assert "stream_options" not in b


@pytest.mark.parametrize("k,v,out", [("max_tokens", 99999, 32768), ("max_tokens", 100, 100), ("max_completion_tokens", 40000, 32768),
                                     ("max_output_tokens", 50000, 32768), ("max_tokens", True, True), ("max_tokens", "9e9", "9e9"),
                                     ("max_tokens", 50000.0, 32768), ("max_tokens", 100.0, 100.0)])
def test_max_tokens_clamp(ic, k, v, out):
    _, b = fwd(call(ic, {**CHAT, k: v}))
    assert b[k] == out


# ---------------- budgets and rpm ----------------

def put(ic, sk, user="alice@example.com", **attrs):
    ic.fake.items[(f"USER#{user}", sk)] = {"pk": {"S": f"USER#{user}"}, "sk": {"S": sk},
                                           **{k: ({"N": str(v)} if isinstance(v, (int, float)) else {"S": v}) for k, v in attrs.items()}}


@pytest.mark.parametrize("window", ["Monthly", "Daily", "Hourly"])
@pytest.mark.parametrize("spent,cap,expect", [(0, 1, "allow"), (0.99, 1, "allow"), (1, 1, 400), (5, 1, 400), (0, 0, 400)])
def test_budget_windows(ic, window, spent, cap, expect):
    g = time.gmtime()
    sk = {"Monthly": time.strftime("MONTH#%Y-%m", g), "Daily": time.strftime("DAY#%Y-%m-%d", g), "Hourly": time.strftime("HOUR#%Y-%m-%dT%H", g)}[window]
    put(ic, sk, spend_usd=spent)
    put(ic, "BUDGET", **{{"Monthly": "budget_usd", "Daily": "daily_usd", "Hourly": "hourly_usd"}[window]: cap})
    r = call(ic, CHAT)
    assert status(r) == expect
    if expect == 400:
        e = resp(r)["error"]
        assert e["code"] == "budget_exceeded" and window in e["message"]


def test_rpm(ic):
    got = [status(call(ic, CHAT)) for _ in range(7)]
    assert got == ["allow"] * 5 + [429, 429]


def test_rpm_is_per_user(ic):
    for _ in range(5):
        call(ic, CHAT)
    assert status(call(ic, CHAT, headers={"Authorization": tok("bob@example.com", ["ocgw-team-b"])})) == "allow"


def test_denied_model_does_not_spend_rpm(ic):
    for _ in range(10):
        call(ic, {**CHAT, "model": "ow/glm-5"})
    assert status(call(ic, CHAT)) == "allow"


# ---------------- models listing ----------------

@pytest.mark.parametrize("groups,ids", [
    (["ocgw-team-a"], ["ow/kimi-k3", "ow/glm-5.3"]), (["ocgw-team-b"], list(MANTLE)),  # aliases are accepted, never listed
    ([], []), (["nobody"], []),
])
def test_models_listing(ic, groups, ids):
    r = call(ic, path="/inference/v1/models", method="GET", headers={"Authorization": tok(groups=groups)})
    d = resp(r)
    assert d["object"] == "list" and [m["id"] for m in d["data"]] == ids


def test_models_listing_is_refused_for_a_user_without_a_project(ic, monkeypatch):
    monkeypatch.setattr(ic, "PROJECTS", {"bob@example.com": "proj_b"})  # alice is not in var.users
    r = call(ic, path="/inference/v1/models", method="GET", headers={"Authorization": tok(groups=["ocgw-team-a"])})
    assert status(r) == 403  # the same refusal as a call: no models listed that every call would refuse


def test_models_listing_rejects_body_model(ic):
    assert status(call(ic, {"model": "x"}, path="/inference/v1/models", method="GET")) == 400


# ---------------- web search ----------------

@pytest.mark.parametrize("body,expect", [({}, 400), ({"q": ""}, 400), ({"q": "x" * 2001}, 400), ({"q": "x", "count": 0}, 400),
                                         ({"q": "x", "count": 21}, 400), ({"q": "x", "count": True}, 400), ({"q": 5}, 400)])
def test_websearch_validation(ic, body, expect):
    assert status(call(ic, body, path="/inference/websearch")) == expect


def test_websearch_needs_model_access(ic):
    r = call(ic, {"q": "x"}, path="/websearch", headers={"Authorization": tok(groups=[])})
    assert status(r) == 403


@pytest.mark.parametrize("code,expect", [(-32002, 400), (-32003, 429), (-32004, 403)])
def test_websearch_maps_mcp_refusals_and_takes_no_rpm_slot(ic, monkeypatch, code, expect):
    calls = []
    monkeypatch.setattr(ic, "take_rpm", lambda *a: calls.append(a) or True)

    def refused(auth, q, n):
        raise ic.SearchRefused(code, "refused by the web search gate")
    monkeypatch.setattr(ic, "web_search", refused)
    r = call(ic, {"q": "x"}, path="/websearch")
    assert status(r) == expect
    assert calls == []  # the MCP-side gate is the only rpm gate for a search


def test_websearch_success_takes_no_rpm_slot(ic, monkeypatch):
    calls = []
    monkeypatch.setattr(ic, "take_rpm", lambda *a: calls.append(a) or True)
    monkeypatch.setattr(ic, "web_search", lambda auth, q, n: [{"title": "t", "url": "https://e.x", "snippet": "", "published": None}])
    assert status(call(ic, {"q": "x"}, path="/websearch")) == 200 and calls == []


def test_mcp_gate(ic):
    ev = lambda groups: {"mcp": {"gatewayRequest": {"headers": {"Authorization": tok(groups=groups)},  # noqa: E731
                                                    "body": {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "web___WebSearch", "arguments": {"query": "x"}}}}}}
    ok = ic.lambda_handler(ev(["ocgw-team-a"]), None)
    assert "transformedGatewayRequest" in ok["mcp"]
    no = ic.lambda_handler(ev([]), None)
    assert no["mcp"]["transformedGatewayResponse"]["body"]["error"]["code"] == -32004
    month = time.strftime("MONTH#%Y-%m", time.gmtime())
    assert float(ic.fake.items[("USER#alice@example.com", month)]["spend_usd"]["N"]) == pytest.approx(0.007)


def test_mcp_other_methods_pass(ic):
    r = ic.lambda_handler({"mcp": {"gatewayRequest": {"headers": {}, "body": {"method": "tools/list", "id": 1}}}}, None)
    assert "transformedGatewayRequest" in r["mcp"]


# ---------------- fail closed ----------------

def test_crash_fails_closed(ic, monkeypatch):
    monkeypatch.setattr(ic, "spend", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom secret")))
    r = call(ic, CHAT)
    assert status(r) == 500 and "boom" not in json.dumps(resp(r))


def test_ledger_throttled_fails_closed(ic, monkeypatch):
    monkeypatch.setattr(ic.fake, "batch_get_item", lambda RequestItems: {"Responses": {}, "UnprocessedKeys": RequestItems})  # noqa: N803
    monkeypatch.setattr(ic.time, "sleep", lambda s: None)
    assert status(call(ic, CHAT)) == 500


def test_allowed_call_counts_request(ic):
    for _ in range(3):
        call(ic, CHAT)
    call(ic, {**CHAT, "model": "ow/glm-5"})  # denied: not counted
    g = time.gmtime()
    for sk in (time.strftime("MONTH#%Y-%m", g), time.strftime("DAY#%Y-%m-%d", g), time.strftime("HOUR#%Y-%m-%dT%H", g)):
        assert ic.fake.items[("USER#alice@example.com", sk)]["requests"]["N"] == "3"


def test_count_failure_does_not_block(ic, monkeypatch):
    real = ic.fake.update_item

    def flaky(**kw):
        if "ADD requests :one SET #ttl = if_not_exists" in kw["UpdateExpression"]:
            raise RuntimeError("throttled")
        return real(**kw)
    monkeypatch.setattr(ic.fake, "update_item", flaky)
    assert status(call(ic, CHAT)) == "allow"


def test_ledger_user_with_and_without_map(monkeypatch):
    import interceptor as m
    monkeypatch.setattr(m, "IDENTITY_MAP", {"00u1": "alice", "00u2": "bob"})
    assert m.ledger_user("00u1") == "alice"
    assert m.ledger_user("alice") == ""   # a claim value naming a ledger user is not an identity
    assert m.ledger_user("00u9") == ""    # unmapped id refused
    monkeypatch.setattr(m, "IDENTITY_MAP", {})
    assert m.ledger_user("carol") == "carol"  # cognito: username is the ledger name


def test_fallback_to_a_model_the_caller_may_not_use_is_refused(ic, monkeypatch):
    # alice's group may use kimi-k3; pretend its chain falls back to deepseek-v3.2, which her group may not use.
    monkeypatch.setattr(ic, "FALLBACKS", {"ow/kimi-k3": ["ow/deepseek-v3.2"]})
    r = call(ic, {"model": "ow/kimi-k3", "messages": [{"role": "user", "content": "hi"}]})
    assert status(r) == 403 and "fall back" in resp(r)["error"]["message"]
    monkeypatch.setattr(ic, "FALLBACKS", {"ow/kimi-k3": ["ow/kimi-k3"]})
    assert status(call(ic, {"model": "ow/kimi-k3", "messages": [{"role": "user", "content": "hi"}]})) != 403


def test_unrepresentable_number_refused_before_rpm_and_count(ic, monkeypatch):
    calls = []
    monkeypatch.setattr(ic, "take_rpm", lambda *a: calls.append("rpm") or True)
    monkeypatch.setattr(ic, "count_request", lambda *a: calls.append("count"))
    import base64 as b64
    raw = b'{"model": "ow/kimi-k3", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1e999}'
    ev = {"http": {"gatewayRequest": {"path": "/v1/chat/completions", "httpMethod": "POST",
                                      "headers": {"Authorization": tok()}, "body": b64.b64encode(raw).decode()}}}
    r = ic.lambda_handler(ev, None)
    assert status(r) == 400 and calls == []


def test_request_counted_in_total_and_per_model(ic):
    r = call(ic, {"model": "kimi", "messages": [{"role": "user", "content": "hi"}]})
    assert "transformedGatewayRequest" in json.dumps(r)
    month = time.strftime("MONTH#%Y-%m", time.gmtime())
    it = ic.fake.items[("USER#alice@example.com", month)]
    assert it["requests"]["N"] == "1" and it["m|ow/kimi-k3|req"]["N"] == "1"
    assert it["last_seen_at"]["S"].startswith(time.strftime("%Y-%m-%dT", time.gmtime()))  # the Users page's "Last seen"


def test_search_runs_when_the_charge_write_fails(ic, monkeypatch, capsys):
    def boom(user, at=None):  # charge_search's own signature, so the failure tested is the write's, not a TypeError
        raise RuntimeError("ProvisionedThroughputExceededException")
    monkeypatch.setattr(ic, "charge_search", boom)
    ev = {"mcp": {"gatewayRequest": {"headers": {"Authorization": tok(groups=["ocgw-team-a"])},
                                     "body": {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "web___WebSearch", "arguments": {"query": "x"}}}}}}
    r = ic.lambda_handler(ev, None)
    assert "transformedGatewayRequest" in r["mcp"]
    assert "charge_error" in capsys.readouterr().out


def test_a_charge_that_fails_on_one_window_names_that_window(ic, capsys):
    # r142 /code-review: an unreadable record on an uncapped hour passes the gate (r141) and its ADD fails; the log
    # line names the window, so an admin can find the record (docs/security.md, Review decisions).
    hour = time.strftime("HOUR#%Y-%m-%dT%H", time.gmtime())
    ic.fake.items[("USER#alice@example.com", hour)] = {"pk": {"S": "USER#alice@example.com"}, "sk": {"S": hour},
                                                      "spend_usd": {"S": "x"}}
    ev = {"mcp": {"gatewayRequest": {"headers": {"Authorization": tok(groups=["ocgw-team-a"])},
                                     "body": {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "web___WebSearch", "arguments": {"query": "x"}}}}}}
    r = ic.lambda_handler(ev, None)
    assert "transformedGatewayRequest" in r["mcp"]  # no hourly cap: the bad record blocks nothing
    logged = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.startswith("{")]
    assert any(hour in (x.get("charge_error") or "") for x in logged)


def test_a_charge_that_fails_on_several_windows_names_each(ic, capsys):
    # r143 /code-review: only the first failed window was named, so a second bad record stayed hidden.
    day, hour = time.strftime("DAY#%Y-%m-%d", time.gmtime()), time.strftime("HOUR#%Y-%m-%dT%H", time.gmtime())
    for sk in (day, hour):
        ic.fake.items[("USER#alice@example.com", sk)] = {"pk": {"S": "USER#alice@example.com"}, "sk": {"S": sk}, "spend_usd": {"S": "x"}}
    with pytest.raises(RuntimeError) as e:
        ic.charge_search("alice@example.com")
    assert day in str(e.value) and hour in str(e.value)


def test_mcp_gate_refuses_tools_other_than_web_search(ic):
    ev = {"mcp": {"gatewayRequest": {"headers": {"Authorization": tok(groups=["ocgw-team-a"])},
                                     "body": {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "other___DeleteAll"}}}}}
    r = ic.lambda_handler(ev, None)
    assert r["mcp"]["transformedGatewayResponse"]["body"]["error"]["code"] == -32004


def test_max_tokens_clamped_to_smallest_limit_in_fallback_chain(ic, monkeypatch):
    import base64 as b64
    monkeypatch.setattr(ic, "FALLBACKS", {"ow/kimi-k3": ["ow/deepseek-v3.2"]})
    monkeypatch.setattr(ic, "allow_list", lambda groups: ["ow/*"])
    r = call(ic, {"model": "ow/kimi-k3", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 30000})
    out = json.loads(b64.b64decode(r["http"]["transformedGatewayRequest"]["body"]))
    assert out["max_tokens"] == 16384  # deepseek-v3.2's limit, not kimi-k3's 32768


def test_framing_headers_not_forwarded(ic):
    r = call(ic, {"model": "ow/kimi-k3", "messages": [{"role": "user", "content": "hi"}]},
             headers={"Authorization": tok(), "Content-Length": "57", "Transfer-Encoding": "chunked"})
    fwd = {k.lower() for k in r["http"]["transformedGatewayRequest"]["headers"]}
    assert "content-length" not in fwd and "transfer-encoding" not in fwd


def test_models_listing_leaves_out_models_whose_fallback_is_denied(ic, monkeypatch):
    monkeypatch.setattr(ic, "FALLBACKS", {"ow/kimi-k3": ["ow/deepseek-v3.2"]})
    r = call(ic, path="/inference/v1/models", method="GET", headers={"Authorization": tok(groups=["ocgw-team-a"])})
    assert [m["id"] for m in resp(r)["data"]] == ["ow/glm-5.3"]


def test_rpm_zero_blocks_every_request(ic):
    assert ic.take_rpm("alice@example.com", 0) is False


def _budget(groups=None, **extra):
    it = {"pk": {"S": "USER#zoe"}, "sk": {"S": "BUDGET"}, **extra}
    if groups is not None:
        it["token_groups"] = {"L": [{"S": g} for g in groups]}
    return it


def test_record_groups_writes_only_when_the_groups_change(ic, monkeypatch):
    # r131: the request compares against the BUDGET item it read, so a steady user costs no write.
    calls = []
    monkeypatch.setattr(ic.display_ddb(), "update_item", lambda **kw: calls.append(kw))
    ic.record_groups("zoe", ["b", "a"], 100, _budget(["a", "b"], groups_iat={"N": "100"}))  # same set and token
    assert calls == []
    ic.record_groups("zoe", ["a"], 101, _budget(["a", "b"]))
    ic.record_groups("zoe", [], 102, _budget())  # nothing recorded yet: an empty list is still recorded
    assert [c["ExpressionAttributeValues"][":g"] for c in calls] == [{"L": [{"S": "a"}]}, {"L": []}]


def test_record_groups_never_creates_a_removed_users_budget_item(ic, monkeypatch):
    calls = []
    monkeypatch.setattr(ic.display_ddb(), "update_item", lambda **kw: calls.append(kw))
    ic.record_groups("ghost", ["a"], 100, None)  # read, and missing: no write, and not settled (r135 /code-review)
    assert calls == []
    ic.record_groups("ghost", ["a"], 100)  # not read: the conditional write decides
    assert calls[0]["ConditionExpression"].startswith("attribute_exists(pk) AND")  # UpdateItem would create it
    assert ic._SETTLED["ghost"] == (100, ["a"])


def test_record_groups_keeps_the_newest_tokens_groups(ic, monkeypatch):
    # r131 /code-review: with an old token (team-a) and a new one (team-b) in use, last-writer-wins flipped the
    # recorded groups with every request. The write carries the token's iat and lands only over an older one.
    calls = []
    monkeypatch.setattr(ic.display_ddb(), "update_item", lambda **kw: calls.append(kw))
    ic.record_groups("zoe", ["team-a"], 1000, _budget(["team-b"]))
    kw = calls[0]
    assert kw["ConditionExpression"] == "attribute_exists(pk) AND (NOT attribute_type(groups_iat, :n) OR groups_iat <= :i)"
    assert kw["ExpressionAttributeValues"][":i"] == {"N": "1000"}
    assert kw["UpdateExpression"] == "SET token_groups = :g, groups_iat = :i REMOVE groups_seen_at"


def test_record_groups_refused_as_older_is_quiet(ic, monkeypatch, capsys):
    def older(**_kw):
        raise ic.display_ddb().exceptions.ConditionalCheckFailedException("older")
    monkeypatch.setattr(ic.display_ddb(), "update_item", older)
    ic.record_groups("zoe", ["team-a"], 1, _budget(["team-b"]))
    assert "record_groups_error" not in capsys.readouterr().out


@pytest.mark.parametrize("claims,iat", [({"iat": 1794620000}, 1794620000), ({"iat": 1794620000.7}, 1794620000), ({}, 0),
                                        ({"iat": "1794620000"}, 0), ({"iat": True}, 0), ({"iat": -5}, 0)])
def test_token_iat(ic, claims, iat):
    assert ic.token_iat(claims) == iat


def test_record_groups_drops_the_old_order_stamp(ic, monkeypatch):
    # r129-r130 wrote groups_seen_at; the first write after the upgrade removes it, even with unchanged groups.
    calls = []
    monkeypatch.setattr(ic.display_ddb(), "update_item", lambda **kw: calls.append(kw))
    ic.record_groups("zoe", ["a"], 100, _budget(["a"], groups_seen_at={"S": "2026-11-14T02:07:00Z"}))
    assert "REMOVE groups_seen_at" in calls[0]["UpdateExpression"]


def test_record_groups_uses_a_one_try_short_timeout_client(ic, monkeypatch):
    # r131 /code-review: on the request path the default client's retries could hold a call for seconds.
    import boto3
    made = []
    monkeypatch.setattr(boto3, "client", lambda *a, **k: made.append(k) or ic.fake)
    sys.modules.pop("interceptor", None)
    mod = importlib.import_module("interceptor")
    assert not [k for k in made if "config" in k]  # made on first use, not at import (r133 /code-review)
    mod.display_ddb()
    mod.display_ddb()
    cfgs = [k["config"] for k in made if "config" in k]
    assert len(cfgs) == 1 and cfgs[0].connect_timeout == 0.5 and cfgs[0].read_timeout == 0.5
    assert cfgs[0].retries == {"total_max_attempts": 1}


def test_record_groups_runs_in_the_request_not_in_the_background(ic, monkeypatch):
    # A background write Lambda finishes after a later request could put an older token's groups back (r128).
    queued = []
    monkeypatch.setattr(ic, "_BG", type("Q", (), {"submit": lambda self, fn, *a, **k: queued.append(fn.__name__)})())
    writes = []
    monkeypatch.setattr(ic.display_ddb(), "update_item", lambda **kw: writes.append(kw))
    ic.record_groups("zoe", ["new"], 100, _budget(["old"]))
    assert "record_groups" not in queued and writes[0]["ExpressionAttributeValues"][":g"] == {"L": [{"S": "new"}]}


def test_record_groups_skips_a_token_older_than_the_recorded_one(ic, monkeypatch):
    # r132 /code-review: the item already says a newer token is recorded, so the certain-to-fail write is not sent.
    calls = []
    monkeypatch.setattr(ic.display_ddb(), "update_item", lambda **kw: calls.append(kw))
    ic.record_groups("zoe", ["team-a"], 1000, _budget(["team-b"], groups_iat={"N": "2000"}))
    ic.record_groups("zoe", ["team-a"], 0, _budget(["team-b"], groups_iat={"N": "2000"}))  # a token with no iat
    assert calls == []
    ic.record_groups("zoe", ["team-c"], 3000, _budget(["team-b"], groups_iat={"N": "2000"}))
    assert len(calls) == 1


@pytest.mark.parametrize("stored", [{"L": [{"S": "a"}, {"NULL": True}]}, {"L": None}, {"S": "a"}, "junk"])
def test_record_groups_never_raises_on_an_odd_stored_list(ic, monkeypatch, stored):
    # r132 /code-review: sorted() over a list with a non-string raised outside the try and failed the user's calls.
    calls = []
    monkeypatch.setattr(ic.display_ddb(), "update_item", lambda **kw: calls.append(kw))
    item = _budget()
    item["token_groups"] = stored
    ic.record_groups("zoe", ["a"], 100, item)  # no exception, whatever the item holds


@pytest.mark.parametrize("path", ["/inference/websearch", "mcp"])
def test_a_websearch_refusal_records_the_callers_groups(ic, monkeypatch, path):
    # r132 /code-review: websearch_denied did not record the groups, unlike model_denied and fallback_denied.
    ic.fake.items[("USER#alice@example.com", "BUDGET")] = {"pk": {"S": "USER#alice@example.com"}, "sk": {"S": "BUDGET"}}
    monkeypatch.setattr(ic, "WEBSEARCH_MCP_URL", "https://ws.example/mcp")
    headers = {"Authorization": tok(groups=["NewGroup"])}
    if path == "mcp":
        ic.lambda_handler({"mcp": {"gatewayRequest": {"headers": headers, "body": {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "web___WebSearch", "arguments": {"query": "x"}}}}}}, None)
    else:
        assert status(call(ic, {"q": "x"}, path=path, headers=headers)) == 403
    assert ic.fake.items[("USER#alice@example.com", "BUDGET")]["token_groups"] == {"L": [{"S": "NewGroup"}]}


def test_record_groups_moves_groups_iat_forward_for_the_same_groups(ic, monkeypatch):
    # r133 /code-review: equal groups from a newer token left groups_iat behind, so an older token with other
    # groups could still overwrite the record.
    calls = []
    monkeypatch.setattr(ic.display_ddb(), "update_item", lambda **kw: calls.append(kw))
    ic.record_groups("zoe", ["A"], 200, _budget(["A"], groups_iat={"N": "100"}))
    assert calls and calls[0]["ExpressionAttributeValues"][":i"] == {"N": "200"}
    ic.record_groups("zoe", ["A"], 100, _budget(["A"], groups_iat={"N": "100"}))  # same token as recorded: nothing
    assert len(calls) == 1


def test_record_groups_settles_a_token_once_per_container(ic, monkeypatch):
    # r133-r134 /code-review: a refused client retrying paid a read per attempt. Nothing is read now, and the highest
    # settled iat is remembered per user, so repeats and older tokens cost nothing.
    writes = []
    monkeypatch.setattr(ic.display_ddb(), "get_item", lambda **kw: (_ for _ in ()).throw(AssertionError("no reads")))
    monkeypatch.setattr(ic.display_ddb(), "update_item", lambda **kw: writes.append(kw))
    for _ in range(3):
        ic.record_groups("zoe", ["new"], 500)
    ic.record_groups("zoe", ["old"], 400)  # an older token on another machine: no call either
    assert len(writes) == 1
    ic.record_groups("zoe", ["new"], 900)  # a new token: one write
    assert len(writes) == 2


def test_record_groups_settles_only_when_a_newer_token_holds_the_record(ic, monkeypatch, capsys):
    # r136 /code-review: a write refused for a missing item was settled like one refused for a newer token.
    calls, item = [], {}

    def refused(**kw):
        calls.append(kw)
        e = ic.display_ddb().exceptions.ConditionalCheckFailedException("refused")
        e.response = {"Item": item} if item else {}
        raise e
    monkeypatch.setattr(ic.display_ddb(), "update_item", refused)
    ic.record_groups("newbie", ["a"], 5)
    ic.record_groups("newbie", ["a"], 5)  # no item: not settled, so asked again
    assert len(calls) == 2 and "newbie" not in ic._SETTLED
    assert calls[0]["ReturnValuesOnConditionCheckFailure"] == "ALL_OLD"
    item.update(_budget(["b"], groups_iat={"N": "9"}))
    ic.record_groups("newbie", ["a"], 5)  # a newer token's record: settled
    ic.record_groups("newbie", ["a"], 5)
    assert len(calls) == 3

    def boom(**_kw):
        raise RuntimeError("ddb down")
    monkeypatch.setattr(ic.display_ddb(), "update_item", boom)
    ic.record_groups("other", ["a"], 5)
    ic.record_groups("other", ["a"], 5)  # an error is not settled: tried again
    assert capsys.readouterr().out.count("record_groups_error") == 2


def test_record_groups_on_a_warm_container_takes_new_groups_at_the_same_iat(ic, monkeypatch):
    # r136 /code-review: the memo keyed on iat alone, so a token with no iat (always 0) or a second sign-in in the
    # same second could not change the groups once the container had settled that iat.
    calls = []
    monkeypatch.setattr(ic.display_ddb(), "update_item", lambda **kw: calls.append(kw))
    ic.record_groups("zoe", ["team-a"], 0)
    ic.record_groups("zoe", ["team-a"], 0)  # the same token again: nothing
    ic.record_groups("zoe", ["team-b"], 0)  # moved in the IdP, new token, still iat 0: recorded
    assert [c["ExpressionAttributeValues"][":g"] for c in calls] == [{"L": [{"S": "team-a"}]}, {"L": [{"S": "team-b"}]}]


def test_record_groups_lets_a_token_without_iat_or_from_the_same_second_change_the_groups(ic, monkeypatch):
    # r135 /code-review: with a strict "<", groups_iat 0 (an IdP with no iat) or a re-sign-in in the same second
    # could never change the recorded groups.
    calls = []
    monkeypatch.setattr(ic.display_ddb(), "update_item", lambda **kw: calls.append(kw))
    ic.record_groups("zoe", ["team-b"], 0, _budget(["team-a"], groups_iat={"N": "0"}))
    assert calls and calls[0]["ExpressionAttributeValues"][":g"] == {"L": [{"S": "team-b"}]}


def test_an_http_web_search_is_recorded_by_its_mcp_call_only(ic, monkeypatch):
    # r135-r136 /code-review: the HTTP leg recorded again after the MCP leg (a write certain to fail), and must
    # still record when its MCP call may never have run (websearch_error).
    seen = []
    monkeypatch.setattr(ic, "record_groups", lambda *a, **k: seen.append(a))
    ic.finish({}, "websearch", 0, "alice@example.com", ["a"], 5, {}, record=False)
    ic.finish({}, "websearch_error", 0, "alice@example.com", ["a"], 5, {})
    ic.finish({}, "model_denied", 0, "alice@example.com", ["a"], 5, {})
    assert len(seen) == 2


def test_an_allowed_call_starts_the_groups_write_beside_the_gate(ic, monkeypatch):
    # r136 /code-review: the write ran after the gate's rpm write, a serial round trip on each new token.
    started = []
    real = ic._POOL.submit
    monkeypatch.setattr(ic._POOL, "submit", lambda fn, *a, **k: started.append(fn.__name__) or real(fn, *a, **k))
    ic.fake.items[("USER#alice@example.com", "BUDGET")] = {"pk": {"S": "USER#alice@example.com"}, "sk": {"S": "BUDGET"}}
    assert status(call(ic, CHAT)) == "allow"
    assert "record_groups" in started and "token_groups" in ic.fake.items[("USER#alice@example.com", "BUDGET")]


def test_a_gate_that_raises_still_waits_for_the_groups_write(ic, monkeypatch):
    # r137 /code-review: a 500 from the gate skipped finish(), leaving the write to run after the reply.
    waited = []

    class Fut:
        def result(self):
            waited.append(True)
    monkeypatch.setattr(ic._POOL, "submit", lambda *a, **k: Fut())
    monkeypatch.setattr(ic, "gate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("throttled")))
    ic.fake.items[("USER#alice@example.com", "BUDGET")] = {"pk": {"S": "USER#alice@example.com"}, "sk": {"S": "BUDGET"}}
    call(ic, CHAT)  # the handler turns the error into its 500
    assert waited


def test_an_unreadable_spend_fails_the_call_closed(ic):
    # r138 /code-review: a hand-edited spend_usd stored as a string counted as $0 at the gate (fail open).
    month = time.strftime("MONTH#%Y-%m", time.gmtime())
    ic.fake.items[("USER#alice@example.com", month)] = {"pk": {"S": "USER#alice@example.com"}, "sk": {"S": month},
                                                       "spend_usd": {"S": "12.5"}}
    r = call(ic, CHAT)  # r139: a budget refusal that says why, not a bare 500
    assert status(r) == 400 and resp(r)["error"]["code"] == "budget_exceeded" and "cannot be read" in resp(r)["error"]["message"]


def test_a_zero_cap_is_refused_as_a_budget_not_as_an_unreadable_record(ic, monkeypatch):
    # r144 /code-review: policy.refused(readable, 0.0, cap) was True for a readable window at a $0 cap, so a user an
    # admin blocked on purpose was told their usage record cannot be read.
    real = ic.policy.resolve

    def caps(*a, **k):
        eff = real(*a, **k)
        eff["caps"]["Daily"]["cap"] = 0.0
        return eff
    monkeypatch.setattr(ic.policy, "resolve", caps)
    r = call(ic, CHAT)
    msg = resp(r)["error"]["message"]
    assert status(r) == 400 and "cannot be read" not in msg and "Daily budget exceeded" in msg


def test_a_negative_spend_edit_blocks_and_never_lifts_the_cap(ic):
    # r140 /code-review: spend_usd = -1000 gave the user $1,000 more than the cap.
    month = time.strftime("MONTH#%Y-%m", time.gmtime())
    ic.fake.items[("USER#alice@example.com", month)] = {"pk": {"S": "USER#alice@example.com"}, "sk": {"S": month},
                                                       "spend_usd": {"N": "-1000"}}
    r = call(ic, CHAT)
    assert status(r) == 400 and "cannot be read" in resp(r)["error"]["message"]


def test_an_unreadable_groups_iat_is_overwritten_not_settled(ic, monkeypatch):
    # r140 /code-review: a groups_iat stored as a string made every write fail, and the failure looked like a newer
    # token, so the user's groups were never recorded again.
    calls = []
    monkeypatch.setattr(ic.display_ddb(), "update_item", lambda **kw: calls.append(kw))
    ic.record_groups("zoe", ["team-b"], 100, _budget(["team-a"], groups_iat={"S": "junk"}))
    assert calls and "NOT attribute_type(groups_iat, :n)" in calls[0]["ConditionExpression"]
    assert "groups_iat <= :i" in calls[0]["ConditionExpression"]  # r141 /code-review: still ordered by iat


@pytest.mark.parametrize("held,iat,written", [({"S": "junk"}, 100, True), (None, 100, True), ({"N": "50"}, 100, True),
                                              ({"N": "100"}, 100, True), ({"N": "200"}, 100, False), ({"NULL": True}, 100, True)])
def test_the_groups_condition_replaces_a_junk_stamp_and_refuses_an_older_token(ic, held, iat, written):
    # r142 /code-review: the tests only string-matched the condition. FakeDDB now evaluates it, on the path where the
    # request did not read the BUDGET item (the write alone decides): a junk or missing stamp is replaced, an equal or
    # older one too, and a newer token's stamp stays.
    item = _budget(["team-a"], **({"groups_iat": held} if held else {}))
    ic.fake.items[("USER#zoe", "BUDGET")] = item
    ic.record_groups("zoe", ["team-b"], iat)
    got = ic.fake.items[("USER#zoe", "BUDGET")]
    assert (got["token_groups"] == {"L": [{"S": "team-b"}]}) is written
    assert (got.get("groups_iat") == {"N": str(iat)}) is written
    assert ic._SETTLED["zoe"] == (iat, ["team-b"])  # settled either way: written, or a newer token is recorded


def test_the_groups_condition_never_creates_a_missing_budget_item(ic):
    ic.record_groups("ghost", ["a"], 100)
    assert ("USER#ghost", "BUDGET") not in ic.fake.items and "ghost" not in ic._SETTLED  # tried again once it exists


def test_an_unreadable_spend_on_an_uncapped_window_blocks_nothing(ic, monkeypatch):
    # r141 /code-review: a bad HOUR# record refused every call although there is no hourly budget.
    real = ic.policy.resolve

    def caps(*a, **k):
        eff = real(*a, **k)
        eff["caps"]["Hourly"]["cap"], eff["caps"]["Daily"]["cap"] = None, 5.0
        return eff
    monkeypatch.setattr(ic.policy, "resolve", caps)
    g = time.gmtime()
    for sk in (time.strftime("HOUR#%Y-%m-%dT%H", g), time.strftime("DAY#%Y-%m-%d", g)):
        ic.fake.items[("USER#alice@example.com", sk)] = {"pk": {"S": "USER#alice@example.com"}, "sk": {"S": sk},
                                                        "spend_usd": {"S": "x"}}
    windows, _rpm, _eff = ic.spend("alice@example.com", ["ocgw-team-a"])
    assert ic.over_budget(windows)[:1] == ("Daily",) and ic.over_budget(windows)[3] is False  # the capped one only


def test_a_raise_after_the_gate_still_waits_for_the_groups_write(ic, monkeypatch):
    # r138 /code-review: only a raising gate waited; normalise() or json.dumps raising later did not.
    waited = []

    class Fut:
        def result(self):
            waited.append(True)
    monkeypatch.setattr(ic._POOL, "submit", lambda *a, **k: Fut())
    monkeypatch.setattr(ic, "normalise", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("odd config")))
    ic.fake.items[("USER#alice@example.com", "BUDGET")] = {"pk": {"S": "USER#alice@example.com"}, "sk": {"S": "BUDGET"}}
    call(ic, CHAT)
    assert waited


def test_a_settled_token_is_not_handed_to_a_thread(ic, monkeypatch):
    # r138 /code-review: every gated call paid a pool handoff even when record_groups would return at once.
    submitted = []
    real = ic._POOL.submit
    monkeypatch.setattr(ic._POOL, "submit", lambda fn, *a, **k: submitted.append(fn.__name__) or real(fn, *a, **k))
    ic.fake.items[("USER#alice@example.com", "BUDGET")] = {"pk": {"S": "USER#alice@example.com"}, "sk": {"S": "BUDGET"}}
    call(ic, CHAT)
    call(ic, CHAT)
    assert submitted.count("record_groups") == 1


def test_record_groups_never_raises_when_the_client_cannot_be_made(ic, monkeypatch):
    # r134 /code-review: the client was built outside the try.
    monkeypatch.setattr(ic, "display_ddb", lambda: (_ for _ in ()).throw(RuntimeError("no endpoint")))
    ic.record_groups("zoe", ["a"], 7)


@pytest.mark.parametrize("path", ["/inference/v1/models"])
def test_listing_models_records_the_callers_groups(ic, path):
    # r133 /code-review: a user whose group allows no model only ever lists models (opencode-config) and never
    # reached the dashboard.
    ic.fake.items[("USER#alice@example.com", "BUDGET")] = {"pk": {"S": "USER#alice@example.com"}, "sk": {"S": "BUDGET"}}
    call(ic, {}, path=path, headers={"Authorization": tok(groups=["data-science"])})
    assert ic.fake.items[("USER#alice@example.com", "BUDGET")]["token_groups"] == {"L": [{"S": "data-science"}]}


def test_an_allowed_call_records_from_the_budget_item_spend_read(ic, monkeypatch):
    # One read for both: spend()'s BatchGetItem hands the BUDGET item to record_groups.
    monkeypatch.setattr(ic.display_ddb(), "get_item", lambda **kw: (_ for _ in ()).throw(AssertionError("second read")))
    ic.fake.items[("USER#alice@example.com", "BUDGET")] = {"pk": {"S": "USER#alice@example.com"}, "sk": {"S": "BUDGET"}}
    assert status(call(ic, CHAT)) == "allow"
    assert "token_groups" in ic.fake.items[("USER#alice@example.com", "BUDGET")]


def test_record_groups_never_raises_when_the_read_fails(ic, monkeypatch):
    def boom(**_kw):
        raise RuntimeError("ddb down")
    monkeypatch.setattr(ic.display_ddb(), "get_item", boom)
    ic.record_groups("zoe", ["a"], 100)  # display data only: the refusal still goes out


@pytest.mark.parametrize("messages", [5, "hi", {"role": "user"}, True])
def test_messages_must_be_a_list(ic, messages):
    r = call(ic, {"model": "ow/kimi-k3", "messages": messages})
    assert status(r) == 400 and "messages" in resp(r)["error"]["message"]


def test_unmapped_idp_user_gets_its_own_refusal(ic, monkeypatch):
    monkeypatch.setattr(ic, "IDENTITY_MAP", {"00uOTHER": "bob"})
    r = call(ic, {"model": "ow/kimi-k3", "messages": [{"role": "user", "content": "hi"}]})
    assert status(r) == 403 and "identity_map" in resp(r)["error"]["message"]


def _serve(ic, monkeypatch, body, ctype, hold=0.0, seen=None):
    """Answer web search from a real local HTTP server with this body; hold: keep the stream open that long after it."""
    import time as _t

    def handler(h):
        if seen is not None:
            seen.update({k.lower(): v for k, v in h.headers.items()})
        data = body.encode()
        h.send_response(200)
        h.send_header("content-type", ctype)
        if not hold:
            h.send_header("content-length", str(len(data)))
        h.end_headers()
        h.wfile.write(data)
        h.wfile.flush()
        if hold:
            _t.sleep(hold)
    srv = _SearchServer(handler)
    monkeypatch.setattr(ic, "WEBSEARCH_MCP_URL", srv.url)
    return srv


class _Resp:
    def __init__(self, body, ctype):
        self.body, self.headers = body.encode(), {"content-type": ctype}

    def read(self, n=-1):  # consumes, like a real response
        i = len(self.body) if n < 0 else n
        out, self.body = self.body[:i], self.body[i:]
        return out

    def read1(self, n=-1):  # what has "arrived": hand it out in small pieces, to exercise lines split over chunks
        return self.read(4096 if n < 0 else min(n, 4096))

    def readline(self, n=-1):  # like io.BufferedReader.readline: at most n bytes
        i = self.body.find(b"\n") + 1 or len(self.body)
        if n >= 0:
            i = min(i, n)
        line, self.body = self.body[:i], self.body[i:]
        return line

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


HITS = json.dumps({"results": [{"title": "T", "url": "https://e.x", "text": "s", "publishedDate": "2026"}]})
OK = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": HITS}]}}


@pytest.mark.parametrize("body,ctype", [
    (json.dumps(OK), "application/json"),
    (json.dumps(OK), "application/json; charset=utf-8"),
    ("data: " + json.dumps(OK) + "\n\n", "text/event-stream"),
    ("event: message\ndata: " + json.dumps(OK) + "\n\n", "text/event-stream; charset=utf-8"),
    ('data: {"jsonrpc":"2.0","method":"notifications/progress","params":{}}\n\ndata: ' + json.dumps(OK) + "\n\n", "text/event-stream"),
    ("data: " + json.dumps(OK), "text/event-stream"),  # no trailing blank line
    ("id: 1\ndata:\n\ndata: " + json.dumps(OK) + "\n\n", "text/event-stream"),  # r51: MCP priming event first
    ("data\ndata: " + json.dumps(OK) + "\r\r", "text/event-stream"),  # r52: bare "data" field, bare-CR line ends
    ("data: " + json.dumps(OK) + "\r\n\r\n", "text/event-stream"),  # CRLF
    ("data: " + json.dumps({**OK, "note": "a\u2028b\u0085c"}, ensure_ascii=False) + "\n\n", "text/event-stream"),  # r53: raw U+2028/U+0085
])
def test_web_search_reads_json_and_event_stream_replies(ic, monkeypatch, body, ctype):
    seen = {}
    _serve(ic, monkeypatch, body, ctype, seen=seen)
    hits = ic.web_search("Bearer t", "q")
    assert hits == [{"title": "T", "url": "https://e.x", "snippet": "s", "published": "2026"}]
    assert "application/json" in seen["accept"] and "text/event-stream" in seen["accept"]


@pytest.mark.parametrize("body,ctype", [
    ('data: {"jsonrpc":"2.0","method":"notifications/progress"}\n\n', "text/event-stream"),
    ("", "text/event-stream"),
])
def test_web_search_stream_without_a_reply_raises(ic, monkeypatch, body, ctype):
    _serve(ic, monkeypatch, body, ctype)
    with pytest.raises(RuntimeError):
        ic.web_search("Bearer t", "q")


class _OpenStream(_Resp):
    """A server that sends the reply and keeps the stream open: a read past it would block (here: fail)."""
    def read1(self, n=-1):  # web_search reads with read1 (r53): a read once the reply is consumed would block
        if not self.body:
            raise AssertionError("read past the reply")
        return super().read1(n)


@pytest.mark.parametrize("end", ["\n\n", "\r\n\r\n", "\r\r"])
def test_web_search_returns_at_the_reply_without_waiting_for_the_stream_to_close(ic, monkeypatch, end):
    # r54: a reply whose dispatching blank line is a bare CR used to wait for more bytes.
    body = "data: " + json.dumps(OK) + end
    _serve(ic, monkeypatch, body, "text/event-stream", hold=3)  # the stream stays open: a read past the reply would wait
    assert ic.web_search("Bearer t", "q")[0]["url"] == "https://e.x"


@pytest.mark.parametrize("ctype", ["application/json", "text/event-stream"])
def test_web_search_reply_over_the_cap_raises_either_way(ic, monkeypatch, ctype):
    monkeypatch.setattr(ic, "SEARCH_REPLY_MAX", 10)
    body = json.dumps(OK) if ctype == "application/json" else "data: " + json.dumps(OK) + "\n\n"
    _serve(ic, monkeypatch, body, ctype)
    with pytest.raises(RuntimeError, match="too large"):
        ic.web_search("Bearer t", "q")


def test_web_search_reply_over_the_cap_raises(ic, monkeypatch):
    monkeypatch.setattr(ic, "SEARCH_REPLY_MAX", 10)
    _serve(ic, monkeypatch, json.dumps(OK), "application/json")
    with pytest.raises(RuntimeError, match="too large"):
        ic.web_search("Bearer t", "q")


def test_web_search_reads_a_data_line_longer_than_64k(ic, monkeypatch):
    big = {"results": [{"title": "é" * 40000, "url": f"https://e.x/{i}", "text": "ü" * 30000} for i in range(3)]}
    ok = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": json.dumps(big, ensure_ascii=False)}]}}
    body = "data: " + json.dumps(ok, ensure_ascii=False) + "\n\n"
    assert len(body.encode()) > 300000
    _serve(ic, monkeypatch, body, "text/event-stream")
    hits = ic.web_search("Bearer t", "q")
    assert [h["url"] for h in hits] == ["https://e.x/0", "https://e.x/1", "https://e.x/2"]


@pytest.mark.parametrize("args", [{}, {"query": ""}, {"query": "   "}, {"query": "x" * 2001}, {"query": 5},
                                  {"query": "x", "maxResults": 0}, {"query": "x", "maxResults": 21},
                                  {"query": "x", "maxResults": True}, {"query": "x", "maxResults": "3"}])
def test_mcp_bad_search_is_refused_before_any_charge(ic, args):
    ev = {"mcp": {"gatewayRequest": {"headers": {"Authorization": tok(groups=["ocgw-team-a"])},
                                     "body": {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                              "params": {"name": "web___WebSearch", "arguments": args}}}}}
    r = ic.lambda_handler(ev, None)
    assert r["mcp"]["transformedGatewayResponse"]["body"]["error"]["code"] == -32602
    month = time.strftime("MONTH#%Y-%m", time.gmtime())
    assert "spend_usd" not in ic.fake.items.get(("USER#alice@example.com", month), {})


@pytest.mark.parametrize("args", [{"query": "x"}, {"query": "x" * 2000, "maxResults": 20}, {"query": " q ", "maxResults": 1}])
def test_mcp_good_search_passes(ic, args):
    ev = {"mcp": {"gatewayRequest": {"headers": {"Authorization": tok(groups=["ocgw-team-a"])},
                                     "body": {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                              "params": {"name": "web___WebSearch", "arguments": args}}}}}
    assert "transformedGatewayRequest" in ic.lambda_handler(ev, None)["mcp"]


def test_mcp_search_by_a_user_without_a_project_is_refused_and_not_charged(ic, monkeypatch):
    monkeypatch.setattr(ic, "PROJECTS", {"bob@example.com": "proj_b"})  # alice is not in var.users
    ev = {"mcp": {"gatewayRequest": {"headers": {"Authorization": tok(groups=["ocgw-team-a"])},
                                     "body": {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                                              "params": {"name": "web___WebSearch", "arguments": {"query": "x"}}}}}}
    r = ic.lambda_handler(ev, None)
    err = r["mcp"]["transformedGatewayResponse"]["body"]["error"]
    assert err["code"] == -32004 and "no usage project" in err["message"]
    month = time.strftime("MONTH#%Y-%m", time.gmtime())
    assert "spend_usd" not in ic.fake.items.get(("USER#alice@example.com", month), {})


def test_a_refused_model_call_still_records_the_callers_groups(ic, monkeypatch):
    ic.fake.items[("USER#alice@example.com", "BUDGET")] = {"pk": {"S": "USER#alice@example.com"}, "sk": {"S": "BUDGET"}}
    r = call(ic, {"model": "ow/kimi-k3", "messages": [{"role": "user", "content": "hi"}]}, headers={"Authorization": tok(groups=["Engineering"])})
    assert status(r) == 403
    assert ic.fake.items[("USER#alice@example.com", "BUDGET")]["token_groups"] == {"L": [{"S": "Engineering"}]}


def test_a_fallback_refusal_also_records_the_callers_groups(ic, monkeypatch):
    # r130 /code-review: fallback_denied returned before the groups were recorded.
    ic.fake.items[("USER#alice@example.com", "BUDGET")] = {"pk": {"S": "USER#alice@example.com"}, "sk": {"S": "BUDGET"}}
    monkeypatch.setattr(ic, "barred_hops", lambda *_a: ["ow/deepseek-v3.2"])
    r = call(ic, CHAT)
    assert status(r) == 403 and "fall back" in resp(r)["error"]["message"]
    assert "token_groups" in ic.fake.items[("USER#alice@example.com", "BUDGET")]


@pytest.mark.parametrize("spent,cap,refused", [(0.009999999999999998, 0.01, True), (0.01, 0.01, True), (0.0099, 0.01, False),
                                               (0.1 + 0.2, 0.3, True), (0.0, 0.0, True), (4.99, 5, False)])
def test_spend_at_the_cap_is_refused_despite_float_sums(ic, spent, cap, refused):
    w = {"Hourly": (0, None), "Daily": (0, None), "Monthly": (spent, cap)}
    assert (ic.over_budget(w) is not None) is refused


def test_a_missing_directory_item_fails_closed(ic, monkeypatch):
    monkeypatch.setenv("DIRECTORY_FROM_TABLE", "1")
    ic.policy._directory.update(at=0.0, tried=0.0, val=None)
    r = call(ic, {"model": "ow/kimi-k3", "messages": []})
    assert status(r) == 500  # never forward a request the gateway could not check
    ic.policy._directory.update(at=0.0, tried=0.0, val=None)


def test_websearch_is_refused_locally_for_a_user_without_a_project(ic, monkeypatch):
    monkeypatch.setattr(ic, "WEBSEARCH_MCP_URL", "https://ws.example/mcp")
    monkeypatch.setattr(ic, "PROJECTS", {"bob@example.com": "proj_b"})
    monkeypatch.setattr(ic, "web_search", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no outbound call")))
    r = call(ic, {"q": "x"}, path="/inference/websearch")
    assert status(r) == 403


def test_http_method_field(ic):
    # Live AgentCore reports httpMethod POST even for GET /v1/models, so routing ignores it; it is only logged.
    assert ic.http_method({"httpMethod": "get"}) == "GET"
    assert ic.http_method({"method": "GET"}) == "GET"
    assert ic.http_method({}) is None
    assert ic.route_of({"path": "/inference/v1/models", "httpMethod": "POST"}) == ("GET", "/v1/models")
    assert ic.route_of({"path": "/inference/v1/models"}) == ("GET", "/v1/models")
    assert ic.route_of({"path": "/inference/v1/chat/completions", "httpMethod": "POST"}) == ("POST", "/v1/chat/completions")


def test_web_search_needs_a_model_the_gateway_serves(ic, monkeypatch):
    # r51: a group whose patterns match no served model (a retired id) can call nothing, so it may not search either.
    monkeypatch.setattr(ic, "GROUP_MODELS", {"ocgw-team-a": ["ow/retired-model"]})
    ev = {"mcp": {"gatewayRequest": {"headers": {"Authorization": tok(groups=["ocgw-team-a"])},
                                     "body": {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "web___WebSearch", "arguments": {"query": "x"}}}}}}
    r = ic.lambda_handler(ev, None)
    assert r["mcp"]["transformedGatewayResponse"]["body"]["error"]["code"] == -32004
    assert status(call(ic, {"q": "x"}, path="/websearch", headers={"Authorization": tok(groups=["ocgw-team-a"])})) == 403


def test_request_counts_never_hold_up_an_allowed_call(ic, monkeypatch):
    # r51: request counts go to the background pool; the call is forwarded without them. The groups write is in the
    # request since r131 (and only when they change).
    queued = []

    def later(self, fn, *a, **k):  # queue, never run: the call must not depend on these writes
        queued.append(getattr(fn, "__name__", None) or getattr(getattr(fn, "func", None), "__name__", "?"))
        return concurrent.futures.Future()
    monkeypatch.setattr(ic, "_BG", type("Q", (), {"submit": later})())
    before = dict(ic.fake.items)
    assert status(call(ic, CHAT)) == "allow"
    assert "record_groups" not in queued and len(queued) == 3  # the count's MONTH, DAY and HOUR writes
    shown = lambda items: {k: v for k, v in items.items() if k[1].split("#")[0] in ("MONTH", "DAY", "HOUR") and "requests" in v}  # noqa: E731
    assert shown(ic.fake.items) == shown(before)  # no display count inline (the RPM# gate counter is the gate itself)


@pytest.mark.parametrize("body", [[{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "web___WebSearch"}}], "x"])
def test_mcp_non_object_bodies_are_refused_not_rewritten(ic, body):
    # r52: a batch was forwarded as {}; it is now a clear JSON-RPC -32600 and never reaches the gateway.
    r = ic.lambda_handler({"mcp": {"gatewayRequest": {"headers": {"Authorization": tok()}, "body": body}}}, None)
    assert r["mcp"]["transformedGatewayResponse"]["body"]["error"]["code"] == -32600


def test_a_background_request_count_lands_in_the_window_it_was_allowed_in(ic):
    # r52: the hour comes from the time captured at submit, not from when the (thawed) thread runs.
    t = calendar.timegm((2026, 11, 12, 9, 59, 58, 0, 0, 0))
    ic.count_request("alice@example.com", "ow/kimi-k3", t)
    assert ("USER#alice@example.com", "HOUR#2026-11-12T09") in ic.fake.items


def test_a_body_less_mcp_request_passes(ic):
    # r53: GET (open the stream) and DELETE (end the session) carry no body and no tools/call: they pass through.
    r = ic.lambda_handler({"mcp": {"gatewayRequest": {"headers": {"Authorization": tok()}, "body": None}}}, None)
    # r98: fail closed: a body the interceptor cannot read is never forwarded as it came (it could hide a tools/call).
    assert r["mcp"]["transformedGatewayRequest"] == {"body": {}}


# r57: the web search path against a real local HTTP server (http.client closes a finished response itself on 3.12+,
# which the fake responses above never do).
class _SearchServer:
    def __init__(self, handler):
        import http.server
        import threading

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):  # noqa: N802
                self.rfile.read(int(self.headers.get("content-length") or 0))
                handler(self)

            def log_message(self, *a):
                pass
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/mcp"

    def close(self):
        self.srv.shutdown()


def _send(h, body, ctype, chunked=False, trickle=0.0):
    data = body.encode()
    h.send_response(200)
    h.send_header("content-type", ctype)
    h.send_header("transfer-encoding" if chunked else "content-length", "chunked" if chunked else str(len(data)))
    h.end_headers()
    if chunked:
        h.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n0\r\n\r\n")
    else:
        h.wfile.write(data)
    h.wfile.flush()


@pytest.mark.parametrize("ctype,body,chunked", [
    ("application/json", json.dumps(OK), False), ("application/json", json.dumps(OK), True),
    ("text/event-stream", "data: " + json.dumps(OK), False),  # stream ends without the trailing blank line
    ("text/event-stream", "data: " + json.dumps(OK) + "\n\n", True)])
def test_web_search_against_a_real_server(ic, monkeypatch, ctype, body, chunked):
    srv = _SearchServer(lambda h: _send(h, body, ctype, chunked))
    monkeypatch.setattr(ic, "WEBSEARCH_MCP_URL", srv.url)
    try:
        assert ic.web_search("Bearer t", "q")[0]["url"] == "https://e.x"
    finally:
        srv.close()


@pytest.mark.parametrize("slow", ["headers", "keepalive"])
def test_web_search_gives_up_at_its_deadline_whatever_the_server_does(ic, monkeypatch, slow):
    import time as _t

    def handler(h):
        if slow == "headers":
            _t.sleep(2)
            return
        h.send_response(200)
        h.send_header("content-type", "text/event-stream")
        h.end_headers()
        for _ in range(40):
            h.wfile.write(b": keepalive\n\n")
            h.wfile.flush()
            _t.sleep(0.05)
    srv = _SearchServer(handler)
    monkeypatch.setattr(ic, "WEBSEARCH_MCP_URL", srv.url)
    monkeypatch.setattr(ic, "SEARCH_DEADLINE_S", 0.5)
    t0 = _t.monotonic()
    try:
        with pytest.raises(RuntimeError, match="no reply within"):
            ic.web_search("Bearer t", "q")
        assert _t.monotonic() - t0 < 1.5
    finally:
        srv.close()



def test_an_abandoned_endless_search_ends_its_worker_and_later_searches_still_work(ic, monkeypatch):
    # r58: a stream of keepalives with no reply pinned a worker forever; a queued search ran after its caller gave up.
    import threading
    import time as _t
    stop = threading.Event()

    def endless(h):
        h.send_response(200)
        h.send_header("content-type", "text/event-stream")
        h.end_headers()
        while not stop.is_set():
            try:
                h.wfile.write(b": keepalive\n\n")
                h.wfile.flush()
            except OSError:
                return
            _t.sleep(0.05)
    slow = _SearchServer(endless)
    good = _SearchServer(lambda h: _send(h, json.dumps(OK), "application/json"))
    monkeypatch.setattr(ic, "SEARCH_DEADLINE_S", 0.4)
    try:
        monkeypatch.setattr(ic, "WEBSEARCH_MCP_URL", slow.url)
        for _ in range(4):  # fill the whole pool with abandoned endless searches
            with pytest.raises(RuntimeError, match="no reply within"):
                ic.web_search("Bearer t", "q")
        monkeypatch.setattr(ic, "WEBSEARCH_MCP_URL", good.url)
        assert ic.web_search("Bearer t", "q")[0]["url"] == "https://e.x"  # nothing left running blocks the next one
    finally:
        stop.set()
        slow.close()
        good.close()


def test_a_reply_that_starts_late_but_inside_the_deadline_is_kept(ic, monkeypatch):
    # r59: a 10 s per-step timeout under a 15 s deadline failed every search that took 11-15 s to answer.
    import time as _t

    def late(h):
        _t.sleep(0.3)
        _send(h, json.dumps(OK), "application/json")
    srv = _SearchServer(late)
    monkeypatch.setattr(ic, "WEBSEARCH_MCP_URL", srv.url)
    monkeypatch.setattr(ic, "SEARCH_DEADLINE_S", 0.6)
    try:
        assert ic.web_search("Bearer t", "q")[0]["url"] == "https://e.x"
    finally:
        srv.close()


def test_a_status_line_trickled_byte_by_byte_still_stops_at_the_deadline(ic, monkeypatch):
    # r60: each byte arrived inside the per-read timeout, so a 1 s deadline ran 44.8 s; the watchdog bounds the call.
    import time as _t

    def drip(h):
        for b in b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n":
            try:
                h.wfile.write(bytes([b]))
                h.wfile.flush()
            except OSError:
                return
            _t.sleep(0.05)
    srv = _SearchServer(drip)
    monkeypatch.setattr(ic, "WEBSEARCH_MCP_URL", srv.url)
    monkeypatch.setattr(ic, "SEARCH_DEADLINE_S", 0.5)
    t0 = _t.monotonic()
    try:
        with pytest.raises(RuntimeError, match="no reply within"):
            ic.web_search("Bearer t", "q")
        assert _t.monotonic() - t0 < 1.2
    finally:
        srv.close()


@pytest.mark.parametrize("status", [401, 429, 500, 503])
def test_a_gateway_http_error_is_a_failure_not_an_empty_search(ic, monkeypatch, status):
    # r60: a 429 with {"message": ...} came back as a successful search with no results.
    def fail(h):
        data = json.dumps({"message": "Too Many Requests"}).encode()
        h.send_response(status)
        h.send_header("content-type", "application/json")
        h.send_header("content-length", str(len(data)))
        h.end_headers()
        h.wfile.write(data)
    srv = _SearchServer(fail)
    monkeypatch.setattr(ic, "WEBSEARCH_MCP_URL", srv.url)
    try:
        with pytest.raises(RuntimeError, match=f"HTTP {status}"):
            ic.web_search("Bearer t", "q")
    finally:
        srv.close()


def test_a_json_rpc_refusal_with_an_error_status_is_still_a_refusal(ic, monkeypatch):
    code = next(iter(ic.SearchRefused.HTTP))

    def refuse(h):
        data = json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": code, "message": "no"}}).encode()
        h.send_response(403)
        h.send_header("content-type", "application/json")
        h.send_header("content-length", str(len(data)))
        h.end_headers()
        h.wfile.write(data)
    srv = _SearchServer(refuse)
    monkeypatch.setattr(ic, "WEBSEARCH_MCP_URL", srv.url)
    try:
        with pytest.raises(ic.SearchRefused):
            ic.web_search("Bearer t", "q")
    finally:
        srv.close()


def test_the_query_string_of_the_search_url_is_sent(ic, monkeypatch):
    seen = []

    def record(h):
        seen.append(h.path)
        _send(h, json.dumps(OK), "application/json")
    srv = _SearchServer(record)
    monkeypatch.setattr(ic, "WEBSEARCH_MCP_URL", srv.url + "?v=2")
    try:
        ic.web_search("Bearer t", "q")
        assert seen == ["/mcp?v=2"]
    finally:
        srv.close()


def test_the_connect_gives_each_address_only_the_time_left(ic, monkeypatch):
    # r60: create_connection gave every DNS address the whole 15 s.
    import socket as _s
    import time as _t
    tried = []

    class Slow:
        def __init__(self, *a):
            pass

        def settimeout(self, t):
            tried.append(t)

        def connect(self, addr):
            _t.sleep(0.3)
            raise TimeoutError

        def close(self):
            pass
    monkeypatch.setattr(ic.socket, "getaddrinfo", lambda *a, **k: [(_s.AF_INET, _s.SOCK_STREAM, 6, "", ("10.0.0.%d" % i, 443)) for i in range(5)])
    monkeypatch.setattr(ic.socket, "socket", Slow)
    monkeypatch.setattr(ic, "SEARCH_DEADLINE_S", 0.5)
    t0 = _t.monotonic()
    with pytest.raises(RuntimeError, match="no reply within"):
        ic.search_connect(ic.urllib.parse.urlsplit("https://gw.example/mcp"), _t.monotonic() + 0.5)
    assert _t.monotonic() - t0 < 0.8 and len(tried) == 2 and tried[1] < 0.3


def test_a_stalled_dns_lookup_is_bounded_by_the_deadline(ic, monkeypatch):
    # r61: getaddrinfo ran before the first deadline check, so a stalled resolver ran past the whole-search limit.
    import time as _t
    monkeypatch.setattr(ic.socket, "getaddrinfo", lambda *a, **k: _t.sleep(1.5) or [])
    t0 = _t.monotonic()
    with pytest.raises(RuntimeError, match="no reply within"):
        ic.search_connect(ic.urllib.parse.urlsplit("https://gw.example/mcp"), _t.monotonic() + 0.3)
    assert _t.monotonic() - t0 < 0.8


def test_an_error_status_on_a_stream_fails_at_once(ic, monkeypatch):
    # r61: a 503 text/event-stream that stayed open was read until the deadline before its status was looked at.
    import time as _t
    import threading
    stop = threading.Event()

    def hold(h):
        h.send_response(503)
        h.send_header("content-type", "text/event-stream")
        h.end_headers()
        h.wfile.flush()
        stop.wait(3)
    srv = _SearchServer(hold)
    monkeypatch.setattr(ic, "WEBSEARCH_MCP_URL", srv.url)
    t0 = _t.monotonic()
    try:
        with pytest.raises(RuntimeError, match="HTTP 503"):
            ic.web_search("Bearer t", "q")
        assert _t.monotonic() - t0 < 1
    finally:
        stop.set()
        srv.close()


def test_a_chunk_read_as_the_watchdog_fires_is_still_used(ic):
    # r61: the complete reply, read in the same instant the timer fired, was thrown away as a timeout.
    import threading
    fired = threading.Event()
    fired.set()

    class R:
        def __init__(self):
            self.parts = [b'{"a": 1}']

        def isclosed(self):
            return not self.parts

        def read1(self, n):
            return self.parts.pop(0) if self.parts else b""
    assert b"".join(ic.search_chunks(R(), fired)) == b'{"a": 1}'


def test_the_tls_context_is_built_once(ic):
    assert ic.tls_context() is ic.tls_context()


@pytest.mark.parametrize("body", ["[]", "{}", '{"jsonrpc": "2.0", "id": 1}', '"text"'])
def test_a_2xx_reply_that_is_not_json_rpc_is_a_failure(ic, monkeypatch, body):
    # r62: a list or {} came back as a successful search with no results.
    srv = _SearchServer(lambda h: _send(h, body, "application/json"))
    monkeypatch.setattr(ic, "WEBSEARCH_MCP_URL", srv.url)
    try:
        with pytest.raises(RuntimeError, match="not a JSON-RPC response"):
            ic.web_search("Bearer t", "q")
    finally:
        srv.close()


def test_stalled_dns_lookups_never_hold_up_a_later_one_and_too_many_fail_fast(ic, monkeypatch):
    # r63: on a 2-worker pool, two stalled lookups made every later search wait behind them.
    import threading
    import time as _t
    gate = threading.Event()

    def lookup(host, *a, **k):
        if host.startswith("stall"):
            gate.wait(3)
        raise OSError("no such host")  # a quick lookup: answered at once
    monkeypatch.setattr(ic.socket, "getaddrinfo", lookup)
    monkeypatch.setattr(ic, "_DNS_SLOTS", __import__("threading").BoundedSemaphore(3))
    try:
        for i in range(2):
            with pytest.raises(RuntimeError, match="no reply within"):
                ic.search_connect(ic.urllib.parse.urlsplit(f"https://stall{i}.example/mcp"), _t.monotonic() + 0.1)
        t0 = _t.monotonic()
        with pytest.raises(OSError, match="no such host"):  # answered straight away, not queued behind the two
            ic.search_connect(ic.urllib.parse.urlsplit("https://fine.example/mcp"), _t.monotonic() + 1)
        assert _t.monotonic() - t0 < 0.3
        with pytest.raises(RuntimeError, match="no reply within"):
            ic.search_connect(ic.urllib.parse.urlsplit("https://stall2.example/mcp"), _t.monotonic() + 0.1)
        with pytest.raises(RuntimeError, match="DNS is not answering"):
            ic.search_connect(ic.urllib.parse.urlsplit("https://any.example/mcp"), _t.monotonic() + 1)
    finally:
        gate.set()


def test_mcp_search_without_model_access_is_refused_before_any_ledger_read(ic, monkeypatch):
    # r62: spend() (a ledger BatchGetItem) ran before the model-access check on the MCP path.
    monkeypatch.setattr(ic, "spend", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ledger read")))
    monkeypatch.setattr(ic, "allow_list", lambda groups: [])
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "web___WebSearch", "arguments": {"query": "q", "maxResults": 3}}}
    r = ic.lambda_handler({"mcp": {"gatewayRequest": {"headers": {"Authorization": tok()}, "body": body}}}, None)
    assert r["mcp"]["transformedGatewayResponse"]["body"]["error"]["code"] == -32004



def test_a_lookup_error_that_is_not_an_oserror_reaches_the_caller(ic, monkeypatch):
    # r64: a UnicodeError from getaddrinfo killed the thread and surfaced as KeyError('addrs').
    import time as _t

    def bad(*a, **k):
        raise UnicodeError("label too long")
    monkeypatch.setattr(ic.socket, "getaddrinfo", bad)
    with pytest.raises(UnicodeError, match="label too long"):
        ic.search_connect(ic.urllib.parse.urlsplit("https://x.example/mcp"), _t.monotonic() + 1)


def test_only_tools_call_needs_the_user_directory():
    # r105: a directory read failure broke MCP initialize and tools/list, which never look a user up.
    import interceptor as ic
    ev = lambda body: {"mcp": {"gatewayRequest": {"body": body}}}  # noqa: E731
    assert ic.needs_directory({}, False)
    assert ic.needs_directory(ev({"method": "tools/call"}), True)
    assert ic.needs_directory(ev([{"method": "tools/call"}]), True)
    assert not ic.needs_directory(ev(None), True)  # r106: a stream GET or DELETE carries no body and needs no lookup
    assert not ic.needs_directory(ev({"method": "tools/list"}), True)
    assert not ic.needs_directory(ev({"method": "initialize"}), True)


def test_a_plain_text_search_reply_is_no_results_not_a_502(ic):
    # r115: the search ran and was charged; a text it cannot parse is an empty result list.
    import json as _json
    reply = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "No results"}, "x"]}}
    assert ic.search_results(reply["result"]) == []
    reply["result"]["content"] = [{"type": "text", "text": _json.dumps({"results": [{"url": "u"}, 3]})}]
    assert [h["url"] for h in ic.search_results(reply["result"])] == ["u"]


def test_a_hit_whose_text_is_not_a_string_still_counts(ic):
    import json as _json
    res = {"content": [{"type": "text", "text": _json.dumps({"results": [{"url": "u", "text": 42}, {"url": "v", "text": {"a": 1}}]})}]}
    assert [h["snippet"] for h in ic.search_results(res)] == ["42", '{"a": 1}']


def test_a_hit_whose_title_or_url_is_not_a_string_becomes_one(ic):
    import json as _json
    res = {"content": [{"type": "text", "text": _json.dumps({"results": [{"url": 7, "title": 42, "text": "x"}, {"text": "y"}]})}]}
    out = ic.search_results(res)
    assert [(h["title"], h["url"]) for h in out] == [("42", "7"), ("", None)]


def test_a_hit_whose_published_date_is_not_a_string_becomes_one(ic):
    import json as _json
    res = {"content": [{"type": "text", "text": _json.dumps({"results": [{"url": "u", "publishedDate": {"y": 2026}}, {"url": "v"}]})}]}
    assert [h["published"] for h in ic.search_results(res)] == ['{"y": 2026}', None]


@pytest.mark.parametrize("body,n", [({"q": "x", "count": None}, 8), ({"q": "x", "count": None, "maxResults": 3}, 3),
                                    ({"q": "x", "maxResults": None}, 8)])
def test_websearch_null_count_means_not_given(ic, monkeypatch, body, n):
    seen = []
    monkeypatch.setattr(ic, "web_search", lambda auth, q, k: seen.append(k) or [])
    assert status(call(ic, body, path="/websearch")) == 200 and seen == [n]


def test_a_result_that_is_not_an_object_is_no_hits(ic):
    import json as _json
    assert ic.search_results({}) == []
    deep = {"content": [{"type": "text", "text": "[" * 100000}]}
    assert ic.search_results(deep) == []
    assert _json.dumps(ic.search_results({"content": "x"})) == "[]"


@pytest.mark.parametrize("result", [[1, 2], "text", 7])
def test_web_search_result_that_is_not_an_object_is_no_hits(ic, monkeypatch, result):
    import json as _json
    _serve(ic, monkeypatch, _json.dumps({"jsonrpc": "2.0", "id": 1, "result": result}), "application/json")
    assert ic.web_search("Bearer t", "q") == []


def test_mcp_null_max_results_means_not_given(ic):
    args = {"query": "x", "maxResults": None}
    ev = {"mcp": {"gatewayRequest": {"headers": {"Authorization": tok(groups=["ocgw-team-a"])},
                                     "body": {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                                              "params": {"name": "web___WebSearch", "arguments": args}}}}}
    out = ic.lambda_handler(ev, None)["mcp"]
    assert "transformedGatewayRequest" in out
    assert "maxResults" not in out["transformedGatewayRequest"]["body"]["params"]["arguments"]


def test_mcp_every_null_argument_is_dropped(ic):
    args = {"query": "x", "maxResults": None, "startPublishedDate": None}
    ev = {"mcp": {"gatewayRequest": {"headers": {"Authorization": tok(groups=["ocgw-team-a"])},
                                     "body": {"jsonrpc": "2.0", "id": 10, "method": "tools/call",
                                              "params": {"name": "web___WebSearch", "arguments": args}}}}}
    out = ic.lambda_handler(ev, None)["mcp"]["transformedGatewayRequest"]["body"]["params"]["arguments"]
    assert out == {"query": "x"}


def test_websearch_echoes_the_query_it_searched(ic, monkeypatch):
    seen = []
    monkeypatch.setattr(ic, "web_search", lambda auth, q, n: seen.append(q) or [])
    b = call(ic, {"q": "  aws news \n"}, path="/websearch")["http"]["transformedGatewayResponse"]["body"]
    b = b if isinstance(b, dict) else json.loads(base64.b64decode(b))
    assert seen == ["aws news"] and b["query"] == "aws news"


@pytest.mark.parametrize("readable,spent,cap", [(False, 0.0, 5.0), (False, 0.0, None), (True, 5.0, 5.0), (True, 4.0, 5.0),
                                                (True, 9.0, None), (True, 0.0, 0.0), (False, 0.0, 0.0)])
def test_the_gate_refuses_exactly_where_policy_refused_says(ic, monkeypatch, readable, spent, cap):
    # r145 /code-review: the gate applies policy.refused's rule in two steps (spend's unreadable list, then
    # over_budget); this holds the two to the one rule the dashboard shows as blocked.
    hour = time.strftime("HOUR#%Y-%m-%dT%H", time.gmtime())
    ic.fake.items[("USER#alice@example.com", hour)] = {"pk": {"S": "USER#alice@example.com"}, "sk": {"S": hour},
                                                      "spend_usd": {"N": repr(spent)} if readable else {"S": "x"}}
    real = ic.policy.resolve

    def caps(*a, **k):
        eff = real(*a, **k)
        for w in ("Monthly", "Daily", "Hourly"):
            eff["caps"][w]["cap"] = cap if w == "Hourly" else None
        return eff
    monkeypatch.setattr(ic.policy, "resolve", caps)
    windows, rpm, eff = ic.spend("alice@example.com")
    refused = ic.gate("alice@example.com", [], windows, 1000, eff) is not None
    assert refused is ic.policy.refused(readable, spent, cap)


@pytest.mark.parametrize("iat", [float("inf"), float("nan"), -5, "123", True, None])
def test_an_iat_that_is_not_a_finite_positive_number_is_zero(ic, iat):
    # r147 /code-review: json.loads reads 1e999 as inf, and int(inf) raised OverflowError on every request.
    assert ic.token_iat({"iat": iat}) == 0


def test_sse_lines_keeps_a_crlf_split_by_an_empty_chunk_as_one_line_end(ic):
    # r186 /code-review: an empty chunk between the CR and the LF of a CRLF made the LF a blank line of its own.
    assert list(ic.sse_lines([b"data: a\r", b"", b"\ndata: b\r\n"])) == ["data: a", "data: b"]
    assert list(ic.sse_lines([b"data: a\r", b"\n", b"\n"])) == ["data: a", ""]


def test_an_iat_far_in_the_future_does_not_freeze_the_recorded_groups(ic):
    """r194: a direct invoke could record groups with iat 9e9; groups writes are ordered by iat, so no real token
    could replace them. An iat more than five minutes ahead now counts as none."""
    import time as _t
    now = int(_t.time())
    assert ic.token_iat({"iat": now + 60}) == now + 60   # small clock skew still orders writes
    assert ic.token_iat({"iat": now + 3600}) == 0
    assert ic.token_iat({"iat": 9e9}) == 0


def test_a_service_tier_is_removed_until_usage_sync_prices_tiers(ic):
    """r194: usage_sync prices every token at one on-demand rate, so a dearer tier would be under-counted."""
    body = {"model": "x", "messages": [{"role": "user", "content": "hi"}], "service_tier": "priority"}
    assert "service_tier_removed" in ic.normalise(body, "x")
    assert "service_tier" not in body
    assert "service_tier_removed" not in ic.normalise({"model": "x", "messages": []}, "x")


def _logged(capsys):
    return [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.startswith("{")]


@pytest.mark.parametrize("reply", [
    {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "not json: secret search terms"}]}},
    {"jsonrpc": "2.0", "id": 1, "error": {"code": -32603, "message": "failed for secret search terms"}},
    {"jsonrpc": "2.0", "id": 1, "result": {"isError": True, "content": [{"type": "text", "text": "secret search terms"}]}},
])
def test_no_web_search_reply_text_reaches_the_log(ic, monkeypatch, capsys, reply):
    """r195: three log paths wrote excerpts of Web Search replies, which can hold the search itself."""
    _serve(ic, monkeypatch, json.dumps(reply), "application/json")
    try:
        ic.web_search("Bearer t", "secret search terms")
    except RuntimeError as e:
        assert "secret" not in str(e)
    assert "secret" not in capsys.readouterr().out


def test_caller_chosen_log_fields_are_capped(ic, capsys):
    """r195: model, stream, path and the MCP tool name were logged as sent, so one request could write megabytes."""
    call(ic, {**CHAT, "model": "m" * 10000, "stream": {"x": "y" * 10000}})
    rows = _logged(capsys)
    row = next(r for r in rows if "model" in r)
    assert len(row["model"]) <= 200 and row["stream"] == "not_a_boolean"
    call(ic, CHAT, path="/inference/v1/chat/completions" + "x" * 10000)
    assert all(len(str(r.get("path", ""))) <= 300 for r in _logged(capsys))
    ev = {"mcp": {"gatewayRequest": {"headers": {"Authorization": tok(groups=["ocgw-team-a"])},
                                     "body": {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "t" * 10000}}}}}
    ic.lambda_handler(ev, None)
    assert all(len(str(r.get("tool", ""))) <= 200 for r in _logged(capsys))
