"""The agent on AgentCore Runtime: inbound JWT auth, and ONE identity end to
end -- the brand+agent JWT the Runtime verified is the same one the agent
forwards to the Gateway, so the tenant the tools see is the JWT's, whatever
the request payload claims.
"""
from __future__ import annotations

import base64
import hashlib
import json
import urllib.parse
import uuid

import httpx
import pytest

from config.tenants import REGION


def _invoke_url(state: dict) -> str:
    arn = urllib.parse.quote(state["runtime"]["agent_runtime_arn"], safe="")
    return f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/{arn}/invocations?qualifier=DEFAULT"


def _invoke(state: dict, token: str | None, payload: dict, timeout: float = 60) -> httpx.Response:
    headers = {
        "Content-Type": "application/json",
        "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": f"pytest-runtime-{uuid.uuid4().hex}",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.post(_invoke_url(state), headers=headers, content=json.dumps(payload), timeout=timeout)


def test_runtime_uses_the_jwt_tenant_and_forwards_the_same_jwt(state, tokens):
    """Northwind's JWT with a payload that claims to be Borealis: the agent
    serves Northwind, and every tool call reaches the Gateway with the very
    same client_id the Runtime authorizer verified."""
    northwind_client = state["cognito"]["clients"]["northwind:support"]["client_id"]
    resp = _invoke(
        state,
        tokens.for_tenant_agent("northwind", "support"),
        {
            "message": "What is your return window?",
            "shopper_id": "pytest" + uuid.uuid4().hex[:8],
            "tenant_id": "borealis",
            "agent_id": "merch",
        },
    )
    assert resp.status_code == 200, resp.text[:500]
    out = resp.json()
    assert out["runtime_identity"]["jwt_client_id"] == northwind_client
    assert out["runtime_identity"]["tenant_id"] == "northwind"
    assert out["tool_calls"], out
    for tc in out["tool_calls"]:
        assert tc["trace"]["jwt_client_id"] == northwind_client, tc["trace"]
        assert tc["trace"]["server_tenant_id"] == "northwind", tc["trace"]
    assert "60 days" not in out["reply"] and "Trail Promise" not in out["reply"], out["reply"]


def test_runtime_rejects_missing_token(state):
    resp = _invoke(state, None, {"message": "hi"})
    assert resp.status_code in (401, 403), resp.text[:300]


def test_runtime_rejects_unsigned_forged_jwt(state):
    northwind_client = state["cognito"]["clients"]["northwind:support"]["client_id"]

    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

    forged = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64({'client_id': northwind_client, 'iss': state['cognito']['issuer'], 'exp': 4102444800})}."
    resp = _invoke(state, forged, {"message": "hi"})
    assert resp.status_code in (401, 403), resp.text[:300]


def test_runtime_rejects_client_outside_allowed_clients(state, tokens):
    """A real, validly signed Cognito token from the no-scope client that is
    not one of the four brand+agent clients."""
    resp = _invoke(state, tokens.for_client_key("attacker:none"), {"message": "hi"})
    assert resp.status_code in (401, 403), resp.text[:300]


def test_webui_chat_is_served_by_runtime(state):
    body = json.dumps(
        {"tenant_id": "cascade", "agent_id": "support", "shopper_id": "pytest" + uuid.uuid4().hex[:8], "message": "Hi!"}
    ).encode()
    resp = httpx.post(
        state["webui"]["cloudfront_url"] + "/api/chat",
        content=body,
        headers={"Content-Type": "application/json", "x-amz-content-sha256": hashlib.sha256(body).hexdigest()},
        timeout=60,
    )
    assert resp.status_code == 200, resp.text[:300]
    out = resp.json()
    assert out["served_by"] == "agentcore-runtime", out.get("runtime_error")
    assert out["runtime_identity"]["tenant_id"] == "cascade"


@pytest.mark.parametrize("bad", [{"message": ""}, {"message": "x" * 1001}, {"message": "hi", "model_id": "not-a-model"}])
def test_runtime_validates_payload(state, tokens, bad):
    """The agent rejects bad payloads with a 400. AgentCore Runtime does not
    pass a container's 4xx through as-is: it answers 424 Failed Dependency
    naming the container's status (observed behaviour)."""
    resp = _invoke(state, tokens.for_tenant_agent("cascade", "support"), bad)
    assert resp.status_code == 424, resp.text[:300]
    assert "(400)" in resp.json()["message"], resp.text[:300]
