"""Connect the local agent to REMOTE tools via Amazon Bedrock AgentCore Gateway.

AgentCore Gateway exposes the six wildlife-investigation Lambdas as a single
Model Context Protocol (MCP) server. This module builds an MCP client pointed at
your gateway so the *same* agent you run on your laptop calls the *same* tools it
will use once deployed to AgentCore Runtime.

This is the "run locally, connect to remote tools" step of the workshop.

Environment variables (see .env.example):
    AGENTCORE_GATEWAY_URL   The gateway MCP endpoint, ending in /mcp
    AGENTCORE_GATEWAY_TOKEN A valid inbound OAuth bearer token (JWT)

Or, to fetch a token automatically from a Cognito machine-to-machine client:
    COGNITO_TOKEN_URL, COGNITO_CLIENT_ID, COGNITO_CLIENT_SECRET, COGNITO_SCOPE
"""

import os
import time

import requests
from mcp.client.streamable_http import streamablehttp_client
from strands.tools.mcp import MCPClient

# Cached client-credentials token: (token, expires_at_epoch_seconds).
_TOKEN_CACHE: tuple[str, float] | None = None
# Refresh this many seconds before the token actually expires, so a call that
# starts just under the wire does not land on the far side of it.
_EXPIRY_MARGIN = 120


def fetch_gateway_token() -> str:
    """Return an inbound bearer token for the gateway.

    Prefers an explicit AGENTCORE_GATEWAY_TOKEN. Otherwise performs an OAuth 2.0
    client-credentials exchange against Cognito using the M2M app client that the
    gateway trusts as its inbound authorizer.

    The exchanged token is cached until shortly before it expires and then fetched
    again. Cognito M2M tokens last about an hour, so a long session or the nightly
    batch run outlives a single token, and holding one forever means every call
    after that hour comes back 401.
    """
    global _TOKEN_CACHE

    token = os.environ.get("AGENTCORE_GATEWAY_TOKEN")
    if token:
        return token

    if _TOKEN_CACHE:
        cached, expires_at = _TOKEN_CACHE
        if time.time() < expires_at:
            return cached

    token_url = os.environ["COGNITO_TOKEN_URL"]
    resp = requests.post(
        token_url,
        data={
            "grant_type": "client_credentials",
            "client_id": os.environ["COGNITO_CLIENT_ID"],
            "client_secret": os.environ["COGNITO_CLIENT_SECRET"],
            "scope": os.environ.get("COGNITO_SCOPE", ""),
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    resp.raise_for_status()
    payload = resp.json()
    access_token = payload["access_token"]
    lifetime = float(payload.get("expires_in", 3600))
    _TOKEN_CACHE = (access_token, time.time() + max(lifetime - _EXPIRY_MARGIN, 0))
    return access_token


def build_gateway_mcp_client(gateway_url: str = None, token: str = None) -> MCPClient:
    """Build a Strands MCPClient for the AgentCore Gateway.

    Use it as a context manager, then list/attach its tools to your Agent:

        gateway = build_gateway_mcp_client()
        with gateway:
            tools = gateway.list_tools_sync()
            agent = Agent(model=model, system_prompt=SYSTEM_PROMPT, tools=tools)
            agent("Investigate ...")
    """
    gateway_url = gateway_url or os.environ["AGENTCORE_GATEWAY_URL"]

    def _transport():
        # Resolve the token per transport rather than closing over one fetched at
        # build time: MCPClient calls this when it (re)connects, so a reconnect
        # after the token's hour is up gets a fresh one instead of replaying a
        # dead bearer.
        bearer = token or fetch_gateway_token()
        return streamablehttp_client(
            gateway_url,
            headers={"Authorization": f"Bearer {bearer}"},
        )

    return MCPClient(_transport)
