"""Minimal async helper for talking to the AgentCore Gateway's MCP endpoint
with a bearer JWT. Used by both the pytest suite and the manual smoke
scripts.
"""
from __future__ import annotations

import base64
import json
from contextlib import asynccontextmanager

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def mint_token(token_endpoint: str, client_id: str, client_secret: str, scope: str | None) -> str:
    import httpx

    data = {"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret}
    if scope:
        data["scope"] = scope
    resp = httpx.post(
        token_endpoint,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def decode_claims(token: str) -> dict:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


@asynccontextmanager
async def mcp_session(gateway_url: str, bearer_token: str | None):
    headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}
    http_client = httpx2.AsyncClient(headers=headers, timeout=30)
    try:
        async with streamable_http_client(gateway_url, http_client=http_client) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
    finally:
        await http_client.aclose()


async def call_tool(session: ClientSession, name: str, arguments: dict) -> dict:
    result = await session.call_tool(name, arguments)
    # MCP tool results carry a list of content blocks; our Lambda returns one
    # JSON-encoded text block.
    for block in result.content:
        if getattr(block, "type", None) == "text":
            try:
                return json.loads(block.text)
            except json.JSONDecodeError:
                return {"_raw_text": block.text}
    return {"_raw": str(result)}


async def poll_until_text_present(
    call_fn, marker: str, timeout_s: float = 150.0, interval_s: float = 5.0
) -> tuple[bool, dict]:
    """Repeatedly invoke `call_fn()` (an async callable returning a
    list_memories-style {"records": [...]} dict) until a record whose
    content text contains `marker` shows up, or `timeout_s` elapses.

    AgentCore Memory's ListMemoryRecords/RetrieveMemoryRecords are backed by
    an index with observed eventual-consistency lag after a
    BatchCreateMemoryRecords write -- GetMemoryRecord-by-id is immediately
    consistent, but list/search visibility can take anywhere from <1s to
    ~60s (see docs/EVIDENCE.md). This helper makes assertions
    robust to that lag instead of flaking.
    """
    import asyncio
    import time

    deadline = time.monotonic() + timeout_s
    last_resp: dict = {}
    while time.monotonic() < deadline:
        last_resp = await call_fn()
        texts = [r["content"]["text"] for r in last_resp.get("records", [])]
        if any(marker in t for t in texts):
            return True, last_resp
        await asyncio.sleep(interval_s)
    return False, last_resp
