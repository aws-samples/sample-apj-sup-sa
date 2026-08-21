# Architecture

![Architecture diagram: caller invokes the AgentCore Runtime, which runs agent.py; the Bedrock model reasons and calls tools over MCP through the AgentCore Gateway (JWT-validated via Cognito Identity); the Gateway invokes the Tools Lambda, which reads S3 datasets and writes reports/audit to S3; OTEL spans flow to Observability and CloudWatch GenAI dashboards.](architecture.svg)

## Components

| Layer | Component | Service | Notes |
|-------|-----------|---------|-------|
| Reasoning | Foundation model | Amazon Bedrock (Claude Sonnet) | The agent's decision loop |
| Agent | `agent.py` (Strands `Agent`) | — | Prompt + tools + model; framework-agnostic |
| Hosting | AgentCore Runtime | Bedrock AgentCore | Serverless, containerized agent endpoint |
| Tools | AgentCore Gateway | Bedrock AgentCore | Exposes the tool Lambda as one MCP endpoint |
| Tool compute | Tools Lambda | AWS Lambda | Implements all 6 tools; dispatches on tool name |
| Data | Datasets | Amazon S3 | 5 JSON files, read by the tool Lambda |
| Audit | Reports + audit log | Amazon S3 | Written by `generate_anomaly_report` and per-call |
| Auth | Inbound OAuth | Amazon Cognito (M2M) | Bearer token for the Gateway |
| Telemetry | Observability | AgentCore Observability + CloudWatch | OTEL spans, token usage, latency |

## Runtime data flow (deployed, gateway mode)

```
  caller: agentcore invoke {"prompt": "..."}
     |
     v
  [ AgentCore Runtime ]  ── runs agent.py handler()
     |
     |  (1) Bedrock InvokeModel  ── Claude reasons, decides a tool call
     v
  Bedrock model
     |
     |  (2) MCP tool call over HTTPS + Bearer(JWT)
     v
  [ AgentCore Gateway ]  ── validates token, maps tool -> target
     |
     |  (3) lambda:InvokeFunction (tool name in client_context)
     v
  Tools Lambda  ── reads s3://.../data/*.json, computes result
     |                writes audit + report to audit bucket
     v
  result -> Gateway -> model -> (loop until Definition of Done) -> report
     |
     v
  spans/metrics -> AgentCore Observability -> CloudWatch GenAI dashboards
```

## The three run modes of one agent

| Mode | Tools come from | Set by | Used in |
|------|-----------------|--------|---------|
| local | `tools_local.py` reading `agent/data/` | `TOOL_MODE=local` | early local dev |
| gateway | AgentCore Gateway over MCP | `TOOL_MODE=gateway` + `gateway.env` | local-against-remote |
| runtime | Gateway (same as above), inside AgentCore | `agentcore deploy` env | production |

The tool *contract* (`tool_definition.json`) is identical across modes, so the
agent's behaviour is portable.

## Trust boundaries

- **Caller -> Runtime:** AgentCore Runtime auth (IAM / SigV4 via the CLI).
- **Agent -> Gateway:** OAuth 2.0 bearer token (Cognito M2M), validated by the
  Gateway's CUSTOM_JWT authorizer.
- **Gateway -> Lambda:** the Gateway's IAM role, scoped to `lambda:InvokeFunction`
  on exactly the tools function.
- **Lambda -> S3:** the Lambda execution role, scoped to GetObject on the data
  bucket and PutObject on the audit bucket only.

## Async batch path (canvas: "async batch, overnight")

`infra/scheduled/` implements the scheduled trigger the canvas calls for:

```
  EventBridge Scheduler (cron, UTC)
     |  (scheduler role) lambda:InvokeFunction
     v
  Batch invoker Lambda ── reads latest month from S3, builds a per-station prompt
     |  bedrock-agentcore:InvokeAgentRuntime
     v
  [ AgentCore Runtime ]  ── same agent, one session per station
     |
     v
  reports + run summary -> S3 audit bucket ("report in the inbox by morning")
```

No human watches the loop; the agent runs overnight and leaves reports behind. See
`docs/COST_ESTIMATION.md` for why a scheduled batch avoids idle real-time capacity.

## Security notes

- No public endpoints: the Gateway requires a valid JWT; the Lambda is only
  invokable by the Gateway principal.
- Least-privilege IAM throughout (see `infra/template.yaml` and
  `infra/create_gateway.py`).
- S3 buckets block all public access and use SSE (AES256).
- Datasets are synthetic; no PII.
