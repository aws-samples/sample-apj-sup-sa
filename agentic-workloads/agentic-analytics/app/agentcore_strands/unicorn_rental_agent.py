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
def load_system_prompt(s3_key_override: str | None = None):
    """Load SOP from S3 if configured, otherwise use local file"""
    s3_bucket = os.getenv("SOP_S3_BUCKET")
    s3_key = s3_key_override or os.getenv("SOP_S3_KEY", "sops/unicorn_rental_analytics.sop.md")
    
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


def _presign_chart_key(s3key: str) -> str | None:
    """Presign an S3 chart key into a short-lived GET URL (agent role has s3:GetObject)."""
    bucket = os.getenv("CHART_BUCKET") or os.getenv("SOP_S3_BUCKET")
    if not bucket or not s3key:
        return None
    key = s3key.replace(f"s3://{bucket}/", "").lstrip("/")
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
model_id = os.getenv("BEDROCK_MODEL_ID", "global.anthropic.claude-opus-4-6-v1")
region = os.getenv("AWS_REGION", "us-east-1")

# Guardrail configuration (native Bedrock integration)
GUARDRAIL_ID = os.getenv("GUARDRAIL_ID", "")
GUARDRAIL_VERSION = os.getenv("GUARDRAIL_VERSION", "DRAFT")

bedrock_model_kwargs = dict(
    model_id=model_id,
    streaming=True,
)
# Opus 4.8+ deprecates the `temperature` parameter (Converse returns a
# ValidationException: "`temperature` is deprecated for this model"). Only send
# temperature for models that still accept it.
if "opus-4-8" not in model_id:
    bedrock_model_kwargs["temperature"] = 0.3
if GUARDRAIL_ID:
    bedrock_model_kwargs.update(
        guardrail_id=GUARDRAIL_ID,
        guardrail_version=GUARDRAIL_VERSION,
        guardrail_redact_input=True,
        guardrail_redact_input_message="I can only help with unicorn rental analytics. Please ask about bookings, revenue, customers, or unicorn management.",
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

# Current datetime tool for relative date handling
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
    # JWT-native inbound auth: the runtime's CustomJWTAuthorizer has already validated
    # the caller's Cognito access token (signature/issuer/client_id) before we run, and
    # passes it through via the request-header allowlist. We read it from
    # context.request_headers['Authorization'] — this is THE user identity, used for
    # AgentCore RBAC/RLS (forwarded to the MCP Gateway). No payload gateway_token.
    def _bearer_from_headers(ctx):
        headers = getattr(ctx, "request_headers", None) or {} if ctx else {}
        # Header name casing can vary; match case-insensitively.
        auth = headers.get("Authorization") or headers.get("authorization")
        if auth and auth.startswith("Bearer "):
            return auth[len("Bearer "):].strip()
        return auth.strip() if auth else None

    gateway_token = _bearer_from_headers(context)
    sop_s3_key = payload.get("sop_s3_key")
    # Response mode: 'text' (default, markdown) or 'voice' (spoken <speak> headline +
    # displayed detail). The browser's text chat sends mode='text'; the voice bot
    # sends mode='voice'. ONE SOP serves both — mode selects the Response Formatting
    # branch only; it never changes tool access or RBAC/RLS.
    mode = (payload.get("mode") or "text").lower()
    if mode not in ("text", "voice"):
        mode = "text"

    print("AgentCore Context:\n-------\n", context)
    print(f"Inbound JWT present: {'Yes' if gateway_token else 'No'}")
    print(f"Response mode: {mode}")
    print("Processing Query:\n*******\n", user_message)

    # Pass-through for now; modify here to enrich the prompt (e.g., inject context, tenant info)
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
        
        system_prompt = load_system_prompt(s3_key_override=sop_s3_key) if sop_s3_key else SYSTEM_PROMPT
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

        async for event in request_agent.stream_async(enhanced_prompt):
            ev = event.get("event") if isinstance(event, dict) else None
            if not isinstance(ev, dict):
                continue
            if ev.get("contentBlockDelta", {}).get("delta", {}).get("text") is not None:
                pending += ev["contentBlockDelta"]["delta"]["text"]
                flush, pending = _split_flushable(pending)
                if flush:
                    yield {"event": {"contentBlockDelta": {"delta": {"text": _rewrite_chart_tags(flush)}}}}
                continue
            # Tool-use START: forward a minimal {name, toolUseId} event so the UI can
            # show "Running <tool>". Strands wraps each raw Bedrock chunk as
            # {"event": <chunk>}; a tool start is contentBlockStart.start.toolUse.
            # We deliberately do NOT forward the tool-INPUT deltas or the toolResult
            # (those carry the big SQL/args/rows that caused the IncompleteRead bloat).
            tool_use = ev.get("contentBlockStart", {}).get("start", {}).get("toolUse")
            if isinstance(tool_use, dict) and tool_use.get("name"):
                yield {"event": {"contentBlockStart": {"start": {"toolUse": {
                    "name": tool_use["name"],
                    "toolUseId": tool_use.get("toolUseId", ""),
                }}}}}

        # Flush whatever remains after the stream ends (presign any final tag).
        if pending:
            yield {"event": {"contentBlockDelta": {"delta": {"text": _rewrite_chart_tags(pending)}}}}

    except Exception as e:
        print(f"❌ Request failed: {str(e)}")
        yield {"type": "text", "content": f"I'm currently unable to connect to the scheduling system: {str(e)}. Please try again later."}

app.run()
