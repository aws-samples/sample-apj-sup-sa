"""The web UI is public (no sign-in): prove it opens without credentials, that
the server-side request caps hold, and that the abuse controls from
deploy/13_abuse_controls.py are actually wired up on the live stack.

The cap tests go through the real CloudFront URL, so they exercise the same
path a browser does (WAF -> CloudFront -> OAC -> Lambda Function URL).
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import boto3
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from config.tenants import REGION  # noqa: E402


def _api(state: dict, path: str, body: dict | None = None) -> tuple[int, dict]:
    url = state["webui"]["cloudfront_url"] + path
    headers = {"Content-Type": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        # CloudFront's Lambda OAC needs the payload hash on any request with a body.
        headers["x-amz-content-sha256"] = hashlib.sha256(data).hexdigest()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "{}")


def _fresh_shopper() -> str:
    return "pytest" + uuid.uuid4().hex[:10]


def test_config_and_memory_need_no_sign_in(state):
    status, cfg = _api(state, "/api/config")
    assert status == 200
    assert {t["tenant_id"] for t in cfg["tenants"]} == {"northwind", "borealis", "cascade"}
    status, mem = _api(state, f"/api/memory?tenant_id=northwind&agent_id=support&shopper_id={_fresh_shopper()}")
    assert status == 200, mem
    assert mem["shared"] == [] and mem["private"] == []


def test_overlong_message_rejected_server_side(state):
    status, out = _api(
        state,
        "/api/chat",
        {"tenant_id": "borealis", "agent_id": "merch", "shopper_id": _fresh_shopper(), "message": "x" * 1001},
    )
    assert status == 400
    assert "message too long" in out["error"]


def test_overlong_history_rejected_server_side(state):
    history = [{"role": "user", "text": "hi"}, {"role": "assistant", "text": "hello"}] * 3
    status, out = _api(
        state,
        "/api/chat",
        {
            "tenant_id": "borealis",
            "agent_id": "merch",
            "shopper_id": _fresh_shopper(),
            "message": "hello",
            "history": history,
        },
    )
    assert status == 400
    assert "history too long" in out["error"]


def test_unknown_tenant_is_a_400_not_a_500(state):
    status, out = _api(state, f"/api/memory?tenant_id=nobody&agent_id=support&shopper_id={_fresh_shopper()}")
    assert status == 400
    assert "unknown tenant/agent" in out["error"]


def test_waf_attached_and_reserved_concurrency_set(state):
    webui = state["webui"]
    cf = boto3.client("cloudfront", region_name=REGION)
    dist_cfg = cf.get_distribution_config(Id=webui["distribution_id"])["DistributionConfig"]
    assert dist_cfg["WebACLId"] == webui["waf_web_acl_arn"]

    waf = boto3.client("wafv2", region_name="us-east-1")  # CLOUDFRONT scope lives in us-east-1
    acl_id = webui["waf_web_acl_arn"].rsplit("/", 1)[1]
    acl = waf.get_web_acl(Name=webui["waf_web_acl_name"], Scope="CLOUDFRONT", Id=acl_id)["WebACL"]
    rules = {r["Name"]: r for r in acl["Rules"]}
    rate = rules["rate-limit-api-per-ip"]["Statement"]["RateBasedStatement"]
    assert rate["Limit"] == 100 and rate["AggregateKeyType"] == "IP"
    assert "aws-managed-common-rules" in rules
    tags = waf.list_tags_for_resource(ResourceARN=webui["waf_web_acl_arn"])["TagInfoForResource"]["TagList"]
    assert {"Key": "project", "Value": "agentcore-mt-memory-demo"} in tags

    lam = boto3.client("lambda", region_name=REGION)
    conc = lam.get_function_concurrency(FunctionName="agentcore-mt-webui-backend")
    assert conc["ReservedConcurrentExecutions"] == 5


def _load_backend_module():
    """Imports the webui backend handler locally (no AWS calls at import
    time beyond creating clients) to unit-test its pure helpers."""
    env = {
        "GATEWAY_URL": "https://example.invalid/mcp",
        "TENANT_AGENTS_JSON": "[]",
        "MODEL_PRICING_JSON": "{}",
        "AVAILABLE_MODELS_JSON": json.dumps([{"model_id": "m1", "label": "M1"}]),
        "AWS_REGION": REGION,
    }
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    # handler.py imports chat_core, which ships next to it in the Lambda zip.
    chat_core_dir = str(ROOT / "lambda_src" / "chat_core")
    if chat_core_dir not in sys.path:
        sys.path.insert(0, chat_core_dir)
    try:
        spec = importlib.util.spec_from_file_location(
            "webui_backend_handler_under_test", ROOT / "lambda_src" / "webui_backend" / "handler.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_history_is_normalized_for_converse():
    mod = _load_backend_module()
    msgs = mod._history_to_messages(
        [
            {"role": "assistant", "text": "leading assistant turn is dropped"},
            {"role": "user", "text": "a"},
            {"role": "user", "text": "b"},
            {"role": "assistant", "text": "c"},
            {"role": "user", "text": "trailing user turn is dropped"},
        ]
    )
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"][0]["text"] == "a\nb"
    assert mod._validate_chat_body({"message": "hi", "model_id": "not-a-model"}).startswith("unknown model_id")
    assert mod._validate_chat_body({"message": "hi", "history": [{"role": "system", "text": "x"}]})
    assert mod._validate_chat_body({"message": "hi", "model_id": "m1"}) is None


def test_cost_includes_prompt_cache_tokens():
    _load_backend_module()
    import chat_core

    chat_core.MODEL_PRICING["m1"] = {"input": 0.001, "output": 0.005}
    assert chat_core.cost_usd("m1", 1000, 0) == 0.001
    # 4,000 cached tokens read at 0.1x + 1,000 written at 1.25x on top of 1,000 uncached
    assert chat_core.cost_usd("m1", 1000, 0, cache_read_tokens=4000, cache_write_tokens=1000) == round(
        0.001 + 0.0004 + 0.00125, 6
    )


def test_runtime_session_id_is_stable_and_long_enough():
    mod = _load_backend_module()
    sid = mod._runtime_session_id("borealis", "merch", "shopper1")
    assert sid == mod._runtime_session_id("borealis", "merch", "shopper1")
    assert sid != mod._runtime_session_id("northwind", "support", "shopper1")
    assert len(sid) >= 33  # AgentCore Runtime minimum session id length


def test_chat_falls_back_to_lambda_when_runtime_fails(monkeypatch):
    """If the Runtime call fails, the turn still runs (same chat_core loop,
    in the Lambda) and the response says so."""
    mod = _load_backend_module()
    monkeypatch.setattr(mod, "RUNTIME_ARN", "arn:aws:bedrock-agentcore:us-east-1:000000000000:runtime/x")
    monkeypatch.setattr(mod, "_mint_token", lambda t, a: "jwt")
    monkeypatch.setattr(mod, "_tenant_agent_info", lambda t, a: {"tenant_display": "T", "agent_display": "A"})

    def boom(*_a, **_k):
        raise TimeoutError("runtime timed out")

    monkeypatch.setattr(mod, "_invoke_runtime", boom)
    seen = {}

    def fake_turn(token, tenant_display, agent_display, *rest, **kw):
        seen["token"] = token
        return {"reply": "ok"}

    monkeypatch.setattr(mod.chat_core, "run_chat_turn", fake_turn)
    out = mod._run_chat_turn("borealis", "merch", "shopper1", "hi", "m1")
    assert out["served_by"] == "lambda-fallback"
    assert "runtime timed out" in out["runtime_error"]
    assert seen["token"] == "jwt"  # the fallback uses the same minted JWT


def test_runtime_success_is_reported(monkeypatch):
    mod = _load_backend_module()
    monkeypatch.setattr(mod, "RUNTIME_ARN", "arn:aws:bedrock-agentcore:us-east-1:000000000000:runtime/x")
    monkeypatch.setattr(mod, "_mint_token", lambda t, a: "jwt")
    monkeypatch.setattr(mod, "_tenant_agent_info", lambda t, a: {"tenant_display": "T", "agent_display": "A"})
    sent = {}

    def fake_invoke(token, session_id, payload):
        sent.update(token=token, payload=payload)
        return {"reply": "from runtime"}

    monkeypatch.setattr(mod, "_invoke_runtime", fake_invoke)
    out = mod._run_chat_turn("borealis", "merch", "shopper1", "hi", "m1")
    assert out["served_by"] == "agentcore-runtime" and out["reply"] == "from runtime"
    assert sent["token"] == "jwt"
    assert "tenant_id" not in sent["payload"] and "agent_id" not in sent["payload"]
