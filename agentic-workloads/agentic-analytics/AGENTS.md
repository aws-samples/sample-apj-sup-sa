# AGENTS.md — Agentic Analytics

## Project Overview

A deployable demo + workshop for agentic AI self-service analytics. Business users ask questions in natural language — **by text or by voice** — and a Strands agent selects tools via MCP Gateway, queries Aurora PostgreSQL, and returns formatted insights (including **generated charts**).

**Scenario:** Timely-Unicorn — multi-tenant SaaS for unicorn rental businesses. Two tenants (Mythical Unicorns, Mythic Unicorns) plus the platform account.

**Two ways to ask:**
- **Text chat** — the React UI calls the **front-door gateway** (`<FrontDoorUrl>/agent/invocations`) with the user's Cognito JWT. The gateway validates the JWT, applies AWS WAF and a per-user rate limit, then invokes the runtime with its own IAM role.
- **Voice** — a Pipecat pipeline (Deepgram STT → AgentCore → Deepgram TTS) bridges speech to the *same* agent. Text and voice turns in one session share a single AgentCore Memory thread, so context carries across both.

Both paths invoke the same Strands agent with the same per-user JWT, so RBAC/RLS is identical regardless of modality. A per-request `mode` field (`text` | `voice`) tells the agent how to format its reply (plain markdown vs. a spoken `<speak>` headline + on-screen detail). Charts work in both modes.

## Two Deployment Modes

- **Workshop mode** (primary): Deploys base infrastructure only (Aurora, Cognito, Glue, Bedrock KB, EC2 Code Editor) via CFN `DeployMode=workshop`. Participants then deploy ONE **AgentCore top-up stack** (`infrastructure/stacks/agentcore-topup-stack.yaml`, shipped to the EC2 box via the `workshop/code/` overlay) and build the agent layer step-by-step by uncommenting one fenced section per lab step and running `make deploy` — no `agentcore` CLI, no imperative `deploy_*.py` scripts. The participant-facing copy is commented down to a Step-2 baseline; `make build` rebuilds the agent image after code edits. Documented script exceptions: the optional Cube lab (`infra/deploy_cube_models.py`, `deploy_semantic_layer_*.py`) and `agentcore eval` (Step 10).
- **Demo mode**: Deploys everything in one shot — base infra + the AgentCore layer (Gateway, Runtime, Memory, toolsets, Cedar, Guardrail) + Amplify-hosted UI — via `infrastructure/scripts/deploy_backend.sh` (nested CFN, `DeployMode=demo`). `--agent-only` / `--voice-only` give fast re-iterate paths.

## Architecture

```
            ┌─ Text: React UI ──────────────────────────────┐
User asks ──┤                                                 ├─► Front-door Gateway (Cognito JWT + AWS WAF + per-user rate limit)
            └─ Voice: Mic → Pipecat (Deepgram STT/TTS) ──────┘         → AgentCore Runtime, IAM-only (Strands Agent + SOP + Memory)
                                                                          → MCP Gateway (Cedar Policy + Interceptor)
                                                                          → PrebakedSQL Lambda (29 tools, DB Views)
                                                                          → APIInteg Lambda (create_booking)
                                                                          → CustomSQL Lambda (Glue schema + Bedrock KB RAG, read-only SQL guard)
                                                                          → BusinessContext (Managed KB connector, Retrieve)
                                                                          → WebSearch (AgentCore Web Search connector)
                                                                          → CodeInterpreter (matplotlib → S3 → <chart> tag)
                                                                              → Aurora PostgreSQL (RLS by tenant)
```

### Front door (demo stack: `infrastructure/stacks/agentcore-stack.yaml`)

- **Gateway:** `FrontDoorGateway` has no `ProtocolType`, so it is an HTTP gateway (only those can carry a runtime target). Its `agent` target uses `GATEWAY_IAM_ROLE` and forwards two headers: `X-Amzn-Bedrock-AgentCore-Runtime-Custom-User-Token` and `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id`.
- **Runtime:** IAM auth, no JWT authorizer. `AgentRuntimeResourcePolicy` denies every principal except the front door's role, so the WAF and rate limit cannot be bypassed.
- **Clients:** the UI and the voice bot send the JWT as `Authorization: Bearer` (checked by the gateway) and again in the custom user-token header. Behind the gateway, the runtime's `Authorization` header carries the gateway's SigV4 signature, so the agent reads the user token from the custom header, verifies it (signature, issuer, `token_use`, client id) before using its `sub` as the memory actor, and forwards it to the tools gateway, which validates it again. There is no `gateway_token` payload field.
- **Session:** both front-ends send the SAME app session id as `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id`, so one conversation can interleave text and voice turns against one Memory thread.
- **WAF:** attached with `AWS::WAFv2::WebACLAssociation`; the Gateway resource's own `WebAclArn` property is ignored by CloudFormation today. Per-IP rate, IP reputation and known-bad-input rules BLOCK. The Common and SQLi rule groups follow `WafBodyRulesAction` (default `COUNT`, because prompts are free text).
- **Direct fallback:** without a front-door URL (early workshop steps, local dev) the clients call the runtime directly and it keeps its own JWT authorizer. The agent reads the custom header first and falls back to `Authorization`.

### Cedar policies on the tools gateway

- **RBAC:** base permit for authenticated users; forbids for analyst writes, staff custom SQL and staff web search.
- **Guardrails in policy** (`Definition.Policy`, not `Definition.Cedar`): block text-to-SQL questions scoring > 0.4 for prompt injection or jailbreak; block web searches whose query scores > 0.4 for NAME, EMAIL or PHONE. ADDRESS is left out because place names trip it. The gateway role needs `bedrock:InvokeGuardrailChecks` on `*`.
- **Temporal rule:** `create_booking_tool` is refused unless `get_unicorn_availability_tool` ran for the same `unicorn_id` in the same policy session within 15 minutes. With any temporal rule in the engine, every tools call must carry `X-Amzn-Bedrock-AgentCore-Policy-Session-Id`; the agent sends its runtime session id. A policy change invalidates open sessions (409), so start a new chat after `make deploy`.
- Switches: `EnablePolicyGuardrails`, `EnableBookingOrderPolicy` (demo stack); workshop Step 7b.

### Schema change pipeline

Step Functions `<env>-schema-sync`, started by a Glue crawler success (EventBridge), daily at 18:00 UTC (demo only) or by hand: Snapshot (pg_catalog: columns, comments, RLS, policies, view SQL) → Classify against `schema/current.json` (none / additive / breaking / blocked) → Describe (agent model) → WaitForApproval for breaking changes (task token in `schema/pending/`) → Publish (Glue table descriptions and column lists rebuilt from the snapshot, so the text-to-SQL tool sees a rename before the next crawl; `docs/schema/<name>.md` KB cards, KB ingestion with retry on ConflictException, EventBridge `agentic-analytics.schema`). A table with `account_id` but no RLS policy fails the run, including when a migration only disables RLS or drops the policy (`rls_changed`). A renamed table's Glue entry moves to the new name and a dropped table's is deleted. Publish skips a run whose snapshot is older than the published one, so approving runs out of order cannot roll the schema back. Input `{"rebuild": true}` republishes everything. Only table/view drops delete cards; a dropped column rewrites its table's card.

### Read-only SQL guard (`execute_sql_tool`)

`validate_readonly_sql` blanks literals, quoted names and comments, then allows one SELECT/WITH statement with no blocked function (`set_config`, `pg_sleep`, file and large-object functions...). It refuses what it cannot model: `E'...'` escape strings, `U&` Unicode escapes, `UESCAPE`, backslashes in quoted names. A `$` right after a name character is part of the name, not a dollar quote. At run time the transaction is READ ONLY with `standard_conforming_strings = on`, and if the query changed `app.current_account_id` or `app.current_user_role` its rows are dropped and the call fails.

### Schema context for text-to-SQL

`text_to_sql_tool` sends the whole Glue schema while it fits `SCHEMA_FULL_MAX_CHARS` (32,000); above that it sends a one-line-per-table index and the model calls again with `tables`. The threshold comes from the benchmark in `dev/evaluation/schema-context/` (method in its `README.md`, per-question raw results in `results.json`): on the schema padded to 181 objects, 60 questions scored 85% with the full schema at ~51k input tokens and 90% with the index at ~11.6k. That is one synthetic schema and one model, so rerun it on your own schema before relying on the number.

### Memory and evaluation

- **Memory:** short-term events plus long-term `EpisodicMemoryStrategy` and `UserPreferenceMemoryStrategy`, namespaced per user (`/analytics/{actorId}/...`). The agent pulls up to 3 preferences and 2 past episodes into the system prompt each turn (`_long_term_context`, fails open).
- **Online evaluation:** `AWS::BedrockAgentCore::OnlineEvaluationConfig` samples finished sessions (default 10%) and scores Correctness, Faithfulness, Helpfulness, ToolSelectionAccuracy and GoalSuccessRate into CloudWatch (`Bedrock-AgentCore/Evaluations`). CloudFormation accepts only `Builtin.*` or custom evaluator ids.

### Charts (both modes)

The agent renders charts in an AgentCore **Code Interpreter** sandbox (matplotlib), which uploads the PNG to `s3://<artifacts>/charts/` and prints only the S3 key. The agent emits a short `<chart s3key="charts/….png" />` tag in its output; the **agent's outbound stream loop presigns that key and rewrites the tag to `<chart url="…" />`** before it leaves the runtime. So:
- The presigned URL is created once, server-side; the short S3 key — never the URL — is what lands in Memory. Only keys matching `charts/<name>.png` are presigned (the model writes the tag, and the bucket also holds the raw dataset), and the runtime role can read only `sops/*` and `charts/*`.
- Both consumers receive the identical `<chart url="…" />`: the **voice bot** re-emits it as an RTVI `chart` message; the **text UI** parses it from the stream. Same render path in `ChatPanel.js`.

## Key Files

| What | Where |
|------|-------|
| Main agent | `app/agentcore_strands/unicorn_rental_agent.py` |
| SOP (one file, mode-conditional) | `app/agentcore_strands/unicorn_rental_analytics.sop.md` |
| Lambda tools | `app/agentcore_strands/tools/*.py` |
| Cube lab scripts (documented exception) | `app/agentcore_strands/infra/deploy_cube_models.py`, `deploy_semantic_layer_{toolset,stack}.py` |
| Gateway interceptor Lambda | `app/agentcore_strands/infra/interceptor_lambda.py` |
| Schema change pipeline Lambda + diff | `app/schema_sync/schema_sync_lambda.py`, `app/schema_sync/schema_diff.py` (tests next to them) |
| Schema change pipeline stack | `infrastructure/stacks/schema-sync-stack.yaml` (demo, nested); workshop: Step 11 fence in the top-up stack |
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
make deploy POLICY_MODE=ENFORCE   # POLICY_MODE and FRONT_DOOR stick: a later bare `make deploy` keeps them
make schema-sync                  # Step 11: start a schema pipeline run
make approve                      # Step 11: approve the run waiting at WaitForApproval (or `make reject`)
# Each lab step = uncomment one fenced block (or flip one toggle) + `make deploy`.

# UI (dev mode)
cd app/ui && npm install && npm start    # Vite dev server on 127.0.0.1:3001, base /app/

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
- Model: `global.anthropic.claude-sonnet-5-5` by default (Global CRIS), chosen by the `AgentModelId` stack parameter (`global.anthropic.claude-opus-5-5` or `global.anthropic.claude-opus-4-6-v1` also allowed). Use `global.*` profile IDs: in Workshop Studio sandbox accounts `us.anthropic.claude-haiku-5-5` is denied (private marketplace) while `global.*` works. Claude Opus 4.8 and all Claude 5.x reject `temperature`; the agent sends it only to the older models in `TEMPERATURE_MODELS`. Fresh sandbox accounts are not pre-subscribed to every model, so the `ModelSubscription` custom resource accepts the Marketplace agreement for each base model in `ModelIds` at deploy time (idempotent, best-effort).

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

## Test Users

All demo users share one password, generated at deploy time and stored in the SecureString parameter `/<env>/demo/users`. Workshop: `make users`. Demo: the SSM command printed at the end of `deploy_backend.sh`.

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
