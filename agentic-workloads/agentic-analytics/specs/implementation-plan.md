# Implementation Plan: Voice Analytics Agent

**Status:** Active  
**Created:** 2026-06-10  
**Derived from:** `specs/voice-integration.md`

## Overview

Four milestones in priority order. Each builds on the previous and directly maps to the spec's
open issues. The critical path is: token spike → pipeline bridge → voice SOP → demo polish.

---

## Milestone 1 — Token Spike (unblocks everything)

**Goal:** Obtain a Cognito `gateway_token` headlessly and confirm a successful `agentcore invoke`
with `{"prompt": "...", "gateway_token": "..."}`. No token → no MCP tools → nothing works.

**Why this is first:** `unicorn_rental_agent.py:195` raises `ValueError` without a token. The
pipeline won't function at all until this is solved.

**Approach:** Cognito Resource Owner Password Credentials (ROPC) grant using a fixed demo user.
Store token in env; refresh on 401. This avoids a browser and keeps the bot stateless.

### Tasks

1. **Confirm ROPC is enabled** on the Cognito User Pool (`ALLOW_USER_PASSWORD_AUTH` flow).
   If not, enable it in the pool's app client settings (or pick a service account route).

2. **Write `server/auth.py`** — a small helper:
   ```python
   import boto3, os

   def get_gateway_token() -> str:
       client = boto3.client("cognito-idp", region_name=os.getenv("AWS_REGION"))
       resp = client.initiate_auth(
           AuthFlow="USER_PASSWORD_AUTH",
           AuthParameters={
               "USERNAME": os.getenv("DEMO_USERNAME"),
               "PASSWORD": os.getenv("DEMO_PASSWORD"),
           },
           ClientId=os.getenv("COGNITO_CLIENT_ID"),
       )
       return resp["AuthenticationResult"]["IdToken"]  # or AccessToken
   ```

3. **Add env vars** to `server/.env.example`:
   ```
   DEMO_USERNAME=
   DEMO_PASSWORD=
   COGNITO_CLIENT_ID=
   COGNITO_USER_POOL_ID=
   GATEWAY_URL=
   AWS_AGENT_ARN=
   ```

4. **Smoke-test** with a direct `agentcore invoke` call (CLI or boto3) before touching Pipecat:
   ```bash
   TOKEN=$(python -c "from auth import get_gateway_token; print(get_gateway_token())")
   aws bedrock-agentcore invoke-agent-runtime \
     --agent-runtime-arn "$AWS_AGENT_ARN" \
     --payload "{\"prompt\": \"What is the total revenue this month?\", \"gateway_token\": \"$TOKEN\"}"
   ```

**Done when:** The invoke returns analytics data — no `ValueError`, no empty tool results.

---

## Milestone 2 — Pipeline Bridge

**Goal:** `server/bot.py` that runs: Deepgram STT → `AWSAgentCoreProcessor`(our ARN) →
Deepgram TTS, end-to-end with a live microphone.

**Inputs:**
- `resources/aws-deepgram-sa-hackathon/server/bot.py` — transport + STT/TTS wiring
  (`PipelineTask` / `PipelineRunner` API — stick to this generation, per spec §6 note)
- `resources/pipecat-aws-agentcore-example/bot.py` — `AWSAgentCoreProcessor` wiring
  (uses `PipelineWorker` / `WorkerRunner` — do NOT mix; pick one API generation)

**API generation decision:** Use `PipelineTask` / `PipelineRunner` (the hackathon starter's
generation) — it already has the RTVI `on_client_ready` hook we need, and the React client
is already wired for it.

### Tasks

1. **Create `server/` directory** with `bot.py`, `auth.py`, `.env.example`.

2. **Write `server/bot.py`** by merging the two references:

   ```python
   # Key diff from hackathon starter bot.py:
   # - Remove AWSBedrockLLMService + function registration
   # - Remove ToolsSchema / weather function
   # - Add:  agent = AWSAgentCoreProcessor(agentArn=os.getenv("AWS_AGENT_ARN"))
   # - Keep: DailyTransport, DeepgramSTTService, DeepgramTTSService, SileroVADAnalyzer
   # - Keep: PipelineTask / PipelineRunner (NOT PipelineWorker)
   # - Keep: LLMContextAggregatorPair (needed for turn management even without local LLM)

   pipeline = Pipeline([
       transport.input(),
       stt,
       user_aggregator,
       agent,           # AWSAgentCoreProcessor stands in for the LLM
       tts,
       transport.output(),
       assistant_aggregator,
   ])
   ```

3. **Resolve the response-shape contract** (spec §7.2):

   `AWSAgentCoreProcessor` expects the agent to yield `{"response": "<text>"}` chunks and
   `{"done": True}` as the final event. Our `unicorn_rental_agent.py` yields raw Strands
   stream events (dicts with `"type"`, `"content"`, etc.).

   **Action:** Add a `voice_agent_invocation` entrypoint to the agent — separate from
   the existing `agent_invocation` — that wraps the Strands stream into the required shape:

   ```python
   @app.entrypoint(name="voice")          # or use a path prefix if AgentCore supports it
   async def voice_agent_invocation(payload, context):
       async for event in agent_invocation(payload, context):
           if "type" in event and event["type"] == "text":
               yield {"response": event["content"]}
       yield {"done": True}
   ```

   If AgentCore doesn't support named entrypoints, add a `"mode"` key to the payload and
   branch inside the existing `agent_invocation`.

   **Verify:** Check `pipecat-ai[aws]` source for the exact dict keys the processor reads
   (use the `pipecat-docs` MCP: search "AWSAgentCoreProcessor response shape").

4. **Thread `gateway_token` through the processor:**

   `AWSAgentCoreProcessor` sends the last user message as `{"prompt": "..."}`. We also need
   `gateway_token` in that payload. Two options:

   - **(A) Custom processor subclass** (preferred for cleanliness):
     ```python
     class AuthedAgentCoreProcessor(AWSAgentCoreProcessor):
         def __init__(self, token_fn, **kwargs):
             super().__init__(**kwargs)
             self._token_fn = token_fn

         async def _build_payload(self, context):
             payload = await super()._build_payload(context)
             payload["gateway_token"] = self._token_fn()
             return payload
     ```
   - **(B) Bake token into the deployed agent's env** (simpler for single-tenant demo):
     Set `GATEWAY_TOKEN` as a deployment-time env var in the AgentCore config and read it
     from `os.getenv` in `unicorn_rental_agent.py` as a fallback when not in the payload.

   Start with (B) for the spike; migrate to (A) if multi-tenant becomes needed.

5. **Add `server/pyproject.toml`**:
   ```toml
   [project]
   dependencies = [
     "pipecat-ai[daily,deepgram,aws]>=0.0.60",
     "python-dotenv",
     "boto3",
   ]
   ```

6. **Smoke-test** with canned audio (or a real mic): confirm STT transcribes, agent responds,
   TTS speaks. Check `enable_metrics` output for per-stage latency.

**Done when:** "What unicorns are available this weekend?" is spoken and answered aloud.

---

## Milestone 3 — Voice SOP

**Goal:** Swap in a voice-variant SOP so the agent answers in 1–2 spoken sentences, no
markdown, no tables, no `<!--SQL_APPROVAL_REQUEST-->` blocks.

**Why this matters:** The existing SOP (`unicorn_rental_analytics.sop.md`) mandates tables and
markdown (§5), and the text-to-SQL approval flow emits HTML comment blocks the TTS will read
verbatim.

### Tasks

1. **Write `server/unicorn_rental_voice.sop.md`** — minimal diff from the original:
   - Replace §5 formatting rules: *"Answer in 1–2 conversational sentences. No markdown, no
     tables, no emoji, no special characters. If numbers matter, say the top 2–3 values aloud
     and offer to drill down."*
   - Remove or gate the `<!--SQL_APPROVAL_REQUEST-->` block in §4: for voice, use verbal
     confirmation ("Shall I run that query?") or skip altogether and rely on pre-baked
     `get_*_summary` tools.
   - Keep all RBAC, JWT, and RLS rules unchanged — they're not display logic.

2. **Thread the voice SOP into the agent.** The `agent_invocation` already has an
   `enhanced_prompt` pass-through hook. Load the SOP based on a mode flag in
   `unicorn_rental_agent.py`:
   ```python
   def load_system_prompt(voice: bool = False):
       key = "sops/unicorn_rental_voice.sop.md" if voice else os.getenv("SOP_S3_KEY", "sops/unicorn_rental_analytics.sop.md")
       # ... existing load logic
   ```
   Or accept a `"mode": "voice"` key in the payload and swap the system prompt dynamically
   inside `voice_agent_invocation`.

3. **Upload the voice SOP to S3** alongside the existing one (same bucket, different key).

4. **Test verbally:** Ask "What's the revenue breakdown by tier?" — confirm you hear a sentence
   like "Premium rentals led with $42k, standard underperformed at $18k" rather than a markdown
   table. Also ask a follow-up ("why is standard low?") to confirm conversational flow works.

**Done when:** Responses sound natural aloud and contain no markdown artifacts.

---

## Milestone 4 — Demo Polish

**Goal:** Record a 3–5 min demo video showing the full golden path.

### Tasks

1. **Session / memory threading** (spec §7.5):
   Extract Pipecat's session ID (from `runner_args` or the Daily room name) and pass it as
   `session_id` in the agent payload so `MemoryHookProvider` links turns across the
   conversation:
   ```python
   payload = {"prompt": user_text, "gateway_token": token, "session_id": daily_room_id}
   ```

2. **Latency tuning** (spec §7.6):
   - Confirm Strands is on `claude-haiku-4-5` for the voice path (faster than the workshop's
     Opus — set `BEDROCK_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0` in the agent's
     deployment env).
   - Enable `USE_FLUX=true` for better turn-taking detection.
   - Monitor per-stage timings via `enable_metrics=True`; investigate any stage >2s.
   - Add a brief filler phrase if first-token latency exceeds ~2s (Pipecat supports injecting
     an `LLMRunFrame` with interim text before the real response arrives).

3. **React client adaptation** (spec §6):
   `resources/aws-deepgram-sa-hackathon/client/` is the starting point. Minimal changes:
   - Point `VITE_API_URL` at `server/bot.py`'s endpoint.
   - Remove the weather-demo UI elements.
   - Add a "Connected to analytics agent" status indicator.

4. **Demo script** (3–5 min):
   ```
   00:00  Open the voice client, connect
   00:20  "What's the revenue breakdown by rental tier this month?"
          → agent summarizes in 2 sentences
   01:00  "Why is the standard tier underperforming?"
          → agent drills down conversationally
   01:40  "Can you book a premium unicorn for tomorrow afternoon?"
          → agent uses create_booking tool, confirms verbally
   02:20  "Actually, make it Thursday instead."
          → follow-up handled via session memory
   03:00  Wrap
   ```

5. **Record** with screen + mic audio. Ensure no API keys visible on screen.

---

## File structure after all milestones

```
voice-analytics-agent/
├── server/
│   ├── bot.py                         # Pipecat pipeline (STT → AgentCore → TTS)
│   ├── auth.py                        # Cognito ROPC token helper
│   ├── unicorn_rental_voice.sop.md    # voice-variant SOP
│   ├── .env.example
│   └── pyproject.toml
├── client/                            # adapted from resources/aws-deepgram-sa-hackathon/client/
│   └── ...
└── specs/
    ├── voice-integration.md
    └── implementation-plan.md         # this file
```

The `resources/agentic-analytics-workshop/app/agentcore_strands/unicorn_rental_agent.py` is
edited (or a copy deployed alongside it) to add `voice_agent_invocation` and voice SOP
loading. The existing UI entrypoint is not disturbed.

---

## Risk register

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| ROPC not enabled on the Cognito app client | Medium | Enable in AWS console or use client-credentials flow for a service account |
| `AWSAgentCoreProcessor` response shape differs from spec — wrong dict key | Medium | Check pipecat-ai[aws] source / pipecat-docs MCP before writing the entrypoint wrapper |
| AgentCore doesn't support named/secondary entrypoints | Low | Use `"mode": "voice"` branch in existing entrypoint instead |
| Analytics tool latency >3s makes TTS feel broken | High | Use Haiku, `USE_FLUX`, summarize tool output, add filler phrase |
| `<!--SQL_APPROVAL_REQUEST-->` leaks into TTS | High | Voice SOP explicitly forbids the pattern; verify in Milestone 3 testing |
