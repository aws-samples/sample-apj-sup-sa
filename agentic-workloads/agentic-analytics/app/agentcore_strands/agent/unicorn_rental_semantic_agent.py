#!/usr/bin/env python3
"""
AgenticAnalytics Semantic Layer Agent — AgentCore Integration with Gateway

This is the semantic layer agent entrypoint. It connects to a dedicated Gateway
that only has the SemanticLayer target registered (cube_meta_tool and cube_query_tool).
The differentiation from the prebaked SQL agent happens at deployment time via the
GATEWAY_URL environment variable pointing to the semantic layer Gateway.

Everything else (Bedrock model, SOP, memory hooks, guardrails, JWT extraction)
is identical to unicorn_rental_agent.py.
"""

import os
import json
import time
import asyncio
import boto3
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
# AgentCore excludes .env from deployment packages, so we use config.env
# which IS bundled. Locally, .env takes precedence if it exists.
_script_dir = Path(__file__).resolve().parent
_project_dir = _script_dir.parent
for _candidate in [
    _script_dir / "config.env",          # deployed runtime: /var/task/agent/config.env
    _project_dir / "config.env",          # local dev: agent/../config.env
    _project_dir / ".env",                # local dev: agent/../.env
    Path("/var/task") / "config.env",     # explicit deployed path
]:
    if _candidate.exists():
        load_dotenv(_candidate)
        print(f"[OK] Loaded config from {_candidate}")
        break
        break

# Set bypass tool consent for AgentCore
os.environ["BYPASS_TOOL_CONSENT"] = "true"

# Strands imports
from strands import Agent, tool
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient
from strands.hooks import HookProvider, HookRegistry, AgentInitializedEvent, MessageAddedEvent
from mcp.client.streamable_http import streamablehttp_client
from datetime import datetime, timezone

# AgentCore imports
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.memory import MemoryClient


# Load SOP from S3 or local fallback
def load_system_prompt():
    """Load SOP from S3 if configured, otherwise use local file"""
    s3_bucket = os.getenv("SOP_S3_BUCKET")
    s3_key = os.getenv("SOP_S3_KEY", "sops/unicorn_rental_analytics.sop.md")

    print(f"DEBUG: SOP_S3_BUCKET={s3_bucket}, SOP_S3_KEY={s3_key}")

    if s3_bucket:
        try:
            s3 = boto3.client('s3')
            response = s3.get_object(Bucket=s3_bucket, Key=s3_key)
            print(f"[OK] Loaded SOP from s3://{s3_bucket}/{s3_key}")
            return response['Body'].read().decode('utf-8')
        except Exception as e:
            print(f"⚠️ Failed to load SOP from S3: {e}, using local fallback")

    # Look for SOP in multiple locations (handles both local dev and deployed runtime)
    for candidate_dir in [Path(__file__).parent, Path(__file__).parent.parent, Path("/var/task")]:
        local_path = candidate_dir / "unicorn_rental_analytics.sop.md"
        if local_path.exists():
            with open(local_path, 'r') as f:
                print(f"[OK] Loaded SOP from {local_path}")
                return f.read()

    print("⚠️ SOP file not found in any location, using inline fallback")
    return "You are a unicorn rental analytics assistant. Help users query data using the available tools."

SYSTEM_PROMPT = load_system_prompt()

# Gateway configuration
GATEWAY_URL = os.getenv("GATEWAY_URL", "")

# Bedrock model configuration
model_id = os.getenv("BEDROCK_MODEL_ID", "us.anthropic.claude-opus-4-5-20251101-v1:0")
region = os.getenv("AWS_REGION", "us-east-1")

# Guardrail configuration (native Bedrock integration)
GUARDRAIL_ID = os.getenv("GUARDRAIL_ID", "")
GUARDRAIL_VERSION = os.getenv("GUARDRAIL_VERSION", "DRAFT")

bedrock_model_kwargs = dict(
    model_id=model_id,
    temperature=0.3,
    streaming=True,
)
if GUARDRAIL_ID:
    bedrock_model_kwargs.update(
        guardrail_id=GUARDRAIL_ID,
        guardrail_version=GUARDRAIL_VERSION,
        guardrail_redact_input=True,
        guardrail_redact_input_message="I can only help with unicorn rental analytics. Please ask about bookings, revenue, customers, or unicorn management.",
        guardrail_latest_message=True,
    )
    print(f"[OK] Guardrails enabled: {GUARDRAIL_ID}")

bedrock_model = BedrockModel(**bedrock_model_kwargs)

# ============================================================================
# Memory Hook — loads conversation history on init, saves each turn
# ============================================================================
MEMORY_ID = os.getenv("MEMORY_ID")

class MemoryHookProvider(HookProvider):
    """Loads recent conversation history and saves new turns to AgentCore Memory (STM)."""

    def __init__(self, memory_client, memory_id):
        self.memory_client = memory_client
        self.memory_id = memory_id

    def register_hooks(self, registry: HookRegistry) -> None:
        registry.add_callback(AgentInitializedEvent, self.on_agent_initialized)
        registry.add_callback(MessageAddedEvent, self.on_message_added)

    def on_agent_initialized(self, event: AgentInitializedEvent):
        """Load recent conversation history when agent starts."""
        try:
            state = event.agent.state or {}
            actor_id = state.get("actor_id") or "default"
            session_id = state.get("session_id") or "default"
            events = self.memory_client.list_events(
                memory_id=self.memory_id,
                actor_id=actor_id,
                session_id=session_id,
                max_results=5
            )
            for ev in events:
                for payload_item in ev.get('payload', []):
                    conv = payload_item.get('conversational', {})
                    role = conv.get('role', '').lower()
                    text = conv.get('content', {}).get('text', '')
                    if not text or role not in ('user', 'assistant'):
                        continue
                    # Replay as PLAIN TEXT only — never reconstruct toolUse/toolResult
                    # blocks. A truncated list_events window or reversed ordering can
                    # orphan a tool block, which makes Bedrock Converse reject the entire
                    # message list and the agent goes silent after a few turns. Plain
                    # text is always Converse-valid. Flatten older JSON-encoded events.
                    if text.lstrip().startswith("["):
                        try:
                            blocks = json.loads(text)
                            if isinstance(blocks, list):
                                text = " ".join(
                                    b.get("text", "") for b in blocks
                                    if isinstance(b, dict) and b.get("text")
                                ).strip()
                        except (json.JSONDecodeError, TypeError):
                            pass
                    if text:
                        event.agent.messages.append({"role": role, "content": [{"text": text}]})
        except Exception as e:
            print(f"[MEMORY] Failed to load history: {e}")

    def on_message_added(self, event: MessageAddedEvent):
        """Save each new message to memory as plain text.

        We deliberately persist ONLY the text parts, not toolUse/toolResult blocks.
        Persisting tool blocks (and replaying them) can orphan a tool pair when the
        load window truncates, which makes Bedrock Converse reject the whole message
        list — the agent then goes silent mid-conversation. The tradeoff is that the
        agent may re-run a tool on a follow-up turn; for analytics that re-query is
        usually correct anyway, and it is strictly better than a mute bot.
        """
        try:
            msg = event.message
            role = msg.get("role", "")
            text_parts = [c.get("text", "") for c in msg.get("content", []) if c.get("text")]
            text = " ".join(text_parts).strip()
            if not text or role not in ("user", "assistant"):
                return
            actor_id = "default"
            session_id = "default"
            if hasattr(event, 'agent') and hasattr(event.agent, 'state'):
                state = event.agent.state or {}
                actor_id = state.get("actor_id") or "default"
                session_id = state.get("session_id") or "default"
            self.memory_client.create_event(
                memory_id=self.memory_id,
                actor_id=actor_id,
                session_id=session_id,
                messages=[(text, role.upper())]
            )
        except Exception as e:
            print(f"[MEMORY] Failed to save message: {e}")

memory_hooks = []
if MEMORY_ID:
    try:
        memory_client = MemoryClient(region_name=region)
        memory_hooks = [MemoryHookProvider(memory_client, MEMORY_ID)]
        print(f"[OK] Memory enabled: {MEMORY_ID}")
    except Exception as e:
        print(f"⚠️  Memory init failed: {e}")

# Current datetime tool for relative date handling
@tool
def current_datetime() -> str:
    """Get the current date and time in UTC. Use this when users request bookings with relative dates like 'tomorrow', 'next week', etc."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")

# Initialize AgentCore app
app = BedrockAgentCoreApp()

print("AgenticAnalytics Semantic Layer Agent Configuration:")
print(f"Gateway URL: {GATEWAY_URL}")
print(f"Using model: {model_id}")
print(f"AWS Region: {region}")
print("[OK] AgenticAnalytics Semantic Layer Agent ready for requests")

@app.entrypoint
async def agent_invocation(payload, context):
    """Handler for agent invocation with streaming support"""
    user_message = payload.get("prompt", "No prompt found in input, please provide a prompt")
    # JWT-native inbound auth: the runtime's CustomJWTAuthorizer has already validated
    # the caller's Cognito access token (signature/issuer/client_id) before we run, and
    # passes it through via the request-header allowlist. We read it from
    # context.request_headers['Authorization'] — this is THE user identity, used for
    # AgentCore RBAC/RLS (forwarded to the MCP Gateway). The UI sends a Bearer header,
    # NOT a payload gateway_token — read the header to match the analytics agent.
    def _bearer_from_headers(ctx):
        headers = getattr(ctx, "request_headers", None) or {} if ctx else {}
        # Header name casing can vary; match case-insensitively.
        auth = headers.get("Authorization") or headers.get("authorization")
        if auth and auth.startswith("Bearer "):
            return auth[len("Bearer "):].strip()
        return auth.strip() if auth else None

    gateway_token = _bearer_from_headers(context)

    print("AgentCore Context:\n-------\n", context)
    print(f"Inbound JWT present: {'Yes' if gateway_token else 'No'}")
    print("Processing Query:\n*******\n", user_message)

    enhanced_prompt = user_message

    try:
        if not gateway_token:
            # Should be unreachable: the runtime's JWT authorizer rejects unauthenticated
            # calls before we run. This guards against a misconfigured request-header
            # allowlist (token validated but not passed through).
            raise ValueError("No Authorization header on the request — check the runtime's "
                             "RequestHeaderConfiguration allowlist includes 'Authorization'")
        access_token = gateway_token
        print(f"[OK] Using validated inbound JWT for gateway auth")

        # Extract actor_id from JWT for memory isolation
        import base64 as _b64
        try:
            jwt_payload = access_token.split('.')[1]
            jwt_payload += '=' * (4 - len(jwt_payload) % 4)
            claims = json.loads(_b64.b64decode(jwt_payload))
            actor_id = claims.get("sub", "default")
        except Exception:
            actor_id = "default"

        # Use runtime session_id for memory session isolation
        runtime_session_id = context.session_id if context and hasattr(context, 'session_id') else "default"
        print(f"[OK] Memory context: actor={actor_id[:12]}..., session={runtime_session_id[:30]}")

        def create_transport():
            return streamablehttp_client(
                GATEWAY_URL,
                headers={"Authorization": f"Bearer {access_token}"}
            )

        mcp_client = MCPClient(create_transport)

        request_agent = Agent(
            model=bedrock_model,
            system_prompt=SYSTEM_PROMPT,
            tools=[mcp_client, current_datetime],
            hooks=memory_hooks,
            callback_handler=None,
            state={"actor_id": actor_id, "session_id": runtime_session_id},
        )

        async for event in request_agent.stream_async(enhanced_prompt):
            yield event

    except Exception as e:
        print(f"❌ Request failed: {str(e)}")
        yield {"type": "text", "content": f"I'm currently unable to connect to the scheduling system: {str(e)}. Please try again later."}

app.run()
