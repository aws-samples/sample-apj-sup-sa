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
# edit .env and set SLACK_SECRET_ARN
```

**Prerequisite — create the Slack credentials secret.** The Slack client id and secret are
**not** put in `.env`. Pre-create a Secrets Manager secret (in the deploy account/region)
holding both as JSON with the keys `client_id` / `client_secret`:

```bash
aws secretsmanager create-secret \
  --name slack/agentcore-gateway-credentials \
  --secret-string '{"client_id":"<your-slack-client-id>","client_secret":"<your-slack-client-secret>"}'
```

Then set `SLACK_SECRET_ARN` in `.env` to the full ARN of that secret.

> The deploying principal needs `secretsmanager:GetSecretValue` on the secret — both
> `client_id` and `client_secret` are resolved by CloudFormation at deploy time. No resource
> policy is required: AgentCore stores its own **MANAGED** copy of the client secret after
> deploy, so it never reads your secret at runtime.

One value is **required** — `cdk deploy` fails fast if it is missing:

- `SLACK_SECRET_ARN` — the full **ARN** of the secret. The deploying principal needs
  `secretsmanager:GetSecretValue` on it. The secret must use the JSON keys `client_id` and
  `client_secret`.

This stack does **not** create any Cognito sign-in users. After deploy, create one yourself
in the AWS console — see [Create a Cognito sign-in user](#create-a-cognito-sign-in-user).

You can also set `COGNITO_DOMAIN_PREFIX` in `.env` (defaults to `agentcore-gateway-slack`).
The Cognito hosted-UI domain prefix must be **globally unique across all AWS accounts**, so
pick your own value if the default is taken.

`.env` is gitignored. The OAuth2 provider uses `ClientSecretSource: MANAGED` — AgentCore
stores its own copy of the client secret after deploy, giving a **stable callback URL** that
does not change across redeployments as long as the provider is not deleted. Your
pre-created secret is only read at deploy time and is not accessed by AgentCore at runtime.

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
| `SlackOAuthProvider` | `AWS::BedrockAgentCore::OAuth2CredentialProvider` | `slack-mcp-server-provider-<sfx>` (SlackOauth2) |
| `GatewayRole` | `AWS::IAM::Role` | `agentcore-ac-gateway-mcp-server-slack-role-<sfx>` |
| `SlackGateway` | `AWS::BedrockAgentCore::Gateway` | `ac-gateway-mcp-server-slack-<sfx>` (MCP, CUSTOM_JWT) |
| `SlackGatewayTarget` | `AWS::BedrockAgentCore::GatewayTarget` | `slack-integration-target` (inline OpenAPI schema + OAuth) |
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

Create a user administratively after deploy (see below); the app client's admin-password auth flow
(`ADMIN_USER_PASSWORD_AUTH`) is what the `cli/` client signs in with.

## Create a Cognito sign-in user

The `cli/` client (and any caller) needs a Cognito user to sign in as. The stack creates no
users, so add one from the **AWS console** after `cdk deploy`. Use the `UserPoolId` from the
stack outputs to find the pool.

1. **Create the user.** Go to **Amazon Cognito > User pools > _your pool_ > Users >
   Create user**. Set **User name** to `user1`, choose **Don't send an invitation**, set a
   password (must satisfy the pool policy: 8+ chars with upper, lower, digit, and symbol),
   and create the user. It starts in the **Force change password** state.
2. **Reset the password once for first login.** A console-created user can't sign in until
   its temporary password is changed. In the same pool, open **App integration > App clients
   > _your app client_ > Login pages > View login page** to open the hosted sign-in page.
   Sign in as `user1` with the password from step 1; you'll be prompted to set a **new
   password**. Enter it — this moves the user to **Confirmed**, which is what the CLI's
   admin-password auth needs. (The page then redirects to `http://localhost:8080/callback`;
   a browser error there is harmless — the password change has already taken effect.)
3. **Use it in the CLI.** Put the final username/password in `cli/.env`
   (`COGNITO_USERNAME` / `TEST_USER_PASSWORD`), or pass `--user` / `--password`.

Repeat for any additional users.

## Slack OpenAPI schema (inline)

The gateway target's tools come from an OpenAPI schema committed to this repo at
`schema/slack-open-api.json`. The stack reads it at synth time and passes it to the target
as an **inline payload** (`TargetConfiguration.Mcp.OpenApiSchema.InlinePayload`), so the
sample is fully self-contained — it does **not** reference any external or private S3
bucket, and nothing needs to be uploaded before `cdk deploy`.

The schema describes the Slack Web API operations (`https://slack.com/api`) that mirror the
AgentCore Gateway **Slack integration template** (`chat.postMessage`, `conversations.*`,
`users.list`, `search.all`, `files.*`, etc.). That console template flow
(*MCP Target > Connectors > Other integrations > Slack*) is **console-only** — AWS does not
expose it through the API/CloudFormation/CDK — so this stack reproduces an equivalent target
using the standard, IaC-supported OpenAPI target type plus the Slack OAuth2 credential
provider. To add or remove tools, edit `schema/slack-open-api.json` and redeploy; keep the
requested Slack `user_scope` in the stack in sync with the operations you expose.

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

- **Secrets are kept out of the synthesized template.** Both the Slack client id and secret
  are resolved from your pre-created Secrets Manager secret at deploy time via CloudFormation
  dynamic references, so only the reference — not the plaintext — appears in `cdk.out/`.
  AgentCore stores its own MANAGED copy after deploy; no resource policy is needed on your
  secret. Never add these values as a `CfnOutput`. The Cognito app client secret is likewise
  intentionally **not** exported — retrieve it on demand via the `UserPoolClientSecretHint`
  command rather than adding an output.
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


