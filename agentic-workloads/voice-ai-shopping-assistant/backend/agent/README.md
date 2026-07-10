# Voice Agent (pipecat on AgentCore Runtime) (`AgentStack`)

**Deploys:** `AgentStack` (ECR image + AgentCore Runtime + Memory + runtime IAM
role; resources defined in `infra/lib/agent-stack.ts`). `contracts.py` is the
shared wire contract — change it only via a coordinated `CONTRACT_VERSION` bump.

## Build
- ARM64 container, host `0.0.0.0:8080`, endpoints `/ws` (voice) + `/ping` (health),
  via `BedrockAgentCoreApp` + `@app.websocket`.
- `pipeline.py` — cascaded pipecat pipeline (mirror reference `tavus-pipecat.py`):
  `transport.input → Deepgram Nova-3 STT → user_transcript → ctx.user → Bedrock
  Claude Haiku 4.5 → agent_transcript → Deepgram Aura-2 TTS → transport.output → ctx.assistant`.
  `audio_in_sample_rate=16000`, `audio_out_sample_rate=24000`, interruptions on,
  Silero VAD + LocalSmartTurnAnalyzerV3.
- `transports.py` — **NET-NEW, highest risk.** Bridge AgentCore `/ws` ⇄ pipecat frames:
  inbound binary PCM16/16k → `InputAudioRawFrame`; outbound `TTSAudioRawFrame` →
  `websocket.send_bytes`; transcript/tool/state/error JSON → `websocket.send_text`.
- `forwarders.py` — `UserTranscriptForwarder` / `AgentTranscriptForwarder` (from reference),
  emitting §3.4 `transcript` events.
- `tools.py` — `FunctionSchema` defs; handlers call Gateway MCP (`GATEWAY_MCP_URL`),
  forward results as §3.4 `tool_result` events.
- `prompts.py` — home vs store system prompts (mode from `init` message).
- AgentCore **Memory** keyed by `session_id`.

Emit WS events per the shapes documented in `contracts.py`.

## Env (from Secrets Manager / SSM)
`DEEPGRAM_API_KEY`, `AWS_REGION`, `BEDROCK_MODEL_ID`, `GATEWAY_MCP_URL`, `MEMORY_ID`.
AWS creds come from the runtime IAM role — never set keys.

## Exports (SSM)
`/aisle/agent/runtime_arn`, `/aisle/agent/memory_id`

## Verify
`python main.py`; connect `ws://localhost:8080/ws`; send `init` + PCM16/16k audio;
expect transcript events + audio out + a `tool_result`.
