# Claude Code Proxy on AWS

An Anthropic-compatible proxy service on AWS. Issues virtual API keys to users authenticated via IAM Identity Center (SSO), routes requests through a policy engine, and forwards them to Amazon Bedrock.

## Architecture

![Architecture](assets/images/architecture.jpg)

## Key Features

- SSO authentication-based virtual API key issuance with automatic reuse
- 8-stage policy chain engine (user/team/model/budget policy evaluation)
- Real-time Anthropic Messages API ↔ Bedrock Converse API translation
- Model alias mapping and fallback routing
- Streaming and non-streaming inference support
- Per-team, per-user, and per-model budget management and usage tracking
- IAM Identity Center user synchronization
- Automatic DB migration via ECS init container
- One-click deployment with AWS CDK

## Key Components

| Component | Location | Description |
|-----------|----------|-------------|
| Gateway | `gateway/` | FastAPI app. Runtime (inference), Auth (key issuance), Admin (management), Sync (synchronization) APIs |
| Shared | `shared/` | ORM models, shared request/response schemas, exceptions, KMS/hashing utilities |
| Migrations | `migrations/` | Alembic DB migrations (executed via ECS init container) |
| Infrastructure | `infra/` | AWS CDK app (`FoundationStack`, `ServiceStack`) and supporting constructs |

## Directory Structure

```
claude-code-proxy-on-aws/
├── gateway/                        # FastAPI application
│   ├── main.py                     # App entry point, router registration
│   ├── core/                       # Config, DB, middleware, telemetry
│   ├── domains/
│   │   ├── auth/                   # SSO auth, virtual API key issuance (/v1/auth/token)
│   │   ├── runtime/                # Inference request handling (/v1/messages)
│   │   │   └── converter/          # Anthropic ↔ Bedrock API translation logic
│   │   ├── policy/                 # 8-stage policy chain engine
│   │   │   └── handlers/           # Individual policy handlers (user/team/model/budget, etc.)
│   │   ├── admin/                  # Admin API (/v1/admin/*)
│   │   ├── sync/                   # IAM Identity Center user synchronization
│   │   └── usage/                  # Usage aggregation and metrics
│   └── repositories/               # DB access layer (SQLAlchemy)
├── shared/                         # Shared modules (used by both gateway and migrations)
│   ├── models/                     # ORM models (User, Team, VirtualKey, ModelCatalog, etc.)
│   ├── schemas/                    # Pydantic input/output schemas
│   ├── utils/                      # KMS encryption, hashing, constants
│   └── exceptions.py               # Shared exception classes
├── migrations/                     # Alembic DB migrations
│   └── versions/                   # Per-version migration scripts
├── infra/                          # AWS CDK infrastructure code
│   ├── app.py                      # CDK app entry point
│   ├── config.py                   # Per-environment config (InfraContext)
│   ├── stacks/                     # FoundationStack, ServiceStack
│   └── cdk_constructs/             # Network, Data, Compute, ALB, API, Observability
├── tests/
│   ├── unit/gateway/               # Gateway unit tests
│   ├── unit/shared/                # Shared unit tests
│   └── test_*_stack.py             # CDK snapshot tests
├── scripts/
│   ├── deploy.sh                   # CDK deployment script (interactive)
│   ├── api_key_helper.sh           # SigV4 key issuance helper for Claude Code
│   ├── local-bootstrap.py          # Local development seed data insertion
│   └── local-entrypoint.sh         # Docker local startup script
├── assets/
│   ├── grafana_dashboard.json      # Grafana dashboard definition
│   └── observability/              # Local O11y compose override configs
├── Dockerfile                      # Gateway container image
├── docker-compose.yml              # Default local dev environment (PostgreSQL + gateway)
└── docker-compose.observability.yml # Optional local O11y (OTel + Prometheus + Grafana)
```

## Local Testing

### Prerequisites

- Docker & Docker Compose
- AWS CLI v2 (`aws --version`)
- An AWS account with Bedrock model access and an SSO profile

### 1. AWS SSO Login

The local gateway requires valid AWS credentials to call Bedrock.

```bash
aws sso login --profile <your-profile>
```

### 2. AWS Profile Configuration (Optional)

If your SSO profile is not `default`, create a `.env.local` file in the project root.

```bash
echo "AWS_PROFILE=<your-profile>" > .env.local
```

> `.env.local` is included in `.gitignore` and the gateway starts normally without it. In that case, the boto3 default credential chain (environment variables → `~/.aws/credentials` → default profile) is used.

### 3. Run Docker Compose

```bash
docker compose up --build
```

Two services start in order:

```
┌─────────────────────────────────────────────────────────┐
│  postgres:16                                            │
│  ├─ DB: claude_proxy, user/pass: dev/dev                │
│  └─ Gateway starts after healthcheck passes             │
├─────────────────────────────────────────────────────────┤
│  gateway (FastAPI)                                      │
│  ├─ Mounts ~/.aws → /aws-config:ro, then copies to     │
│  │  /tmp/app-home/.aws (SSO refresh writable)           │
│  ├─ Waits for PostgreSQL connection (up to 60s)         │
│  ├─ Runs local-bootstrap.py (seed data insertion)       │
│  │   ├─ User: local-user                               │
│  │   ├─ Models: claude-opus-4-8 / sonnet-4-6 / haiku-4-5│
│  │   ├─ Alias mappings: per-model patterns + * → sonnet │
│  │   │   fallback                                       │
│  │   ├─ Default prompt caching: 5m                      │
│  │   └─ Initial ACTIVE virtual key seed                 │
│  ├─ OTLP exporter disabled (local default)              │
│  └─ uvicorn :8000 starts                               │
└─────────────────────────────────────────────────────────┘
```

When the gateway starts successfully, the following log appears:

```
gateway-1 | INFO:     Uvicorn running on http://0.0.0.0:8000
```

### Optional: Run with Observability

To also verify Prometheus/Grafana, use the override compose file.

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.observability.yml \
  up --build
```

The following additional services start:

- `otel-collector`: Receives OTLP gRPC metrics from the gateway and exposes them in Prometheus format
- `prometheus`: Scrapes the collector's `/metrics` endpoint
- `grafana`: Auto-loads Prometheus datasource and default dashboard

Access URLs:

- Gateway: `http://localhost:8000`
- OTEL Collector metrics: `http://localhost:9464/metrics`
- Prometheus: `http://localhost:9090`
- Grafana: `http://localhost:3000` (`admin` / `admin`)

![Grafana Dashboard](assets/images/dashboard.jpg)

On startup, Grafana automatically loads the default datasource and the local provisioning dashboard [`assets/observability/grafana/dashboard.local.json`](assets/observability/grafana/dashboard.local.json). The original importable dashboard is maintained at [`assets/grafana_dashboard.json`](assets/grafana_dashboard.json).

Notes:

- Gateway business metrics are primarily generated in the runtime request processing path. Calling only `GET /v1/healthz` may result in an empty dashboard.
- The default OTLP export interval is 60 seconds, but `docker-compose.observability.yml` lowers it to 30 seconds (`OTLP_EXPORT_INTERVAL_MILLIS=30000`) for local verification convenience.
- In local O11y mode, there may be up to a 30-second delay before `POST /v1/messages` calls are reflected in Prometheus/Grafana.

### 4. Connect Claude Code

Configure Claude Code settings by referring to `settings.local.json`.

```json
{
  "apiKeyHelper": "/absolute/path/to/scripts/api_key_helper.local.sh",
  "env": {
    "ANTHROPIC_BASE_URL": "http://127.0.0.1:8000",
    "CLAUDE_CODE_API_KEY_HELPER_TTL_MS": "60000",
    "LOCAL_GATEWAY_AUTH_PRINCIPAL_ARN": "arn:aws:sts::local:assumed-role/GatewayAuth/local-user"
  }
}
```

- `apiKeyHelper` calls the local gateway's `/v1/auth/token` and outputs the current Virtual Key to stdout.
- `CLAUDE_CODE_API_KEY_HELPER_TTL_MS=60000` causes Claude Code to re-run the helper every 1 minute.
- The local gateway runs with `VIRTUAL_KEY_TTL_MS=300000` (5 minutes) by default, allowing you to reproduce helper re-invocation and post-expiry auto-refresh locally.
- `ANTHROPIC_BASE_URL` points to the local gateway address.

> The `apiKeyHelper` path must be an absolute path. `scripts/api_key_helper.local.sh` requires execute permission (`chmod +x`).

### 5. Verify

```bash
curl -s http://localhost:8000/v1/healthz
# {"status":"ok"}

LOCAL_KEY="$(./scripts/api_key_helper.local.sh)"
curl -s http://localhost:8000/v1/models \
  -H "x-api-key: ${LOCAL_KEY}" | python3 -m json.tool
```

### Local Environment Differences

| Item | Production | Local |
|------|-----------|-------|
| Auth | SigV4 → IAM → SSO user lookup | local helper → `/v1/auth/token` (`local-user` principal) |
| KMS | AWS KMS encrypt/decrypt | Local reversible fallback only when `ENVIRONMENT=local` + `KMS_KEY_ID=local-dev-placeholder` |
| DB Migration | Alembic (ECS init container) | Alembic `upgrade head` followed by `Base.metadata.create_all` in bootstrap |
| Model Routing | Per-model alias mapping | `claude-opus-4-8*`, `claude-sonnet-4-6*`, `claude-haiku-4-5*`, `*` → Sonnet 4.6 fallback |
| Observability | ADOT sidecar → Amazon Managed Prometheus | Disabled by default, optionally start local OTel/Prometheus/Grafana via override compose |
| AWS Credentials | ECS Task IAM Role | Host `~/.aws` mounted read-only, then copied to container writable home |

## AWS Deployment and Testing

### Prerequisites

- AWS CLI v2, CDK CLI, Docker, [uv](https://docs.astral.sh/uv/), jq
- An AWS account with Bedrock model access
- IAM Identity Center (SSO) enabled

### 0. Set Environment Variables

Set the common environment variables used in all subsequent steps.

```bash
export AWS_PROFILE=<your-profile>        # SSO profile name
export AWS_REGION=<region>               # e.g., ap-northeast-2
export API_GW_ID=<your-api-id>           # API Gateway ID from cdk-outputs.json
export RUNTIME_BASE_URL=<http-or-https>://<your-runtime-host>  # ALB DNS or custom domain

# Temporary credentials for SigV4 signing (re-run before expiry for each Admin API call)
eval "$(aws configure export-credentials --format env)"

# Admin API URL and common SigV4 options
API_URL="https://${API_GW_ID}.execute-api.${AWS_REGION}.amazonaws.com/prod"
SIGV4_OPTS=(
  --aws-sigv4 "aws:amz:${AWS_REGION}:execute-api"
  --user "${AWS_ACCESS_KEY_ID}:${AWS_SECRET_ACCESS_KEY}"
  -H "x-amz-security-token: ${AWS_SESSION_TOKEN:-}"
  -H "Content-Type: application/json"
)
```

### 1. Deploy

```bash
aws sso login --profile ${AWS_PROFILE}
./scripts/deploy.sh
```

The deployment script interactively guides you through region selection, Identity Store configuration, ACM certificate, and CDK bootstrap. After deployment, the API Gateway ID and ALB DNS are output to `cdk-outputs.json`. Use those values to fill in the Step 0 environment variables.

### 1.1. Populate Anthropic API Key (For 1P Fallback)

The Anthropic API key used for Bedrock-to-1P fallback is stored in AWS Secrets Manager. CDK creates an empty placeholder; populate it with your real key after deployment.

```bash
ANTHROPIC_API_KEY_SECRET_ARN=$(jq -r '.[] | .AnthropicApiKeySecretArn // empty' cdk-outputs.json | head -n1)

aws secretsmanager put-secret-value \
  --secret-id "$ANTHROPIC_API_KEY_SECRET_ARN" \
  --secret-string '{"api_key":"sk-ant-..."}' \
  --region "$AWS_REGION"
```

The gateway accepts either a JSON envelope (`{"api_key":"..."}`) or a raw key string. The value is read on first fallback request and cached in memory; the running ECS task does not need a restart for the key to take effect.

To enable fallback for a model, set `anthropic_model_id` when registering the model in Step 3 below (e.g. `"anthropic_model_id": "claude-sonnet-4-6"`). Models without `anthropic_model_id` skip fallback even when Bedrock fails.

Fallback covers both non-streaming and streaming requests. An in-memory circuit breaker, keyed per (Bedrock region, model), trips on Bedrock provider/throttle failures and routes subsequent requests for that same region+model straight to 1P until it half-opens (default `BEDROCK_BREAKER_OPEN_SECONDS=300`) and a probe succeeds, after which traffic returns to Bedrock automatically. Keying by model as well as region keeps one model's outage or throttle (for example hitting its TPM/RPM quota) from diverting unrelated models in the same region. For streaming, fallback only applies while the stream has not started yet (the `ConverseStream` call fails before the first chunk, or the breaker is already open); once SSE bytes have been sent to the client, a mid-stream Bedrock disconnect cannot fall over to 1P and must be retried by the client. Request-shape, auth, and policy rejections (`ValidationException`, `AccessDeniedException`, ...) do not trigger fallback because the same payload would fail at 1P too.

Usage and budget tracking covers Bedrock spend only. When a request is served by 1P fallback, its cost and token usage are recorded on the Anthropic console, not in this gateway, so they are not metered and do not decrement any user/team/model budget. As a result, while a breaker is open, requests served by 1P are not subject to the gateway's budget hard limits — 1P spend is governed by the Anthropic account, not by this gateway.

### 2. Sync Identity Center Users

Synchronize IAM Identity Center users to the gateway DB.

```bash
curl -s -X POST "$API_URL/v1/admin/sync/identity-center" \
  "${SIGV4_OPTS[@]}" | python3 -m json.tool
```

Synchronization must complete before Identity Center users can obtain virtual API keys from `/v1/auth/token`. Skipping this step causes helper calls to fail with `user_not_synced`. Re-run the sync when new users are added or users leave the organization.

### 3. Register Models

Register the 3 models to use with Bedrock in the catalog. Use the `"id"` value from each response in the next step.

```bash
# Claude Sonnet 4.6
curl -s -X POST "$API_URL/v1/admin/models" "${SIGV4_OPTS[@]}" \
  -d '{
    "canonical_name": "claude-sonnet-4-6",
    "bedrock_model_id": "global.anthropic.claude-sonnet-4-6",
    "anthropic_model_id": "claude-sonnet-4-6",
    "bedrock_region": "ap-northeast-2",
    "provider": "anthropic",
    "family": "claude-sonnet-4-6",
    "supports_prompt_cache": true
  }' | python3 -m json.tool

# Claude Opus 4.8
curl -s -X POST "$API_URL/v1/admin/models" "${SIGV4_OPTS[@]}" \
  -d '{
    "canonical_name": "claude-opus-4-8",
    "bedrock_model_id": "global.anthropic.claude-opus-4-8",
    "anthropic_model_id": "claude-opus-4-8",
    "bedrock_region": "ap-northeast-2",
    "provider": "anthropic",
    "family": "claude-opus-4-8",
    "supports_prompt_cache": true
  }' | python3 -m json.tool

# Claude Haiku 4.5
curl -s -X POST "$API_URL/v1/admin/models" "${SIGV4_OPTS[@]}" \
  -d '{
    "canonical_name": "claude-haiku-4-5",
    "bedrock_model_id": "global.anthropic.claude-haiku-4-5-20251001-v1:0",
    "anthropic_model_id": "claude-haiku-4-5",
    "bedrock_region": "ap-northeast-2",
    "provider": "anthropic",
    "family": "claude-haiku-4-5"
  }' | python3 -m json.tool
```

Specifying `bedrock_region` allows each model to use a different Bedrock Runtime region. If omitted, the gateway's default `AWS_REGION` is used.

### 4. Register Model Pricing

Register pricing information for each model. Without pricing, inference requests will fail with an `Active pricing is required` error.

```bash
SONNET_ID="<Sonnet id from Step 3 response>"
OPUS_ID="<Opus id from Step 3 response>"
HAIKU_ID="<Haiku id from Step 3 response>"

# Claude Sonnet 4.6 pricing
curl -s -X POST "$API_URL/v1/admin/model-pricing" "${SIGV4_OPTS[@]}" \
  -d "{
    \"model_id\": \"$SONNET_ID\",
    \"input_price_per_1k\": 0.003,
    \"output_price_per_1k\": 0.015,
    \"cache_read_price_per_1k\": 0.0003,
    \"cache_write_5m_price_per_1k\": 0.00375,
    \"cache_write_1h_price_per_1k\": 0.015,
    \"effective_from\": \"2025-01-01T00:00:00Z\"
  }" | python3 -m json.tool

# Claude Opus 4.8 pricing
curl -s -X POST "$API_URL/v1/admin/model-pricing" "${SIGV4_OPTS[@]}" \
  -d "{
    \"model_id\": \"$OPUS_ID\",
    \"input_price_per_1k\": 0.015,
    \"output_price_per_1k\": 0.075,
    \"cache_read_price_per_1k\": 0.0015,
    \"cache_write_5m_price_per_1k\": 0.01875,
    \"cache_write_1h_price_per_1k\": 0.075,
    \"effective_from\": \"2025-01-01T00:00:00Z\"
  }" | python3 -m json.tool

# Claude Haiku 4.5 pricing
curl -s -X POST "$API_URL/v1/admin/model-pricing" "${SIGV4_OPTS[@]}" \
  -d "{
    \"model_id\": \"$HAIKU_ID\",
    \"input_price_per_1k\": 0.0008,
    \"output_price_per_1k\": 0.004,
    \"cache_read_price_per_1k\": 0.00008,
    \"cache_write_5m_price_per_1k\": 0.001,
    \"cache_write_1h_price_per_1k\": 0.004,
    \"effective_from\": \"2025-01-01T00:00:00Z\"
  }" | python3 -m json.tool
```

### 5. Register Alias Mappings

Map the model name patterns requested by Claude Code to the registered models.

```bash
# claude-sonnet-4-6* → Sonnet 4.6
curl -s -X POST "$API_URL/v1/admin/model-mappings" "${SIGV4_OPTS[@]}" \
  -d "{\"selected_model_pattern\": \"claude-sonnet-4-6*\", \"target_model_id\": \"$SONNET_ID\", \"priority\": 30, \"is_fallback\": false}" | python3 -m json.tool

# claude-opus-4-8* → Opus 4.8
curl -s -X POST "$API_URL/v1/admin/model-mappings" "${SIGV4_OPTS[@]}" \
  -d "{\"selected_model_pattern\": \"claude-opus-4-8*\", \"target_model_id\": \"$OPUS_ID\", \"priority\": 20, \"is_fallback\": false}" | python3 -m json.tool

# claude-haiku-4-5* → Haiku 4.5
curl -s -X POST "$API_URL/v1/admin/model-mappings" "${SIGV4_OPTS[@]}" \
  -d "{\"selected_model_pattern\": \"claude-haiku-4-5*\", \"target_model_id\": \"$HAIKU_ID\", \"priority\": 10, \"is_fallback\": false}" | python3 -m json.tool

# Fallback mapping (* → Sonnet 4.6, applies to all unmatched requests)
curl -s -X POST "$API_URL/v1/admin/model-mappings" "${SIGV4_OPTS[@]}" \
  -d "{\"selected_model_pattern\": \"*\", \"target_model_id\": \"$SONNET_ID\", \"priority\": 0, \"is_fallback\": true}" | python3 -m json.tool
```

> Higher `priority` values are evaluated first. Set fallback mappings with `is_fallback: true` and assign the lowest priority.

### 6. API Key Helper Setup

Configure Claude Code settings by referring to `scripts/settings.json`.

```json
{
  "apiKeyHelper": "/absolute/path/to/scripts/api_key_helper.sh",
  "env": {
    "ANTHROPIC_BASE_URL": "<RUNTIME_BASE_URL>"
  }
}
```

- `ANTHROPIC_BASE_URL` must point to the public ALB or custom domain that receives runtime traffic, not the API Gateway.
- If no ACM certificate/custom domain is configured, use `http://<AlbDnsName>`.
- `scripts/api_key_helper.sh` uses the API Gateway endpoint for `POST /v1/auth/token`.
- This script is intended to be copied outside the repository to a location like `~/.claude`.
- Required environment variables:
  - `AWS_PROFILE`: SSO profile name (default: `ccob`)
  - `API_GW_ID`: API Gateway ID set in Step 0
  - `AWS_REGION`: AWS region set in Step 0

On first run, the helper automatically issues a virtual API key via SigV4 authentication.

> **Required IAM permission (do not skip):** The SigV4 call to `POST /v1/auth/token` is protected by API Gateway IAM authorization, so the caller's IAM principal (the SSO permission set used by `AWS_PROFILE`) **must** be granted `execute-api:Invoke` on the token route. Without it the helper fails with `not authorized to perform: execute-api:Invoke`. Add an inline policy to the user's permission set:
>
> ```json
> {
>   "Version": "2012-10-17",
>   "Statement": [
>     {
>       "Sid": "InvokeTokenApi",
>       "Effect": "Allow",
>       "Action": "execute-api:Invoke",
>       "Resource": "arn:aws:execute-api:<AWS_REGION>:<ACCOUNT_ID>:<API_GW_ID>/prod/POST/v1/auth/token"
>     }
>   ]
> }
> ```
>
> After editing the permission set, re-provision it to the account and re-run `aws sso login` so the new permission takes effect. Admins calling the Step 2–5 Admin APIs (`/v1/admin/*`) likewise need `execute-api:Invoke` on those routes (or admin credentials that already allow it).
>
> **On redeploy:** `<API_GW_ID>` changes when the service stack is recreated (for example after `destroy`). Update this policy's `Resource` (and `API_GW_ID` in Step 0 / helper env) with the new ID, otherwise token issuance breaks with a stale-ARN denial.

#### Virtual Key TTL

- `VIRTUAL_KEY_TTL_MS` sets the Virtual Key TTL in milliseconds. The default is `14400000` (4 hours), and `0` disables expiration.
- When a key expires during a runtime request, the gateway returns `401 virtual_key_expired`.
- Claude Code can re-run `apiKeyHelper` after a 401 to obtain a new key.
- TTL-based automatic reissuance **updates the existing `ACTIVE` key row**.
- Admin rotation **marks the existing row as `ROTATED` and creates a new `ACTIVE` row**.

You can optionally set `CLAUDE_CODE_API_KEY_HELPER_TTL_MS` in Claude Code settings to a value shorter than the TTL to trigger proactive re-invocation, but even without it, 401-based re-execution works.

### 7. Verify

```bash
# Health check
curl -s "$RUNTIME_BASE_URL/v1/healthz"
# {"status":"ok"}

# Model list (verify registered mappings)
curl -s "$RUNTIME_BASE_URL/v1/models" \
  -H "x-api-key: <your-api-key>" | python3 -m json.tool
```

### 8. Run Claude Code

Once configuration is complete, run Claude Code to verify the gateway is working correctly.

### 9. Delete Stacks

```bash
./scripts/deploy.sh destroy
```

## Documentation

| Document | Description |
|----------|-------------|
| [docs/README.md](./docs/README.md) | Documentation index |
| [docs/SYSTEM_ARCHITECTURE.md](./docs/SYSTEM_ARCHITECTURE.md) | System architecture |
| [docs/API_SPEC.md](./docs/API_SPEC.md) | API specification |
| [docs/DATA_MODEL.md](./docs/DATA_MODEL.md) | Data model |
| [docs/RUNTIME_TRANSLATION.md](./docs/RUNTIME_TRANSLATION.md) | Anthropic ↔ Bedrock translation rules |
| [docs/BEDROCK_FALLBACK.md](./docs/BEDROCK_FALLBACK.md) | Bedrock → Anthropic 1P fallback mechanism |

## License

This library is licensed under the Apache 2.0 License.
