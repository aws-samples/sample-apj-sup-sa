"""Reusable helper: build a strands Agent wired to the AgentCore Gateway's
MCP tools, authenticated as one (tenant, agent) identity.
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx2  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402
from strands import Agent  # noqa: E402
from strands.models import BedrockModel  # noqa: E402
from strands.tools.mcp import MCPClient  # noqa: E402

from config.tenants import MODEL_HAIKU, REGION  # noqa: E402


def _make_mcp_client(gateway_url: str, token: str) -> MCPClient:
    def transport_callable():
        headers = {"Authorization": f"Bearer {token}"}
        http_client = httpx2.AsyncClient(headers=headers, timeout=60)
        return streamable_http_client(gateway_url, http_client=http_client)

    return MCPClient(transport_callable)


@contextmanager
def tenant_agent(gateway_url: str, token: str, system_prompt: str, model_id: str = MODEL_HAIKU):
    """Context manager yielding a ready-to-use strands Agent, with the MCP
    connection (and its tools) attached for its lifetime."""
    mcp_client = _make_mcp_client(gateway_url, token)
    with mcp_client:
        tools = mcp_client.list_tools_sync()
        model = BedrockModel(model_id=model_id, region_name=REGION)
        agent = Agent(model=model, tools=tools, system_prompt=system_prompt)
        yield agent
