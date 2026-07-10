# agentic-payments-mpp-workshop

Reference implementation for a 90-minute hands-on workshop on building
**machine-to-machine paid agents** with Stripe's [Machine Payments
Protocol](https://mpp.dev/) (MPP) and Amazon Bedrock AgentCore.

Two agents talk over HTTP and **pay each other per call**:

- **Samurai** — a user-facing assistant that gathers product details from a
  human and orchestrates payments. Strands TypeScript agent on AgentCore
  Runtime + AgentCore Memory.
- **ListingBot** — a paid marketplace-listing generator (Amazon, Etsy,
  Shopify, Lazada, Generic). Each call to `/generate` costs **$1.00 USD**
  via Stripe **Shared Payment Tokens (SPT)**, gated by `mppx/server`
  before the Lambda invokes Bedrock Converse.

The participant tutorial lives in [`workshop/content/`](workshop/content/).
This README orients you in the code so you can navigate the repo.

> **AWS-run events only.** This workshop is delivered through Workshop
> Studio. The platform-side CloudFormation (Cognito, Code Editor EC2,
> Stripe secret stores, MPP logs DynamoDB, S3 + CloudFront for the SPA,
> ListingBot Lambda + API Gateway) is provisioned by Workshop Studio at
> event start and reclaimed at event end. Those templates are **not** in
> this repo — only the participant-deployed CFN is (see below).

## Repo layout

```
app/
├── listing-bot-lambda/        Node 20 Lambda (ESM). Three routes:
│                                GET  /openapi.json  — free service discovery
│                                POST /validate      — free input check
│                                POST /generate      — paid via MPP, then Bedrock
├── samurai-agentcore/         Strands TS container for AgentCore Runtime
│                                (linux/arm64). Three tools: discover_service,
│                                check_completeness, generate_listing.
└── samurai-spa/               React 19 + Vite SPA. Amplify Auth →
                                 Cognito Identity Pool → SigV4 →
                                 bedrock-agentcore:InvokeAgentRuntime, in-browser.

workshop/
├── content/                   Hugo workshop chapters (introduction, 01–06,
│                                summary, troubleshooting).
├── overlay/                   TODO-stubbed versions of the files participants
│   ├── app/                     edit during the workshop. Workshop Studio's
│   │   ├── listing-bot-lambda/  packaging step swaps these in over `app/` so
│   │   └── samurai-agentcore/   participants start from the "incomplete" state.
│   └── workshop/code/participant/
│       ├── samurai-agentcore.yaml   Participant CFN (Runtime + Memory + IAM)
│       └── participant-deploy.sh    Build → push to ECR → deploy CFN → patch
│                                     SPA /config.json with the runtime ARN
└── static/images/             Architecture diagrams used by the chapters.
```

## How Samurai and ListingBot fit together

```
Browser  ──► CloudFront / S3 (SPA)
   │
   │  Cognito USER_SRP_AUTH → ID token
   │  Cognito Identity Pool → temporary AWS credentials
   │  bedrock-agentcore:InvokeAgentRuntime (SigV4, signed in-browser)
   │
   ▼
Samurai AgentCore Runtime   ── Strands TS agent with three tools:
 (participant-deployed)        1. discover_service   → GET /openapi.json   (free)
                               2. check_completeness → POST /validate      (free)
                               3. generate_listing   → POST /generate      (paid)
                             + AgentCore Memory (per-session conversation state)
                             + writes MPP protocol events to DynamoDB
   │
   │  generate_listing uses mppx/client:
   │    • on 402, parses the challenge to read the seller's profile_test_*
   │    • POSTs Stripe /v1/shared_payment/issued_tokens with the buyer's
   │      sk_test_ to mint a fresh SPT scoped to that profile + amount
   │    • retries with `Authorization: Payment spt=spt_test_…`
   │
   ▼
ListingBot API Gateway (REST)
   │
   ▼
ListingBot Lambda (Node 20, ESM)
   ├─ GET  /openapi.json  → publishes per-platform input schemas + x-payment-info
   ├─ POST /validate      → deterministic rule-based check, free
   └─ POST /generate      → tiered response:
         400  malformed JSON
         422  invalid input (RFC 7807 problem, NO payment taken)
         402  MPP challenge (mppx/server builds the WWW-Authenticate header)
         200  Bedrock Converse (global.anthropic.claude-sonnet-4-6) → listing JSON
```

**Validation runs before payment.** A buggy caller never gets charged for
a 422 — that's the MPP-compliant shape: payment only fires for requests
that would actually do work.

## The MPP handshake (1 paragraph)

The buyer agent sends a normal HTTP request. If payment is required, the
seller responds **402** with a `WWW-Authenticate: Payment …` header
containing the price, currency, and the seller's Stripe Profile id. The
buyer agent calls `POST /v1/shared_payment/issued_tokens` on Stripe
(authenticated with its own `sk_test_…`) to mint a one-time **Shared
Payment Token** scoped to that profile and capped at the challenge
amount. It retries the request with `Authorization: Payment spt=…`. The
seller hands the SPT to `mppx/server`, which creates a Stripe
PaymentIntent against the seller's profile. On success the seller returns
**200 OK** with the response body and a `Payment-Receipt` header.

## What participants build (5 TODOs)

| #   | Module                  | What you do                                                                                                                  |
| --- | ----------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| 1   | ListingBot              | Paste three values into Secrets Manager: shared buyer `sk_test_` (announced at the event) plus your own seller `sk_test_` and `profile_test_`. |
| 2   | ListingBot              | Fill in the `methods: [...]` argument to `Mppx.create` with `stripe.charge({ networkId, paymentMethodTypes: ['card','link'], secretKey })`. |
| 3   | ListingBot              | Fill in `bedrock.send(new ConverseCommand({ modelId, system, messages, inferenceConfig }))` inside `bedrock.mjs`.            |
| 4   | Samurai                 | Finish Samurai's system prompt — when to call each tool, how to behave on 422, when to ask the human for missing fields.     |
| 5   | Samurai                 | Deploy the AgentCore Runtime, demo the no-Memory failure, then wire AgentCore Memory in three places: `MEMORY_ID` env var in CFN, the IAM grant for `bedrock-agentcore:CreateEvent` / `ListEvents` / `GetEvent`, and the agent code that calls `loadHistory()` / `saveTurn()`. |

You never write the 402-challenge construction, the SPT signing, or the
SigV4 plumbing — `mppx/{server,client}` and AgentCore handle those. You
own the merchant business logic.

## Mock mode (no Stripe keys yet)

When the seller secrets are still the literal string `PLACEHOLDER` (the
default state at event start, before TODO 1), the Lambda short-circuits
the whole MPP gate:

- First pass returns a real, HMAC-bound MPP `Challenge` object built by
  `mppx` — no Stripe call.
- Retry pass (any `Authorization: Payment …`) skips verify and returns
  200 with the listing.

This keeps the workshop demoable end-to-end before participants set up
their Stripe sandbox accounts. See `app/listing-bot-lambda/stripe-mock.mjs`.

## Auth and IAM at a glance

| Actor                              | How it gets permission                                                                                                                       |
| ---------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| Human → SPA                        | Amplify Auth (USER_SRP_AUTH) against the Cognito User Pool.                                                                                  |
| SPA → AgentCore Runtime            | Identity Pool exchanges the Cognito ID token for temporary AWS creds. The authenticated role holds `bedrock-agentcore:InvokeAgentRuntime` scoped to this region. |
| SPA → DynamoDB (MPP logs)          | Same Identity Pool creds. The authenticated role holds `dynamodb:Query` on the MPP logs table only.                                          |
| Samurai Runtime → Stripe (mint SPT)| Reads `BUYER_STRIPE_SECRET_ARN` from Secrets Manager, calls Stripe `/v1/shared_payment/issued_tokens` directly.                              |
| Samurai Runtime → ListingBot       | Plain HTTPS to the API Gateway URL. No IAM; MPP is the only gate.                                                                            |
| ListingBot Lambda → Stripe         | Lambda execution role reads the seller secrets from Secrets Manager (30 s TTL cache) and lets `mppx/server` call Stripe.                     |
| ListingBot Lambda → Bedrock        | Lambda execution role holds `bedrock:Converse` on the Sonnet 4.6 inference profile.                                                          |

**No private keys ever live in the browser.** The buyer's `sk_test_` is a
Secrets Manager entry loaded at AgentCore container startup and held in
module-level closure. The LLM never sees it, and no tool takes it as a
parameter.

## Local code references

- `app/listing-bot-lambda/index.mjs` — REST router with CORS.
- `app/listing-bot-lambda/generate.mjs` — three-tier response, mppx wiring.
- `app/listing-bot-lambda/openapi-spec.mjs` — service discovery doc with `x-payment-info`.
- `app/listing-bot-lambda/rules.json` — per-platform validation + guidance rules.
- `app/listing-bot-lambda/bedrock.mjs` — Sonnet 4.6 Converse call.
- `app/samurai-agentcore/src/agent.ts` — Strands `Agent` + system prompt + 3 tools.
- `app/samurai-agentcore/src/tools/generate-listing.ts` — `mppx/client` + Stripe SPT mint.
- `app/samurai-agentcore/src/memory.ts` — AgentCore Memory load/save (no-op when `MEMORY_ID` unset).
- `app/samurai-spa/src/agentcore-client.js` — direct in-browser AgentCore invocation.
- `workshop/overlay/workshop/code/participant/samurai-agentcore.yaml` — participant CFN.

## Assumptions and limits

- Stripe is **test mode only** (`sk_test_…`). The Stripe API version
  pinned by Samurai for the SPT mint is `2026-04-22.preview`
  (`app/samurai-agentcore/src/tools/generate-listing.ts`).
- Default Bedrock model is `global.anthropic.claude-sonnet-4-6` (override
  via `BEDROCK_MODEL_ID` env var on both Lambda and AgentCore Runtime).
- AgentCore Runtime requires **linux/arm64** images. The Dockerfile uses
  `node:20-slim` and `participant-deploy.sh` builds with `docker buildx`
  on a `docker-container` builder.
- AgentCore Memory `EventExpiryDuration` is **30 days** in the participant
  CFN; `actorId` is hard-coded to `participant` and history is capped at
  20 turns per session in `memory.ts`.
- The MPP logs DynamoDB table is session-scoped with TTL. The SPA queries
  only the signed-in user's session rows (provisioned by the Workshop
  Studio platform stack — not in this repo).

## Where to learn more

- Participant tutorial (chapters 1–6): [`workshop/content/`](workshop/content/)
- Stripe MPP docs: https://docs.stripe.com/payments/machine/mpp
- Stripe SPT concepts: https://docs.stripe.com/agentic-commerce/concepts/shared-payment-tokens
- Bedrock AgentCore Runtime: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime.html
- Bedrock AgentCore Memory: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html
- Strands Agents SDK (TS): https://github.com/strands-agents/sdk-typescript
- MPP protocol overview: https://mpp.dev/
