# Agentic Analytics for Multi-tenant SaaS with AgentCore

A reference implementation and [AWS Workshop Studio](https://workshops.aws/) deployable for building AI-powered self-service analytics on multi-tenant SaaS. Business users ask questions in plain English — by **text or by voice** — the AI agent selects the right query, enforces security policies, and returns formatted insights, including **generated charts**.

Built with [Amazon Bedrock AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html), [Strands Agents SDK](https://strandsagents.com/latest/), Aurora PostgreSQL, and Cedar policies.

## Architecture

![Full Architecture](workshop/static/images/full-architecture.png)

### How It Works

1. **User** asks a question in the React chat UI (e.g., *"Who are my top 3 customers this month?"*)
2. **Cognito** authenticates the user and issues a JWT with tenant (`account_id`) and role (`admin`/`analyst`) claims
3. **AgentCore Runtime** hosts a Strands agent that interprets the query and selects the right tool(s)
4. **MCP Gateway** routes tool calls to Lambda functions, enforcing Cedar RBAC policies and propagating the JWT
5. **Lambda Tools** execute parameterized SQL against Aurora PostgreSQL, with Row-Level Security filtering data by tenant
6. **Agent** formats the results and streams them back to the UI — as text, a markdown table, and/or a generated chart image
7. **(Optional) Voice** — the same agent answers spoken questions: a Pipecat pipeline (its own AgentCore Runtime, WebRTC + Amazon KVS managed TURN, Deepgram STT/TTS) calls the analytics agent and speaks the answer back

### Key Components

| Component | Purpose |
|-----------|---------|
| **React UI** | Chat interface with streaming responses, SQL approval workflow, rendered charts, and optional voice mode |
| **AgentCore Runtime** | Hosts the Strands agent with memory, SOP, and guardrails |
| **Voice Runtime** *(optional)* | A second AgentCore Runtime running the Pipecat voice pipeline (WebRTC + KVS TURN, Deepgram STT/TTS) that invokes the analytics agent |
| **Chart Code Interpreter** | Sandboxed matplotlib rendering → PNG to S3; the agent returns a short presigned `<chart>` tag (no base64 in the stream) |
| **MCP Gateway** | Authenticated tool routing with Cedar policy enforcement |
| **Prebaked SQL Toolset** | 27+ analytics tools backed by database Views |
| **API Integration Toolset** | Write operations (e.g., create booking) with tenant-scoped inserts |
| **Custom SQL Toolset** | Text-to-SQL with Glue schema + Bedrock KB RAG + human approval |
| **Aurora PostgreSQL** | Multi-tenant data store with RLS and pgvector for semantic search |
| **Bedrock Knowledge Base** | Business context retrieval for RAG-augmented custom SQL |
| **Cognito** | Authentication with custom claims for tenant and role |
| **CloudWatch + X-Ray** | End-to-end observability via GenAI Observability dashboard |

## Multi-Tenancy: JWT + Row-Level Security

Every request carries a JWT from Cognito containing `custom:account_id` and `custom:role`. The MCP Gateway interceptor propagates this token to each Lambda tool, which extracts the claims and sets PostgreSQL session variables:

```sql
SET app.current_account_id = '<account_id_from_jwt>';
SET app.current_user_role = '<role_from_jwt>';
```

All tables have RLS policies that filter rows by `account_id`, so `SELECT * FROM bookings` automatically returns only the current tenant's data. No application-level filtering needed — the database enforces isolation.

**Pre-Token Lambda V2** enriches the Cognito access token with `custom:role` and `custom:account_id` claims, making them available for both RLS and Cedar policy evaluation.

## Role-Based Access Control: Cedar Policies

The AgentCore Gateway uses a Cedar Policy Engine to control which tools each role can access:

- **Default policy**: All authenticated users can access all read-only analytics tools
- **Role restriction**: Only `admin` users can access write tools (e.g., `create_booking_tool`). Analysts are blocked at the gateway level — the tool is hidden from the agent entirely

When the policy engine is in **ENFORCE** mode, unauthorized tool calls are blocked before reaching the Lambda. In **LOG_ONLY** mode, decisions are logged but all calls are allowed (useful for testing).

## Workshop

This repository is designed as a deployable for **AWS Workshop Studio**. The workshop guides participants through building the system step by step:

| Step | What You Build |
|------|---------------|
| 0 | Environment setup on EC2 Code Editor |
| 1 | Basic local agent (exercise) |
| 2 | Agent infrastructure — Gateway + Runtime |
| 3 | React chat UI connected to AgentCore |
| 4 | Prebaked SQL toolset (27+ analytics tools) |
| 5 | API integration toolset (booking creation) |
| 6 | Custom SQL with Glue + Bedrock KB RAG + human approval |
| 7 | Multi-tenant isolation (Cedar + JWT → PostgreSQL RLS) |
| 8 | Guardrails (topic blocking, PII filtering) |
| 9 | Observability (CloudWatch + X-Ray tracing) |
| 10 | Evaluation (LLM-as-a-Judge with Strands Evals) |
| Optional | Semantic layer with Cube Core |
| Optional | Voice — talk to your data (Pipecat + WebRTC + KVS TURN, no 3rd-party SFU) |

Workshop content is in the [`workshop/`](workshop/) directory. For hands-on instructions, deploy via Workshop Studio or follow the Hugo markdown in `workshop/content/`.

### Voice & charts

Beyond text chat, the agent can **speak** and **draw**:

- **Charts** — when a question calls for a visual, the agent renders a real chart in a sandboxed Code Interpreter, uploads the PNG to S3, and returns a short presigned `<chart>` tag the UI renders. Image bytes never cross the model stream. Works in both text and voice.
- **Voice** — an optional second AgentCore Runtime hosts a [Pipecat](https://www.pipecat.ai/) pipeline (Deepgram STT/TTS). Two transports are supported: **`agentcore`** (WebRTC over **Amazon Kinesis Video Streams (KVS) managed TURN** — no third-party media vendor; both the runtime and the browser fetch the same TURN creds, the browser via the signaling proxy's JWT-gated `GET /api/ice`, so ICE can traverse NAT to the VPC runtime) and **`pipecat-cloud`** (Daily's hosted SFU). Either way it invokes the *same* analytics agent over the same JWT, so RBAC/RLS and the conversation memory thread are shared across text and voice. See [`DEPLOYMENT.md`](DEPLOYMENT.md) for the voice deploy modes.

### Workshop Deployment

The workshop uses CloudFormation to pre-provision base infrastructure (Aurora, Glue, Bedrock KB, Cognito, EC2 Code Editor). Participants then deploy a single **AgentCore top-up CloudFormation stack** and build the agent layer step-by-step — each lab step uncomments one fenced section of the template (or flips one value) and redeploys with `make deploy`. There's no `agentcore` CLI in the runtime lifecycle; the container image is built by CloudFormation via CodeBuild. See [`workshop/contentspec.yaml`](workshop/contentspec.yaml) for the Workshop Studio configuration.

## Demo Deployment (Full Automation)

Demo mode deploys everything including AgentCore Gateway and Amplify UI. Uses the same packaging script as Workshop mode, only `DeployMode` differs.

> **Artifacts bucket:** pick a globally-unique, account-scoped name (e.g.
> `agentic-analytics-demo-<account-id>`) — a bare `agentic-analytics-demo` may
> already be owned by another account and fail with `AccessDenied`.
>
> **npm registry:** the UI build (`package_and_upload.sh`) runs `npm install`. The
> repo pins the public registry in `app/ui/.npmrc`, so no private/CodeArtifact
> auth is needed — a developer's global `~/.npmrc` pointing at an authenticated
> mirror won't break the build.

```bash
# 1. Create S3 bucket for artifacts (account-scoped name)
aws s3 mb s3://agentic-analytics-demo-<account-id> --region us-west-2

# 2. Upload deployment artifacts (demo is the default mode — packages agent code, datafoundation Lambda, psycopg2 layer, amplify Lambda, and UI build in addition to workshop artifacts)
cd infrastructure/scripts
./package_and_upload.sh agentic-analytics-demo-<account-id>

# 3. Deploy demo stack (use the command output from step 2, changing DeployMode to demo and stack name to agentic-analytics-demo)
aws cloudformation create-stack \
  --stack-name agentic-analytics-demo \
  --template-url https://your-artifacts-bucket.s3.us-west-2.amazonaws.com/templates/main-stack.yaml \
  --parameters \
      ParameterKey=ArtifactsBucket,ParameterValue=your-artifacts-bucket \
      ParameterKey=DeployMode,ParameterValue=demo \
      ParameterKey=DatabaseInitLambdaKey,ParameterValue=<from-output> \
      ParameterKey=GlueCrawlerLambdaKey,ParameterValue=<from-output> \
      ParameterKey=BedrockKBLambdaKey,ParameterValue=<from-output> \
      ParameterKey=AmplifyLambdaKey,ParameterValue=<from-output> \
      ParameterKey=InterceptorLambdaKey,ParameterValue=<from-output> \
      ParameterKey=ApiIntegLambdaKey,ParameterValue=<from-output> \
      ParameterKey=CustomSqlLambdaKey,ParameterValue=<from-output> \
      ParameterKey=SemanticLayerLambdaKey,ParameterValue=<from-output> \
      ParameterKey=ObservabilityLambdaKey,ParameterValue=<from-output> \
      ParameterKey=UIBuildKey,ParameterValue=<from-output> \
      ParameterKey=AgentCodeS3Key,ParameterValue=agent/agent_code.zip \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
  --region us-west-2
```

### Demo with voice

Voice is off by default (Voice button hidden). To deploy with voice, add these to the
create/update parameters and pick a mode:

```
ParameterKey=EnableVoice,ParameterValue=true
ParameterKey=VoiceMode,ParameterValue=agentcore        # or pipecat-cloud
ParameterKey=DeepgramApiKey,ParameterValue=<key>
ParameterKey=DeepgramVoiceId,ParameterValue=aura-2-apollo-en
```

- **`agentcore`** — fully CFN: a second AgentCore Runtime (Pipecat) + WebRTC over
  Amazon KVS managed TURN + a JWT signaling proxy. One deploy, no third-party SFU.
- **`pipecat-cloud`** — deploy with `VoiceMode=pipecat-cloud`, then run the post-deploy
  finisher (Pipecat Cloud is external SaaS that can't be pure CFN):
  ```bash
  PCC_PAT=… PCC_PUBLIC_KEY=… PCC_PRIVATE_KEY=… DEEPGRAM_API_KEY=… DAILY_API_KEY=… \
    infrastructure/scripts/deploy_voice_pcc.sh
  ```

Both modes invoke the *same* analytics agent with the signed-in user's JWT, so RBAC/RLS
and the shared memory thread are identical to text. See [`DEPLOYMENT.md`](DEPLOYMENT.md)
for details (including the agentcore voice teardown caveat — VPC ENIs reclaim slowly).

## Project Structure

```
├── app/
│   ├── agentcore_strands/       # Strands agent + Lambda tools
│   │   ├── unicorn_rental_agent.py          # main agent entrypoint
│   │   ├── unicorn_rental_analytics.sop.md  # SOP (one file, text/voice mode-conditional)
│   │   ├── tools/               # Lambda toolsets (prebaked SQL, API integration, custom SQL, semantic layer)
│   │   ├── infra/               # Cube lab scripts (deploy_cube_models, deploy_semantic_layer_*) + the Gateway interceptor Lambda
│   │   ├── agent/               # semantic-layer agent variant (Cube lab)
│   │   ├── ui/                  # Amplify hosting deploy helper (demo mode)
│   │   └── tests/               # agent unit tests
│   ├── ui/                      # React frontend (text chat + charts + optional voice)
│   └── voice/                   # Pipecat voice bot (optional) — runs as its own AgentCore Runtime
├── infrastructure/
│   ├── stacks/                  # CloudFormation templates (nested stacks; incl. agentcore-topup-stack.yaml, voice-agentcore-stack.yaml)
│   ├── custom-resource-lambdas/ # Custom Resource Lambda handlers (DB init, Glue crawler, Bedrock KB ingestion)
│   ├── voice-proxy/             # JWT signaling/start proxy for the voice modes (optional)
│   ├── voice-pcc-cr/            # Pipecat Cloud custom resource (pipecat-cloud voice mode, optional)
│   ├── config/                  # deployment-config sample
│   ├── scripts/                 # Deployment and packaging scripts
│   └── tests/                   # infrastructure tests
├── workshop/
│   ├── content/                 # Workshop guide (Steps 0–10 + optional Cube & Voice, grouped into modules)
│   ├── code/                    # Code overlays with TODO placeholders (incl. the participant Makefile + top-up stack)
│   └── static/images/           # Architecture diagrams
├── dataset/                     # → symlink to unicorn-rental-dataset
├── exercises/                   # Learning exercises (basic_agent.py)
├── common/                      # Shared utilities (build/amplify helpers)
└── dev/                         # Maintainer-only tooling (NOT shipped): eval harness, specs, skills, app-index.json
```

> **Note on the AgentCore layer:** Gateway, Runtime, Memory, the toolset Lambdas, the Cedar policy engine, and the Bedrock Guardrail are all **CloudFormation resources** in `infrastructure/stacks/agentcore-topup-stack.yaml` (workshop) / `agentcore-stack.yaml` (demo) — not standalone `deploy_*.py` scripts. The pre-token Lambda lives in `cognito-stack.yaml`.

## The Scenario: Timely-Unicorn

**Timely-Unicorn** is a fictional multi-tenant SaaS platform for unicorn rental businesses. Two rental companies — Mythical Unicorns and Mythic Unicorns — each manage their own fleet of unicorns, customers, bookings, and revenue. The synthetic dataset includes ~14,000 bookings, 500 customers, and 100 unicorns across 2 tenant accounts (plus the platform account).

Staff and analysts need answers like *"Who are my top 3 customers this month?"* or *"Create a 3-hour booking for customer X with unicorn Y"* — but they don't know SQL. The AI assistant solves this for all tenants simultaneously, with full data isolation.

## License

This project is licensed under the MIT-0 License. See the [LICENSE](LICENSE) file.

The synthetic dataset (`dataset/`) is licensed under CC0-1.0. See [dataset/LICENSE](dataset/LICENSE).
