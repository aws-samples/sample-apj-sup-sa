# Spec: Voice Integration for Agentic Analytics

**Status:** Draft for team review · **Team:** Agentic Analytics
**Last updated:** 2026-06-09

## 1. Goal

Let a business user **talk** to the existing agentic-analytics agent. User asks for a report
by voice → the agent runs analytics tools → it **summarizes findings aloud** ("premium rentals
had the highest yield; the standard tier underperformed") → user asks conversational follow-ups
("wait, why exactly is it bad?"). Hackathon deliverable is a 3–5 min demo video.

## 2. Decision

**Keep the Strands agent as-is; add Pipecat as a voice front-end; bridge them with Pipecat's
built-in `AWSAgentCoreProcessor`.** Pipecat does **not** replace Strands — they work together
at different layers (Pipecat = ears & mouth, Strands = brain).

Rationale:
- All the value already lives in the workshop agent: 27 analytics tools via MCP Gateway, the
  SOP, multi-tenant RLS (JWT → Cedar → Postgres), AgentCore Memory, Bedrock Guardrails,
  human-in-the-loop text-to-SQL. Rebuilding any of that as Pipecat function tools would throw
  the workshop away and is infeasible in a hackathon.
- The agent is **already deployed on AgentCore Runtime** in exactly the shape Pipecat's
  processor expects (`@app.entrypoint`, `{"prompt": ...}` in, streamed text out).
- Cascaded (STT → text agent → TTS) keeps tool-calling reliable (vs. speech-to-speech /
  Nova Sonic), which the team already agreed on.

## 3. Architecture

```
                 ┌─────────────────── Pipecat (voice shell) ───────────────────┐
 Mic → WebRTC →  transport.input → STT(Deepgram) → user_agg →  AWSAgentCoreProcessor  → TTS(Deepgram) → transport.output → Speaker
 (Daily)                                                              │  ▲
                 └────────────────────────────────────────────────── │ ─┼───────────────┘
                                                                      │  │ invoke_agent_runtime({"prompt": <last user turn>})
                                                                      ▼  │ streamed text
                                              ┌──────────── AgentCore Runtime (the brain) ───────────┐
                                              │  Strands Agent + SOP + AgentCore Memory              │
                                              │     → MCP Gateway (Cedar RBAC + JWT interceptor)     │
                                              │         → 27 analytics tools / create_booking / SQL  │
                                              │             → Aurora PostgreSQL (RLS by tenant)      │
                                              └──────────────────────────────────────────────────────┘
```

The `AWSAgentCoreProcessor` stage **replaces** what would otherwise be an LLM service in the
pipeline. There is exactly **one** reasoning LLM (inside Strands) — no double-LLM — which keeps
latency (our #1 risk) manageable.

## 4. The two "Pipecat + AgentCore" patterns (don't confuse them)

| | (A) Pipecat **ON** AgentCore | **(B) AgentCore agent INSIDE Pipecat** ✅ |
|---|---|---|
| What's hosted on AgentCore | the *whole Pipecat bot* (container) | *our Strands agent* (already done) |
| AgentCore's role | execution env / scaling for the bot | the **LLM/brain stage** of the pipeline |
| Reference | `pipecat-examples/deployment/aws-agentcore-*`; [AWS blog series](https://aws.amazon.com/blogs/machine-learning/deploy-voice-agents-with-pipecat-and-amazon-bedrock-agentcore-runtime-part-1/) | `resources/pipecat-aws-agentcore-example/` ; [DeepWiki](https://deepwiki.com/pipecat-ai/pipecat-examples/4.2-aws-agentcore-pipeline-integration) |

**We build (B).** (A) is an optional later choice for *hosting* the voice bot; the two compose.

## 5. Reference implementation

Vendored at `resources/pipecat-aws-agentcore-example/` (first-party Pipecat example). Its
`agents/code_agent.py` is itself a **Strands-on-AgentCore** agent — same shape as our
`unicorn_rental_agent.py`. The pipeline wiring (verbatim from its `bot.py`):

```python
from pipecat.services.aws.agent_core import AWSAgentCoreProcessor

agent = AWSAgentCoreProcessor(agentArn=os.getenv("AWS_AGENT_ARN"))

pipeline = Pipeline([
    transport.input(),
    stt,                 # DeepgramSTTService
    user_aggregator,
    agent,               # ← AgentCore agent stands in for the LLM
    tts,                 # (example uses Cartesia; we use DeepgramTTSService)
    transport.output(),
    assistant_aggregator,
])
```

The agent side streams back in this shape (verbatim from `code_agent.py`):

```python
async for event in agent.stream_async(payload.get("prompt", "")):
    if "data" in event:
        yield {"response": event["data"]}   # incremental text → spoken
    elif "result" in event:
        yield {"done": True}
```

## 6. What we reuse vs. what's new

| Component | Source | Status |
|-----------|--------|--------|
| Strands analytics agent + SOP + tools + RLS + memory | `resources/agentic-analytics-workshop/app/agentcore_strands/` | **reuse as-is** (deployed on AgentCore) |
| Cascaded Pipecat bot skeleton (STT/TTS/Daily) | `resources/aws-deepgram-sa-hackathon/server/bot.py` | adapt |
| `AWSAgentCoreProcessor` wiring | `resources/pipecat-aws-agentcore-example/bot.py` | adapt |
| React voice client | `resources/aws-deepgram-sa-hackathon/client/` | adapt |
| **Voice bot** (`server/bot.py`) — STT → AgentCoreProcessor → TTS | — | **new** (we write) |
| **Voice-variant SOP** | — | **new** |
| **Cognito token strategy for headless bot** | — | **new** |

> Note: our cascaded starter uses the `PipelineTask` / `PipelineRunner` API; the AgentCore
> example uses the newer `PipelineWorker` / `WorkerRunner` API and kicks off on
> `on_client_connected`. Pick one generation and be consistent when merging the two references.

## 7. Open issues / gaps to solve (in priority order)

### 7.1 — `gateway_token` is mandatory  ⚠️ do this first
`unicorn_rental_agent.py` **raises `ValueError` without `gateway_token`** — it carries Cognito
`custom:role` + `custom:account_id` for RBAC + Postgres RLS. The React UI gets it via Cognito
Hosted-UI login (`authService.js`). A headless voice bot has no browser.
**Options:** (a) Cognito ROPC / client-credentials for a fixed demo user, token injected as env
var; (b) pass the token from the voice client at session start; (c) relax the requirement for a
single-tenant demo. **No token → no tools.** Prototype this before anything else.

### 7.2 — Response-shape contract  ⚠️
The reference agent yields `{"response": ...}` / `{"done": true}`. **Our workshop agent yields
raw Strands stream events** (`async for event in request_agent.stream_async(...): yield event`)
— the React UI parses that raw SSE shape itself. These differ.
**Action:** confirm exactly what `AWSAgentCoreProcessor` parses (check the `pipecat-ai[aws]`
source or the `pipecat-docs` MCP), then add a **voice entrypoint** to our agent that yields the
processor's expected shape — without disturbing the existing UI entrypoint.

### 7.3 — Voice-variant SOP
The current SOP is written for a text UI and is hostile to voice: it mandates *"present data in
clear, formatted tables"* and *"MUST use markdown"* (§5), and the text-to-SQL flow emits
`<!--SQL_APPROVAL_REQUEST-->` JSON blocks for the UI to render (§4). You can't speak a markdown
table. The agent already loads its SOP from S3/local and has a pass-through prompt hook
(`enhanced_prompt`), so add a **voice SOP**: "answer in 1–2 spoken sentences, conversational,
no tables/markdown/emoji, offer to drill down." This directly serves the "summarize, then I ask
why" demo flow.

### 7.4 — Human-in-the-loop SQL doesn't map to voice
For the demo, lean on the pre-baked `get_*_summary` tools (no approval step). Either skip the
custom text-to-SQL path or handle approval verbally ("shall I run that?" → "yes").

### 7.5 — Session / memory threading
The processor sends **only the last user message** (not the rolling transcript) — by design;
the agent owns its memory. Ours already does via `MemoryHookProvider` keyed on `session_id`.
**Action:** thread a stable session id from the Pipecat session into the agent payload so turns
link up across the conversation.

### 7.6 — Latency (our #1 risk)
Larger analytics payloads than plain chat. Mitigations: summarize tool output *before* it
reaches TTS; stream sentence chunks into TTS as they arrive; consider a faster model (Haiku) on
the voice path vs. the workshop's Opus-4.6; consider Deepgram Flux (`USE_FLUX=true`) for
turn-taking; watch `enable_metrics` per-stage timing.

## 8. Milestones

1. **Token spike** — get a Cognito token headlessly and successfully `agentcore invoke` our
   deployed agent with `{"prompt", "gateway_token"}`. (Unblocks everything — §7.1)
2. **Wire bridge** — minimal `server/bot.py`: Deepgram STT → `AWSAgentCoreProcessor`(our ARN) →
   Deepgram TTS, talking end-to-end with canned audio. Resolve response-shape (§7.2).
3. **Voice SOP** — swap in the voice-variant SOP; confirm spoken summaries sound natural (§7.3).
4. **Demo polish** — follow-up Q&A, latency tuning, the React client, record the video.

## 9. Open questions

- Exact input/output contract of `AWSAgentCoreProcessor` (does it require `{"response"}`/`{"done"}`,
  or handle raw Strands events?) — verify against the package source.
- Can we pass `gateway_token` through the processor to the agent payload, or do we need a custom
  processor subclass / a token baked into the agent's env?
- Single-tenant demo (one fixed user) vs. multi-tenant (token per voice session)?
- Run locally for the demo (fine per starter README) vs. deploy the bot via pattern (A)?

## 10. References

- Local: `resources/pipecat-aws-agentcore-example/` (the (B) pattern, with a Strands agent)
- Local: `resources/aws-deepgram-sa-hackathon/` (cascaded Deepgram+Pipecat starter)
- Local: `resources/agentic-analytics-workshop/` (our agent, SOP, tools, UI bridge)
- Skill: `.claude/skills/pipecat/SKILL.md` → "Integrating our Strands/AgentCore agent"
- [Pipecat example on GitHub](https://github.com/pipecat-ai/pipecat-examples/tree/main/aws-agentcore)
- [AWS blog: Deploy voice agents with Pipecat + AgentCore Runtime](https://aws.amazon.com/blogs/machine-learning/deploy-voice-agents-with-pipecat-and-amazon-bedrock-agentcore-runtime-part-1/)
