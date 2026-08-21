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

import requests
from mcp.client.streamable_http import streamablehttp_client
from strands.tools.mcp import MCPClient


def fetch_gateway_token() -> str:
    """Return an inbound bearer token for the gateway.

    Prefers an explicit AGENTCORE_GATEWAY_TOKEN. Otherwise performs an OAuth 2.0
    client-credentials exchange against Cognito using the M2M app client that the
    gateway trusts as its inbound authorizer.
    """
    token = os.environ.get("AGENTCORE_GATEWAY_TOKEN")
    if token:
        return token

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
    return resp.json()["access_token"]


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
    token = token or fetch_gateway_token()

    def _transport():
        return streamablehttp_client(
            gateway_url,
            headers={"Authorization": f"Bearer {token}"},
        )

    return MCPClient(_transport)
