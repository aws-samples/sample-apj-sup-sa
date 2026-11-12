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

SYSTEM_PROMPT = load_system_prompt()

# ── Chart tag presigning ────────────────────────────────────────────────────
# The model emits a short, faithful tag: <chart caption="..." s3key="charts/x.png" />.
# We presign the s3key into a viewable URL and rewrite the tag to
# <chart caption="..." url="https://..." /> on the OUTBOUND stream ONLY — the
# model's own message text keeps the short s3key, so AgentCore Memory never stores
# an (expiring, 400-char) presigned URL. Both the voice bot and the text UI then
# receive a ready-to-render <chart url="..."> with no client-side S3 access.
import re

_CHART_TAG_RE = re.compile(r'<chart\b([^>]*?)/?>(?:\s*</chart>)?', re.IGNORECASE | re.DOTALL)
_S3KEY_ATTR_RE = re.compile(r'\bs3key\s*=\s*"([^"]+)"', re.IGNORECASE)
# Longest plausible <chart ...> tag; used to decide how much trailing text to hold
# back while a tag might still be mid-stream across delta boundaries.
_CHART_TAG_MAX = 4096

_s3_presign_client = None
_CHART_KEY_RE = re.compile(r"charts/[A-Za-z0-9_-]{1,64}\.png")


def _presign_chart_key(s3key: str) -> str | None:
    """Presign a charts/<name>.png key into a short-lived GET URL (the role can read charts/*)."""
    bucket = os.getenv("CHART_BUCKET") or os.getenv("SOP_S3_BUCKET")
    if not bucket or not s3key:
        return None
    key = s3key.replace(f"s3://{bucket}/", "").lstrip("/")
    # Only the chart sandbox's own output. The model writes the tag, so a prompt
    # could otherwise ask it to "chart" data/customers.csv and get a signed URL.
    if not _CHART_KEY_RE.fullmatch(key):
        print("[CHART] refused to presign a key outside charts/")
        return None
    global _s3_presign_client
    try:
        if _s3_presign_client is None:
            _s3_presign_client = boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-west-2"))
        return _s3_presign_client.generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=3600
        )
    except Exception as e:  # pragma: no cover
        print(f"[CHART] presign failed for {s3key}: {e}")
        return None


def _rewrite_chart_tags(text: str) -> str:
    """Replace every <chart ... s3key="..."> with <chart ... url="<presigned>">.

    Leaves a tag unchanged if it has no s3key or presigning fails (the consumer
    then simply finds no url and skips it — never breaks the turn).
    """
    def _sub(m: "re.Match[str]") -> str:
        attrs = m.group(1)
        key_m = _S3KEY_ATTR_RE.search(attrs)
        if not key_m:
            return m.group(0)
        url = _presign_chart_key(key_m.group(1).strip())
        if not url:
            return m.group(0)
        # Swap the s3key attribute for a url attribute, preserving caption etc.
        new_attrs = _S3KEY_ATTR_RE.sub(f'url="{url}"', attrs, count=1)
        return f"<chart{new_attrs}/>"

    return _CHART_TAG_RE.sub(_sub, text)


# Gateway configuration
GATEWAY_URL = os.getenv("GATEWAY_URL", "")

# Bedrock model configuration
model_id = os.getenv("BEDROCK_MODEL_ID", "global.anthropic.claude-sonnet-5-5")
region = os.getenv("AWS_REGION", "us-east-1")

# Guardrail configuration (native Bedrock integration)
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
        # Scope guardrail evaluation to the LATEST user message only (guardContent
        # qualifier). Without this, Bedrock scans the whole input — including this
        # agent's appended system/SOP directives ("You MUST…", "NEVER…") — which the
        # PROMPT_ATTACK content filter (set to HIGH on input) flags as injection,
        # falsely blocking benign turns like "Yes. Go ahead." after a SQL-approval
        # card. REQUIRES strands-agents>=1.22.0 (silently dropped at 1.21.0 — see the
        # 'Invalid configuration parameters' UserWarning that version emits).
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
                max_results=20
            )
            # list_events returns NEWEST-first. Replaying in that order scrambles
            # the transcript (an answer appears before its question), which makes
            # the model think a prior question was never answered and re-answer it
            # on the next turn. Replay OLDEST-first so the conversation reads in
            # true chronological order.
            events = list(reversed(list(events)))
            for ev in events:
                for payload_item in ev.get('payload', []):
                    conv = payload_item.get('conversational', {})
                    role = conv.get('role', '').lower()
                    text = conv.get('content', {}).get('text', '')
                    if not text or role not in ('user', 'assistant'):
                        continue
                    # Replay as PLAIN TEXT only — never reconstruct toolUse/toolResult
                    # blocks. A truncated list_events window (max_results) or reversed
                    # ordering can orphan a tool block, which makes Bedrock Converse
                    # reject the entire message list and the agent goes silent after a
                    # few turns. Plain text is always Converse-valid.
                    # If an event was written by an older build that JSON-encoded the
                    # full content array, flatten it back down to its text parts.
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

print("AgenticAnalytics AgentCore Gateway Configuration:")
print(f"Gateway URL: {GATEWAY_URL}")
print(f"Using model: {model_id}")
print(f"AWS Region: {region}")
print("[OK] AgenticAnalytics AgentCore ready for requests")

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
    # Response mode: 'text' (default, markdown) or 'voice' (spoken <speak> headline +
    # displayed detail). The browser's text chat sends mode='text'; the voice bot
    # sends mode='voice'. ONE SOP serves both — mode selects the Response Formatting
    # branch only; it never changes tool access or RBAC/RLS.
    mode = (payload.get("mode") or "text").lower()
    if mode not in ("text", "voice"):
        mode = "text"

    # The request context carries the caller's headers (tokens); log only the session id.
    print(f"AgentCore session: {getattr(context, 'session_id', None)}")
    print(f"Inbound JWT present: {'Yes' if gateway_token else 'No'}")
    print(f"Response mode: {mode}")
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
            return streamablehttp_client(
                GATEWAY_URL,
                headers=tools_headers
            )
        
        mcp_client = MCPClient(create_transport)
        
        system_prompt = SYSTEM_PROMPT
        # Tell the agent which Response Formatting branch (Step 5 of the SOP) to follow
        # this turn. Appended (not file-swapped) so one SOP serves both modalities.
        system_prompt = (
            f"{system_prompt}\n\n## ACTIVE MODE: {mode}\n"
            f"Format this response for `{mode}` mode per Step 5 of the SOP. "
            + (
                # Voice: emit a streamed spoken acknowledgement FIRST (before any tool
                # call), then the headline, then the displayed answer. The <ack> is the
                # very first thing you output — it is spoken aloud immediately so the
                # user hears you engage during the (silent) data-fetch gap.
                "Begin your response with exactly one <ack>...</ack> block as the VERY FIRST "
                "output, BEFORE calling any tool: a brief, natural, CONTEXT-SPECIFIC spoken "
                "acknowledgement of THIS request (e.g. <ack>Sure — let me pull your booking "
                "numbers.</ack>). It must reference what was asked, vary every turn, contain NO "
                "markup/numbers/SQL, and be one short sentence. Then call your tools. Then emit "
                "exactly one <speak>...</speak> headline (1-3 spoken sentences, no markup), then "
                "the full displayed answer with any tables and <chart> tags."
                if mode == "voice"
                # Text: plain markdown ONLY. No spoken tags at all — they would leak into chat.
                else "Respond in plain markdown ONLY. Do NOT emit any <ack> or <speak> tags — "
                     "those are for voice mode only; in text mode they would appear as literal text."
            )
        )
        # Chart upload target — the code-interpreter sandbox can't read the agent's
        # env, so pass the bucket/region as literals the model substitutes into the
        # Step-4b upload code (placeholders __CHART_BUCKET__ / __CHART_REGION__).
        chart_bucket = os.getenv("CHART_BUCKET") or os.getenv("SOP_S3_BUCKET", "")
        chart_region = os.getenv("AWS_REGION", "us-west-2")
        if chart_bucket:
            system_prompt += (
                f"\n\n## CHART UPLOAD TARGET\n"
                f"When generating a chart (Step 4b), use these LITERAL values in the sandbox code:\n"
                f"  __CHART_BUCKET__ = {chart_bucket}\n"
                f"  __CHART_REGION__ = {chart_region}\n"
            )

        agent_tools = [mcp_client, current_datetime]
        # Code Interpreter (matplotlib charts). The sandbox renders a PNG, uploads it
        # to s3://CHART_BUCKET/charts/ (via its execution role), and prints ONLY the
        # tiny S3 key. The model then emits a short <chart s3key="charts/..."> tag —
        # NOT base64 — so the streamed response stays small. The runtime presigns
        # that key into a viewable URL in the stream loop below (see SOP Step 4b).
        if _CODE_INTERPRETER_AVAILABLE and os.getenv("ENABLE_CHART_TOOL", "false").lower() == "true":
            try:
                # Use a CUSTOM code interpreter bound to an execution role that can
                # write to S3 (CHART_CI_ID, provisioned by agentcore-stack.yaml). The
                # default aws.codeinterpreter.v1 sandbox has NO execution role, so it
                # cannot reach S3 — which is why a custom interpreter is required.
                ci_kwargs = {"region": os.getenv("AWS_REGION", "us-west-2")}
                chart_ci_id = os.getenv("CHART_CI_ID")
                if chart_ci_id:
                    ci_kwargs["identifier"] = chart_ci_id
                code_interpreter = AgentCoreCodeInterpreter(**ci_kwargs)
                agent_tools.append(code_interpreter.code_interpreter)
            except Exception as e:
                print(f"[CHART] Code interpreter unavailable: {e}")

        system_prompt = system_prompt + await asyncio.to_thread(_long_term_context, actor_id, user_message)
        request_agent = Agent(
            model=bedrock_model,
            system_prompt=system_prompt,
            tools=agent_tools,
            hooks=memory_hooks,
            callback_handler=None,
            state={"actor_id": actor_id, "session_id": runtime_session_id},
        )
        
        # Yield text-delta events PLUS a tiny tool-START signal. Strands'
        # stream_async also emits events that embed the full accumulated message +
        # tool results (e.g. the code interpreter's stdout, which echoes the raw
        # query rows) and the streamed tool-INPUT deltas (the full SQL/args). Those
        # re-serialize on every streaming event and can balloon the SSE response,
        # which trips IncompleteRead on the client and kills the turn. So we forward
        # ONLY: (1) contentBlockDelta text, and (2) the tool-use START event, which
        # carries just the tool name + id (NOT its input args, NOT its result). That
        # restores the UI's "Running <tool>" indicator without re-introducing bloat.
        #
        # Chart presigning: the model writes a short <chart ... s3key="..."> tag in
        # its text output. We hold back a small tail of the outbound text so a
        # <chart> tag that spans delta boundaries is never split, presign s3key →
        # url on whatever is safe to flush, and emit <chart ... url="..."> instead.
        # We mutate ONLY the outbound copy; the model's own message text (and thus
        # Memory) keeps the short s3key.
        pending = ""  # buffered outbound text not yet safe to flush

        def _split_flushable(buf: str) -> tuple[str, str]:
            # Hold back from the last '<' that could begin an as-yet-incomplete
            # <chart ...> tag, so we never presign/emit half a tag.
            lt = buf.rfind("<")
            if lt == -1:
                return buf, ""
            tail = buf[lt:]
            # A complete tag (has a '>') anywhere from lt is fine to flush; only an
            # OPEN, still-growing "<...":  hold it — unless it's clearly not <chart
            # and already long enough to decide, or we've exceeded the max tag size.
            if ">" in tail:
                return buf, ""
            if len(tail) <= len("<chart") and not "<chart".startswith(tail.lower()):
                return buf, ""  # '<' starts something that can't be <chart
            if len(tail) > _CHART_TAG_MAX:
                return buf, ""  # runaway; stop holding
            return buf[:lt], tail

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
                flush, pending = _split_flushable(pending)
                if flush:
                    sent_text = True
                    yield {"event": {"contentBlockDelta": {"delta": {"text": _rewrite_chart_tags(flush)}}}}
                continue
            # Tool-use START: forward a minimal {name, toolUseId} event so the UI can
            # show "Running <tool>". Strands wraps each raw Bedrock chunk as
            # {"event": <chunk>}; a tool start is contentBlockStart.start.toolUse.
            # We deliberately do NOT forward the tool-INPUT deltas or the toolResult
            # (those carry the big SQL/args/rows that caused the IncompleteRead bloat).
            tool_use = ev.get("contentBlockStart", {}).get("start", {}).get("toolUse")
            if isinstance(tool_use, dict) and tool_use.get("name"):
                after_tool = True
                yield {"event": {"contentBlockStart": {"start": {"toolUse": {
                    "name": tool_use["name"],
                    "toolUseId": tool_use.get("toolUseId", ""),
                }}}}}

        # Flush whatever remains after the stream ends (presign any final tag).
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
