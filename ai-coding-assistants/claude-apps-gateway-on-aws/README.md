# Claude Apps Gateway on AWS

TypeScript AWS CDK that deploys Anthropic's self-hosted
[Claude Apps Gateway](https://code.claude.com/docs/en/claude-apps-gateway) on
Amazon Bedrock. Developers sign in through an **Amazon Cognito-native IdP** (OIDC) and
reach the gateway only over a **private network** — no AWS or Anthropic credentials are
handed to individuals. Per-developer usage (tokens, cost, sessions, by model and team) flows
to **[Amazon CloudWatch Coding Agent Insights](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/coding-agents-claude-code-gateway.html)**.

> **Private-network requirement.** Claude Code's `/login` only connects to a gateway
> whose hostname resolves to private IPs. Keep CloudFront and public DNS off the
> login path. See [Why a private network](#why-a-private-network).

Throughout this guide the gateway URL `https://claude-gateway.corp.example.com` and
account/region values are **masked examples** — substitute your own.

> **Scope.** Identity is an **Amazon Cognito-native IdP**: Cognito is the user
> directory and users are created directly in the pool (e.g. `admin-create-user`).
> Federating an external corporate IdP (Entra / Okta / Google Workspace) into Cognito
> is possible but out of scope here. The stack targets **`us-east-1`** — commands and
> examples assume that region; other regions can work but need review (Bedrock model
> availability, and a `models:` block in the gateway config for non-US regions).

## Highlights

- **Cognito-native sign-in, not keys** — Amazon Cognito (OIDC) is the IdP; short-lived
  tokens, users managed directly in the Cognito user pool.
- **Credentials stay in AWS** — Bedrock is called through the ECS task role, never
  static keys on laptops.
- **Private by design** — internal ALB + Route 53 Private Hosted Zone; the login and
  inference path never leaves your network.
- **Managed state** — Aurora PostgreSQL Serverless v2 for the device flow, sessions,
  and rate limits.
- **Usage telemetry in CloudWatch** — the gateway relays Claude Code OTLP metrics to an
  ADOT collector that SigV4-signs them into the CloudWatch OTLP endpoint; the Coding Agent
  Insights dashboards populate with no per-developer setup and no long-lived API keys.
- **All infrastructure as code** — one CDK stack (written for `us-east-1`),
  reproducible in any account by changing context values.

## Table of Contents

1. [Architecture](#architecture)
2. [Prerequisites](#prerequisites)
3. [Deploy with CDK](#deploy-with-cdk)
4. [Telemetry → CloudWatch Coding Agent Insights](#telemetry--cloudwatch-coding-agent-insights)
5. [AWS Client VPN](#aws-client-vpn)
6. [Cleanup](#cleanup)

Also: [Troubleshooting](#troubleshooting) · [Documentation](#documentation) · [License](#license)

## Architecture

A developer on a private path resolves the gateway host to the **internal ALB**, which
fronts the **ECS Fargate** gateway; the gateway signs users in via **Cognito** (OIDC),
keeps state in **Aurora**, and calls **Bedrock** through its task role. Claude Code's OTLP
metrics go through the gateway to an **ADOT collector** and on to **CloudWatch**.

```mermaid
flowchart LR
  Dev["Developer laptop<br/>Claude Code"]
  R53["Route 53<br/>Private Hosted Zone"]
  ALB["Internal ALB<br/>HTTPS 443"]
  ECS["ECS Fargate<br/>claude gateway"]
  DB["Aurora PostgreSQL<br/>Serverless v2"]
  Cognito["Cognito<br/>(OIDC IdP)"]
  Bedrock["Amazon Bedrock"]
  Collector["ADOT collector<br/>ECS Fargate"]
  CW["CloudWatch OTLP endpoint<br/>Coding Agent Insights"]

  Dev -.->|"resolve host"| R53
  Dev -->|"private path (VPN / DX / ZTNA / devbox)"| ALB
  ALB --> ECS
  ECS --> DB
  ECS -->|"OIDC"| Cognito
  ECS -->|"InvokeModel"| Bedrock
  ECS -->|"OTLP relay · HTTPS 4318 via ALB"| Collector
  Collector -->|"SigV4 (task role)"| CW
```

Full diagram, request path, network isolation, and the security-group matrix:
[`docs/architecture.md`](docs/architecture.md).

### What gets deployed

| Resource | Purpose |
|---|---|
| VPC (2 AZs, 1 NAT) | Public / application / isolated-database subnet tiers |
| VPC endpoints (6 interface + S3 gateway) | Bedrock Runtime, Secrets Manager, ECR (api + dkr), CloudWatch Logs/Monitoring, S3 — AWS-service traffic stays on the AWS backbone instead of the NAT path (`createVpcEndpoints: false` to opt out) |
| Internal ALB (HTTPS 443) | TLS termination (ACM); 443 only from `allowedClientCidrs`; 3600s idle timeout for streaming; target group health check on `/healthz` |
| ECS Fargate service | Runs `claude gateway`; task role limited to Bedrock `InvokeModel*` |
| Aurora PostgreSQL Serverless v2 | Device flow, sessions, rate-limit state (isolated) |
| Cognito User Pool + client | OIDC identity provider (email sign-in, confidential client) |
| ACM certificate | Gateway TLS cert, DNS-validated via the public hosted zone |
| Secrets Manager (×3) | DB credentials, JWT secret, Cognito client secret |
| Route 53 Private Hosted Zone | Zone scoped to the gateway FQDN only (alias → ALB), so the rest of the corporate domain still resolves publicly ([why](docs/dns.md#zone-scoping)) |
| CloudWatch Logs + alarm | Gateway logs (1-month) + unhealthy-host alarm |
| ADOT collector (ECS Fargate ×2, ARM64) | Receives the gateway's OTLP relay on an ALB HTTPS **:4318** listener (reachable only from gateway tasks) and SigV4-signs metrics into the CloudWatch OTLP endpoint; task role limited to `cloudwatch:PutMetricData`; read-only root FS, all capabilities dropped; no-healthy-collector alarm (`enableTelemetry: false` to opt out) |

Not created: CloudFront, public DNS for the gateway host, Client VPN / Direct Connect
/ ZTNA, Route 53 Resolver endpoints, or Cognito users.

### Why a private network

At `/login`, Claude Code requires the gateway hostname to resolve **only** to private
addresses (RFC 1918, CGNAT `100.64.0.0/10`, IPv6 ULA `fc00::/7`, or loopback). If any
resolved IP is public, it rejects the URL. This is a security guard: a trusted gateway
can push managed settings that run commands on developer machines, so gateways are
restricted to private addresses. That is why the ALB is **internal** and its record
lives in a **private hosted zone** — and why developers need a private path
([Client VPN](#aws-client-vpn)) and private DNS ([`docs/dns.md`](docs/dns.md)). See the
upstream [Quickstart](https://code.claude.com/docs/en/claude-apps-gateway#quickstart)
and [Prerequisites](https://code.claude.com/docs/en/claude-apps-gateway#prerequisites).

## Prerequisites

**Local tools:** Node.js + npm, Docker (CDK builds the `linux/arm64` gateway image),
AWS CLI credentials for the target account, and `curl` + `gpg` for the binary
preparation step (`brew install gnupg` on macOS).

**AWS / gateway** (maps to the
[upstream prerequisites](https://code.claude.com/docs/en/claude-apps-gateway#prerequisites)):

- **Claude Code v2.1.195+** on the gateway image and every developer machine.
- **Bedrock model access** enabled for the Claude models you use.
- A **public Route 53 hosted zone** named `hostedZoneName` in the account — the stack
  issues the ALB certificate there via ACM DNS validation.
- A **private network path** for developers (VPN / Direct Connect / ZTNA / devbox) —
  see [AWS Client VPN](#aws-client-vpn).
- Cognito is provisioned by this stack as the OIDC IdP (SAML and LDAP are not
  supported by the gateway).

## Deploy with CDK

### 1. Configure

Real values live in `cdk.context.json` (gitignored; also caches Route 53 lookups).
Create it from the template and edit the key values:

```bash
cp cdk.context.json.example cdk.context.json
# edit at least: gatewayHost, hostedZoneName, awsAccount, allowedEmailDomains,
#                cognitoDomainPrefix, availableModels
```

| Context key | Meaning |
|---|---|
| `gatewayHost` | Private gateway FQDN (also the ACM cert subject) |
| `hostedZoneName` | Parent zone name, used **only** for ACM cert validation; a **public** zone of this name must exist. Private DNS is scoped to `gatewayHost` |
| `awsAccount` / `awsRegion` | Deployment target (needed for the hosted-zone lookup) |
| `allowedClientCidrs` | CIDRs allowed to reach the internal ALB on 443 (VPN/corporate/devbox) |
| `allowedEmailDomains` | Cognito ID-token email domains the gateway accepts |
| `availableModels` | Model allowlist shown in the Claude Code model picker and enforced server-side (`managed.policies` + `enforceAvailableModels`). **List only the Claude models you have enabled access for in Bedrock** — models outside the allowlist can't be selected, and without one the picker offers every built-in model, including ones that fail with `AccessDenied` mid-conversation |
| `cognitoDomainPrefix` | Cognito hosted-domain prefix (globally unique per region) |
| `bedrockRegion` | Region for Bedrock inference |
| `claudeVersion` | Pinned Claude Code version baked into the image |
| `desiredCount` / `natGateways` | Fargate task count / NAT Gateway count |
| `createVpcEndpoints` | Create the interface + S3 gateway VPC endpoints (default `true`). Interface endpoints cost ~$0.01/AZ/hour each; set `false` to route AWS-service traffic through NAT instead |
| `enableTelemetry` | Deploy the ADOT collector + OTLP listener and turn on the gateway's telemetry relay (default `true`) |
| `telemetryResourceAttributes` | Fixed OTel resource attributes on every session's metrics, e.g. `{"organization": "acme"}`. Coding Agent Insights slices by `organization`, `department`, `team.id`, `cost_center`. Validated at synth time (no `user.*` / `service.name`, no spaces or `, ; = \\ " %`) |
| `collectorImage` / `collectorDesiredCount` | ADOT collector image (pin by digest for production) / task count |

Defaults in [`lib/config.ts`](lib/config.ts) are placeholders; context always
overrides them, and any value can also be passed per-command with `cdk -c key=value`.

### 2. Prepare the Claude binary

```bash
npm run prepare:claude -- 2.1.285
```

Downloads and cryptographically verifies (GPG signature + SHA256) the native
`linux-arm64` `claude` binary and writes `docker/claude`, which the image requires —
the gateway server runs only the native binary. Use the same version as `claudeVersion`;
telemetry resource attributes need **2.1.281 or later**.

### 3. Build, bootstrap, and deploy

```bash
npm install
npm run build && npm test
npm run cdk -- bootstrap aws://ACCOUNT_ID/us-east-1   # once per account/region
npm run cdk -- deploy
```

The first deploy pauses a few minutes while ACM validates the certificate via the DNS
record it creates in the public hosted zone.

### 4. Create the first user and read outputs

```bash
aws cognito-idp admin-create-user \
  --region us-east-1 \
  --user-pool-id USER_POOL_ID \
  --username user@example.com \
  --user-attributes Name=email,Value=user@example.com Name=email_verified,Value=true
```

Stack outputs: `GatewayUrl`, `AlbDnsName`, `PrivateHostedZoneId`, `UserPoolId`,
`UserPoolClientId`, `CognitoDomain`, `RdsEndpoint`, `LogGroupName`, and with telemetry
`OtlpForwardUrl`, `CollectorLogGroupName`.

Then push the managed settings file so developer `/login` targets the gateway, and run
the smoke tests — see [`docs/operations.md`](docs/operations.md).

## Telemetry → CloudWatch Coding Agent Insights

This implements the enterprise path from
[Set up Claude Code with the Claude apps gateway](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/coding-agents-claude-code-gateway.html).

```text
Claude Code ──OTLP──▶ gateway (/v1/metrics) ──HTTPS :4318──▶ internal ALB ──▶ ADOT collector ──SigV4──▶ monitoring.<region>.amazonaws.com/v1/metrics
```

1. With `telemetry.forward_to` and `listen.public_url` set, the gateway **pushes the OTEL
   exporter settings to every signed-in client** (via managed settings). Developers configure
   nothing, and a local OTEL config can't redirect the export.
2. Each client exports metrics to the gateway, **stamped with `user.id`, `user.email` and
   `user.groups` from the SSO session**, plus the `telemetryResourceAttributes` labels.
3. The gateway relays them over HTTPS to the ALB's **:4318** listener (same hostname and
   certificate). Only the gateway task security group may reach it. Because it's a real
   HTTPS address, the gateway's SSRF guard stays on (no `CLAUDE_GATEWAY_ALLOW_LOOPBACK`).
4. The **ADOT collector** batches and SigV4-signs them into the **CloudWatch OTLP metrics
   endpoint** using its task role (`cloudwatch:PutMetricData` only). There is no bearer-token
   API key. With `createVpcEndpoints`, this uses the CloudWatch Monitoring VPC endpoint.
5. The dashboards appear under **CloudWatch → GenAI Observability → Coding Agent Insights →
   Claude Code**, usually within minutes of the first session.

**Metrics only.** Logs and traces are not forwarded: they can contain full bash commands,
file paths and tool inputs. Enable them only toward destinations with suitable access
controls and retention (see the
[telemetry reference](https://code.claude.com/docs/en/claude-apps-gateway-config#telemetry)).

**The gateway doesn't buffer.** If no collector is healthy, exports are dropped, and the
`CollectorUnhealthyAlarm` fires.

**Team and cost-center labels.** Organization-wide labels come from
`telemetryResourceAttributes`. For per-group values, add a `managed.policies` entry whose
`cli.env.OTEL_RESOURCE_ATTRIBUTES` sets them (for example
`organization=acme,department=eng,team.id=platform,cost_center=cc-1`). That value replaces
the global labels for matching sessions, so repeat `organization` in it.

**Verify** after a developer has used Claude Code through the gateway. In CloudWatch
**Query Studio** (PromQL):

```promql
sum by ("user.email") ({"claude_code.cost.usage"})           # USD by developer
sum by ("type") ({"claude_code.token.usage"})                 # input / output / cacheRead / cacheCreation
sum by ("team.id", "model") ({"claude_code.token.usage"})
```

If nothing arrives, check the collector log group (`CollectorLogGroupName`) for export errors,
and the gateway log for forward failures. Also confirm the developer approved the one-time
prompt for the telemetry settings the gateway pushes.

## AWS Client VPN

Client VPN is the most common way to give laptops a private path into the VPC. It is
**not** created by this stack; set it up separately.

**Essentials:** mutual-certificate auth, a client CIDR that does **not** overlap the
VPC (e.g. `172.16.0.0/22`), `--dns-servers 10.0.0.2` (the VPC resolver, so the private
hosted zone resolves), and `--split-tunnel`, all in `us-east-1`. The full annotated
walkthrough — certificates, endpoint creation, subnet association/authorization,
client profile, teardown, cost notes, and alternatives — is in
[`docs/client-vpn.md`](docs/client-vpn.md).

Once connected, verify both the DNS and network paths:

```bash
dig +short claude-gateway.corp.example.com                                        # expect 10.0.x.x
curl -s -o /dev/null -w "%{http_code}\n" https://claude-gateway.corp.example.com/healthz  # expect 200
```

DNS options (VPC resolver, corporate forwarder, or in-VPC devbox) are in
[`docs/dns.md`](docs/dns.md).

## Cleanup

```bash
npm run cdk -- destroy      # removes the stack (Aurora and Cognito use RemovalPolicy.DESTROY)
```

Also remove what the stack does not manage:

- **Client VPN** (if created): disassociate the target network, then
  `aws ec2 delete-client-vpn-endpoint` — see [`docs/client-vpn.md`](docs/client-vpn.md#teardown).
- **Collector log group** and Coding Agent Insights data: the log groups are deleted with the
  stack; metrics already in CloudWatch age out per CloudWatch retention.
- **Developer managed settings** on each machine, so Claude Code stops forcing gateway
  login:

```bash
sudo rm "/Library/Application Support/ClaudeCode/managed-settings.json"   # macOS
# Linux/WSL: /etc/claude-code/managed-settings.json
# Windows:   C:\Program Files\ClaudeCode\managed-settings.json
```

## Troubleshooting

Common symptoms and fixes (private-network errors, DNS resolution, `443` timeouts,
`/readyz` vs `/healthz`, Bedrock authorization) are in
[`docs/operations.md`](docs/operations.md#troubleshooting).

## Documentation

**This repo**

- [`docs/architecture.md`](docs/architecture.md) — deployed resources, request path, security groups
- [`docs/client-vpn.md`](docs/client-vpn.md) — full AWS Client VPN walkthrough
- [`docs/dns.md`](docs/dns.md) — DNS setup options
- [`docs/operations.md`](docs/operations.md) — managed settings, smoke tests, troubleshooting

**Anthropic — Claude Apps Gateway**

- [Overview](https://code.claude.com/docs/en/claude-apps-gateway)
- [Configuration reference](https://code.claude.com/docs/en/claude-apps-gateway-config)
- [Deployment guide](https://code.claude.com/docs/en/claude-apps-gateway-deploy)
- [Settings and precedence](https://code.claude.com/docs/en/settings#settings-files)
- [Feature availability](https://code.claude.com/docs/en/feature-availability)

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
