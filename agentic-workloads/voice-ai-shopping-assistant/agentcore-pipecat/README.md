# Aisle Voice Shopping Agent on Amazon Bedrock AgentCore Runtime

The **Aisle** voice grocery shopping assistant, deployed to **Amazon Bedrock
AgentCore Runtime**. The shopper talks to Aisle to search the Aisle catalogue,
keep a running cart and a persistent grocery list, hear what's on
special, and place a pickup order — all by voice. Cascaded pipeline (Deepgram
does both STT and TTS):

```
Daily (WebRTC) -> Deepgram STT (multilingual) -> Bedrock Claude -> Deepgram Aura TTS -> [Tavus avatar, optional] -> Daily
```

The agent calls the live **AgentCore Gateway** for all grocery actions (the
Aisle catalogue + cart/order in Aurora) and uses **AgentCore Memory** to
remember the shopper across sessions (dietary needs, preferred brands). The
**Tavus avatar is optional** — set both `TAVUS_API_KEY` and `TAVUS_REPLICA_ID`
to enable it, otherwise the agent runs audio-only.

## Tools (AgentCore Gateway)

The agent exposes ten grocery tools, all backed by the live Gateway:

| Tool | What it does |
|---|---|
| `search_products` | Search the Aisle catalogue (brand, price, size, allergens, dietary tags, specials) |
| `get_product_variants` | Compare brand/variant options for a staple |
| `add_to_cart` | Add a product to the cart (by name or product_id) |
| `get_cart` | Read back the current cart and subtotal |
| `remove_from_cart` | Remove a product or reduce its quantity |
| `create_order` | Place a pickup order for everything in the cart |
| `get_offers` | Browse what's on special, biggest savings first |
| `get_grocery_list` | Read back the persistent grocery list (survives across sessions) |
| `update_grocery_list` | Add / mark-as-have / remove grocery-list items |
| `check_relevant_changes` | Flag list items now on special or out of stock |

The bot also forwards UI events (product grids, cart, order confirmation,
transcript) to the frontend over the Daily data channel.

## How this differs from the Fargate version

| | Fargate | AgentCore (this) |
|---|---|---|
| Compute | Always-on container behind ALB | Per-session, invoked via `InvokeAgentRuntime` |
| Start endpoint | ALB + CloudFront `/start` | **API Gateway** (`lambda/`) |
| Avatar | `TavusVideoService` republish | **`TavusTransport`** (bot + replica + user share one Tavus-created Daily room) |
| Networking | ALB / public | **VPC + NAT** (Daily uses UDP, blocked in PUBLIC mode) |
| AWS creds in container | env vars | **execution role** (no keys) |
| Entry point | `python tavus-pipecat.py ...` | `app.run()` (`@app.entrypoint`) |

## Architecture

```
Browser ──(WebRTC media)──> Daily room <──(joins)── AgentCore Runtime (Pipecat bot, VPC/private subnets)
   │                                                         │           │
   │                                                         │           └──> AgentCore Gateway (grocery tools, Aurora)
   │                                                         └──> AgentCore Memory (cross-session prefs)
   └──POST /start──> API Gateway ──Lambda──InvokeAgentRuntime─┘
```

When the Tavus avatar is enabled, the bot uses `TavusTransport`: it creates a
Tavus-managed Daily room that the bot, the Tavus replica, and the browser all
join, so the avatar serves synced audio + video natively. The Lambda waits for
the bot to report that room URL and returns it to the frontend.

## Prerequisites

- AWS account with AgentCore access in `ap-southeast-2`, plus Bedrock model access for `au.anthropic.claude-haiku-4-5`
- **Deepgram** account + API key (handles both STT and TTS)
- A **Daily** account (audio-only mode uses a pre-created room URL; with the Tavus avatar, the room is created per session by Tavus)
- *(Optional)* **Tavus** account for the avatar (`TAVUS_API_KEY` + `TAVUS_REPLICA_ID`)
- An **AgentCore Gateway** + **Memory** resource (grocery tools and cross-session prefs) — set `GATEWAY_URL` and `MEMORY_ID` in `agent/.env`
- Python 3.11+, `uv`, Docker (AgentCore container build), AWS CLI
- IAM permissions per the [AgentCore starter-toolkit docs](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html) plus EC2 VPC create/delete (for `setup-vpc.sh`)

## Setup

```bash
cd agentcore-pipecat
uv sync
cp agent/env.example agent/.env   # fill in API keys + AWS_REGION + GATEWAY_URL + MEMORY_ID
```

## Deploy

```bash
# 1. Configure the agent (creates IAM execution role + container config)
./scripts/configure.sh

# 2. Create VPC + NAT (one-time; NAT ~$32/month)
./scripts/setup-vpc.sh

# 3. Build + deploy the bot to AgentCore (writes the Agent ARN to lambda/start.env)
#    Re-run this alone after any agent code change.
./scripts/launch.sh

# 4. Deploy the /start Lambda (code/config only — exposure is via API Gateway,
#    NOT a public Function URL). Set DAILY_ROOM_URL
#    in lambda/start.env (or agent/.env) first.
./lambda/deploy.sh
```

## Run

```bash
# /start is fronted by API Gateway (prod stage):
curl -X POST https://<api-id>.execute-api.ap-southeast-2.amazonaws.com/prod
#   → { "room_url": "...", "status": "ok" }
```

Open the returned Daily room URL in a browser (or point the frontend's start
action at the **API Gateway** URL), allow the mic, and talk to Aisle.

> **Security note:** do **not** expose `/start` via a public Lambda Function URL (`AuthType=NONE`) — it is unauthenticated and anyone could launch bot sessions on your account. Use an API Gateway REST API instead. `lambda/deploy.sh` deliberately creates no Function URL, and deletes one if found. Any future CDK `ApiStack` must expose `/start` via API Gateway (or a Function URL with IAM auth), never a public Function URL.

## Local development (no AgentCore)

```bash
# In agent/.env set PIPECAT_LOCAL_DEV=1 and a real DAILY_ROOM_URL
PIPECAT_LOCAL_DEV=1 uv run agent/pipecat-agent.py -t daily -d
```

## Teardown

```bash
./scripts/destroy.sh       # remove the AgentCore agent
./scripts/cleanup-vpc.sh   # remove VPC + NAT (stops the NAT cost)
# Delete the Lambda + role manually if no longer needed.
```

## Notes / not-yet-done

- **Frontend wiring**: point the React frontend's start call at the **API Gateway** URL (`https://<api-id>.execute-api.ap-southeast-2.amazonaws.com/prod`).
- **Nova Sonic mode** was intentionally dropped for this first cut (cascaded only).
- **Production hardening**: API Gateway method auth is currently `NONE`; lock it to the CloudFront origin (CORS/auth), and consider a per-session Daily room + meeting token instead of one shared room. Never expose `/start` via a public (unauthenticated) Lambda Function URL.
