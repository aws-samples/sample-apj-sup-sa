# Always-on deployment

This folder deploys the benchmark as a small serverless web app. Every day a worker task:

1. runs a discovery agent that checks the Bedrock documentation, AWS What's New and the AWS Machine
   Learning blog for model and tier changes, and probes changed models live;
2. benchmarks every model in the registry that has more than one service tier;
3. loads the results into Aurora.

A React app shows the results, with filters on every dimension and a tier-vs-Standard comparison.

If you only want to run the benchmark once, use the CLI in the [parent folder](../README.md) instead.

## Architecture

```
            Cognito (users, MFA) ─────────────┐
                                              │ JWT
 Browser ── Amplify Hosting (React SPA) ── API Gateway HTTP API ── Lambda (read-only)
                                                                        │ RDS Data API
                                                                        ▼
 EventBridge Scheduler (daily) ── ECS Fargate Spot task ────────► Aurora PostgreSQL
   (public subnet, no ingress,      discovery agent (Strands)     Serverless v2 (0-2 ACU,
    egress 443 only)                + benchmark + loader          isolated subnets)
                                         │
                                         ▼
                                   Amazon Bedrock (runtime + Mantle)
```

| Stack | Contents |
|---|---|
| `BedrockTierBench-Data` | VPC (public + isolated subnets, no NAT), Aurora PostgreSQL 17 Serverless v2, customer managed KMS key, Secrets Manager secrets with rotation, schema custom resource |
| `BedrockTierBench-Web` | Amplify Hosting app (manual deployments) with strict security headers |
| `BedrockTierBench-Api` | Cognito user pool and app client, HTTP API with JWT authorizer, read-only Lambda |
| `BedrockTierBench-Worker` | ECS cluster, Fargate Spot task definition (arm64), EventBridge Scheduler schedule |

## Security

| Control | How |
|---|---|
| Database not reachable from any network | Isolated subnets, security group with no ingress except the secret-rotation Lambda, access only through the RDS Data API (IAM + Secrets Manager) |
| Encryption | One customer managed KMS key (rotation on) for Aurora storage, Performance Insights, secrets and the schema Lambda's logs; `rds.force_ssl=1` |
| Least-privilege database users | `bench_reader` (SELECT only) for the API, `bench_writer` (DML) for the worker, `bench_admin` only for the schema; all three passwords rotate every 30 days |
| Data protection | Deletion protection, 7-day backups, snapshot on stack deletion |
| Sign-in | Cognito: no self sign-up, TOTP MFA required, threat protection in full-function mode, authorization code + PKCE (no client secret) |
| API | JWT validated by API Gateway on every route, CORS limited to the web origin, throttling (20 rps, burst 40), access logs, Lambda concurrency capped at 10 |
| SQL | Column names from a fixed allowlist, every value a Data API bind parameter |
| Worker network | Public IP but **zero ingress**; egress TCP 443 only; read-only root filesystem; runs as a non-root user |
| Worker data integrity | The agent can only store models through the probe tool, which writes only what a live probe verified |
| Web | CSP `default-src 'none'` with explicit sources, HSTS, `X-Frame-Options: DENY`, no inline scripts or styles |
| Supply chain | Hash-pinned Python lock files, `npm audit` clean, `cdk-nag` AwsSolutions with justified suppressions only |

### NAT variant

The worker uses a public subnet so the stack needs no NAT gateway (about USD 32 per month per AZ).
If your policy forbids public IPs on tasks, add `nat_gateways=1` and a `PRIVATE_WITH_EGRESS` subnet
group in `stacks/data_stack.py`. Then run the task in that subnet group with `assign_public_ip="DISABLED"` in
`stacks/worker_stack.py`. Interface endpoints for `bedrock-runtime`, `rds-data`, `secretsmanager`, `ecr.api`,
`ecr.dkr`, `logs` and an S3 gateway endpoint can replace the NAT gateway, except for the documentation
and RSS fetches and the Mantle endpoint, which still need internet egress.

## Prerequisites

- An AWS account with Bedrock access to the models you want to benchmark, and the AWS CLI configured.
- Python 3.11+, Node.js 20+, Docker (or Finch) with arm64 support (`docker buildx`).
- CDK bootstrapped in the account and region: `npx aws-cdk@2 bootstrap aws://ACCOUNT_ID/us-east-1`.

## Deploy

```bash
cd deploy
python3 -m venv .venv && .venv/bin/pip install --require-hashes -r requirements.lock
npx aws-cdk@2 deploy --all -c account=ACCOUNT_ID
```

Then publish the web app. Take the values from the stack outputs:

```bash
cd web
npm ci
cat > public/config.json <<EOF
{"apiUrl": "<ApiUrl>", "cognitoDomain": "<CognitoDomain>", "clientId": "<UserPoolClientId>", "identityProvider": ""}
EOF
npm run build
(cd dist && zip -qr ../site.zip .)
JOB=$(aws amplify create-deployment --app-id <AppId> --branch-name main --query '[jobId,zipUploadUrl]' --output text)
curl -sS -T site.zip "$(echo "$JOB" | cut -f2)"
aws amplify start-deployment --app-id <AppId> --branch-name main --job-id "$(echo "$JOB" | cut -f1)"
```

Create the first user (there is no self sign-up). They set a password and TOTP MFA at first sign-in:

```bash
aws cognito-idp admin-create-user --user-pool-id <UserPoolId> --username you@example.com \
  --user-attributes Name=email,Value=you@example.com Name=email_verified,Value=true
```

Run the worker once without waiting for the schedule (the first run discovers every model, because
the database starts empty):

```bash
aws ecs run-task --cluster <Cluster> --task-definition <TaskDefinition> --capacity-provider-strategy capacityProvider=FARGATE_SPOT,weight=1 \
  --network-configuration "awsvpcConfiguration={subnets=[<PublicSubnet>],securityGroups=[<TaskSg>],assignPublicIp=ENABLED}"
```

## Verified deployment

Deployed end to end on 2026-10-08 in us-east-1 (`auth_mode=cognito`, `worker_arch=x86_64`):

| Step | Result |
|---|---|
| `cdk deploy --all` | 4 stacks created; cdk-nag 0, Checkov 0 failed |
| First worker run (empty database) | Discovery agent stored 35 verified models and 216 offerings; the quick benchmark wrote 1 run with 140 cells, 70 comparisons and 699 samples; about 40 minutes |
| Web app | Hosted-UI sign-in with password and TOTP MFA, then the comparison table showed all 70 rows from Aurora |
| `auth_mode=midway` with a custom domain (2026-10-09) | Same stacks updated in place to an OIDC identity provider (Amazon's internal IdP) and an Amplify custom domain; single sign-on led straight to the dashboard with 69 comparison rows and no console errors |

Switching an existing `cognito` deployment to a custom domain keeps the Web stack's default-domain
export, because the Api stack still imports it during the update. An OIDC identity provider may
require short Cognito token lifetimes; this app uses 10-minute access tokens, 1-hour ID tokens and
10-hour refresh tokens, and the web app sends the ID token.

Problems found on the way and fixed in this folder: the agent model rejects `temperature`; the worker
role needed the Data API transaction actions; Fargate mounts the scratch volume as root, so a
non-essential init container hands `/work` to the non-root worker user.

## Configuration (`cdk.json` context or `-c key=value`)

| Key | Default | Meaning |
|---|---|---|
| `auth_mode` | `cognito` | `cognito` (admin-created users) or `midway` (federated OIDC only) |
| `domain_name` | empty | Custom domain for the web app (for example `bench.example.com`); its zone must be in Route 53 in this account |
| `federate_issuer_url`, `federate_client_id`, `federate_client_secret_name` | empty | OIDC identity provider for `auth_mode=midway`; the client secret is read from Secrets Manager at deploy time |
| `schedule_expression` | `cron(0 18 * * ? *)` | When the worker runs (UTC) |
| `benchmark_args` | `--preset quick` | Arguments passed to `bedrock-bench`; the cost scales with this |
| `worker_arch` | `arm64` | Worker CPU architecture. `arm64` (Graviton) is cheaper; use `x86_64` if your build host cannot build arm64 images (no `docker buildx` arm64 emulation) |
| `agent_model_id` | `us.anthropic.claude-sonnet-5-5` | Bedrock model for the discovery agent |

## Cost

At rest the stack costs about USD 18 per month (us-east-1 list prices, October 2026):

| Item | Approx. USD / month |
|---|---|
| Secrets Manager interface endpoint, 2 AZs (needed by the in-VPC password rotation) | 14.60 |
| 3 secrets | 1.20 |
| KMS customer managed key | 1.00 |
| Aurora storage and backups (small database), compute paused at 0 ACU when idle | < 1 |
| Lambda, API Gateway, Cognito (per use), Amplify Hosting | < 1 at low traffic |

Each daily worker run costs the Fargate Spot time (cents) plus the Bedrock tokens of the configured
benchmark. Measured on 2026-10-08 with the default `--preset quick` over the 35 verified multi-tier models:
140 cells, 699 samples, about 1.26M input and up to 0.43M output tokens, roughly 40 minutes (Flex requests
queue for up to about 4 minutes). To benchmark fewer models, add `--keys` or `--families` to
`benchmark_args`, for example `-c benchmark_args="--preset quick --keys zai.glm-5.3,moonshotai.kimi-k3"`. Run `bedrock-bench --dry-run <benchmark_args>` to see the token estimate
before raising `benchmark_args`.

## Tear down

```bash
npx aws-cdk@2 destroy --all -c account=ACCOUNT_ID
```

The Aurora cluster has deletion protection, and the KMS key and user pool are retained. To remove them,
first turn deletion protection off on the cluster and the user pool, then delete them and schedule the
key for deletion. A final snapshot is kept unless you delete it.

## Tests

```bash
../.venv/bin/python -m pytest -q tests        # API handler, worker DB layer, discovery agent tools
(cd web && npx vitest run)                     # PKCE
npx aws-cdk@2 synth -q                         # cdk-nag AwsSolutions (fails on any unsuppressed finding)
```
