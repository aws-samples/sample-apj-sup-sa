# Pipecat Cloud Deploy + Existing-UI Voice Integration — Research

Research for (A) deploying the Pipecat bot to Pipecat Cloud with a secure JWT-gated
`/start`, and (B) wiring voice into the EXISTING agentic-analytics React dashboard.

Sources: Pipecat docs (`docs.pipecat.ai/llms.txt` and pages below), and local files in
`resources/`. The `pipecat-docs` MCP requires interactive OAuth (not completed in this
session); doc facts below are from the public `.md` doc pages, which are authoritative.

Key doc pages used:
- https://docs.pipecat.ai/pipecat-cloud/fundamentals/deploy.md
- https://docs.pipecat.ai/pipecat-cloud/fundamentals/active-sessions.md
- https://docs.pipecat.ai/api-reference/cli/cloud/secrets.md
- https://docs.pipecat.ai/api-reference/server/utilities/runner/guide.md

---

## 1. Pipecat Cloud deploy

### 1.1 What's in `pcc-deploy.toml`

File: `resources/aws-deepgram-sa-hackathon/server/pcc-deploy.toml`

```toml
agent_name = "aws-deepgram-sa-hackathon"
secret_set = "aws-deepgram-sa-hackathon-secrets"
agent_profile = "agent-1x"
[krisp_viva]
    audio_filter = "tel"
[scaling]
    min_agents = 1
```

- `agent_name` — the deployed agent's name; becomes the path segment in the public start URL.
- `secret_set` — name of the server-side secret bundle injected as env vars into the
  running bot (see 1.2). NOT the secret values — just a reference.
- `agent_profile` — `agent-1x` (default: 0.5 vCPU / 1 GB; voice agents). Others: `agent-2x`
  (1 vCPU / 2 GB), `agent-3x` (1.5 vCPU / 3 GB).
- `[scaling] min_agents = 1` — keeps 1 warm instance (avoids cold-start latency; costs while
  idle). The schema also supports `max_agents`. Other top-level fields supported by the
  schema: `image`, `image_credentials` (private registry), `region` (e.g. `us-west`),
  `websocket_auth` (default `"none"`).
- `[krisp_viva] audio_filter = "tel"` — Krisp noise cancellation (telephony profile). The
  bot loads `KrispVivaFilter` (see `bot.py` line ~200).

### 1.2 How secrets are stored server-side

Secrets live in a **secret set** in Pipecat Cloud, referenced by `secret_set` in the toml.
They are NEVER committed; you push them via CLI and the platform injects them as env vars.

Create / update (inline):
```shell
pipecat cloud secrets set aws-deepgram-sa-hackathon-secrets \
  'DAILY_API_KEY=xxx DEEPGRAM_API_KEY=yyy AWS_ACCESS_KEY_ID=zzz AWS_SECRET_ACCESS_KEY="..." AWS_REGION=us-east-1 AWS_BEDROCK_MODEL=us.anthropic.claude-haiku-4-5-20251001-v1:0'
```
- Multiple pairs in one call; quote values with spaces.
- `name` must be a valid identifier (letters, numbers, hyphens) and should match `secret_set`.

From a file (cleanest — reuse `.env`):
```shell
pipecat cloud secrets set aws-deepgram-sa-hackathon-secrets --file .env   # -f short flag
```
Each line is `KEY=value`.

List (values stay hidden; shows readiness `ready|pending|failed`):
```shell
pipecat cloud secrets list aws-deepgram-sa-hackathon-secrets
```

So `DAILY_API_KEY`, `DEEPGRAM_API_KEY`, and AWS creds all go into the secret set and reach
the bot as `os.environ[...]` — exactly the vars `bot.py` already reads.

### 1.3 Deploy commands

```shell
# from server/ (where pcc-deploy.toml lives)
pipecat cloud deploy                       # builds in the cloud + deploys (no local registry needed)
pipecat cloud deploy --region us-east      # pick region
pipecat cloud deploy --profile agent-2x    # override profile
pipecat cloud deploy --min-agents 2        # override scaling
pipecat cloud agent status aws-deepgram-sa-hackathon   # check status
pipecat cloud agent delete aws-deepgram-sa-hackathon   # tear down
```
The CLI handles the container build/push automatically. The `Dockerfile`
(`FROM dailyco/pipecat-base:latest`, `uv sync`, `COPY ./bot.py`) is the build context.

### 1.4 Public START URL a deployed agent exposes

```
https://api.pipecat.daily.co/v1/public/{agent_name}/start
```
For this agent:
```
https://api.pipecat.daily.co/v1/public/aws-deepgram-sa-hackathon/start
```
- Auth: `Authorization: Bearer <PUBLIC_API_KEY>`. Public API keys authorize who may start
  sessions and can be revoked/cycled.
- Request body (Daily transport): `{"createDailyRoom": true, "body": {...custom data...}}`.
- Response: `dailyRoom` (room URL) + `dailyToken`. Join URL = `f"{dailyRoom}?t={dailyToken}"`.
- Body size limit: 1 MB for daily/webrtc (4 KB websocket); over → `413`.

NOTE: the starter client (`client/src/config.ts`) points `VITE_BOT_START_URL` at the bot's
own `/start` (default `http://localhost:7860/start` for local dev). For Pipecat Cloud you
either point it at the public URL above with the public key in the `Authorization` header
(key exposed in browser — fine for a demo, NOT for prod), OR proxy it (section 2).

---

## 2. Secure JWT-gated `/start` proxy

Pipecat's docs explicitly recommend keeping the public API key server-side: "Provide a
secure endpoint to receive requests that keeps your API key secret." The browser hits YOUR
endpoint; YOUR endpoint holds the public key and calls Pipecat Cloud.

### 2.1 Smallest proxy design

One endpoint, e.g. `POST /voice/start`:

1. **(a) Verify the Cognito JWT.** The dashboard already obtains Cognito tokens
   (`authService.js`). The browser sends its `access_token` (and/or `id_token`) as
   `Authorization: Bearer <jwt>`. The proxy validates it against the Cognito User Pool JWKS
   (`https://cognito-idp.{region}.amazonaws.com/{userPoolId}/.well-known/jwks.json`):
   verify signature, `iss`, `aud`/`client_id`, `exp`. Extract claims `sub`, `custom:role`,
   `custom:account_id`.
2. **(b) Call Pipecat Cloud `/start`** with the SECRET public key:
   `POST https://api.pipecat.daily.co/v1/public/aws-deepgram-sa-hackathon/start`
   header `Authorization: Bearer <PIPECAT_PUBLIC_API_KEY>` (held only server-side, e.g. in a
   Secrets Manager / Lambda env var).
3. **(c) Pass user identity to the bot** via the start `body` (section 3):
   ```json
   {
     "createDailyRoom": true,
     "dailyRoomProperties": { "start_video_off": true },
     "body": {
       "gateway_token": "<user Cognito access_token>",
       "id_token": "<user Cognito id_token>",
       "user_id": "<sub>",
       "account_id": "<custom:account_id>",
       "role": "<custom:role>"
     }
   }
   ```
   (Forwarding the user's own token means the bot acts as the real user, not a fixed demo
   user. Caveat: token must outlive the call; tokens are short-lived, so the bot should use
   it promptly. Consider scoping/refresh if voice sessions run long.)
4. **(d) Return connection info** to the browser — relay Pipecat Cloud's response
   (`dailyRoom` + `dailyToken`), or just the join URL `{dailyRoom}?t={dailyToken}`.

### 2.2 What the browser receives & how it connects

The browser gets the Daily room URL + token. With the Pipecat JS client + Daily transport
(`@pipecat-ai/client-js` + `@pipecat-ai/daily-transport`), you point the client's
`connectParams.endpoint` at the proxy (`/voice/start`) and add the Cognito JWT in
`connectParams.headers` (`Authorization: Bearer <jwt>`). The transport POSTs to the proxy,
receives the room+token, and joins the Daily WebRTC room — mic audio flows to the bot, bot
TTS audio plays back. This mirrors `config.ts` today: it already supports `endpoint`,
`requestData`, and `headers` on the connect params — we just repoint `endpoint` to the proxy
and swap the `Bearer` value from the public key to the user JWT.

### 2.3 Where the proxy should live

Best fit for THIS stack: a small **API Gateway (HTTP API) + Lambda**, sitting alongside the
existing Cognito/AgentCore infra (the workshop already deploys Cognito + Lambda + Amplify).
- API GW HTTP API with a **JWT authorizer** bound to the existing Cognito User Pool does
  step (a) for free — Lambda only runs for already-authenticated callers.
- Lambda holds `PIPECAT_PUBLIC_API_KEY` (env var / Secrets Manager), does steps (b)-(d).
- Lambda forwards the caller's JWT (from the validated request) into the start `body`.
Alternative: add the route to any existing always-on server. Avoid putting the public key in
the browser except for a throwaway local demo.

---

## 3. Passing the user JWT to the bot (start body → runner args)

Flow: proxy puts fields in the `/start` request's `body` object → Pipecat Cloud forwards
`body` into the bot runner → bot reads it from `runner_args.body` → bot uses it as
`gateway_token` when invoking the AgentCore agent.

Bot entrypoint (matches `bot.py` line 197: `async def bot(runner_args: RunnerArguments)`):
```python
from pipecat.runner.types import RunnerArguments, DailyRunnerArguments

async def bot(runner_args: RunnerArguments):
    body = getattr(runner_args, "body", None) or {}
    gateway_token = body.get("gateway_token")   # user's Cognito access token
    id_token      = body.get("id_token")
    account_id    = body.get("account_id")
    # ... build transport (DailyRunnerArguments -> room_url/token), then pass
    # gateway_token to the AgentCore call instead of a hardcoded demo user.
```
- `DailyRunnerArguments` carries `room_url`, `token`, and `body`. `body` is exactly the JSON
  `body` object from the `/start` request.
- The current `bot.py` only reads `runner_args.room_url`/`.token` in the
  `case DailyRunnerArguments():` branch — add the `body` read there.
- When the bot invokes the AgentCore agent (the workshop's `invokeAgent` does this in JS via
  `payload.gateway_token`; the bot does the equivalent server-side `InvokeAgentRuntime` with
  `payload = {"prompt": ..., "gateway_token": gateway_token}`), it passes the forwarded
  `gateway_token` so RBAC/account scoping reflects the real user.
- `args.session_id`: documented as part of `bot()` session args (and the `/start` response
  carries a `sessionId`); for our purposes the identity travels in `body`, which is the
  reliable mechanism.
- Local-dev parity: the dev runner (`if __name__ == "__main__": from pipecat.runner.run
  import main`) lets the same `bot(runner_args)` run locally on port 7860 with `/start`.

---

## 4. Existing UI integration points

Files under `resources/agentic-analytics-workshop/app/ui/src/`.

### 4.1 How the dashboard authenticates + calls the agent today

- **Auth (Cognito, OAuth Authorization-Code via Hosted UI)** — `services/authService.js`:
  - `login()` → Hosted UI; `handleAuthCallback()` exchanges `?code` for tokens
    (`exchangeCodeForTokens`), stores `access_token`/`id_token`/`refresh_token` in
    localStorage.
  - `fetchAccessToken()` → OAuth **access token**, used as `gateway_token` for Gateway RBAC.
  - `fetchIdToken()` → **ID token**, used for the Cognito Identity Pool flow.
  - ID-token claims carry `custom:role` and `custom:account_id`.
- **Agent call** — `services/awsAgentCore.js → invokeAgent({...})`:
  - Builds a `BedrockAgentCoreClient` (`@aws-sdk/client-bedrock-agentcore`) using temporary
    creds from the Cognito **Identity Pool** (`getIdentityPoolCredentials(idToken)`).
  - Sends `InvokeAgentRuntimeCommand` with payload `{ prompt: message, gateway_token }`
    (line ~310). `gateway_token` is the user's access token — the SAME value our voice proxy
    must forward into the bot `body`.
  - Streams SSE back; calls `onStreamChunk`, `onToolUse`, `onStreamComplete`.
- **Chat UI** — `components/ChatPanel.js`:
  - `sendMessage()` (line ~205) gates on `authenticated`, fetches
    `gatewayToken = await fetchAccessToken()` and `idToken = fetchIdToken()`, then calls
    `invokeAgent({ message, sessionId, gatewayToken, idToken, onStreamChunk, onToolUse,
    onStreamComplete, ... })`.
  - `App.js` wraps everything in `AuthProvider` / `useAuth()`; `ChatPanel` reads
    `const { user, authenticated } = useAuth()`.

### 4.2 How responses render today (where voice-driven visuals go)

- Streamed assistant text accumulates in `streamingState.message` and renders live through
  `MarkdownContent` (ReactMarkdown + remark-gfm; custom table/code renderers).
- `onToolUse` sets `currentTool` ("Running <tool>") and tags each assistant message with
  `tools[]` chips.
- `detectPanelContext(content)` (line ~190) inspects text for keywords (revenue / customer /
  unicorn / booking) and calls `onPanelUpdate(...)` to switch the side dashboard panel — this
  is the existing hook a voice turn would reuse to drive visuals.
- SQL approval: `parseSqlApproval()` extracts `<!--SQL_APPROVAL_REQUEST-->...` blocks and
  renders `SqlApprovalCard` (Approve/Cancel). `stripSensitiveContent()` scrubs account_id
  UUIDs before display.

### 4.3 Where the mic/speaker toggle + Pipecat client hook in

- Add a **`VoiceButton`/mic toggle** in `ChatPanel`'s input row (line ~508, next to the
  `Send`/clear `IconButton`s) — or in `App.js`/`SplitScreenLayout` header.
- On enable: instantiate a Pipecat JS client + Daily transport, call `connect()` against the
  **proxy** with `connectParams.headers = { Authorization: Bearer <fetchAccessToken()> }`
  and `requestData` mirroring `config.ts` (`createDailyRoom: true`,
  `dailyRoomProperties: { start_video_off: true }`). Reuse the SAME `fetchAccessToken()` the
  chat already uses — that token becomes the bot's `gateway_token`.
- Bridge voice → existing render path: subscribe to the Pipecat client's transcript / bot
  text events and feed them through the SAME pipeline the chat uses — append assistant
  messages, run `detectPanelContext()` to update side panels, surface tool activity. This
  keeps voice and text answers rendering identically (no duplicate UI).
- Keep TTS-bound text short (CLAUDE.md): the bot should summarize aloud while the dashboard
  shows the full table/visual.

### 4.4 npm packages to add to the dashboard `package.json`

The dashboard is **Create-React-App + MUI** (NOT the Vite voice-ui-kit starter). Minimum:
- `@pipecat-ai/client-js` — core RTVI client (`PipecatClient`, connect, events).
- `@pipecat-ai/client-react` — React hooks/providers (`PipecatClientProvider`,
  `usePipecatClient`, `useRTVIClientTransportState`, audio components) — matches the stack's
  React idioms.
- `@pipecat-ai/daily-transport` — Daily WebRTC transport (`DailyTransport` /
  `DailyConnectionEndpoint`), the type used in `config.ts`.

(The starter additionally uses `@pipecat-ai/voice-ui-kit` for a full prebuilt UI —
`PipecatAppBase`, `ConnectButton`, `UserAudioControl`, `ConversationPanel`, `EventsPanel`.
We do NOT need it for the dashboard; we want a thin mic toggle that reuses the existing MUI
chat/panel rendering, not the kit's full-screen UI. Pull it in only if we want its
ready-made audio controls.)

---

## Summary of exact symbols/paths

- Deploy config: `resources/aws-deepgram-sa-hackathon/server/pcc-deploy.toml`
  (`agent_name=aws-deepgram-sa-hackathon`, `secret_set=aws-deepgram-sa-hackathon-secrets`,
  `agent_profile=agent-1x`, `[scaling] min_agents=1`).
- Bot entrypoint: `server/bot.py:197 async def bot(runner_args: RunnerArguments)` — add
  `runner_args.body.get("gateway_token")` in the `DailyRunnerArguments` branch.
- Public start URL: `https://api.pipecat.daily.co/v1/public/aws-deepgram-sa-hackathon/start`.
- Proxy: API GW (HTTP API + Cognito JWT authorizer) + Lambda holding
  `PIPECAT_PUBLIC_API_KEY`; forwards user JWT into start `body`.
- UI auth: `app/ui/src/services/authService.js` (`fetchAccessToken`, `fetchIdToken`).
- UI agent call: `app/ui/src/services/awsAgentCore.js` (`invokeAgent`, payload
  `{prompt, gateway_token}`).
- UI render/hooks: `app/ui/src/components/ChatPanel.js` (`sendMessage`, `onStreamChunk`,
  `onToolUse`, `detectPanelContext`, `MarkdownContent`) — mic toggle goes in the input row.
- Client config to mirror: `resources/aws-deepgram-sa-hackathon/client/src/config.ts`
  (`endpoint`, `requestData`, `headers`).
- npm: `@pipecat-ai/client-js`, `@pipecat-ai/client-react`, `@pipecat-ai/daily-transport`.
