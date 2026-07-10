# Aisle: Voice AI Shopping Assistant

Aisle is a voice-first shopping assistant. You talk to it the way you'd talk to
a knowledgeable shop assistant: keep a running list, ask which product actually
fits your needs (gluten-free, cheapest, on special, the right part), get recipe
ideas, and place an order end-to-end, all by voice. It's built for the two moments
shopping is hardest: **at home** while planning, and **in-store** while staring
at a shelf of near-identical products. The same idea generalises beyond
groceries to pharmacies, hardware, and any catalog where the product
information is dense and hard to navigate.

Built on **Amazon Bedrock AgentCore** (Runtime, Gateway, Memory, Browser, and
Payments), with a **Pipecat** voice pipeline (Deepgram + Bedrock Claude) and an
**AWS CDK** serverless backend.

---

## Highlights

- **Natural voice shopping** multilingual speech in, spoken answers out, with
  an optional video avatar.
- **Grounded product knowledge** semantic + lexical search over a synthetic
  grocery catalogue (~435 products), with allergen, dietary, price, and specials awareness.
- **Remembers you** conversational context and per-user preferences (dietary
  needs, favourite brands) persist across sessions via AgentCore Memory.
- **Actually checks out** a dual-pathway checkout that pays each merchant the
  way it really accepts payment (see [Checkout](#checkout-the-differentiator)).
- **Fully observable** every order step is streamed to CloudWatch and a
  `get_order_status` timeline.

---

## Architecture

```
                       Browser (React / Vite, CloudFront + S3)
                                     │  WebRTC media (Daily)  +  JSON events
                                     ▼
                Amazon Bedrock AgentCore Runtime  --  Pipecat pipeline
                Deepgram STT → Bedrock Claude Haiku 4.5 → Deepgram Aura-2 TTS
                                (optional Tavus avatar)
                                     │  MCP tool calls (SigV4 / AWS_IAM)
                                     ▼
                       AgentCore Gateway  --  Lambda tools
       search_products · add_to_cart · get_cart · grocery list · offers · create_order …
                                     │
                                     ▼
                 Aurora Serverless v2 (PostgreSQL + pgvector, RDS Data API)

  Checkout (create_order routes by merchant):
    x402-native merchant   → pay directly via AgentCore Payments (x402 / USDC on Base)
    card-only retailer      → SQS → AgentCore Browser worker drives a web checkout,
                              paying with a card issued by the Stripe-Issuing broker
```

---

## Checkout (the differentiator)

`create_order` is a **router**, not a single payment path. Each merchant declares
how it can be paid (a `merchants` table), and the agent uses the matching tool:

| Merchant type | Pathway | How it pays |
|---|---|---|
| **x402-native** (paid APIs, MCP tools, x402 stores) | direct | **AgentCore Payments** signs a real x402 / USDC micropayment on Base. No card, no browser. |
| **card-only** (normal web retailers) | async browser | An **AgentCore Browser** worker drives a live web checkout and pays with a card issued by a **Stripe Issuing** broker (the crypto-to-card bridge). |

This mirrors reality: machine-to-machine rails (x402) work for digital services,
while physical retailers only take cards through web checkouts. A `TEST_MODE`
flag (on by default) reports success to the agent immediately while the real
fulfilment still runs and logs every step. See
[`docs/CHECKOUT_TOOL.md`](./docs/CHECKOUT_TOOL.md) and
[`docs/STRIPE_ISSUING.md`](./docs/STRIPE_ISSUING.md).

---

## Tech stack

| Layer | Technology |
|---|---|
| Voice pipeline | **Pipecat** (cascaded), VAD + smart turn detection |
| Speech-to-text | **Deepgram** Nova (multilingual) |
| LLM | **Amazon Bedrock** Claude Haiku 4.5 (`au.anthropic.claude-haiku-4-5`) |
| Text-to-speech | **Deepgram** Aura-2 |
| Avatar (optional) | **Tavus** over a **Daily** WebRTC room (audio-only fallback) |
| Agent compute | **AgentCore Runtime** |
| Tools | **AgentCore Gateway** (MCP) → Lambda tools |
| Memory | **AgentCore Memory** (session + per-user) |
| Browser automation | **AgentCore Browser** (live web checkout) |
| Payments | **AgentCore Payments** (x402 / USDC) + **Stripe Issuing** broker |
| Catalog / data | **Aurora Serverless v2** (PostgreSQL 16, pgvector), Bedrock Cohere embeddings, RDS Data API |
| Async fulfilment | **SQS** + Lambda worker, **S3** for browser artifacts |
| Frontend | **React + Vite + TypeScript**, CloudFront + S3 |
| Infrastructure | **AWS CDK** (TypeScript) |

---

## Repository layout

| Path | What it is |
|---|---|
| `agentcore-pipecat/` | The voice agent that runs on AgentCore Runtime (Pipecat pipeline, Daily/Tavus transport, tools, deploy scripts). |
| `backend/db/` | Aurora schema + synthetic catalogue seed (products, specials) and the seed loader. `DataStack`. |
| `backend/tools/` | Lambda tools behind the AgentCore Gateway, plus the checkout merchants (`card_broker`, `delivery_api`, `merchant_api`, `storefront`) and the async `place_order_async` worker. `ToolsStack`. |
| `backend/agent/` | Wire payload + tool-result shapes (`contracts.py`). |
| `backend/api/` | Session Broker (mints pre-signed session access). `ApiStack`. |
| `backend/infra/` | AWS CDK app and stacks. |
| `frontend/` | React/Vite voice UI (CloudFront + S3). `WebStack`. |
| `docs/` | Checkout + Stripe Issuing guides. |

---

## Wire contracts

- [`backend/agent/contracts.py`](./backend/agent/contracts.py) documents the
  wire payload and tool-result shapes.
- [`frontend/src/types/contracts.live.ts`](./frontend/src/types/contracts.live.ts)
  holds the TypeScript types the frontend consumes from the deployed agent.

All wire fields are `snake_case`, money is integer cents, ids are UUIDv4,
timestamps are ISO-8601 UTC, and every payload carries a `v` version.

---

## Deploy

Infrastructure is AWS CDK (TypeScript) under `backend/infra/`. Stacks deploy in
order, with cross-stack handoffs via SSM Parameter Store under `/aisle/*`:

```
DataStack → ToolsStack → AgentStack → ApiStack → WebStack
```

The voice agent container is built and deployed from `agentcore-pipecat/` (see
its [README](./agentcore-pipecat/README.md) for AgentCore Runtime deployment and
the required Deepgram / Tavus / Daily credentials).

---

## Going beyond the demo (real sources)

This sample runs end-to-end on synthetic data and demo merchants. To wire it to
real sources, start at these seams:

- **Real product catalogue** — replace `backend/db/seed/generate_catalogue.py`
  with your own product source, emitting the same `products.json` /
  `specials.json` shape (`Product` in `backend/agent/contracts.py`). Mind the
  licensing/terms of any third-party product data or images you ingest.
- **Real card issuing** — flip `STRIPE_MODE=live`; see
  [`docs/STRIPE_ISSUING.md`](./docs/STRIPE_ISSUING.md).
- **Real merchant / storefront** — register the merchant in the `merchants`
  table (`backend/db/schema.sql`) and, for the browser pathway, adapt the
  selectors/navigation in `backend/tools/place_order_async/handler.py` to the
  target site. **Only automate sites you operate or are authorized to automate,
  respect their terms of use, and do not add bot-detection / CAPTCHA
  circumvention.** An x402-native merchant settles the signed payment proof via
  a facilitator (the bundled merchant APIs are non-settling stand-ins).

> Defaults are safe for a public demo: payments run **simulated / on testnet**,
> the browser drives an IAM-authorized demo storefront you own, and no real money
> moves until you opt in.

---

## Documentation

- [`docs/CHECKOUT_TOOL.md`](./docs/CHECKOUT_TOOL.md): the `create_order` tool, both pathways, simulation vs live, and observability.
- [`docs/STRIPE_ISSUING.md`](./docs/STRIPE_ISSUING.md): the `STRIPE_MODE` flag and how to switch a fork to real Stripe Issuing.
- [`agentcore-pipecat/README.md`](./agentcore-pipecat/README.md): voice agent runtime + deployment.
