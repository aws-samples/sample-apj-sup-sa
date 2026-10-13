# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""AgentCore Runtime entrypoint for the Slack gateway Strands agent.

Auth model (3LO passthrough): this Runtime is deployed with a **Cognito JWT
authorizer** wired to the same Cognito user pool the gateway trusts. The caller
passes their Cognito access token as the bearer when invoking the Runtime;
AgentCore validates it (signature, issuer, expiry, allowed client) before this
code runs. We then **forward that same token** to the AgentCore Gateway as
inbound auth, so the downstream Slack tool calls act as the signed-in user.
No credentials live in this code.

Env vars (set by the CDK stack):
  GATEWAY_URL            the gateway MCP URL (streamable HTTP)
  BEDROCK_MODEL_ID       the Bedrock model the agent uses
  MCP_PROTOCOL_VERSION   MCP protocol version (default 2025-11-25)
  AWS_REGION             region (provided by the runtime)
"""

from __future__ import annotations

from bedrock_agentcore import BedrockAgentCoreApp
from bedrock_agentcore.runtime.context import RequestContext
from mcp.client.streamable_http import streamablehttp_client
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient

from config import Settings

app = BedrockAgentCoreApp()


def build_gateway_client(settings: Settings, access_token: str) -> MCPClient:
    """Create an MCP client bound to the AgentCore Gateway with the token.

    Opens an MCP (streamable HTTP) connection to the gateway URL, forwarding the
    caller's Cognito access token as ``Authorization: Bearer <token>`` so the
    gateway's CUSTOM_JWT authorizer validates it and the downstream Slack target
    acts as the signed-in user (3LO).
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Mcp-Protocol-Version": settings.mcp_protocol_version,
    }

    def transport():
        return streamablehttp_client(settings.gateway_url, headers=headers)

    return MCPClient(transport)

SYSTEM_PROMPT = (
    "You are an assistant with access to Slack tools exposed through an Amazon "
    "Bedrock AgentCore Gateway. Use the tools to fulfill the user's request and "
    "summarize the results clearly. Actions are performed as the signed-in user."
)

# In-process conversation history keyed by the AgentCore session id. AgentCore
# routes a given session id back to the same warm MicroVM, so seeding each turn
# with the prior messages (and saving them afterward) makes the conversation
# multi-turn: the agent remembers earlier turns within the same session.
#
# This is intentionally ephemeral — it lives only for the life of the MicroVM.
# If the instance is recycled the history is lost and the conversation restarts.
# For durability across restarts/instances, back this with AgentCore Memory.
_SESSIONS: dict[str, list] = {}

# Runtime-only config loaded from environment variables. No credentials: the
# inbound token is forwarded, so this Runtime never mints tokens from a password.
SETTINGS = Settings.load()

MODEL = BedrockModel(model_id=SETTINGS.model_id, region_name=SETTINGS.region)


def _inbound_token(context: RequestContext) -> str:
    """Extract the caller's validated Cognito access token from the request."""
    try:
        auth = context.request.headers.get("authorization")
    except Exception:  # noqa: BLE001 - request may be unavailable locally
        auth = None

    if not auth or not auth.lower().startswith("bearer "):
        raise ValueError(
            "Missing bearer token. Invoke the Runtime with the caller's "
            "Cognito access token (the JWT authorizer requires it)."
        )
    return auth.split(" ", 1)[1]


@app.entrypoint
async def invoke(payload: dict, context: RequestContext):
    """Stream the agent loop as Server-Sent Events.

    Yields small typed dicts as the loop runs; BedrockAgentCoreApp serializes
    each one into an SSE ``data:`` frame:
      {"type": "tool_use",  "name": ...}   a gateway tool is being called
      {"type": "reasoning", "text": ...}   model reasoning delta (if emitted)
      {"type": "text",      "text": ...}   answer text delta
      {"type": "error",     "message": ...}
    """
    prompt = payload.get("prompt", "")
    if not prompt:
        yield {"type": "error", "message": "Missing 'prompt' in payload."}
        return

    try:
        # Forward the validated inbound token to the gateway.
        access_token = _inbound_token(context)
    except ValueError as exc:
        yield {"type": "error", "message": str(exc)}
        return

    # Restore this conversation's prior messages so the turn is context-aware.
    # The session id is the same value the caller sends on every turn, so all
    # turns of one conversation share the same history.
    session_id = context.session_id or "default"
    history = _SESSIONS.get(session_id, [])

    gateway_client = build_gateway_client(SETTINGS, access_token)
    try:
        with gateway_client:
            tools = gateway_client.list_tools_sync()
            agent = Agent(
                model=MODEL,
                tools=tools,
                system_prompt=SYSTEM_PROMPT,
                messages=history,
            )

            announced_tools: set[str] = set()
            async for event in agent.stream_async(prompt):
                # Answer text deltas.
                if "data" in event:
                    yield {"type": "text", "text": event["data"]}
                # Model reasoning deltas (only if the model emits them).
                elif "reasoningText" in event:
                    yield {"type": "reasoning", "text": event["reasoningText"]}
                # Tool calls — announce each tool-use id once as its name appears.
                elif event.get("current_tool_use"):
                    tool_use = event["current_tool_use"]
                    tool_id = tool_use.get("toolUseId", "")
                    name = tool_use.get("name")
                    if name and tool_id not in announced_tools:
                        announced_tools.add(tool_id)
                        yield {"type": "tool_use", "name": name}

            # Persist the updated transcript (new user prompt, assistant reply,
            # and any tool messages) so the next turn on this session continues
            # the conversation.
            _SESSIONS[session_id] = agent.messages
    except Exception as exc:  # noqa: BLE001 - stream the failure to the client
        yield {"type": "error", "message": str(exc)}


if __name__ == "__main__":
    app.run()
