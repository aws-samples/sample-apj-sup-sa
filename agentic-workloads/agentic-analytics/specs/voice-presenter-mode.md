# Voice Analytics Agent — Presenter Mode Specification

**Version:** 1.0
**Status:** Working draft — post-research (Pipecat 1.3.0 RTVI, AgentCore Code Interpreter charts, Pipecat Cloud hosting + JWT-gated start proxy, dashboard voice integration).
**Date:** 2026-06-12
**Editor:** Yudho Diponegoro
**Team:** Agentic Analytics (AWS + Deepgram Voice AI Hackathon)
**Namespace:** `voice.agentic-analytics`

---

## Abstract

The Voice Analytics Agent adds a real-time **voice presenter** layer to the existing agentic-analytics "Timely-Unicorn" text-to-SQL dashboard. A business user talks to their data: they ask a question by voice or text, a Strands agent on Amazon Bedrock AgentCore runs analytics tools (pre-baked SQL, custom SQL with human approval, the booking API, and a new chart generator), and the agent responds **like a presenter** — it *speaks* a short conversational summary aloud while *displaying* the full answer (formatted text, tables, SQL-approval cards, and charts) in the dashboard. The spoken track and the visual track are produced from one agent turn but delivered on two channels.

**Voice is a mode, not a separate app.** The user toggles a single mic/speaker control in the existing dashboard. When **Voice Mode is ON**, a WebRTC transport is established, the agent listens continuously, and responses are split into spoken + displayed tracks. When **OFF**, there is no transport and the dashboard behaves exactly as the text-only product does today. Text input works in both modes; voice input works only when ON.

**Architecture.** The dashboard (React, hosted on AWS Amplify) embeds a Pipecat JS client. The Pipecat **bot** (the STT→agent→TTS pipeline) is hosted on **Pipecat Cloud**. A thin **JWT-gated `/start` proxy** (API Gateway + Lambda) authenticates the user's Cognito session, holds the Pipecat public key server-side, and starts a voice session — passing the user's identity to the bot so the same JWT/RLS/RBAC that governs text queries also governs voice queries. The bot uses **Deepgram** for STT/TTS and invokes the **already-deployed AgentCore Strands agent** as its reasoning step. The agent is unchanged in kind from the text product; it gains presenter-aware output formatting, filler/interim messaging, a chart tool, and structured split-output markers.

This document specifies, for each persona and functional area, the user stories, system contracts, acceptance criteria, and observable behaviours that any compliant implementation MUST exhibit. It is the canonical input for implementation and E2E test authoring.

## Status of this memo

This document is an industrial-style application specification. It uses the requirement levels of [RFC 2119] as updated by [RFC 8174]: **MUST**, **MUST NOT**, **REQUIRED**, **SHALL**, **SHALL NOT**, **SHOULD**, **SHOULD NOT**, **RECOMMENDED**, **MAY**, **OPTIONAL** are to be interpreted as in BCP 14 when in all caps.

It supersedes neither `specs/voice-integration.md` (the original cascaded-pipeline design) nor `specs/implementation-plan.md` (milestones 1–4, now substantially delivered). It builds on both: this is the "presenter mode + production hosting + dashboard UI" evolution. Where this document and the earlier ones conflict, this document is authoritative for presenter-mode behaviour.

---

## Table of Contents

1. Introduction
2. Conventions and Terminology
3. System Overview
4. Personas, Roles, and Scopes
5. Voice Mode State Machine
6. The Split-Output Contract (Spoken vs Displayed)
7. The Voice SOP (Agent Instruction Contract)
8. Transport and Session Lifecycle
9. Authentication and JWT Propagation
10. The `/start` Proxy
11. Filler and Progress Messaging
12. Interruption (Barge-in) Handling
13. Chart Generation (AgentCore Code Interpreter)
14. SQL Approval in Voice Mode
15. The RTVI Message Catalog (server↔client)
16. Dashboard UI Integration
17. Hosting and Deployment (Pipecat Cloud)
18. Latency Budget
19. Error Model
20. Security Considerations
21. Accessibility and Theming
22. Risks and Accepted Tradeoffs
23. Acceptance Criteria
24. Appendix A: Test Scenario Index
25. Appendix B: Rejected Alternatives
26. Document History

---

## 1. Introduction

The text-only dashboard (vendored at `resources/agentic-analytics-workshop/app/ui/`) already lets a user chat with the analytics agent and renders streamed markdown, tool chips, SQL-approval cards, and a context-driven side panel (Revenue / Bookings / Customers / Unicorns). The voice bot (built in `server/`) already runs a cascaded Deepgram→AgentCore→Deepgram pipeline and answers in short spoken sentences via a voice-variant SOP.

What is missing — and what this spec defines — is the **convergence**: one UI where voice and visuals coexist, where the agent behaves like a presenter (speaks the narrative, shows the data), where long operations are covered by spoken fillers, where the user can barge in naturally, where charts are generated and displayed, and where the whole thing is hosted and secured for production rather than run from a laptop.

The guiding experience principle: **talking to this agent must be wonderfully convenient — responsive, natural with voice, yet complete with visuals.** The agent is a presenter who speaks while sharing a screen. What it *says* is the conversational narrative; what it *shows* is the formal answer and the data.

## 2. Conventions and Terminology

### 2.1 Requirement levels

Per BCP 14 [RFC 2119] [RFC 8174], as in §Status.

### 2.2 Principal terms

- **Voice Mode** — A per-session UI state, toggled by the user, in which a WebRTC transport is active, the agent listens continuously, and agent output is split into spoken + displayed tracks. Its complement is **Text Mode** (no transport).
- **Presenter behaviour** — The agent's split-output behaviour: a short *spoken track* (conversational narrative, verbal number forms) plus a complete *displayed track* (formal text, tables, cards, charts). Active only in Voice Mode.
- **Spoken track** — The portion of an agent turn that is synthesized to audio by TTS. Conversational, 1–3 sentences, no markdown, verbal numbers ("ninety-nine"), no tables/UUIDs/SQL.
- **Displayed track** — The portion rendered in the dashboard chat + side panels: full formal text, markdown tables, SQL-approval cards, chart images. Includes a textual echo of the spoken narrative so a user scrolling back sees what was said.
- **Split marker** — The structured convention by which the agent demarcates spoken vs displayed content within one response (§6).
- **Bot** — The Pipecat Python application (`server/bot.py`): transport.input → STT → user aggregator → AnalyticsAgentCoreProcessor → TTS → transport.output. Hosted on Pipecat Cloud.
- **Agent** — The deployed Strands agent on AgentCore Runtime (`resources/.../unicorn_rental_agent.py`). The bot's reasoning step; unchanged in kind from the text product.
- **`/start` proxy** — A JWT-gated HTTP endpoint that authenticates the user and starts a Pipecat Cloud voice session, holding the Pipecat public key server-side (§10).
- **Filler** — A short spoken phrase ("let me pull that up…") emitted before/while a slow tool runs, to keep the conversation responsive (§11).
- **Barge-in** — The user speaking while the bot is talking; MAY interrupt the bot (§12).
- **RTVI** — Real-Time Voice Interaction protocol; the Pipecat server↔client message layer carried over the WebRTC data channel (§15).
- **Server message** — An RTVI message pushed bot→client carrying structured data (a table, card, or chart) for the UI to render WITHOUT it being spoken.
- **gateway_token** — The Cognito **access token** carrying `custom:role` + `custom:account_id`; required by the agent for MCP Gateway auth, Cedar RBAC, and Postgres RLS.

### 2.3 Namespace and message types

RTVI message `type` discriminators live under the flat namespace listed in §15. Split markers use the convention in §6.

### 2.4 Time

Timestamps in payloads are RFC 3339 UTC millisecond precision. Latency budgets (§18) are wall-clock at the client.

## 3. System Overview

### 3.1 Architecture

```
┌──────────────── Browser: Timely-Unicorn dashboard (AWS Amplify) ─────────────────┐
│  React (CRA + MUI) + @pipecat-ai/client-js + client-react + daily-transport       │
│  ┌── ChatPanel ──────────────┐   ┌── Side panels ───────────────────────────┐    │
│  │  text input + 🎤 toggle    │   │  Revenue / Bookings / Customers / Unicorns│    │
│  │  spoken-echo bubbles       │   │  + ChartCard + SqlApprovalCard            │    │
│  └────────────────────────────┘   └───────────────────────────────────────────┘   │
└───────────┬───────────────────────────────────────────────┬──────────────────────┘
            │ (1) POST /voice/start  (Cognito JWT)            │ (5) WebRTC media + RTVI
            ▼                                                 │     (Daily)
   ┌─────────────────────────┐                               │
   │ /start proxy            │  (2) start w/ secret pk_       │
   │ API GW (Cognito authz)  │ ─────────────────────────────▶│
   │ + Lambda (holds pk_)    │     Pipecat Cloud /start       │
   └─────────────────────────┘                               ▼
            │ (3) body: {gateway_token, id_token, user_id}   ┌──────────────────────────┐
            └───────────────────────────────────────────────│ Pipecat Cloud: the BOT   │
                                                             │ STT→Processor→TTS        │
                                                             │  ▲ Deepgram   ▲ Deepgram │
                                                             └──┬───────────────────────┘
                                       (4) invoke_agent_runtime │ {prompt, gateway_token,
                                           + runtimeSessionId   │  session_id, mode:voice}
                                                                ▼
                                          ┌──────────────────────────────────────────┐
                                          │ AgentCore Runtime — Strands agent (v2+)   │
                                          │  voice SOP + split output + filler hints  │
                                          │  + chart tool (Code Interpreter)          │
                                          │  → MCP Gateway (Cedar/JWT) → Lambda tools  │
                                          │  → Aurora (RLS)  + AgentCore Memory        │
                                          └──────────────────────────────────────────┘
```

### 3.2 Channels

A voice turn uses three logical channels, all over the one WebRTC connection:

1. **Audio** — mic in, speaker out (Deepgram STT/TTS).
2. **RTVI server messages** — bot→client structured visuals (tables, cards, charts). Never spoken.
3. **RTVI client messages** — client→bot typed input and UI events (e.g. SQL approval click, "user typed text").

### 3.3 What is reused vs new

| Component | Source | Status |
|---|---|---|
| AgentCore Strands agent + 33 tools + RLS + memory | `resources/.../unicorn_rental_agent.py` | reuse; extend with chart tool + presenter SOP |
| Cascaded bot (STT→processor→TTS) | `server/bot.py`, `server/analytics_processor.py` | extend for split output, fillers, interruption gating, RTVI messages |
| Voice SOP | `server/unicorn_rental_voice.sop.md` | extend to presenter contract (§7) |
| Dashboard chat + panels | `resources/.../app/ui/` | extend with voice toggle + Pipecat client + ChartCard |
| Pipecat Cloud hosting | — | **new** |
| `/start` proxy (JWT-gated) | — | **new** |
| Chart generation (Code Interpreter) | — | **new** |
| Split-output contract | — | **new** |

## 4. Personas, Roles, and Scopes

| Persona | Description | Voice rights |
|---|---|---|
| **Authenticated analyst** | Cognito user, `custom:role=analyst`, read-only analytics | MAY use Voice Mode; MUST NOT trigger write tools (booking) — Cedar blocks |
| **Rental admin** | `custom:role=rental_admin`, full access incl. `create_booking` | MAY use Voice Mode and all tools |
| **Staff** | `custom:role=staff`, scoped writes | MAY use Voice Mode; Cedar governs tool set |
| **Unauthenticated visitor** | not logged in | MUST NOT start a voice session (`/start` proxy returns 401) |

The voice layer MUST NOT widen any user's tool access. Whatever a role can do by text, it can do by voice; whatever it cannot, voice cannot either. This is enforced by JWT propagation (§9), not by the voice layer.

## 5. Voice Mode State Machine

### 5.1 States

- **OFF** (default on page load): no transport, no mic capture, no TTS. Text chat works normally.
- **CONNECTING**: user toggled ON; `/start` proxy called; awaiting transport ready.
- **ON**: transport ready; agent listening continuously; responses split into spoken + displayed.
- **DISCONNECTING**: user toggled OFF or session error; tearing down transport.

### 5.2 Transitions and rules

- The toggle control (§16) MUST move OFF→CONNECTING on user click, and ON/CONNECTING→DISCONNECTING on a second click.
- CONNECTING MUST reach ON only after (a) `/start` returns a room + token, (b) the Pipecat client connects, (c) the client receives the RTVI `bot-ready` event. On any failure it MUST return to OFF and surface an error (§19).
- ON→DISCONNECTING MUST stop mic capture immediately, cancel any in-flight bot speech, leave the Daily room, and release the microphone device.
- When OFF, the implementation MUST NOT hold the microphone, MUST NOT keep a WebRTC connection, and MUST NOT incur Daily/Deepgram cost.
- Text input MUST be available in every state. Voice input (mic capture) MUST be active ONLY in ON.
- The mode MUST be per-session and MUST default to OFF on every fresh page load (no auto-start).

### 5.3 Always-listening semantics

In ON, the agent listens continuously (no push-to-talk). Turn boundaries are decided by the STT/turn strategy (§12). The user MAY speak any number of turns until they toggle OFF.

## 6. The Split-Output Contract (Spoken vs Displayed)

This is the heart of presenter mode. The agent MUST, when invoked in voice mode, produce a response that the bot can route into a spoken track and a displayed track.

### 6.1 Mechanism (agent-emitted markers)

The agent MUST wrap the spoken narrative in an explicit marker at the **start** of its response so the bot can extract it deterministically as the stream begins (low latency to first audio):

```
<speak>Premium rentals led this quarter at about forty-three thousand dollars, while the standard tier lagged. Want me to break it down?</speak>
The full displayed answer follows here as normal markdown / tables / etc.
```

- The agent MUST emit exactly one `<speak>...</speak>` block, and it MUST be the first content in the response.
- The text inside `<speak>` is the **spoken track**: 1–3 conversational sentences, verbal number forms, no markdown, no tables, no UUIDs/SQL/column names.
- Everything after `</speak>` is the **displayed track**: the full, formal answer — markdown, tables, etc.
- The agent MUST also include, in the displayed track, a short textual echo of the spoken narrative (so a user scrolling the chat sees what was said). The echo MAY be the same sentences in digit form (e.g. "$43,000" instead of "forty-three thousand dollars").

> Rationale: a leading, single, well-known delimiter lets the bot start TTS on the spoken track the instant `</speak>` arrives, without waiting for the full response. Marker choice (`<speak>`) is XML-like per the user's "some xml" intent and is unlikely to collide with analytics content; the agent is instructed never to use it elsewhere. See §7.

### 6.2 Bot routing

The `AnalyticsAgentCoreProcessor` (or a dedicated splitter `FrameProcessor` downstream of it) MUST:

1. Buffer the streamed agent output until `</speak>` is seen.
2. Route the `<speak>` inner text to **TTS** (spoken track) AND emit it to the client as a chat bubble (the spoken echo) via `onBotTtsText` mirroring or an RTVI `spoken-text` message.
3. Route the post-`</speak>` content to the client as the **displayed track** — NOT to TTS. It MUST set `TextFrame.skip_tts` (or push it as an RTVI server message / a non-TTS text frame) so Aura-2 never reads the tables aloud.
4. If no `<speak>` block is present (agent non-compliance), the bot MUST fall back to speaking the first sentence of the response and displaying the whole thing — never read the entire markdown aloud. The existing `MarkdownTextFilter` on the TTS service remains as a second line of defense.

### 6.3 Tables, cards, charts are display-only

Markdown tables, the SQL-approval card, and chart images MUST NOT be spoken. The agent MUST NOT place them inside `<speak>`. The bot MUST guarantee (via skip_tts + MarkdownTextFilter) that even if the agent errs, structured/markdown content is not synthesized.

### 6.4 Text Mode behaviour

When the agent is invoked NOT in voice mode (text-only dashboard chat, or `mode != voice`), it MUST NOT emit `<speak>` markers and MUST produce the normal full markdown answer as the text product does today. The split contract is voice-mode-only.

## 7. The Voice SOP (Agent Instruction Contract)

The agent's behaviour is governed by a per-request system prompt (SOP) loaded from S3 by key (`sop_s3_key`), already wired. Presenter mode requires a **presenter SOP** (extend `server/unicorn_rental_voice.sop.md`) that instructs the agent to:

1. **Split output** per §6: emit one leading `<speak>…</speak>` with a 1–3 sentence conversational narrative using verbal numbers, then the full displayed answer; include a short spoken echo in the displayed track.
2. **Never speak** tables, SQL, UUIDs, column names, account_id, or markdown syntax.
3. **Use fillers** (§11): when about to call a slow tool, first emit a brief spoken filler via the designated mechanism so the user knows work is in progress.
4. **Charts** (§13): when a trend/comparison/breakdown would be clearer visually, call the chart tool and reference the chart in the spoken narrative ("I've put a chart on screen showing…").
5. **SQL approval** (§14): present the approval card in the displayed track and speak a short "Here's the query plan — shall I run it?" in the spoken track; do not read the SQL aloud.
6. **Preserve all security rules** unchanged: RBAC, JWT, RLS, the `current_datetime`-first rule for relative dates, no fabricated IDs. The presenter SOP changes only *presentation*, never *authorization*.

The presenter SOP MUST keep the text SOP (`unicorn_rental_analytics.sop.md`) intact for the text product; presenter behaviour is selected per-request by `sop_s3_key` + a `mode: "voice"` payload flag.

## 8. Transport and Session Lifecycle

- Transport is **Daily WebRTC**, established only in Voice Mode ON.
- The browser MUST obtain the Daily room URL + short-lived room token from the `/start` proxy (§10) — it MUST NOT hold the Daily API key or the Pipecat public key.
- A stable `runtimeSessionId` (≥33 chars) MUST be threaded from the bot into every `invoke_agent_runtime` call for the duration of the voice session, so AgentCore Memory links turns (already implemented; presenter mode MUST preserve it).
- On DISCONNECTING the client MUST leave the room; the bot session on Pipecat Cloud ends when the room empties (idle timeout) or on explicit cancel.

## 9. Authentication and JWT Propagation

### 9.1 Requirement

The voice path MUST use the **logged-in user's** Cognito identity, not a fixed demo user. The same `gateway_token` (access token) that authorizes the text chat MUST authorize voice queries, so RLS/RBAC are identical across modes.

### 9.2 Flow

1. The dashboard already holds the user's Cognito tokens (`authService.js`: `fetchAccessToken()` = access token = `gateway_token`; `fetchIdToken()`).
2. On voice start, the browser sends its Cognito JWT to the `/start` proxy.
3. The proxy verifies the JWT, then includes the user's `gateway_token` (and `id_token`/`user_id` as needed) in the Pipecat `/start` **body**.
4. Pipecat forwards the body to the bot as `runner_args.body`.
5. The bot reads `runner_args.body["gateway_token"]` and uses it as the `gateway_token` in `invoke_agent_runtime` — replacing the fixed-demo-user ROPC path for hosted/production use.

### 9.3 Token lifetime

Cognito access tokens are short-lived (≈1 hour). For long voice sessions the implementation SHOULD support a client→bot RTVI `refresh-token` message carrying a fresh `gateway_token`, OR the bot SHOULD re-mint via a refresh token if provided. For the hackathon demo, a single token per session is acceptable; the spec marks token refresh as SHOULD, not MUST.

### 9.4 Local/dev fallback

For local development without the proxy, the bot MAY retain the ROPC demo-user path (`auth.py`) gated behind an env flag. Production MUST use propagated user identity.

## 10. The `/start` Proxy

### 10.1 Contract

`POST /voice/start`

- **AuthN**: a Cognito JWT (API Gateway Cognito JWT authorizer, or in-Lambda JWKS verification). Missing/invalid → `401`.
- **Request body**: `{}` (identity comes from the verified JWT) or optional `{ "session_hint": string }`.
- **Behaviour**: the Lambda holds `PIPECAT_PUBLIC_API_KEY` (server-side secret), extracts `gateway_token`/`id_token`/`sub` from the verified token context, and calls Pipecat Cloud `POST https://api.pipecat.daily.co/v1/public/<agent>/start` with `Authorization: Bearer <pk_>` and body `{ "createDailyRoom": true, "body": { "gateway_token": ..., "id_token": ..., "user_id": ... } }`.
- **Response (200)**: `{ "dailyRoom": string, "dailyToken": string }` (or the room URL the Pipecat client expects). The browser joins that room; it never sees `pk_` or the Daily API key.
- **Rate limit**: per-user (keyed on `sub`), e.g. ≤ N sessions/minute, to bound credit-burn abuse (§20). Excess → `429`.

### 10.2 Placement

The proxy SHOULD be an **API Gateway HTTP API + Lambda** in the same account/region as the existing Cognito/Amplify/AgentCore infra, defined in the CloudFormation stack so it deploys with the backend. It MAY instead be added as a route on an existing API if one is reachable from the SPA.

### 10.3 Why a proxy (normative)

Pipecat's own docs state: *"keep your public API key server-side."* A `pk_` placed in the SPA is readable in dev-tools and can be replayed from anywhere (CORS does not stop non-browser clients). The proxy is therefore REQUIRED for any non-demo deployment: it keeps `pk_` server-side, gates start on a real Cognito login, attributes every session to a user, and rate-limits. See §20 and `specs/research/pipecat-cloud-deploy-and-ui-integration.md`.

## 11. Filler and Progress Messaging

### 11.1 Requirement

When the agent calls a tool that may take more than ~1.5 s (custom SQL, chart generation, multi-tool chains), the user MUST receive a spoken filler so the experience stays responsive, and SHOULD receive a visual progress indicator.

### 11.2 Mechanisms

- **Spoken filler**: within a tool/function flow, the bot emits `TTSSpeakFrame("Let me pull that up…")` before awaiting the slow call. Multiple distinct fillers MAY be used for very long operations ("still working… almost there").
- **Agent-driven filler**: the agent MAY also emit a short `<speak>` narrative acknowledging the request before tool results return, then a second turn with the answer. The presenter SOP instructs this for known-slow intents.
- **Visual progress**: the bot SHOULD push an RTVI server message `{ type: "progress", state: "running"|"done", label }` so the UI can show a "Running query…" chip (the dashboard already has tool chips — reuse them).

### 11.3 Constraints

- Fillers MUST be short and MUST NOT be mistaken for the answer.
- A filler MUST be followed by either the answer or an error within the latency ceiling (§18); a filler with no resolution is a bug.

## 12. Interruption (Barge-in) Handling

### 12.1 Requirement

The user MAY interrupt the bot while it is speaking. A genuine interruption MUST stop the bot's current speech promptly. Incidental noise MUST NOT.

### 12.2 Mechanism

- Interruptions are bounded by `UserStartedSpeakingFrame` / `UserStoppedSpeakingFrame`; a real interruption emits a single `InterruptionFrame` (SystemFrame) that clears pending bot output. (Pipecat 1.3.0 — note the old `StartInterruptionFrame` name is gone.)
- The **noise gate** is the user-turn-start strategy on `LLMUserAggregatorParams`: `MinWordsUserTurnStartStrategy(min_words=N)` requires N words to barge in *while the bot is speaking* (1 word otherwise). The implementation MUST set `N ≥ 2` so a cough/"mm-hm" does not cancel the bot.
- With Deepgram Flux (`USE_FLUX=true`), turn boundaries defer to Flux via `ExternalUserTurnStrategies()`; the gate is tuned via Flux `min_confidence` rather than Silero `stop_secs`.

### 12.3 Agent-side decision (SHOULD)

The user asked that the agent be able to decide whether an interruption is "truly an interruption or just some noise" and choose to continue. Full agent-level interruption arbitration is **out of scope for v1** (the turn-start strategy provides the practical noise gate). The implementation MAY, in a later version, forward a low-confidence interruption to the agent as a signal and let it decide whether to resume; v1 MUST at minimum implement the `min_words` gate so trivial noise does not cancel the bot.

## 13. Chart Generation (AgentCore Code Interpreter)

### 13.1 Requirement

The agent MUST be able to generate a chart from analytics data and display it in the dashboard. Charts are display-only (never spoken; the narrative references them).

### 13.2 Mechanism

- The agent gains a chart capability via the AgentCore **Code Interpreter** tool, wired exactly as the vendored example:
  ```python
  from strands_tools.code_interpreter import AgentCoreCodeInterpreter
  code_interpreter = AgentCoreCodeInterpreter(region=REGION, auto_create=True)
  agent = Agent(model=MODEL_ID, system_prompt=..., tools=[..., code_interpreter.code_interpreter])
  ```
  (`pip install strands-agents-tools`; the runtime `aws.codeinterpreter.v1` has matplotlib/numpy/pandas pre-installed.)
- The agent writes Python that renders a chart with the `Agg` backend and `plt.savefig`, then retrieves the bytes. Because `executeCode` returns stdout/stderr (not artifacts), the agent MUST print the image as **base64 to stdout with a sentinel** (e.g. `__CHART_PNG__<base64>__END__`) OR read the file back via the interpreter's `readFiles`. The agent MUST keep image size modest (≤ ~150 KB; 100–120 dpi).
- The code-interpreter **session SHOULD be reused** across turns (bind to the conversation) to keep follow-up renders warm; cold start is ~2–6 s, warm ~1 s (see `specs/research/agentcore-code-interpreter-charts.md`).

### 13.3 Surfacing to the UI

- **Default (RECOMMENDED): base64 PNG over RTVI.** The bot extracts the base64 chart from the agent stream and pushes an RTVI server message `{ type: "chart", mime: "image/png", b64: "...", caption: string }`. The UI renders it in a `ChartCard`. Keep total message < ~1 MB (data-channel limit, not the 100 MB inline cap).
- **For large/high-dpi images: S3 presigned URL.** The agent uploads to S3 (via `executeCommand aws s3 cp`) and the bot sends `{ type: "chart", url: "<presigned>", caption }`. v1 MAY implement only the base64 path; the URL path is a documented fallback.

### 13.4 Spoken reference

The spoken track MUST reference the chart conversationally ("I've put a revenue-by-tier chart on screen") and MUST NOT attempt to describe every data point. The chart caption carries the detail.

## 14. SQL Approval in Voice Mode

The text product uses an `<!--SQL_APPROVAL_REQUEST-->` card; the user clicks Approve/Edit/Cancel. In voice mode:

- The agent MUST present the approval **card** in the displayed track (the existing card), AND speak a short "Here's the query plan — shall I run it?" in the spoken track. The SQL itself MUST NOT be spoken.
- The user MAY approve by **voice** ("yes, run it") or by **clicking** the card. A voice "yes" MUST be routed to the same approval action as the click. The bot MUST map an affirmative voice turn following an approval prompt to a client→agent approval message equivalent to the card's Approve action.
- Decline/cancel MUST be similarly available by voice or click.
- This preserves the human-in-the-loop guarantee: SELECT-only, no write without approval.

## 15. The RTVI Message Catalog (server↔client)

All structured (non-audio) data crosses the WebRTC data channel as RTVI messages. The implementation MUST use a `type` discriminator. Server→client via `task.rtvi.send_server_message({...})` or `RTVIServerMessageFrame(data={...})`; received by the client via `RTVIEvent.ServerMessage` / `useRTVIClientEvent(RTVIEvent.ServerMessage, …)`. Client→server via `sendClientMessage(type, data)`; handled by `@task.rtvi.event_handler("on_client_message")`.

### 15.1 Server→client

| `type` | Payload | UI effect |
|---|---|---|
| `spoken-text` | `{ text }` | Render the spoken narrative as a chat bubble (echo). MAY instead use `onBotTtsText`. |
| `display-text` | `{ markdown }` | Render the full formal answer (markdown, tables) in chat. |
| `sql-approval` | `{ query_plan, explanation }` | Render the SQL-approval card (SQL hidden). |
| `chart` | `{ mime, b64 }` or `{ url }`, `{ caption }` | Render a ChartCard. |
| `progress` | `{ state, label }` | Show/clear a "Running …" chip. |
| `panel` | `{ panel: "revenue"\|"bookings"\|… }` | Switch the side dashboard panel (reuse `detectPanelContext`). |

### 15.2 Client→server

| `type` | Payload | Effect |
|---|---|---|
| `sql-approve` | `{ }` | Equivalent to clicking Approve on the current card. |
| `sql-decline` | `{ sql? }` | Decline / edit. |
| `sql-cancel` | `{ }` | Cancel. |
| `text-input` | `{ text }` | User typed instead of spoke; treat as a user turn. MAY use Pipecat `sendText(content, {audio_response})`. |
| `refresh-token` | `{ gateway_token }` | Provide a fresh Cognito token (long sessions, §9.3). |

## 16. Dashboard UI Integration

### 16.1 The toggle control

- The dashboard chat input row (`ChatPanel.js`, near the Send button) MUST gain a **mic/speaker toggle** button.
- One click: OFF→ON (enter Voice Mode). Second click: ON→OFF.
- The button MUST reflect state: idle (OFF), connecting (spinner), listening (active/animated), error.
- It MUST be disabled / hidden for unauthenticated users.

### 16.2 Pipecat client

- The dashboard MUST add `@pipecat-ai/client-js`, `@pipecat-ai/client-react`, `@pipecat-ai/daily-transport`.
- On OFF→ON it MUST call the `/start` proxy (with the Cognito JWT), then connect the Daily transport to the returned room.
- It MUST register RTVI event handlers per §15 and route messages into the existing render paths: spoken echo + display text into the chat (reuse `MarkdownContent`), `chart` into a new `ChartCard`, `sql-approval` into the existing `SqlApprovalCard`, `panel` into `detectPanelContext`'s panel switch.

### 16.3 Coexistence with text chat

- Text input MUST continue to call the agent the existing way (`awsAgentCore.js → invokeAgent`) when Voice Mode is OFF.
- When Voice Mode is ON, typed text SHOULD be routed through the voice session as a `text-input` RTVI message so the conversation (and memory/session) stays unified; OR it MAY continue via the direct invoke path with the same `session_id`. The implementation MUST keep one coherent conversation thread regardless of input modality.

### 16.4 Rendering parity

Voice-driven answers MUST render with the same components as text answers (markdown, tables, tool chips, approval cards, panels) plus charts. A user MUST be able to scroll back and read the full conversation including a textual echo of everything spoken (§6.1).

## 17. Hosting and Deployment (Pipecat Cloud)

- The bot MUST be deployable to **Pipecat Cloud** via `pcc-deploy.toml` (`agent_name`, `secret_set`, `agent_profile`, `[scaling] min_agents`) and `pipecat cloud deploy`.
- Secrets (`DEEPGRAM_API_KEY`, AWS creds for AgentCore invoke, and any model config) MUST be stored in a Pipecat Cloud **secret set** (`pipecat cloud secrets set`), never committed. `DAILY_API_KEY` is managed by Pipecat Cloud for the Daily transport.
- The deployed agent exposes the public `/start` shape; production access MUST go through the §10 proxy, not the public key directly.
- The existing local path (`uv run bot.py --transport daily`, `localhost:7860/client/`) MUST remain functional for development.
- `min_agents = 1` keeps one warm instance for low session-start latency during the demo; set to 0 when idle to save cost.

## 18. Latency Budget

Wall-clock at the client, voice turn (no chart): target **≤ 3 s** to first spoken audio; ceiling 4 s before a filler is mandatory.

| Stage | Target |
|---|---|
| STT final transcript | ≤ 0.5 s after user stops |
| Agent first `<speak>` token | ≤ 1.5 s (Haiku on the voice path RECOMMENDED) |
| TTS first audio | ≤ 0.5 s after `</speak>` |
| Tool-bearing turn | filler within 1.5 s; answer ≤ 8 s or progressive |
| Chart turn | filler MANDATORY; chart ≤ 8 s warm session |

The implementation MUST enable Pipecat metrics (`enable_metrics`) and SHOULD log per-stage timings. Where the agent's reasoning model is configurable, the voice path SHOULD use a low-latency model (Haiku) and reserve larger models for non-voice.

## 19. Error Model

| Condition | Behaviour |
|---|---|
| `/start` 401 | Toggle returns to OFF; UI shows "Please sign in to use voice." |
| `/start` 429 | Toggle returns to OFF; "Too many voice sessions, try again shortly." |
| Transport fails to connect | Return to OFF; "Couldn't start voice. Check your mic and retry." |
| Agent error mid-turn | The agent's error-fallback text MUST be spoken (the `{type:text,content:…}` shape is handled by `_extract_text`) — never silent. |
| Guardrail false-positive on in-scope query | Known accepted limitation (§22); the canned topic-rejection is spoken + displayed. |
| Token expired mid-session | Bot surfaces "Your session expired, please reconnect"; client returns to OFF (or refreshes per §9.3). |
| Chart generation fails | The agent MUST still answer in text+voice; it speaks "I couldn't render the chart, but here are the numbers" and displays the data. |

No error condition may leave the bot silent or the UI in a permanent CONNECTING state; both MUST resolve to a spoken+visible message or back to OFF.

## 20. Security Considerations

- **No Pipecat/Daily key in the browser.** Per Pipecat's own guidance and §10.3, the SPA holds neither the Pipecat public key nor the Daily API key. It receives only a short-lived Daily room token from the JWT-gated proxy.
- **JWT-gated start.** Only an authenticated Cognito user can start a session; sessions are attributed to `sub` and rate-limited to bound credit abuse.
- **Identity parity.** Voice queries run under the user's real `gateway_token`; RLS/RBAC/Cedar are identical to text. The voice layer MUST NOT bypass or widen authorization.
- **Secrets server-side.** Deepgram/AWS keys live in Pipecat Cloud secret sets; the proxy's `pk_` lives in Lambda config/secrets.
- **No data spoken that shouldn't be.** UUIDs, account_id, SQL, raw PII MUST NOT enter the spoken track (SOP + skip_tts + MarkdownTextFilter enforce this).
- **Guardrail.** The existing Bedrock Guardrail remains in force on the voice path (same agent).

## 21. Accessibility and Theming

- Voice MUST be additive, never required: every action available by voice MUST also be available by text + click. A user who cannot or will not speak retains full function.
- The spoken-echo bubbles (§6.1) provide a text record for users who are deaf/hard-of-hearing or who have audio off.
- The toggle control MUST be keyboard-operable and screen-reader-labeled (e.g. "Turn voice mode on").
- Charts MUST carry a text caption (§13.4) for non-visual access.

## 22. Risks and Accepted Tradeoffs

| Risk | Disposition |
|---|---|
| **Latency** on tool-heavy/chart turns | Mitigated by fillers (§11), warm code-interpreter session, Haiku on voice path. #1 risk per original spec. |
| **Agent non-compliance with `<speak>` markers** | Bot fallback (§6.2 step 4) + MarkdownTextFilter ensure tables are never read aloud even if the agent errs. |
| **Guardrail false-positives** on in-scope analytics queries | Accepted for the demo (user decision). Documented in §19; fixable later by relaxing the `DangerousAdvice`/`PROMPT_ATTACK` policy. |
| **Token expiry** in long sessions | v1 single-token-per-session; refresh is SHOULD (§9.3). |
| **Data-channel size** for charts | Keep base64 < ~1 MB; S3-URL fallback for large images (§13.3). |
| **Agent-level interruption arbitration** deferred | v1 uses `min_words` noise gate only (§12.3). |
| **Pipecat Cloud dependency** | Accepted; the bot remains portable (AgentCore Runtime / ECS are alternatives per `specs/voice-integration.md`). |

## 23. Acceptance Criteria

A compliant implementation MUST satisfy:

- **AC-1 (toggle)** Page loads in Text Mode; no mic held, no transport. Clicking the toggle enters Voice Mode (transport up, listening); clicking again fully tears down (mic released, room left).
- **AC-2 (split output)** In Voice Mode, asking "what's the revenue breakdown by tier this month?" results in: a ≤3-sentence spoken summary with verbal numbers AND a displayed markdown table; the table is NOT read aloud; a spoken echo appears as a chat bubble.
- **AC-3 (memory)** A follow-up ("why is the standard tier low?") is answered contextually using the prior turn (shared `runtimeSessionId`), spoken + displayed.
- **AC-4 (filler)** A custom-SQL or chart turn produces a spoken filler within 1.5 s, then the answer.
- **AC-5 (chart)** Asking for a trend/breakdown chart renders a ChartCard image in the UI; the spoken track references it; the chart is not "read."
- **AC-6 (SQL approval)** A custom-SQL query shows the approval card AND speaks "shall I run it?"; approving by voice OR click runs it; SQL is never spoken.
- **AC-7 (barge-in)** Speaking ≥2 words while the bot talks interrupts it; a single short noise does not.
- **AC-8 (auth parity)** An analyst's voice session cannot create a booking (Cedar blocks), identical to text. A rental_admin's can.
- **AC-9 (security)** No Pipecat/Daily key is present in the browser bundle or network calls; `/start` requires a valid Cognito JWT (401 without).
- **AC-10 (text parity)** With Voice Mode OFF, the dashboard behaves exactly as the text-only product; the agent emits no `<speak>` markers.
- **AC-11 (no silent failures)** Every error path resolves to a spoken+visible message or returns the toggle to OFF; never silent, never stuck in CONNECTING.
- **AC-12 (hosting)** The bot is deployable to Pipecat Cloud and reachable via the JWT-gated proxy; local `uv run bot.py` still works.

## 24. Appendix A: Test Scenario Index

| ID | Scenario | Verifies |
|---|---|---|
| VPM-1 | Toggle on→off lifecycle, mic/transport release | AC-1, §5 |
| VPM-2 | Revenue-by-tier: spoken summary + displayed table | AC-2, §6 |
| VPM-3 | Contextual follow-up across turns | AC-3, §8 |
| VPM-4 | Filler on custom-SQL turn | AC-4, §11 |
| VPM-5 | Chart request renders ChartCard + spoken reference | AC-5, §13 |
| VPM-6 | SQL approval by voice and by click | AC-6, §14 |
| VPM-7 | Barge-in: 2+ words interrupts, noise does not | AC-7, §12 |
| VPM-8 | Analyst cannot book by voice; admin can | AC-8, §9 |
| VPM-9 | No key in browser; `/start` 401 without JWT; 429 on abuse | AC-9, §10, §20 |
| VPM-10 | Voice OFF = text product unchanged | AC-10, §6.4 |
| VPM-11 | Agent error spoken not silent; chart-fail degrades gracefully | AC-11, §19 |
| VPM-12 | Pipecat Cloud deploy + proxy reachable; local still works | AC-12, §17 |

## 25. Appendix B: Rejected Alternatives

- **Pipeline in the browser (no bot).** Rejected: Pipecat's pipeline is Python/server-side; a browser reimplementation abandons Pipecat and leaks the Deepgram key (verified against docs.pipecat.ai).
- **`pk_` public key embedded in the SPA.** Rejected: Pipecat docs say keep it server-side; it's dev-tools-readable and replayable; CORS doesn't stop non-browser clients. Hence the §10 proxy.
- **Hosting the bot on the same AgentCore Runtime as the Strands agent.** Rejected for v1: different workload shape (long streaming session vs short invoke). AgentCore Runtime *can* host a Pipecat bot (AWS blog, Mar 2026) as a *separate* runtime; kept as a documented future option. Pipecat Cloud chosen for lowest-effort production hosting.
- **Speech-to-speech (Nova Sonic).** Rejected per original spec: tool-calling reliability; cascaded keeps a strong text LLM doing tools.
- **Reading tables aloud / no split.** Rejected: poor UX; the presenter split is the core value.
- **Separate voice app instead of dashboard integration.** Rejected: the goal is voice + data in one screen (the dashboard already has the panels).

## 26. Document History

- **1.0 (2026-06-12)** — Initial presenter-mode spec. Synthesizes `specs/voice-integration.md` + `specs/implementation-plan.md` + user requirements (split output, fillers, interruption, charts, JWT propagation, Pipecat Cloud hosting, JWT-gated start) + research in `specs/research/`.
