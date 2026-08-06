# CDK Deployment

AWS CDK sample that provisions an agent with AgentCore Runtime, an AgentCore Gateway with Slack MCP configured, and AgentCore Identity with Slack OAuth credentials. It also deploys a sample Cognito user pool that acts as the inbound authorizer for invoking AgentCore Runtime and AgentCore Gateway.

## Prerequisites

- **You are logged in to AWS in your CLI** for the account you want to deploy to. CDK reads
  its credentials from your environment, so sign in before running any commands below — for
  example run `aws sso login` (if you use IAM Identity Center / SSO), or export temporary
  credentials by copying/pasting the access key, secret access key, and session token into
  your shell:

  ```bash
  export AWS_ACCESS_KEY_ID="<access-key-id>"
  export AWS_SECRET_ACCESS_KEY="<secret-access-key>"
  export AWS_SESSION_TOKEN="<session-token>"   # if using temporary credentials
  ```

  Verify with `aws sts get-caller-identity`. The account comes from these credentials; the
  region defaults to `us-east-1` (override with `CDK_DEFAULT_REGION` / `AWS_REGION`).
- **Node.js 18+ and npm** (to install dependencies and run the CDK CLI).
- **Docker with buildx, running** — the agent container image is built for `linux/arm64`
  and pushed during `cdk deploy`.
- If this is the first CDK deployment in the account/region, bootstrap it once with
  `npx cdk bootstrap`.

## Configuration (.env)

Configuration is read from a `.env` file at the project root (loaded via `dotenv` in
`bin/app.ts`). Copy the template and fill it in:

```bash
cp .env.example .env
# edit .env and set SLACK_CLIENT_SECRET (and SLACK_CLIENT_ID if different)
# and TEST_USER_PASSWORD
```

Two values are **required** — `cdk deploy` fails fast if either is missing:

- `SLACK_CLIENT_ID` / `SLACK_CLIENT_SECRET` — the Slack OAuth2 app credentials.
- `TEST_USER_PASSWORD` — the permanent password assigned to the seeded test users
  (`user1`, `user2`). Must satisfy the Cognito password policy (8+ chars with upper,
  lower, digit, and symbol).

You can also set `COGNITO_DOMAIN_PREFIX` in `.env` (defaults to `agentcore-gateway-slack`).
The Cognito hosted-UI domain prefix must be **globally unique across all AWS accounts**, so
pick your own value if the default is taken.

`.env` is gitignored. The live OAuth2 provider stores its Slack client secret as an
AgentCore **MANAGED** secret, whose plaintext cannot be read back from the API — so you
must provide the real secret value in `.env`. AgentCore re-stores it in Secrets Manager
for you (`ClientSecretSource = MANAGED`).

## Usage

The app runs directly from TypeScript via `ts-node` (configured in `cdk.json`), so there is
no separate build step before deploying:

```bash
npm install

# Synthesize
npx cdk synth

# Deploy
npx cdk deploy
```

The stack deploys to **us-east-1** by default, using the account of your active AWS
credentials. To deploy to a different region, set `CDK_DEFAULT_REGION` (or `AWS_REGION`) in
`.env` or your shell.

## Resources deployed

All account/region-scoped names below carry a unique suffix (`<sfx>`), controlled by
`RESOURCE_SUFFIX` in `.env` or derived automatically from the construct address.

| Logical ID | CFN Type | Resource name |
| --- | --- | --- |
| `GatewayUserPool` | `AWS::Cognito::UserPool` | `agentcore-gateway-pool-slack-<sfx>` |
| `GatewayUserPool/GatewayResourceServer` | `AWS::Cognito::UserPoolResourceServer` | `agentcore-gateway-id-slack` (scope `invoke`) |
| `GatewayUserPool/GatewayUserPoolDomain` | `AWS::Cognito::UserPoolDomain` | `agentcore-gateway-slack-<sfx>` (hosted UI) |
| `GatewayUserPool/GatewayUserPoolClient` | `AWS::Cognito::UserPoolClient` | `agentcore-gateway-client-slack-<sfx>` |
| `TestUser1` / `TestUser2` | `AWS::Cognito::UserPoolUser` | `user1`, `user2` (seeded test users) |
| `TestUser1SetPassword` / `TestUser2SetPassword` | `Custom::AWS` | sets each user's permanent password |
| `SlackOAuthProvider` | `AWS::BedrockAgentCore::OAuth2CredentialProvider` | `slack-mcp-server-provider-<sfx>` (SlackOauth2) |
| `GatewayRole` | `AWS::IAM::Role` | `agentcore-ac-gateway-mcp-server-slack-role-<sfx>` |
| `SlackGateway` | `AWS::BedrockAgentCore::Gateway` | `ac-gateway-mcp-server-slack-<sfx>` (MCP, CUSTOM_JWT) |
| `SlackGatewayTarget` | `AWS::BedrockAgentCore::GatewayTarget` | `slack-integration-target` (OpenAPI + OAuth) |
| `RuntimeExecutionRole` | `AWS::IAM::Role` | (auto-named) runtime execution role |
| `RuntimeImage` | ECR image asset | `cdk-hnb659fds-container-assets-<account>-<region>` (agent container) |
| `SlackRuntime` | `AWS::BedrockAgentCore::Runtime` | `slack_gateway_agent_<sfx>` (Container, CUSTOM_JWT) |

The gateway's `CUSTOM_JWT` authorizer is wired to the Cognito user pool created by this
stack: the `DiscoveryUrl` is derived from the new pool id and `AllowedClients` references
the new app client id — so the gateway trusts JWTs issued by this pool (no hardcoded pool
id). The user pool sets a password policy, `PASSWORD` first-auth factor, MFA off,
email+phone recovery, and the `ESSENTIALS` tier; the app client uses the auth-code OAuth
flow, admin-password auth, `email/openid/profile` + `agentcore-gateway-id-slack/invoke`
scopes, a generated client secret, and a 30-day refresh token.

Self sign-up is disabled, so the stack **seeds two test users** (`user1`, `user2`)
administratively via `CfnUserPoolUser` (with `MessageAction: SUPPRESS`, so no invitation
email is sent). Because `CfnUserPoolUser` only creates the account with a temporary
password, each user is paired with an `AwsCustomResource` that calls `AdminSetUserPassword`
with `Permanent: true` — using the `TEST_USER_PASSWORD` from `.env` — so the accounts are
immediately usable with the admin-password auth flow (this is who the `cli/` client signs
in as).

## AgentCore Runtime (JWT passthrough)

The stack deploys an **AgentCore Runtime** (`SlackRuntime`) that hosts a Strands agent
(source under `runtime/`). The runtime's `CUSTOM_JWT` authorizer is wired to the **same**
Cognito user pool and app client as the gateway, so a single Cognito access token works for
both hops:

1. The caller invokes the runtime with `Authorization: Bearer <cognito-access-token>`.
2. AgentCore validates the token against the pool, then forwards the `Authorization`
   header into the container (`RequestHeaderAllowlist: ["Authorization"]`).
3. `runtime/runtime_agent.py` reads that bearer token and opens an MCP connection to the
   gateway (`GATEWAY_URL`), passing the **same** token to AgentCore Gateway. The gateway's `CUSTOM_JWT`
   authorizer validates it, and the Slack target runs the 3LO flow as the signed-in user.

No credentials live in the runtime code; the token is supplied by the caller and forwarded.
The runtime execution role grants Bedrock model invocation, runtime log/trace permissions,
and ECR pull for the agent image — reaching the gateway needs no IAM grant because it is
called over HTTPS with the bearer token.

The agent in AgentCore Runtime runs from a **Container** image. The `runtime/Dockerfile` installs the Python
dependencies and serves the AgentCore HTTP contract on port 8080 (`/invocations`, `/ping`)
via `BedrockAgentCoreApp`. CDK's `DockerImageAsset` builds the image for **linux/arm64**
(AgentCore Runtime is ARM64) and pushes it to the CDK-managed ECR repository; the runtime
references it by ECR image URI. **Docker (with buildx) must be running** when you
`cdk deploy` — the image is built and pushed during deploy. The container entrypoint is
`opentelemetry-instrument python runtime_agent.py` (auto-instrumentation for tracing). Set
`BEDROCK_MODEL_ID` in `.env` to change the model (defaults to
`global.anthropic.claude-sonnet-4-6`).

```
POST https://bedrock-agentcore.<region>.amazonaws.com/runtimes/<url-encoded-runtime-arn>/invocations?qualifier=DEFAULT
Authorization: Bearer <cognito-access-token>
Content-Type: application/json
Accept: text/event-stream
X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: <33+ char session id>

{"prompt": "list the slack users"}
```

The runtime ARN is available in the `RuntimeArn` stack output.

## Security considerations (production)

This is a **sample** optimized for a quick end-to-end demo. Review the following before
adapting it for a production deployment:

- **Keep secrets out of CDK outputs and synthesized templates.** The Slack client secret
  and `TEST_USER_PASSWORD` are injected at synth time from `.env`, so they are rendered in
  cleartext into the generated CloudFormation under `cdk.out/` (the OAuth provider config
  and the `adminSetUserPassword` custom-resource call both carry them). Never commit
  `cdk.out/` (it is gitignored) and never add these values as `CfnOutput`s. For production,
  resolve secrets at deploy time from **AWS Secrets Manager or SSM Parameter Store** (e.g.
  in `bin/app.ts`, or via CloudFormation dynamic references) instead of a local `.env`, so
  plaintext never touches the template or your shell history. The Cognito app client secret
  is already intentionally **not** exported — retrieve it on demand via the
  `UserPoolClientSecretHint` command rather than adding an output.
- **Remove the seeded test users.** `user1`/`user2` are created with a single shared,
  permanent password purely for demo sign-in. Delete the `CfnUserPoolUser` /
  `AwsCustomResource` seeding (and `TEST_USER_PASSWORD`) for production and provision real
  users through your identity provider / federation with per-user credentials.
- **The runtime is internet-facing (`NetworkMode: PUBLIC`).** Access is gated only by the
  Cognito `CUSTOM_JWT` authorizer, so token hygiene matters: enable **MFA** on the user
  pool, shorten the 30-day refresh token and access-token validities, and consider WAF /
  throttling in front of the endpoint. Anyone holding a valid token can act as that user in
  Slack.
- **Harden the Cognito user pool.** This sample sets `deletionProtection: false`,
  `RemovalPolicy.DESTROY`, and `Mfa.OFF` so the demo tears down cleanly. For production,
  enable deletion protection, use `RemovalPolicy.RETAIN`, turn on MFA, and enforce a
  stronger password policy.
- **Tighten IAM to least privilege.** The runtime execution role uses wildcards for
  observability (`xray:*` / `logs:DescribeLogGroups` on `*`) and Bedrock model invocation
  (`inference-profile/*`, `foundation-model/*`). Scope these to the specific model IDs and
  log groups you actually use.
- **Request least-privilege Slack scopes.** The requested Slack `user_scope` is broad
  (`chat:write`, `channels:write`, `users:read`, plus history/read scopes). Request only the
  scopes your workload needs.


