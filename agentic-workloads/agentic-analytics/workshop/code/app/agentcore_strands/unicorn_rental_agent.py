#!/usr/bin/env python3
"""
AgenticAnalytics AgentCore Integration with Gateway
Uses AgentCore Gateway instead of local MCP server
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
for _candidate in [_project_dir / "config.env", _project_dir / ".env", _script_dir / "config.env"]:
    if _candidate.exists():
        load_dotenv(_candidate)
        break

# Set bypass tool consent for AgentCore
os.environ["BYPASS_TOOL_CONSENT"] = "true"

# Strands imports
from strands import Agent, tool
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient
from strands.hooks import HookProvider, HookRegistry, AgentInitializedEvent, MessageAddedEvent
from mcp.client.streamable_http import streamablehttp_client
try:
    from strands_tools.code_interpreter import AgentCoreCodeInterpreter
    _CODE_INTERPRETER_AVAILABLE = True
except Exception:  # pragma: no cover
    _CODE_INTERPRETER_AVAILABLE = False
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
    
    local_path = Path(__file__).parent / "unicorn_rental_analytics.sop.md"
    with open(local_path, 'r') as f:
        print(f"[OK] Loaded SOP from {local_path}")
        return f.read()

# ============================================================================
# TODO 2.3.1 (Step 2): Load the SOP as the system prompt
# Replace the basic prompt below with: SYSTEM_PROMPT = load_system_prompt()
# ============================================================================
SYSTEM_PROMPT = "You are a helpful unicorn rental analytics assistant. Help users query their business data."

# ── Chart rendering support ───────────────────────────────────────────────────
# When the agent draws a chart, the code-interpreter sandbox renders a PNG, uploads
# it to s3://CHART_BUCKET/charts/, and prints only the tiny S3 key. The model emits
# a short <chart s3key="charts/..."> tag; here we presign that key into a viewable
# URL in the outbound stream (the UI renders it as an <img>). See SOP Step 4b.
import re as _re_chart

_CHART_TAG_RE = _re_chart.compile(r'<chart\b([^>]*?)/?>(?:\s*</chart>)?', _re_chart.IGNORECASE | _re_chart.DOTALL)
_S3KEY_ATTR_RE = _re_chart.compile(r'\bs3key\s*=\s*"([^"]+)"', _re_chart.IGNORECASE)
_CHART_TAG_MAX = 4096
_s3_presign_client = None
# Only the chart sandbox's own output is presigned. The model writes the tag, so a
# prompt could otherwise ask it to "chart" data/customers.csv and get a signed URL.
_CHART_KEY_RE = _re_chart.compile(r"charts/[A-Za-z0-9_-]{1,64}\.png")


def _presign_chart_key(s3key):
    """Presign an S3 chart key into a short-lived GET URL (the agent role has s3:GetObject)."""
    bucket = os.getenv("CHART_BUCKET") or os.getenv("SOP_S3_BUCKET")
    if not bucket or not s3key:
        return None
    key = s3key.replace("s3://%s/" % bucket, "").lstrip("/")
    if not _CHART_KEY_RE.fullmatch(key):
        return None
    global _s3_presign_client
    try:
        if _s3_presign_client is None:
            _s3_presign_client = boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-1"))
        return _s3_presign_client.generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=3600
        )
    except Exception as e:  # pragma: no cover
        print("[CHART] presign failed for %s: %s" % (s3key, e))
        return None


def _rewrite_chart_tags(text):
    """Replace every <chart ... s3key="..."> with <chart ... url="<presigned>">."""
    def _sub(m):
        attrs = m.group(1)
        key_m = _S3KEY_ATTR_RE.search(attrs)
        if not key_m:
            return m.group(0)
        url = _presign_chart_key(key_m.group(1).strip())
        if not url:
            return m.group(0)
        new_attrs = _S3KEY_ATTR_RE.sub('url="%s"' % url, attrs, count=1)
        return "<chart%s/>" % new_attrs
    return _CHART_TAG_RE.sub(_sub, text)


def _chart_split_flushable(buf):
    """Hold back from the last '<' that could begin an incomplete <chart ...> tag."""
    lt = buf.rfind("<")
    if lt == -1:
        return buf, ""
    tail = buf[lt:]
    if ">" in tail:
        return buf, ""
    if len(tail) <= len("<chart") and not "<chart".startswith(tail.lower()):
        return buf, ""
    if len(tail) > _CHART_TAG_MAX:
        return buf, ""
    return buf[:lt], tail
# ──────────────────────────────────────────────────────────────────────────────

# Gateway configuration
GATEWAY_URL = os.getenv("GATEWAY_URL", "")

# Bedrock model configuration
model_id = os.getenv("BEDROCK_MODEL_ID", "global.anthropic.claude-sonnet-5-5")
region = os.getenv("AWS_REGION", "us-east-1")

# Guardrail configuration (native Bedrock integration — activated in Step 8)
# When GUARDRAIL_ID is set in config.env, the model automatically applies guardrails.
GUARDRAIL_ID = os.getenv("GUARDRAIL_ID", "")
GUARDRAIL_VERSION = os.getenv("GUARDRAIL_VERSION", "DRAFT")
GUARDRAIL_BLOCKED_MESSAGE = "I can only help with unicorn rental analytics. Please ask about bookings, revenue, customers, or unicorn management."
EMPTY_TURN_MESSAGE = "I couldn't finish an answer this time. Please try again, or ask a narrower question."

bedrock_model_kwargs = dict(
    model_id=model_id,
    streaming=True,
)
# Claude Opus 4.8 and every Claude 5.x model reject `temperature` (Converse returns
# ValidationException: "`temperature` is deprecated for this model"). Send it only to
# the older models known to accept it, so a new model ID never breaks the agent.
TEMPERATURE_MODELS = ("claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5", "claude-opus-4-5", "claude-sonnet-4-5")
if any(m in model_id for m in TEMPERATURE_MODELS):
    bedrock_model_kwargs["temperature"] = 0.3
if GUARDRAIL_ID:
    bedrock_model_kwargs.update(
        guardrail_id=GUARDRAIL_ID,
        guardrail_version=GUARDRAIL_VERSION,
        guardrail_redact_input=True,
        guardrail_redact_input_message=GUARDRAIL_BLOCKED_MESSAGE,
        guardrail_latest_message=True,
    )
    print(f"[OK] Guardrails enabled: {GUARDRAIL_ID}")

# Bedrock Model (pre-configured)
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
                    content = conv.get('content', {}).get('text', '')
                    if content and role in ('user', 'assistant'):
                        event.agent.messages.append({"role": role, "content": [{"text": content}]})
        except Exception as e:
            print(f"[MEMORY] Failed to load history: {e}")

    def on_message_added(self, event: MessageAddedEvent):
        """Save each new message to memory."""
        try:
            msg = event.message
            role = msg.get("role", "")
            text_parts = [c.get("text", "") for c in msg.get("content", []) if "text" in c]
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

print("AgenticAnalytics AgentCore Gateway Configuration:")
print(f"Gateway URL: {GATEWAY_URL}")
print(f"Using model: {model_id}")
print(f"AWS Region: {region}")
print("[OK] AgenticAnalytics AgentCore ready for requests")

# ============================================================================
# TODO 2.4 (Step 2): Add the @app.entrypoint decorator on the line below
# This tells AgentCore Runtime which function handles requests.
# See: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.html
# ============================================================================
# @app.entrypoint  # TODO 2.4: Uncomment this line
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

    # Pass-through for now; modify here to enrich the prompt (e.g., inject context, tenant info)
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
            return streamablehttp_client(GATEWAY_URL, headers=tools_headers)
        
        mcp_client = MCPClient(create_transport)

        # The agent's tools and system prompt are assembled here for you, ready to
        # pass to the Agent() in TODO 2.3.2. `agent_tools` always includes the Gateway
        # MCP client + current_datetime; `system_prompt` starts from the SOP you loaded
        # in TODO 2.3.1. The optional chart code-interpreter tool (and its upload hint)
        # are added below only when the top-up stack enables charts (ENABLE_CHART_TOOL=
        # true + a custom CHART_CI_ID interpreter that can write PNGs to S3) — you don't
        # need to touch this for Step 2; it just works once charts are turned on later.
        agent_tools = [mcp_client, current_datetime]
        system_prompt = SYSTEM_PROMPT
        if _CODE_INTERPRETER_AVAILABLE and os.getenv("ENABLE_CHART_TOOL", "false").lower() == "true":
            try:
                ci_kwargs = {"region": os.getenv("AWS_REGION", "us-east-1")}
                if os.getenv("CHART_CI_ID"):
                    ci_kwargs["identifier"] = os.getenv("CHART_CI_ID")
                _ci = AgentCoreCodeInterpreter(**ci_kwargs)
                agent_tools.append(_ci.code_interpreter)
                _cb = os.getenv("CHART_BUCKET") or os.getenv("SOP_S3_BUCKET", "")
                if _cb:
                    # The sandbox does NOT inherit env vars — give it literal values.
                    system_prompt = SYSTEM_PROMPT + (
                        "\n\n## CHART UPLOAD TARGET\n"
                        "When generating a chart (Step 4b), use these LITERAL values in the sandbox code:\n"
                        "  __CHART_BUCKET__ = %s\n"
                        "  __CHART_REGION__ = %s\n" % (_cb, os.getenv("AWS_REGION", "us-east-1"))
                    )
            except Exception as e:
                print("[CHART] Code interpreter unavailable: %s" % e)

        system_prompt = system_prompt + await asyncio.to_thread(_long_term_context, actor_id, user_message)

        # ====================================================================
        # TODO 2.3.2 (Step 2): Create the Strands Agent.
        #   Replace `None` below with an Agent that wires the pieces together:
        #     Agent(model=bedrock_model,
        #           system_prompt=system_prompt,            # the SOP you loaded in TODO 2.3.1
        #           tools=agent_tools,                      # Gateway MCP client + current_datetime
        #           hooks=[],                               # <-- you'll change this in TODO 2.8
        #           callback_handler=None,
        #           state={"actor_id": actor_id, "session_id": runtime_session_id})
        #
        # TODO 2.8 (Step 2): Enable memory — change `hooks=[]` above to `hooks=memory_hooks`.
        #   (memory_hooks is already built for you near the top of this file.)
        # ====================================================================
        request_agent = None  # TODO 2.3.2: replace with Agent(...)

        # Stream events, presigning any <chart s3key="..."> tag into a viewable URL.
        # A small tail is held back so a tag spanning delta boundaries is never split.
        pending = ""
        sent_text = False
        # Text after a tool call is a new model turn; without a break it runs into the
        # previous sentence ("...against it.The schema...").
        after_tool = False
        final = None
        async for event in request_agent.stream_async(enhanced_prompt):
            if isinstance(event, dict) and "result" in event:
                final = event["result"]
            ev = event.get("event") if isinstance(event, dict) else None
            if not isinstance(ev, dict):
                continue
            if ev.get("contentBlockDelta", {}).get("delta", {}).get("text") is not None:
                text = ev["contentBlockDelta"]["delta"]["text"]
                if after_tool and text and (sent_text or pending) and not text[0].isspace():
                    text = "\n\n" + text
                after_tool = False
                pending += text
                flush, pending = _chart_split_flushable(pending)
                if flush:
                    sent_text = True
                    yield {"event": {"contentBlockDelta": {"delta": {"text": _rewrite_chart_tags(flush)}}}}
                continue
            tool_use = ev.get("contentBlockStart", {}).get("start", {}).get("toolUse")
            if isinstance(tool_use, dict) and tool_use.get("name"):
                after_tool = True
                yield {"event": {"contentBlockStart": {"start": {"toolUse": {
                    "name": tool_use["name"],
                    "toolUseId": tool_use.get("toolUseId", ""),
                }}}}}
        if pending:
            sent_text = True
            yield {"event": {"contentBlockDelta": {"delta": {"text": _rewrite_chart_tags(pending)}}}}
        # A turn can end with no text at all: a guardrail block streams nothing, and so can
        # other stop reasons. Never return an empty reply; say why, and log the stop reason.
        if not sent_text:
            stop = getattr(final, "stop_reason", None)
            print(f"[empty-turn] stop_reason={stop}")
            text = GUARDRAIL_BLOCKED_MESSAGE if stop == "guardrail_intervened" else EMPTY_TURN_MESSAGE
            yield {"event": {"contentBlockDelta": {"delta": {"text": text}}}}
                
    except Exception as e:
        print(f"❌ Request failed: {str(e)}")
        # Same event shape as normal text, so the UI and the voice bot both show it.
        yield {"event": {"contentBlockDelta": {"delta": {"text": "Sorry, I couldn't reach the analytics tools just now. Please try again in a moment."}}}}

app.run()
