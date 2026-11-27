# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""The other two ways in: the MCP server (PKCE sign-in, stdio) and the chat assistant."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from mcp_live import mcp_sign_in, pinned_domain, result_text, server_params
from session import Deployment, chat, outputs, unsigned_jwt


async def _mcp_session(home: str, out: dict[str, str], domain: str) -> dict[str, tuple[bool, str]]:
    params = server_params(home, out["AppUrl"], out["McpClientId"], domain)
    results: dict[str, tuple[bool, str]] = {}
    async with stdio_client(params) as (read, write), ClientSession(read, write) as s:
        await s.initialize()
        names = {t.name for t in (await s.list_tools()).tools}
        # 28 tools (mcp/test/server.test.ts) less the 3 radar writes, which are off unless
        # AIR_MCP_RADAR_WRITES is set: 25, none of them a write.
        assert len(names) == 25, sorted(names)
        assert not names & {"import_radar_entries", "tag_radar_version", "restore_radar_version"}
        calls: list[tuple[str, dict[str, Any]]] = [
            ("list_navigation", {}),
            ("search", {"query": "standing desk", "limit": 3}),
            ("list_radars", {}),
            ("list_modules", {}),
            ("run_module", {"slug": "signal", "confirm": True}),  # confirmed, so the API's 403 is what is seen
        ]
        for name, args in calls:
            res = await s.call_tool(name, args)
            results[name] = (bool(res.is_error), result_text(res))
        # A real id from the search, and one that does not exist (CTR QA-111): only lists were read before.
        found = _fenced_json(results["search"][1])
        hits = found.get("items", []) if isinstance(found, dict) else []
        targets = [("missing_node", "get_node", {"node_id": "organization/does-not-exist"})]
        if hits:
            real = hits[0]["node"]["id"]
            targets += [("get_node", "get_node", {"node_id": real}), ("neighbors", "neighbors", {"node_id": real})]
        for key, name, args in targets:
            res = await s.call_tool(name, args)
            results[key] = (bool(res.is_error), result_text(res))
    return results


def _fenced_json(text: str) -> Any:
    head, _, rest = text.partition("\n")
    assert head.startswith("<untrusted-tool-output"), text[:200]
    return json.loads(rest.rsplit("\n", 1)[0])


@pytest.mark.browser
def test_mcp_server_signs_in_and_enforces_the_role(deployment: Deployment) -> None:
    out = outputs()
    with mcp_sign_in(deployment, out["AppUrl"], out["McpClientId"]) as (home, tokens):  # revoked and deleted on exit
        assert tokens.stat().st_mode & 0o777 == 0o600
        results = asyncio.run(_mcp_session(home, out, pinned_domain(deployment)))
    # Every read succeeded and returned the API's JSON: a deployment where each read fails must not pass,
    # and an error now comes back as an MCP error result (P3-025, A3-014).
    for name in ("list_navigation", "search", "list_radars", "list_modules"):
        failed, text = results[name]
        body = _fenced_json(text)
        assert not failed and not (isinstance(body, dict) and "error" in body), (name, text[:300])
    for name in ("get_node", "neighbors"):
        if name in results:  # the search found something to open
            failed, text = results[name]
            assert not failed and "error" not in _fenced_json(text), (name, text[:300])
    failed, text = results["missing_node"]
    assert failed and '"error": "404: ' in text and "not found" in text.lower(), text[:300]
    failed, text = results["run_module"]
    # The API's own refusal (editors cannot run modules), not a WAF page or a network error.
    assert failed and '"error": "403: ' in text and "<html" not in text.lower(), text[:300]


@pytest.mark.ai
def test_chat_answers_and_is_charged(
    deployment: Deployment, tokens: dict[str, str], api: dict[str, Any], ai_on: None
) -> None:
    before = api["viewer"].call("GET", "/api/chat/status")[1]["user"]["remaining"]
    frames = chat(deployment, tokens["viewer"], "In one sentence: what does this deployment track?", f"e2e-{uuid.uuid4()}")
    text = "".join(f.get("data", "") for f in frames)
    assert text.strip() and not any("error" in f for f in frames), frames
    after = api["viewer"].call("GET", "/api/chat/status")[1]["user"]["remaining"]
    assert after < before


@pytest.mark.ai
def test_chat_refuses_a_forged_token(deployment: Deployment) -> None:
    """The runtime's JWT authorizer rejects an unsigned token before any of our code runs."""
    frames = chat(deployment, unsigned_jwt({"sub": "x"}), "hi", f"e2e-{uuid.uuid4()}")
    assert frames and frames[0].get("http_error") in (401, 403), frames
