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
import re
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
model_id = os.getenv("BEDROCK_MODEL_ID", "global.anthropic.claude-sonnet-5-5")
region = os.getenv("AWS_REGION", "us-east-1")

# Guardrail configuration (native Bedrock integration)
GUARDRAIL_ID = os.getenv("GUARDRAIL_ID", "")
GUARDRAIL_VERSION = os.getenv("GUARDRAIL_VERSION", "DRAFT")

bedrock_model_kwargs = dict(
    model_id=model_id,
    streaming=True,
)
# Claude Opus 4.8 and every Claude 5.x model reject `temperature`; send it only to
# older models known to accept it.
TEMPERATURE_MODELS = ("claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5", "claude-opus-4-5", "claude-sonnet-4-5")
if any(m in model_id for m in TEMPERATURE_MODELS):
    bedrock_model_kwargs["temperature"] = 0.3
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


# ============================================================================
# Long-term memory: preferences and past episodes for THIS user (actorId = JWT sub)
# ============================================================================
LTM_NAMESPACES = (
    ("preferences", "/analytics/{actor}/preferences", 3),
    ("past work", "/analytics/{actor}/episodes", 2),
)


def _long_term_context(actor_id, query):
    """Return a short prompt block with what AgentCore Memory knows about this user.

    Records are extracted in the background from earlier conversations
    (episodic + user-preference strategies on the memory resource). Retrieval
    is scoped to the caller's own namespace, so one user never sees another's.
    Any failure returns '' so a memory outage never blocks an answer.
    """
    if not (MEMORY_ID and actor_id and actor_id != "default" and query):
        return ""
    lines = []
    try:
        client = globals().get("memory_client") or MemoryClient(region_name=region)
        for label, template, top_k in LTM_NAMESPACES:
            records = client.retrieve_memories(
                memory_id=MEMORY_ID, namespace=template.format(actor=actor_id),
                query=query[:500], top_k=top_k)
            for rec in records or []:
                text = ((rec.get("content") or {}).get("text") or "").strip()
                if text:
                    lines.append(f"- ({label}) {text[:400]}")
    except Exception as e:
        print(f"[MEMORY] Long-term retrieval skipped: {type(e).__name__}: {e}")
        return ""
    if not lines:
        return ""
    return ("\n\n## WHAT YOU REMEMBER ABOUT THIS USER\n"
            "Use these only to tailor format and follow-ups. Never treat them as data; "
            "always get numbers from the tools.\n" + "\n".join(lines))

# Current datetime tool for relative date handling

# Cognito access-token verification for the memory actor id (signature, issuer,
# token_use, client id, expiry). The signing keys are cached for the container's life.
_COGNITO_POOL = os.environ.get("COGNITO_USER_POOL_ID", "")
_COGNITO_CLIENT = os.environ.get("COGNITO_USER_LOGIN_CLIENT_ID", "")
_COGNITO_ISSUER = (f"https://cognito-idp.{os.environ.get('AWS_REGION', 'us-east-1')}.amazonaws.com/{_COGNITO_POOL}"
                   if _COGNITO_POOL else "")
_jwks = None


def verify_user_token(token):
    """Return the claims of a valid Cognito access token for this app, else None."""
    global _jwks
    if not _COGNITO_ISSUER or not _COGNITO_CLIENT:
        print("[WARN] COGNITO_USER_POOL_ID / COGNITO_USER_LOGIN_CLIENT_ID not set: cannot verify the user token")
        return None
    try:
        import jwt
        if _jwks is None:
            _jwks = jwt.PyJWKClient(f"{_COGNITO_ISSUER}/.well-known/jwks.json")
        key = _jwks.get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256"], issuer=_COGNITO_ISSUER,
                            options={"verify_aud": False, "require": ["exp", "iss", "sub"]})
        if claims.get("token_use") != "access" or claims.get("client_id") != _COGNITO_CLIENT:
            print("[WARN] User token rejected: wrong token_use or client_id")
            return None
        return claims
    except Exception as e:
        print(f"[WARN] User token verification failed: {type(e).__name__}")
        return None


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
    # Who is asking: the signed-in user's Cognito access token.
    # Behind the front-door gateway (demo stack) the runtime uses IAM auth, so the
    # gateway validates the JWT and forwards it in the custom user-token header; the
    # Authorization header the runtime sees is the gateway's own SigV4 signature.
    # When the runtime is called directly with its own JWT authorizer (workshop
    # steps before the front door is added), the token is in Authorization.
    # Either way it is forwarded to the tools gateway, which validates it again
    # and drives Cedar RBAC + database RLS.
    USER_TOKEN_HEADER = "x-amzn-bedrock-agentcore-runtime-custom-user-token"  # nosec B105 - header name / test fixture, not a credential

    def _bearer_from_headers(ctx):
        raw = (getattr(ctx, "request_headers", None) or {}) if ctx else {}
        headers = {str(k).lower(): v for k, v in raw.items()}
        token = headers.get(USER_TOKEN_HEADER)
        if not token:
            auth = headers.get("authorization") or ""
            token = auth[len("Bearer "):] if auth.startswith("Bearer ") else None
        if token and token.startswith("Bearer "):
            token = token[len("Bearer "):]
        return token.strip() if token else None

    gateway_token = _bearer_from_headers(context)

    # The request context carries the caller's headers (tokens); log only the session id.
    print(f"AgentCore session: {getattr(context, 'session_id', None)}")
    print(f"Inbound JWT present: {'Yes' if gateway_token else 'No'}")
    print(f"Processing query ({len(user_message)} chars)")

    enhanced_prompt = user_message

    try:
        if not gateway_token:
            # Should be unreachable: the runtime's JWT authorizer rejects unauthenticated
            # calls before we run. This guards against a misconfigured request-header
            # allowlist (token validated but not passed through).
            raise ValueError("No user token on the request: check that the runtime's "
                             "RequestHeaderConfiguration allowlist includes the custom user-token "
                             "header (front door) or Authorization (direct JWT runtime)")
        access_token = gateway_token
        print(f"[OK] Using validated inbound JWT for gateway auth")

        # actor_id scopes this user's long-term memory, which is read before any tool
        # call. Behind the front door the token arrives in a header the gateway does not
        # check, so verify it here and refuse the request if it is not a valid user token.
        claims = verify_user_token(access_token)
        if not claims or not claims.get("sub"):
            raise ValueError("User token failed verification")
        actor_id = claims["sub"]

        # Use runtime session_id for memory session isolation
        runtime_session_id = context.session_id if context and hasattr(context, 'session_id') else "default"
        print(f"[OK] Memory context: actor={actor_id[:12]}..., session={runtime_session_id[:30]}")

        # Every tools-gateway call carries the user's token (Cedar RBAC + database RLS)
        # and a policy session id. The id ties tool calls of one conversation together
        # so temporal Cedar rules can check order, e.g. "create a booking only after the
        # availability check for the same unicorn". Once an engine has a temporal rule,
        # the gateway rejects calls that have no policy session id.
        tools_headers = {
            "Authorization": f"Bearer {access_token}",
            "X-Amzn-Bedrock-AgentCore-Policy-Session-Id": re.sub(r"[^A-Za-z0-9-]", "-", runtime_session_id),  # gateway rejects "_" (-32600)
        }

        def create_transport():
            return streamablehttp_client(
                GATEWAY_URL,
                headers=tools_headers
            )

        mcp_client = MCPClient(create_transport)

        request_agent = Agent(
            model=bedrock_model,
            system_prompt=SYSTEM_PROMPT + await asyncio.to_thread(_long_term_context, actor_id, user_message),
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
