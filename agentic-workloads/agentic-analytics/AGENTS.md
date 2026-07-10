# AGENTS.md — Agentic Analytics

## Project Overview

A deployable demo + workshop for agentic AI self-service analytics. Business users ask questions in natural language — **by text or by voice** — and a Strands agent selects tools via MCP Gateway, queries Aurora PostgreSQL, and returns formatted insights (including **generated charts**).

**Scenario:** Timely-Unicorn — multi-tenant SaaS for unicorn rental businesses. Two tenants (Mythical Unicorns, Mythic Unicorns) plus the platform account.

**Two ways to ask:**
- **Text chat** — the React UI invokes AgentCore Runtime directly over HTTPS with the user's Cognito JWT as a Bearer token (the runtime's CustomJWTAuthorizer validates it).
- **Voice** — a Pipecat pipeline (Deepgram STT → AgentCore → Deepgram TTS) bridges speech to the *same* agent. Text and voice turns in one session share a single AgentCore Memory thread, so context carries across both.

Both paths invoke the same Strands agent with the same per-user JWT, so RBAC/RLS is identical regardless of modality. A per-request `mode` field (`text` | `voice`) tells the agent how to format its reply (plain markdown vs. a spoken `<speak>` headline + on-screen detail). Charts work in both modes.

## Two Deployment Modes

- **Workshop mode** (primary): Deploys base infrastructure only (Aurora, Cognito, Glue, Bedrock KB, EC2 Code Editor) via CFN `DeployMode=workshop`. Participants then deploy ONE **AgentCore top-up stack** (`infrastructure/stacks/agentcore-topup-stack.yaml`, shipped to the EC2 box via the `workshop/code/` overlay) and build the agent layer step-by-step by uncommenting one fenced section per lab step and running `make deploy` — no `agentcore` CLI, no imperative `deploy_*.py` scripts. The participant-facing copy is commented down to a Step-2 baseline; `make build` rebuilds the agent image after code edits. Documented script exceptions: the optional Cube lab (`infra/deploy_cube_models.py`, `deploy_semantic_layer_*.py`) and `agentcore eval` (Step 10).
- **Demo mode**: Deploys everything in one shot — base infra + the AgentCore layer (Gateway, Runtime, Memory, toolsets, Cedar, Guardrail) + Amplify-hosted UI — via `infrastructure/scripts/deploy_backend.sh` (nested CFN, `DeployMode=demo`). `--agent-only` / `--voice-only` give fast re-iterate paths.

## Architecture

```
            ┌─ Text: React UI ──────────────────────────────┐
User asks ──┤                                                 ├─► AgentCore Runtime (Strands Agent + SOP + Memory)
            └─ Voice: Mic → Pipecat (Deepgram STT/TTS) ──────┘         → MCP Gateway (Cedar Policy + Interceptor)
                                                                          → PrebakedSQL Lambda (29 tools, DB Views)
                                                                          → APIInteg Lambda (create_booking)
                                                                          → CustomSQL Lambda (Glue schema + Bedrock KB RAG)
                                                                          → CodeInterpreter (matplotlib → S3 → <chart> tag)
                                                                              → Aurora PostgreSQL (RLS by tenant)
```

Both front-ends send the user's Cognito JWT as the `Authorization: Bearer` request header (the runtime's CustomJWTAuthorizer validates it and passes it through to the agent; there is no `gateway_token` payload field) and the SAME app session id as `runtimeSessionId`, so one conversation can interleave text and voice turns against one Memory thread.

### Charts (both modes)

The agent renders charts in an AgentCore **Code Interpreter** sandbox (matplotlib), which uploads the PNG to `s3://<artifacts>/charts/` and prints only the S3 key. The agent emits a short `<chart s3key="charts/….png" />` tag in its output; the **agent's outbound stream loop presigns that key and rewrites the tag to `<chart url="…" />`** before it leaves the runtime. So:
- The presigned URL is created once, server-side (agent role has `s3:GetObject`); the short S3 key — never the URL — is what lands in Memory.
- Both consumers receive the identical `<chart url="…" />`: the **voice bot** re-emits it as an RTVI `chart` message; the **text UI** parses it from the stream. Same render path in `ChatPanel.js`.

## Key Files

| What | Where |
|------|-------|
| Main agent | `app/agentcore_strands/unicorn_rental_agent.py` |
| SOP (one file, mode-conditional) | `app/agentcore_strands/unicorn_rental_analytics.sop.md` |
| Lambda tools | `app/agentcore_strands/tools/*.py` |
| Cube lab scripts (documented exception) | `app/agentcore_strands/infra/deploy_cube_models.py`, `deploy_semantic_layer_{toolset,stack}.py` |
| Gateway interceptor Lambda | `app/agentcore_strands/infra/interceptor_lambda.py` |
| Semantic-layer agent variant | `app/agentcore_strands/agent/unicorn_rental_semantic_agent.py` |
| Workshop AgentCore top-up | `infrastructure/stacks/agentcore-topup-stack.yaml` (canonical) + `workshop/code/app/agentcore_strands/{agentcore-topup-stack.yaml,Makefile}` (fenced participant copy) |
| CFN stacks | `infrastructure/stacks/*.yaml` |
| React UI | `app/ui/` |
| Voice bot (Pipecat) | `app/voice/bot.py`, `app/voice/analytics_processor.py`, `app/voice/auth.py` |
| Voice CFN stack | `infrastructure/stacks/voice-agentcore-stack.yaml` (own AgentCore Runtime, WebRTC + KVS TURN, + signaling proxy) |
| Voice post-deploy (PCC fallback) | `infrastructure/voice-proxy/`, `infrastructure/voice-pcc-cr/`, `infrastructure/scripts/deploy_voice_pcc.sh` |
| Dataset | `dataset/` (symlink → `../unicorn-rental-dataset`) |
| Workshop content | `workshop/content/` (Hugo markdown for Workshop Studio) |
| Workshop overlays | `workshop/code/` (TODO versions for exercises) |
| Project file index | `dev/app-index.json` |

## Build & Deploy Commands

The Gateway, toolset Lambdas, AgentCore Memory, observability, Cedar policy
engine and Bedrock Guardrail are all **native CloudFormation resources** now —
no `agentcore` CLI and no imperative `deploy_*.py` scripts in the runtime
lifecycle.

```bash
# Demo mode — one command deploys the whole backend (nested CFN via main-stack.yaml)
bash infrastructure/scripts/deploy_backend.sh
# Fast agent-code iteration (re-zip + rebuild image + bump runtime, no full stack):
bash infrastructure/scripts/deploy_backend.sh --agent-only

# Workshop mode — participant edits ONE template then redeploys (on EC2 Code Editor)
# On the Code Editor box the workshop/code/ overlay has placed the Makefile +
# agentcore-topup-stack.yaml into app/agentcore_strands/ (in this repo they live in
# workshop/code/app/agentcore_strands/ and infrastructure/stacks/).
cd app/agentcore_strands
make deploy                       # deploy/redeploy the AgentCore top-up stack
make build                        # rebuild the agent image after a code edit
make outputs                      # show GatewayUrl / AgentRuntimeArn
# Each lab step = uncomment one fenced block (or flip one toggle) + `make deploy`.

# UI (dev mode)
cd app/ui && npm install && npm start    # Port 3001, PUBLIC_URL=/app

# Voice bot — laptop dev (Pipecat pipeline + local UI)
cd app/voice && uv sync && uv run bot.py --transport daily
# or: bash infrastructure/scripts/run_voice_laptop.sh
# Hosted voice: deploy main CFN with EnableVoice=true VoiceMode=agentcore|pipecat-cloud
# (agentcore = Pipecat on its own AgentCore Runtime, WebRTC+KVS TURN; fast iterate with
#  deploy_backend.sh --voice-only. pipecat-cloud also needs: deploy_voice_pcc.sh.) See DEPLOYMENT.md.

# Workshop packaging (see dev/skills/workshop-deployment/)
cd infrastructure/scripts && bash package_for_workshop.sh
```

## Code Style & Conventions

- Python 3.11/3.12, no type hints enforced
- Lambda handlers: `lambda_handler(event, context)` → route by tool name from `context.client_context.custom['bedrockAgentCoreToolName']`
- RLS pattern: `rls_context` extracted from JWT in `lambda_handler`, passed as parameter through `handler(args, rls_context)` → `get_db_connection(rls_context)`
- **Never use global variables for request-scoped state** (prevents cross-tenant leakage on Lambda container reuse)
- Deploy scripts must be **idempotent**: delete existing Gateway target before creating, always update IAM policies
- Glue table names are prefixed `{db}_public_` by Crawler — strip prefix in `get_schema_context` so LLM sees real PostgreSQL names
- Model: `global.anthropic.claude-opus-4-6-v1` (Global CRIS). NOTE: fresh sandbox accounts are NOT pre-subscribed to every Bedrock model — without a subscription the runtime gets AccessDenied (`aws-marketplace:Subscribe ... to enable access to this model`) on every turn. The `ModelSubscription` custom resource in the agentcore stacks accepts the model's Bedrock Marketplace agreement at deploy time (idempotent) so the runtime can invoke it. (Opus 4.8/4.7 additionally appear gated behind account Containment Score ≥350; 4.6-v1 needs only the subscription.)

**Voice + mode conventions:**
- The agent reads `payload["mode"]` (`text` | `voice`, default `text`) and follows the matching branch of the single SOP's Response Formatting section. There is no separate voice SOP and no `sop_s3_key` file-swap.
- Voice mode replies lead with exactly one `<speak>…</speak>` block (1–3 spoken sentences, no markup) followed by the full on-screen answer; text mode replies are plain markdown. Never speak tables/SQL/UUIDs.
- Charts: the agent emits `<chart s3key="…">`; the runtime's stream loop presigns and rewrites to `<chart url="…">` outbound only — keep the URL out of `agent.messages`/Memory.
- Hosted voice forwards the SIGNED-IN user's own token (per-user RBAC/RLS). There is **no demo identity** in hosted mode; laptop dev may set `ALLOW_DEMO_FALLBACK=true` to mint a token via Cognito ROPC.

## Workshop Overlay System

`workshop/code/` contains files with TODO placeholders that override `app/` files in the packaged repo ZIP. See `dev/skills/overlay-management/SKILL.md` for rules.

**Critical rule:** When editing `app/` files that have overlays in `workshop/code/`, sync changes to both. If an overlay has no TODOs left, delete it.

## Config

- `config.env` — Runtime config on EC2 (not in git). Generated by CFN, appended by deploy scripts.
- `config.env.sample` — Template
- `dev/app-index.json` — Project file index with summaries of every significant file. **Must be updated** when adding new files, renaming files, or making changes that invalidate existing summaries. Read this first when you need to understand the project structure. If changes don't invalidate existing content, no update needed.

## Source Control

- **GitHub** (this sample lives under): `aws-samples/sample-apj-sup-sa` → `agentic-workloads/agentic-analytics/`
- **Workshop Studio**: published from the `workshop/` directory (see `workshop/contentspec.yaml`)

## Test Users (password: Unicorn123!)

| User | Role | Tenant |
|------|------|--------|
| lyra.starwhisper@example-mythicalunicorns.com | rental_admin | Mythical Unicorns |
| stella.moonbeam@example-mythicalunicorns.com | staff | Mythical Unicorns |
| orion.moonshadow@example-mythicalunicorns.com | analyst | Mythical Unicorns |
| aria.skybloom@example-mythicunicorns.com | rental_admin | Mythic Unicorns |

## Skills

See `dev/skills/` for procedural skills:
- `workshop-deployment` — Package, sync, and push to Workshop Studio
- `overlay-management` — Manage workshop code overlays

## Specs

See `dev/specs/agentic-analytics/` for the project spec (`requirements.md`, `design.md`, `tasks.md`).
