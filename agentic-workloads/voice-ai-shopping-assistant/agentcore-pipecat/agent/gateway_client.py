"""Minimal MCP-over-HTTP client for the Aisle AgentCore Gateway (SigV4 / IAM auth).

The gateway speaks MCP streamable-HTTP and authorizes with AWS_IAM, so every
request is SigV4-signed for the `bedrock-agentcore` service using the runtime's
IAM role credentials (default chain). We do the MCP handshake (initialize +
initialized) once, cache the session id and the base->exposed tool-name map,
then route tool calls through `call_tool`.
"""

from __future__ import annotations

import asyncio
import json
import os

import aiohttp
import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from loguru import logger

_SERVICE = "bedrock-agentcore"
_PROTOCOL_VERSION = "2025-06-18"


class GatewayMCPClient:
    def __init__(self, url: str | None = None, region: str | None = None):
        self.url = url or os.getenv("GATEWAY_MCP_URL", "")
        self.region = region or os.getenv("AWS_REGION", "ap-southeast-2")
        self._session_id: str | None = None
        self._tool_map: dict[str, str] = {}  # base name -> gateway-exposed name
        self._creds = boto3.Session().get_credentials()
        self._lock = asyncio.Lock()

    # --- signing / transport -------------------------------------------------
    def _headers(self, body: str) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        req = AWSRequest(method="POST", url=self.url, data=body, headers=headers)
        SigV4Auth(self._creds.get_frozen_credentials(), _SERVICE, self.region).add_auth(req)
        return dict(req.headers)

    @staticmethod
    def _parse(text: str):
        text = (text or "").strip()
        if not text:
            return None
        if text[0] == "{":
            return json.loads(text)
        # SSE framing: pull the JSON out of the last `data:` line
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload and payload[0] == "{":
                    try:
                        return json.loads(payload)
                    except json.JSONDecodeError:
                        pass
        return None

    async def _post(self, session: aiohttp.ClientSession, payload: dict, capture_session=False):
        body = json.dumps(payload)
        async with session.post(self.url, data=body, headers=self._headers(body)) as resp:
            if capture_session:
                sid = resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id")
                if sid:
                    self._session_id = sid
            return self._parse(await resp.text())

    async def _ensure_ready(self, session: aiohttp.ClientSession):
        if self._session_id and self._tool_map:
            return
        await self._post(session, {
            "jsonrpc": "2.0", "id": "init", "method": "initialize",
            "params": {"protocolVersion": _PROTOCOL_VERSION, "capabilities": {},
                       "clientInfo": {"name": "aisle-agent", "version": "1.0"}},
        }, capture_session=True)
        await self._post(session, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        res = await self._post(session, {"jsonrpc": "2.0", "id": "list", "method": "tools/list"})
        for t in ((res or {}).get("result", {}) or {}).get("tools", []):
            exposed = t.get("name", "")
            base = exposed.split("___")[-1]
            self._tool_map[base] = exposed
        logger.info(f"Gateway tools discovered: {list(self._tool_map.keys())}")

    # --- public API ----------------------------------------------------------
    async def call_tool(self, base_name: str, arguments: dict, _retries: int = 4) -> dict:
        """Call a gateway tool by base name; returns the parsed `data` dict.

        Retries automatically while Aurora Serverless is resuming from 0-ACU
        auto-pause. Raises RuntimeError on other MCP/tool errors.
        """
        last_err: Exception | None = None
        for attempt in range(_retries):
            try:
                return await self._call_once(base_name, arguments)
            except RuntimeError as e:
                msg = str(e).lower()
                if "resuming" in msg or "databaseresuming" in msg:
                    last_err = e
                    logger.info(f"Aurora resuming; retry {attempt + 1}/{_retries} for {base_name}")
                    await asyncio.sleep(3)
                    continue
                raise
        raise RuntimeError(f"{base_name} failed after retries: {last_err}")

    async def _call_once(self, base_name: str, arguments: dict) -> dict:
        async with self._lock:
            async with aiohttp.ClientSession() as session:
                await self._ensure_ready(session)
                exposed = self._tool_map.get(base_name, base_name)
                res = await self._post(session, {
                    "jsonrpc": "2.0", "id": base_name, "method": "tools/call",
                    "params": {"name": exposed, "arguments": arguments},
                })

        if res is None:
            raise RuntimeError(f"No response from gateway for {base_name}")
        if "error" in res:
            raise RuntimeError(f"Gateway error for {base_name}: {res['error']}")
        result = res.get("result", {})
        # MCP tool results come back as content blocks; the Lambda returns JSON text.
        for block in result.get("content", []):
            if block.get("type") == "text":
                try:
                    parsed = json.loads(block["text"])
                except json.JSONDecodeError:
                    parsed = {"text": block["text"]}
                # Tools return {"data": {...}} or {"error": {...}}
                if isinstance(parsed, dict) and "error" in parsed:
                    raise RuntimeError(f"Tool {base_name} error: {parsed['error']}")
                if isinstance(parsed, dict) and "data" in parsed:
                    return parsed["data"]
                return parsed
        # Some gateways return structuredContent directly
        if "structuredContent" in result:
            sc = result["structuredContent"]
            return sc.get("data", sc)
        return result
