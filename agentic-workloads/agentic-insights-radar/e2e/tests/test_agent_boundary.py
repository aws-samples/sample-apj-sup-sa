# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""The /agent boundary: only the module runtime role may call the tool gate, only the chat runtime role
the chat-usage route, and the HTTP API's own address serves neither (and still demands a token).

These tests sign with the test runner's own AWS credentials, which are never a runtime role."""

from __future__ import annotations

import json
from typing import Any

import boto3
import httpx
import pytest
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

from session import Deployment


def _signed(url: str, region: str, body: dict[str, Any], headers: dict[str, str] | None = None) -> httpx.Response:
    data = json.dumps(body).encode()
    request = AWSRequest(method="POST", url=url, data=data, headers={"content-type": "application/json", **(headers or {})})
    SigV4Auth(boto3.Session().get_credentials(), "lambda", region).add_auth(request)
    return httpx.post(url, content=data, headers=dict(request.headers.items()), timeout=30)


@pytest.fixture(scope="module")
def urls(deployment: Deployment) -> dict[str, str]:
    return {"tools": deployment.function_url("BackendAgentTools"), "usage": deployment.function_url("BackendChatUsage")}


def test_an_unsigned_call_to_the_tool_gate_is_refused(urls: dict[str, str]) -> None:
    res = httpx.post(f"{urls['tools']}/agent/tools/run_context", json={}, timeout=30)
    assert res.status_code == 403


def _code(res: httpx.Response) -> str | None:
    """The backend's error code, or None when Lambda refused the caller before the function ran. A
    bare 403 cannot tell those apart, so a regression in run-token verification would pass (P2-084)."""
    try:
        body = res.json()
    except ValueError:
        return None
    return str(body.get("code")) if isinstance(body, dict) and "code" in body else None


def test_a_signed_caller_that_is_not_the_module_runtime_is_refused(deployment: Deployment, urls: dict[str, str]) -> None:
    # Lambda refuses a principal without InvokeFunctionUrl (403); one that has it (an administrator) reaches
    # the backend, which refuses every role but the module runtime's (403).
    res = _signed(f"{urls['tools']}/agent/tools/run_context", deployment.region, {}, {"x-mr-run-token": "forged.token"})
    assert res.status_code == 403, res.text
    # Either Lambda refused the signature (no body) or the backend refused the role. What must never
    # happen is the backend accepting the caller and then reporting a token problem: that would mean
    # the role check passed for the test runner. The run-token path itself is covered by a backend
    # integration test, because this caller never gets past the role check.
    assert _code(res) in (None, "forbidden"), res.text


def test_the_chat_usage_route_refuses_other_callers(deployment: Deployment, urls: dict[str, str]) -> None:
    body = {"subject": "u", "turn_id": "e2e-turn", "reserve_tokens": 1}
    unsigned = httpx.post(f"{urls['usage']}/agent/chat/usage", json=body, timeout=30)
    assert unsigned.status_code == 403
    signed = _signed(f"{urls['usage']}/agent/chat/usage", deployment.region, body)
    assert signed.status_code == 403, signed.text
    # No budget was reserved for a caller that is not the chat runtime: the refusal is the role
    # check, not a validation error on the body.
    assert _code(signed) in (None, "forbidden"), signed.text


def test_the_direct_api_address_serves_no_agent_route_and_requires_a_token(deployment: Deployment) -> None:
    api_id = deployment.stack_resource("AWS::ApiGatewayV2::Api", "BackendApi")
    base = f"https://{api_id}.execute-api.{deployment.region}.amazonaws.com"
    assert httpx.get(f"{base}/api/radars", timeout=30).status_code == 401
    assert httpx.post(f"{base}/agent/tools/run_context", json={}, timeout=30).status_code == 404
