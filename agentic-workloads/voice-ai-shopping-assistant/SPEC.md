# Technical Spec — Voice AI Shopping Assistant ("Aisle")

> **STATUS: LOCKED (v2, 2026-06-10).** This document and the two frozen
> `contracts.*` files are the interface of record. Changing a wire field is a
> coordinated two-file PR with a `CONTRACT_VERSION` bump (see §2). Do not edit
> shapes mid-build without a coordinated bump.

## Context

We are building a **multi-turn voice AI assistant for grocery shopping**
(expandable later to pharmacy/hardware). The user is **at home**: they talk to
the assistant to keep a running grocery list, get recipe ideas, and — when they
decide to buy — either **order online** (Woolworths delivery or click & collect,
placed for real) or **take the list to a store** themselves. The assistant
**remembers the user across sessions** (dietary needs, preferred brands, their
list) and **proactively flags relevant changes** ("your usual oat milk is on
special", "the bread on your list is out of stock"). This is a **demo** —
functionality + UX/UI matter; account governance, multi-env, and HA do not.

**Five use cases, all voice-driven:**

| # | Use case | Example | Primary mechanism |
|---|---|---|---|
| **UC1** | **Keep a grocery list** | "I need to buy bread" / "I'm out of milk" | persistent per-user list (DB), updated incrementally by voice |
| **UC2** | **Transact an online order** | "Order my groceries for delivery / click & collect" | turn the list into a **real Woolworths cart** via **AgentCore Browser automation + payment** |
| **UC3** | **Recipe recommendations** | (a) "Dinner ideas from what's on special" (b) "I need to use up milk — what can I make?" | recipe tools over catalog + offers |
| **UC4** | **Keep track of personalisations** | dietary requirements, preferred brands | per-user profile (DB) + AgentCore long-term Memory |
| **UC5** | **Track relevant data changes** | specials, seasonality, product availability | separate **offers/availability** store + **proactive change-detection** on connect |

**Stack decisions (locked):** pipecat (orchestration) · Deepgram (STT Nova-3 +
TTS Aura-2) · Bedrock Claude Haiku 4.5 (LLM) · AWS-only · Aurora Serverless v2
PostgreSQL (system of record: user, list, catalog, offers/availability, recipes,
order log) · **AgentCore Runtime** (compute, WebSocket bidi) + **AgentCore
Gateway** (data tools as Lambda/MCP targets) + **AgentCore Memory** (multi-turn +
long-term per-user) + **AgentCore Browser** (real Woolworths ordering) · React
(Vite + TS) static site on **CloudFront/S3** · **AWS CDK (TypeScript)** for all
IaC.

> Online ordering (UC2) is the ambitious centerpiece: the agent drives a live
> Woolworths web session — search, add to cart, checkout, pay — via the
> **AgentCore Browser** tool. This is the **#1 risk** (live site, bot detection,
> login, real money); see §5.1 for the mitigation + fallback. Everything else
> degrades gracefully if ordering is stubbed.

Inspiration repo: `./Voice-AI-Hackathon` (`tavus-pipecat-example/` cascaded
pipeline, transcript forwarders, tool-call data-channel contract, React Daily
client). We **reuse the pipecat cascaded pipeline + transcript/tool-call message
contract**, **drop Tavus/Cartesia/Daily**, and swap transport to AgentCore's
native WebSocket. (A prior `agentcore-pipecat/` gateway experiment lives on
branch `kenny-memory-branch`; this spec supersedes its ordering approach with
browser automation.)

**AWS account:** `597437436235` · region `ap-southeast-2` (Sydney; pinned for
all services). Nova Sonic is **not** available in Sydney → we use a Deepgram
cascaded pipeline, not speech-to-speech.

---

## 1. Architecture Overview

```
Browser (React/Vite SPA on CloudFront)
  │  1. HTTPS GET /session  ──────────────►  Session Broker (Lambda + Function URL)
  │     ◄── { user_id, session_id, ws_url (SigV4 presigned), expires_in }
  │
  │  2. wss:// (mic PCM16/16k up · agent PCM/24k audio + JSON events down)
  ▼
AgentCore Runtime  ── ARM64 container, port 8080, /ws  (pipecat process)
  ├─ Deepgram Nova-3            (STT)
  ├─ Bedrock Claude Haiku 4.5   (LLM, via pipecat AWSBedrockLLMService)
  ├─ Deepgram Aura-2            (TTS)
  ├─ AgentCore Memory           (short-term per session_id + long-term per user_id)
  │
  ├─ DATA tool calls ──► AgentCore Gateway (MCP) ──► Tool Lambdas ──► Aurora Data API
  │     get_grocery_list · update_grocery_list · search_products · get_product_variants
  │     get_recipe · suggest_recipes · get_offers · check_relevant_changes
  │     add_to_cart · get_cart · remove_from_cart · create_order · get_order_status
  │                                                              ┌──────────────────────┐
  │                                                              │ Aurora Serverless v2 │
  │                                                              │  grocery_items       │
  │                                                              │  products (+embedding)│
  │                                                              │  specials      ◄──── offers refresh job
  │                                                              │  recipes / ingredients│
  │                                                              │  carts / cart_items   │
  │                                                              │  orders / order_events│
  │                                                              └──────────────────────┘
  └─ ORDER (UC2) ──► create_order (pay + enqueue) ──► SQS ──► place_order_async worker
        ──► AgentCore Browser session ──► woolworths.com.au
        (drives add-to-cart → checkout → confirm; writes order_events; UI polls
         get_order_status for progress + payment audit + browser artifacts)
```

**Why this shape**

- **AgentCore Runtime `/ws`** is purpose-built for real-time voice (persistent
  bidi, session isolation, scales to zero). ARM64 container, host `0.0.0.0:8080`,
  path `/ws`, `/ping` health.
- **Browsers cannot set headers on a WS handshake** → the only browser-viable
  auth is a **SigV4 pre-signed `wss://` URL** (query-param signing). This is the
  load-bearing integration constraint and the reason the **Session Broker Lambda**
  exists (browser never holds AWS creds).
- **Data path = Gateway + Lambda + Aurora Data API.** All *reads/writes* of user,
  list, catalog, offers, and recipes are stateless tool calls → cheap, isolated,
  no VPC client, no connection pool. Aurora Serverless v2 scales to **0 ACU**.
- **Order path = AgentCore Browser, NOT a Lambda.** Placing a real Woolworths
  order is long-running and stateful (a live logged-in browser session across
  many steps), so it lives **in the agent runtime** driving an AgentCore Browser
  session — not as a stateless Gateway Lambda. It is still exposed to the LLM as a
  callable tool (`place_order`).
- **Offers/availability is a separate store** (own `availability` table, own
  refresh job) so specials/seasonality/stock can change independently of the
  static catalog — this is what powers UC5 change-detection.

---

## 2. Naming & Interface Conventions (GLOBAL — every silo MUST follow)

Contract rules so people in separate silos integrate cleanly.

| Layer | File case | Identifier case | Wire/JSON field case |
|---|---|---|---|
| Python (agent, Lambdas) | `snake_case.py` | `snake_case` vars/fns, `PascalCase` classes, `SCREAMING_SNAKE` consts | **`snake_case`** |
| TypeScript (frontend, CDK) | `kebab-case.ts` / `PascalCase.tsx` components | `camelCase` vars/fns, `PascalCase` types/components | see rule below |
| SQL (Aurora) | — | `snake_case` tables/cols, plural tables | `snake_case` |

**JSON field-case rule (mandatory, removes all ambiguity):**
- **All WebSocket + tool + DB payloads use `snake_case`** (Python-native, matches
  reference repo & AgentCore docs).
- The **frontend defines TS interfaces with `snake_case` fields** mirroring the
  wire exactly (no auto-camelization). `frontend/src/types/contracts.ts` is the
  source of truth, hand-kept in sync with `backend/agent/contracts.py`.
- IDs: `user_id`, `session_id`, `order_id`, `product_id`, `item_id` — all
  **string UUIDv4** unless noted. Money: integer **cents** (`*_cents`), never
  floats. Timestamps: **ISO-8601 UTC string** (`created_at`, `updated_at`).
- Enums are lowercase snake strings (e.g. `status: "active" | "out_of_stock"`).

**Versioning:** every WS event and tool payload carries `"v": 2`
(`== CONTRACT_VERSION`). Bump on breaking change.

---

## 3. Component Specs

### 3.1 Frontend — React (Vite + TypeScript) on CloudFront/S3

**Stack:** Vite 5 + React 18 + TypeScript 5. Package manager npm. State: Zustand.
Styling: distinctive, NOT generic. Build output `dist/` → S3 → CloudFront.

**Repo layout** (`/frontend`):
```
src/
  types/contracts.ts        # SHARED CONTRACT — mirrors backend/agent/contracts.py (snake_case fields)
  lib/session-client.ts     # GET /session → { user_id, presigned wss url }
  lib/voice-client.ts       # WebSocket + WebAudio (mic capture PCM16 16k, playback PCM 24k)
  store/conversation.ts     # Zustand: transcript, agent_state, profile, list, offers, changes, order
  components/
    VoiceOrb.tsx            # mic/agent state visualization
    TranscriptPanel.tsx     # rolling user/agent transcript (from reference)
    GroceryListPanel.tsx    # the persistent list — items + status (active/have/out_of_stock)  [UC1]
    ProfileChips.tsx        # dietary + preferred-brand chips (personalisation)                [UC4]
    OffersPanel.tsx         # "for you" specials/seasonal + proactive change alerts            [UC5]
    ProductCard.tsx         # variant comparison cards (brand, allergens, price, quality)       [UC3]
    RecipePanel.tsx         # dish → ingredients, "uses specials" badge                         [UC3]
    OrderPanel.tsx          # preview → live browser-automation progress → confirmation         [UC2]
    MicControl.tsx          # mute / end / confirm-order (from reference FloatingMicControl)
  App.tsx
```

**Audio:** capture mic via `getUserMedia` → `AudioWorklet` → downsample to
**PCM16 mono 16 kHz** → send as **binary** WS frames. Playback agent audio:
**PCM 24 kHz** binary frames → WebAudio buffer. (Matches pipecat
`audio_in_sample_rate=16000`, `audio_out_sample_rate=24000`.)

**Order confirmation gate (real money):** before `place_order` submits payment,
the UI surfaces the `OrderPreview` and an explicit **Confirm order** button
(emits `user_action: "confirm_order"`). The agent will not pay until it receives
either that action or an unambiguous spoken confirmation. See §5.1.

**Build-time env (`VITE_` prefix, baked at build):**
| Var | Purpose | Demo value |
|---|---|---|
| `VITE_SESSION_API_URL` | Session Broker base URL | `https://<id>.lambda-url.ap-southeast-2.on.aws` |

**CloudFront config:** OAC to private S3 bucket; default root `index.html`;
**403/404 → `/index.html` (200)** for SPA; HTTPS only; cache hashed JS/CSS long,
`index.html` no-cache.

**API the frontend calls (only one HTTP call; rest is WS):**
```
GET {VITE_SESSION_API_URL}/session
200 → { "v":2, "user_id":"<uuid>", "session_id":"<uuid>", "ws_url":"wss://...presigned...", "expires_in":300 }
```

---

### 3.2 Session Broker — Lambda (Python) + Lambda Function URL

**Why:** mints the SigV4 **pre-signed `wss://` URL** for AgentCore Runtime
(browser can't sign), and resolves the **known demo user** so the assistant
"remembers me" from the first turn.

- **Runtime:** Python 3.12, arm64. **Auth:** Function URL `AuthType: NONE` for
  demo (lock to CloudFront origin via CORS allowlist). **CORS:** allow CloudFront
  domain only.
- **Logic:** resolve `user_id` (the single known demo user, from `DEMO_USER_ID`
  env / SSM) → generate `session_id` (uuid4) → call
  `AgentCoreRuntimeClient.generate_presigned_url(runtime_arn=AGENT_RUNTIME_ARN,
  expires=300)` with `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` bound to
  `session_id` → return.
- **IAM:** `bedrock-agentcore:InvokeAgentRuntimeWithWebSocketStream` on the
  runtime ARN.
- **Env:** `AGENT_RUNTIME_ARN`, `ALLOWED_ORIGIN`, `DEMO_USER_ID`.
- **Response contract:** exactly the `GET /session` shape in 3.1 (includes
  `user_id`).

---

### 3.3 Voice Agent — pipecat on AgentCore Runtime

**Container:** ARM64, host `0.0.0.0`, port `8080`, endpoints `/ws` (voice) +
`/ping` (health). Built with `BedrockAgentCoreApp` (`@app.websocket` handler)
wrapping the pipecat pipeline. Deployed via `agentcore` CLI / CDK custom resource.

**Repo layout** (`/backend/agent`):
```
agent/
  main.py            # BedrockAgentCoreApp, @app.websocket → runs pipeline per connection
  pipeline.py        # builds pipecat Pipeline (cascaded)
  transports.py      # AgentCoreWebSocketTransport: bridges /ws <-> pipecat frames
  forwarders.py      # UserTranscriptForwarder, AgentTranscriptForwarder (from reference)
  tools.py           # FunctionSchema defs + handlers → Gateway/MCP (data tools)
  ordering.py        # place_order: drives AgentCore Browser session against Woolworths  [UC2]
  memory.py          # AgentCore Memory: short-term per session_id, long-term per user_id
  prompts.py         # system prompt + opening routine (proactive change heads-up)
  contracts.py       # SOURCE OF TRUTH for WS event + tool payload shapes (dataclasses)
  Dockerfile         # ARM64
  requirements.txt
```

**Pipeline (cascaded, mirrors reference `tavus-pipecat.py`):**
```python
Pipeline([
    transport.input(),            # AgentCore /ws inbound: binary PCM16 16k → audio frames
    stt,                          # DeepgramSTTService(model="nova-3", language="multi")
    user_transcript,              # UserTranscriptForwarder → WS JSON event (snake_case)
    context_aggregator.user(),
    llm,                          # AWSBedrockLLMService(model="<claude-haiku-4-5 id>", region=...)
    agent_transcript,             # AgentTranscriptForwarder → WS JSON event
    tts,                          # DeepgramTTSService(model="aura-2-<voice>-en")
    transport.output(),           # outbound: TTS PCM 24k → binary WS frames
    context_aggregator.assistant(),
])
task = PipelineTask(pipeline, params=PipelineParams(
    audio_in_sample_rate=16000, audio_out_sample_rate=24000,
    enable_metrics=True, allow_interruptions=True))
```
VAD: `SileroVADAnalyzer`. Turn: `LocalSmartTurnAnalyzerV3`. Interruptions ON
(barge-in).

**`transports.py` is the key new piece** (reference used Daily/SmallWebRTC; we
bridge AgentCore `/ws`): inbound binary frames → `InputAudioRawFrame`; outbound
`TTSAudioRawFrame` → `websocket.send_bytes`; JSON control/transcript/tool/
order-progress events → `websocket.send_text(json)`. Mirror the reference's
`OutputTransportMessageUrgentFrame` JSON usage for the data events.

**Opening routine (UC5 proactive heads-up).** On the first `init` message the
agent: (1) loads the user (profile + grocery list) via tools, (2) calls
`check_relevant_changes(user_id)`, (3) greets the user **by what matters now** —
e.g. "Morning! Two things: the bread on your list is out of stock, and your usual
oat milk is on special this week." Then it listens. If there are no relevant
changes, it gives a normal greeting.

**Ordering (UC2)** is implemented in `ordering.py` and surfaced to the LLM as the
`place_order` tool. It does **not** go through the Gateway; it drives an
**AgentCore Browser** session. It is a **single long-running call that blocks at
the review step** — one browser session stays alive across the whole flow so the
real cart persists through the confirmation gate (do NOT split preview and pay
into two tool calls — that would lose the live cart). Flow (each step emits an
`order_progress` event, §3.4):
1. **resolving** — map confirmed list items → Woolworths search terms (respect
   preferred brands / dietary from profile).
2. **searching / adding** — for each item: navigate, search, pick best match, add
   to cart on woolworths.com.au.
3. **reviewing** — read back the real cart + total; emit the `reviewing`
   `order_progress` event **carrying an `OrderPreview`** (§3.7); then **block for
   explicit confirmation** — the next `user_action` of `confirm_order` (proceed)
   or `cancel_order` (abort), or an unambiguous spoken yes/no. No timeout default
   to "yes".
4. **paying** — on confirm: select fulfillment (`delivery` | `click_and_collect`)
   and complete checkout + payment on the demo Woolworths account (see §5.1).
5. **placed / failed** — write a row to the `orders` log, emit final state with
   `woolworths_order_ref` + `eta`. On `cancel_order`, end as `failed` with a
   "cancelled by user" message and place nothing.

**Memory** (`memory.py`): AgentCore Memory keyed by `session_id` for short-term
turn history; long-term namespace keyed by `user_id` for derived "usuals" and
conversational preferences. **The DB is the system of record** for profile + list
+ orders (tools read/write it); Memory is for conversational recall and phrasing
personalised suggestions. They complement; the DB wins on conflict.

**No `mode`.** There is one assistant; the user decides *fulfillment* mid-
conversation. ("Go to a store myself" simply keeps the list — no order placed;
the list can be read back grouped by `aisle`.)

**Env / secrets (from Secrets Manager / SSM, injected to container):**
| Var | Purpose |
|---|---|
| `DEEPGRAM_API_KEY` | STT + TTS |
| `AWS_REGION` | Bedrock + Data API region |
| `BEDROCK_MODEL_ID` | Claude Haiku 4.5 inference profile id |
| `GATEWAY_MCP_URL` | AgentCore Gateway MCP endpoint (data tools) |
| `MEMORY_ID` | AgentCore Memory resource id |
| `WOOLWORTHS_ACCOUNT_SECRET_ARN` | Secret holding demo Woolworths login + payment method ref |
| (AWS creds) | from runtime IAM role — do NOT set keys |

---

### 3.4 WebSocket Message Contract (Browser ⇄ Agent)

All text messages JSON, `snake_case`, carry `v` + `type`. Audio is **binary** (no
JSON wrapper): uplink PCM16 mono 16 kHz, downlink PCM mono 24 kHz.

**Browser → Agent**
```jsonc
// first message after connect
{ "v":2, "type":"init", "session_id":"<uuid>", "user_id":"<uuid>" }
// binary frames thereafter = PCM16 mono 16kHz mic audio
// control / order gating
{ "v":2, "type":"user_action", "action":"mute"|"unmute"|"end"|"confirm_order"|"cancel_order" }
```

**Agent → Browser** (text events; binary frames = PCM 24kHz agent audio)
```jsonc
// transcript (streaming + final) — from reference forwarders
{ "v":2, "type":"transcript", "role":"user"|"agent", "text":"...", "final":false }

// agent lifecycle for UI orb
{ "v":2, "type":"agent_state", "state":"listening"|"thinking"|"speaking"|"idle" }

// data tool result → drives UI panels (list, profile, offers, recipe, variants)
{ "v":2, "type":"tool_result", "tool":"get_grocery_list", "data":{ /* see 3.5 */ } }

// live ordering progress (UC2 browser automation) → OrderPanel.
// the "reviewing" step carries `preview` (OrderPreview, §3.7); other steps omit it.
{ "v":2, "type":"order_progress", "order_id":"<uuid>",
  "step":"resolving"|"searching"|"adding"|"reviewing"|"paying"|"placed"|"failed",
  "message":"...", "item":"oat milk", "preview": { /* OrderPreview, only on "reviewing" */ } }

// error
{ "v":2, "type":"error", "code":"...", "message":"..." }
```
`tool` ∈ `get_grocery_list | update_grocery_list | search_products |
get_product_variants | get_recipe | suggest_recipes | get_offers |
check_relevant_changes | add_to_cart | get_cart | remove_from_cart |
create_order | get_order_status`. Frontend `voice-client.ts` switches on `type`,
then `tool`/`step`, updating Zustand → panels re-render.

---

### 3.5 AgentCore Gateway + Data Tool Lambdas

Each **data** tool = **one Lambda (Python 3.12, arm64)** registered as a **Gateway
MCP target**. Agent invokes via MCP over `GATEWAY_MCP_URL`. Lambdas talk to Aurora
via **Data API** (`rds-data:ExecuteStatement`) — no VPC, no pooling.

> **`create_order` does the checkout cheaply and synchronously** (process the
> x402 payment, write the order, enqueue it), so it is a normal Gateway Lambda
> like the rest. The long-running, stateful part — driving a live AgentCore
> Browser session against woolworths.com.au — runs OUT of band in the
> **`place_order_async`** worker (SQS-triggered, NOT a Gateway tool). The agent/UI
> observe it via `get_order_status` (a Gateway tool) rather than blocking on it.

**Gateway:** one Gateway, OAuth/SigV4 inbound from runtime; each Lambda a target
with its input JSON schema = the tool's `properties`. Tool name = function name
(snake_case).

**Tool catalog** (`name`, args → returns `data`). All `snake_case`, money in
`*_cents`.

| Tool | Args | Returns (`data`) | UC | Owner |
|---|---|---|---|---|
| `get_grocery_list` | `{ user_id }` | `{ list:GroceryList }` | 1 | Tools |
| `update_grocery_list` | `{ user_id, add?:[{raw_text, qty?, product_id?, name?}], remove?:[item_id], update?:[{item_id, qty?, status?, product_id?, name?}] }` | `{ list:GroceryList }` | 1 | Tools |
| `search_products` | `{ query, category?, limit?=10, dietary_tags?, exclude_allergens?, min_price_cents?, max_price_cents?, quality_tier?, in_stock_only?, on_special_only?, sort?, mode? }` | `{ products:[Product] }` | 1,3 | Tools |
| `get_product_variants` | `{ product_name }` | `{ variants:[Product] }` (brands, `allergens`, `quality_tier`, `price_cents`) | 3 | Tools |
| `get_recipe` | `{ dish }` | `{ recipe:Recipe }` (ingredients[] w/ `product_id` matches) | 3 | Tools |
| `suggest_recipes` | `{ from_specials?:bool, use_up?:[str], dietary?:[str], n?:int=3 }` | `{ recipes:[RecipeSummary] }` | 3 | Tools |
| `get_offers` | `{ category?, limit?:int=10 }` | `{ offers:[Offer] }` | 5 | Tools |
| `check_relevant_changes` | `{ user_id }` | `{ changes:[RelevantChange] }` | 5 | Tools |
| `add_to_cart` | `{ session_id, product_id, qty? }` | `{ cart:Cart }` | 2 | Tools |
| `get_cart` | `{ session_id }` | `{ cart:Cart }` | 2 | Tools |
| `remove_from_cart` | `{ session_id, product_id, qty? }` | `{ cart:Cart }` | 2 | Tools |
| `create_order` | `{ session_id, pickup_time? }` | `{ order:Order }` | 2 | Tools |
| `get_order_status` | `{ order_id }` | `{ order_status:OrderStatusDetail }` | 2 | Tools |

**Ordering model (UC2):** the agent builds a cart with `add_to_cart` /
`get_cart` / `remove_from_cart`, then `create_order` finalises it — it processes
the x402 payment and enqueues the order for async fulfilment. A separate
**`place_order_async`** worker (SQS-triggered, NOT a Gateway tool) drives the
AgentCore Browser session against woolworths.com.au and writes progress to the
`order_events` log; the agent/UI poll `get_order_status` for the lifecycle
(`paid → placing → placed`, or `declined_insufficient_funds` / `browser_blocked`
/ `failed`) plus the payment audit and browser artifacts.

**Personalisation (UC4):** no profile tool — dietary prefs / preferred brands
live in **AgentCore Memory** (long-term per `user_id`), applied by the agent as
`search_products` filters; there is no `get_user_profile` / `update_user_profile`
Gateway tool.

**Lambda I/O contract (data tools):** event = MCP tool-call →
`{ "arguments": { ...args } }`; response = `{ "data": { ... } }` matching the
table (or `{ "error": { "code","message" } }`).

---

### 3.6 Database — Aurora Serverless v2 (PostgreSQL)

**Config:** Aurora PostgreSQL, Serverless v2, **MinCapacity 0 ACU** (auto-pause,
`SecondsUntilAutoPause: 300`), **MaxCapacity 2 ACU**. **Data API enabled.**
Single-AZ. Credentials in **Secrets Manager** (referenced by Data API). Private
subnets, no public access (Data API reaches it over the AWS backbone).

**Schema** (`backend/db/schema.sql`, snake_case, plural tables):
```sql
-- user identity + personalisation (UC4)
users(
  user_id uuid pk, display_name text, dietary text[], avoid_allergens text[],
  updated_at timestamptz)
user_preferred_brands(user_id uuid fk, category text, brand text)

-- persistent grocery list (UC1)
grocery_items(
  item_id uuid pk, user_id uuid fk, raw_text text, product_id uuid null,
  name text, qty numeric, unit text, status text,        -- active|have|out_of_stock|removed
  added_at timestamptz)

-- static catalog
products(
  product_id uuid pk, name text, brand text, category text, aisle text,
  price_cents int, unit text, allergens text[], dietary_tags text[],
  quality_tier text, image_url text)

-- SEPARATE offers/availability store (UC5) — refreshed independently of catalog
availability(
  product_id uuid pk fk, in_stock bool, on_special bool, special_price_cents int null,
  seasonal bool, message text null, valid_until timestamptz null, updated_at timestamptz)

-- recipes (UC3)
recipes(recipe_id uuid pk, name text, servings int, steps text[])
recipe_ingredients(recipe_id uuid fk, product_id uuid fk, qty numeric, unit text)

-- order log (UC2) — record of what we placed on Woolworths; cart itself lives on the real site
orders(
  order_id uuid pk, user_id uuid fk, fulfillment text, status text,  -- previewing|placing|placed|failed
  items jsonb, total_cents int, woolworths_order_ref text null,
  eta timestamptz null, created_at timestamptz)
```
Indexes: `products(name)`, `products(category)`, GIN on `allergens`,
`dietary_tags`; `grocery_items(user_id, status)`; `availability(on_special)`,
`availability(in_stock)`. (Demo scale — `ILIKE`/pg full-text is fine; no pgvector.)

**Wire `Product` merges `products ⨝ availability`** — tools join and present the
combined shape (§3.7); storage keeps catalog and availability separate so
specials/stock can change on their own.

**Seed** (`backend/db/seed/`): JSON files (`users.json`, `products.json`,
`availability.json`, `recipes.json`) → loader script runs
`rds-data:BatchExecuteStatement`. Seed **one known demo user** (an opaque
`user_id`; dietary + preferred brands live in AgentCore Memory, not a profile
table) with a starter grocery list. Seed **~150–300 grocery products**
across aisles with realistic brands, multiple variants per staple (pasta ×5
brands, milk ×4), real `allergens`/`dietary_tags`/`quality_tier`. Seed
`availability` so a meaningful subset is `on_special`/`seasonal` and a few list/
usual items are `out_of_stock` — this makes UC5's proactive heads-up demo-able.
Seed JSON field-case = `snake_case`.

**Offers refresh (UC5):** an `availability` refresh path (script or scheduled
Lambda) updates `availability` independently — this is the "separate database of
specials/seasonal/stock." For the demo, `check_relevant_changes` reports the
**current** relevant state (list/usual items that are `on_special` or
`out_of_stock`); true diffing (price-drop vs last-seen) would need snapshots —
out of scope, noted in §5.6.

---

### 3.7 Shared Object Shapes

Defined once in `backend/agent/contracts.py` AND `frontend/src/types/contracts.ts`
(snake_case, integer cents, UUIDv4 ids, ISO-8601 UTC timestamps):
Personalisation (UC4) is NOT a wire object — dietary prefs / preferred brands
live in AgentCore Memory, not a `UserProfile` payload.
```jsonc
GroceryItem = { item_id, raw_text, status:"active"|"have"|"out_of_stock"|"removed",
                product_id|null, name|null, qty, unit|null }
GroceryList = { user_id, items:[GroceryItem] }

Product     = { product_id, name, brand, category, aisle, price_cents, unit,
                allergens:[str], dietary_tags:[str], quality_tier:"value"|"standard"|"premium",
                in_stock:bool, image_url|null }            // + additive `special` obj when on special

Offer       = { product_id, name, brand, category, aisle, unit, price_cents,
                special_price_cents, was_price_cents, savings_cents, pct_below_usual,
                special_type, image_url|null }

RelevantChange = { kind:"on_special"|"out_of_stock", item_id, product_id, name,
                   special_price_cents|null, was_price_cents|null, savings_cents|null,
                   special_type|null }

RecipeIngredient = { product_id, name, qty, unit }
Recipe        = { recipe_id, name, servings, steps:[str], ingredients:[RecipeIngredient] }
RecipeSummary = { recipe_id, name, servings }

Cart         = { cart_id, session_id, items:[{ product_id, name, qty, price_cents }], subtotal_cents }
Order        = { order_id, session_id, status:OrderStatus, pickup_code, pickup_time|null,
                 total_cents, created_at, payment_id?, browser_session_id?,
                 status_detail?, updated_at? }
// OrderStatus = draft|submitted|ready_for_pickup|paid|placing|placed|
//               declined_insufficient_funds|browser_blocked|failed
OrderStatusDetail = { order:Order, timeline:[OrderEvent], payment:PaymentAudit|null,
                      artifacts:[OrderArtifact] }   // returned by get_order_status
// Browser-ordering progress (UC2) is streamed on the WS as OrderProgressEvent;
// OrderItem / OrderPreview describe the basket shown at the "reviewing" step.
```

---

## 4. AWS CDK Stacks (TypeScript) — ownership boundaries

`/backend/infra` (CDK v2, TS). **One stack per silo so deploys don't overlap.**
Cross-stack values via SSM Parameter Store (not hard refs) to keep silos
deployable independently.

| Stack | Owns (resources) | Exports (SSM) | Consumes |
|---|---|---|---|
| `DataStack` | Aurora SV2 cluster, Secret, DB SG/subnets, seed runner (mints the known demo user), offers-refresh runner | `/aisle/db/cluster_arn`, `/aisle/db/secret_arn`, `/aisle/demo/user_id` | — |
| `ToolsStack` | data-tool Lambdas (list, search, recipes, offers/changes, cart, order) + the `place_order_async` browser worker, AgentCore Gateway + targets, fulfillment SQS, order-artifacts S3 | `/aisle/gateway/mcp_url` | db params |
| `AgentStack` | ECR image, AgentCore Runtime, Memory, **Browser** config, runtime IAM role, Woolworths secret | `/aisle/agent/runtime_arn`, `/aisle/agent/memory_id` | gateway url |
| `ApiStack` | Session Broker Lambda + Function URL | `/aisle/session/url` | runtime arn, `/aisle/demo/user_id` |
| `WebStack` | S3 bucket, CloudFront (OAC), bucket deploy of `dist/` | `/aisle/web/url` | session url (build-time) |

---

## 5. Critical Integration Risks (call-outs)

### 5.1 Live Woolworths ordering + real payment (#1 — the centerpiece)
`place_order` drives a **live, logged-in woolworths.com.au session** via the
AgentCore Browser tool and completes a **real payment**. Failure modes: bot/CAPTCHA
detection, login/2FA, DOM changes breaking selectors, rate limiting, and the
obvious — **spending real money in a live demo**.
**Mitigations (do all):**
- Use a **dedicated demo Woolworths account** with a saved address + payment
  method; **pre-warm/pre-login** the browser session before the demo.
- **Hard confirmation gate before `paying`** — never submit payment without an
  explicit `confirm_order` action or unambiguous spoken "yes" (§3.3, §3.1).
- **Verify the payment mechanism early.** Confirm whether AgentCore provides a
  first-class payment capability in `ap-southeast-2`, or whether checkout must be
  completed via browser-driven entry of the stored card. **If neither is reliable,
  fall back to "stop before final submit"** (assemble the real cart, read back the
  total, stop at the pay button) — UC2 still demos end-to-end minus the final
  click. Treat this fallback as the safe default for a public demo.
- **Confirm AgentCore Browser is available in `ap-southeast-2`**; if not, run the
  browser session in the nearest supported region (the rest of the stack stays in
  Sydney).
- Keep a **recorded run** as ultimate fallback.
- **Confirm-gate plumbing (intra-process):** `confirm_order`/`cancel_order` arrive
  on the WS **transport**, but `place_order` blocks inside an **LLM tool call** —
  same process, different async contexts. `transports.py` must route these
  actions to an `asyncio.Event`/`Future` (or per-order `asyncio.Queue`) keyed by
  `order_id` that `ordering.py` awaits at the `reviewing` step. Spec this hand-off
  before Agent 3 splits transport vs ordering work, or the gate silently hangs.

### 5.2 WS browser auth
Must use SigV4 **pre-signed URL** (query params), NOT signed headers. Broker
Lambda owns this. (#1 *infra* thing to get right.)

### 5.3 Audio format must match exactly
Mic PCM16/16k up, agent PCM/24k down, binary frames. Frontend AudioWorklet +
pipecat sample rates must agree or you get noise/silence.

### 5.4 Custom transport
pipecat has no built-in AgentCore `/ws` transport; `transports.py` is net-new
code bridging `@app.websocket` ⇄ pipecat frames. Build & test locally first
(`python main.py` + local WS client) before deploying.

### 5.5 Contract drift
`backend/agent/contracts.py` and `frontend/src/types/contracts.ts` are hand-synced
at `CONTRACT_VERSION = 2`; any field change is a two-file change. Treat as one PR.

### 5.6 Change-detection scope
"Usual" items are derived from `user_preferred_brands` + past `orders`;
`check_relevant_changes` reports **current** relevant state (list/usual items
`on_special` or `out_of_stock`), not a true time-diff. Real price-drop diffing
needs availability snapshots — out of scope for the demo.

### 5.7 Aurora cold start
0-ACU auto-pause adds ~seconds on first query after idle; acceptable for demo,
but **warm it before a live demo**.

---

## 6. Verification (end-to-end, by use case)

1. **DB & seed:** deploy `DataStack`, run seed, `aws rds-data execute-statement
   "select count(*) from products"` → ~200; one row in `users`; starter list in
   `grocery_items`; subset of `availability` `on_special`/`out_of_stock`.
2. **Data tools:** invoke each Lambda locally w/ a sample MCP event; assert `data`
   shape vs §3.5/§3.7.
3. **Agent local:** `python backend/agent/main.py`; connect `ws://localhost:8080/ws`;
   send `init` + a WAV of PCM16/16k; confirm transcript events + audio out + a
   `tool_result`.
4. **Agent deployed:** broker `/session` → presigned wss → repeat (3) against
   AgentCore.
5. **Use-case pass (frontend, `npm run dev`, full mic):**
   - **UC1** "I need to buy bread" / "I'm out of milk" → `GroceryListPanel` updates
     (add; mark out_of_stock).
   - **UC4** "I'm vegetarian, I like Macro brand" → `ProfileChips` update; persists
     after reconnect.
   - **UC5** reconnect → agent **proactively** flags a list item out of stock + a
     usual item on special; `OffersPanel` shows them.
   - **UC3** (a) "dinner ideas from what's on special" → `RecipePanel` w/ "uses
     specials"; (b) "I need to use up milk" → recipe suggestions.
   - **UC2** "order it for click & collect" → `OrderPanel` shows preview → confirm
     → live `order_progress` steps → placed (or stop-before-pay per §5.1).
6. **Persistence:** disconnect, reconnect → profile + list survive (DB + Memory).
7. **Prod:** `cdk deploy --all`; load CloudFront URL; full mic demo of all 5 UCs.

---

## 7. Five Sub-Agent Work Split (no write/deploy overlap)

Split **by silo**; each owns a disjoint directory and exactly one CDK stack.
Contract files (`contracts.py` / `contracts.ts`) are frozen up front (Step 0) so
silos integrate against a fixed interface.

> **Step 0 (shared, done before fan-out):** lock §2 conventions + §3.4 WS contract
> + §3.5 tool catalog + §3.7 object shapes into `contracts.py` and `contracts.ts`
> at `CONTRACT_VERSION = 2`. All five agents code against these; no agent edits
> them afterward without a coordinated bump.

| # | Agent | Owns (writes only here) | Deploys | Interface it honors |
|---|---|---|---|---|
| **1** | **Database & Seed** | `backend/db/**` (schema, seed JSON incl. demo user + availability, loader, offers-refresh), `backend/infra/DataStack` | `DataStack` | Tables matching §3.6/§3.7; exports DB SSM params |
| **2** | **Data Tools & Gateway** | `backend/tools/**` (10 data Lambdas), `backend/infra/ToolsStack` | `ToolsStack` | Consumes DB params; implements §3.5 data-tool I/O exactly |
| **3** | **Voice Agent + Ordering** | `backend/agent/**` (pipeline, transport, forwarders, prompts, memory, **ordering.py browser+payment**, Docker), `backend/infra/AgentStack` | `AgentStack` | Calls data tools via Gateway MCP; runs UC2 via AgentCore Browser; emits §3.4 events incl. `order_progress` |
| **4** | **Frontend** | `frontend/**` (Vite app, audio, panels, Zustand), `backend/infra/WebStack` | `WebStack` | Consumes §3.4 events + §3.1 `/session`; renders list/profile/offers/recipe/order panels |
| **5** | **Edge & Session/IaC glue** | `backend/api/**` (Session Broker), `backend/infra/ApiStack`, app-level `bin/aisle.ts` + SSM cross-stack params | `ApiStack` (+ app wiring) | Mints presigned wss + resolves demo `user_id`; wires every stack's SSM exports/imports |

> Repo layout: backend silos under `backend/` (`db`, `tools`, `agent`, `api`,
> `infra`); React app at `frontend/`.

**Why this is clash-free:**
- Each agent writes to **one silo dir** + **one stack**; no shared source files
  post Step 0.
- Cross-stack coupling is **only via SSM parameters** (string handoffs), so agents
  deploy independently. Deploy order: **1 → 2 → 3 → 5 → 4**.
- Integration bugs localize: bad SQL → Agent 1; wrong tool payload → Agent 2;
  audio/transport/ordering → Agent 3; UI/event handling → Agent 4; auth/wss/wiring
  → Agent 5.
- **UC2 (browser ordering + payment)** is the one cross-cutting risk and lives
  wholly inside Agent 3; if you have a sixth person, split `ordering.py` +
  Woolworths secret into its own silo, otherwise keep it in Agent 3.

---

## Deliverable

This document **is** the spec, and it is **LOCKED at v2**. The two frozen
`contracts.*` files are updated to match. Next step on approval: flesh out the
per-silo skeletons (`backend/{db,tools,agent,api}`, `frontend/`, the five CDK
stacks) so the five sub-agents can build in parallel against this fixed interface.
