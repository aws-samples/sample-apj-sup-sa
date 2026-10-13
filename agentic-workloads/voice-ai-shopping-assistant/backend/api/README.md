# Session Broker + IaC glue (`ApiStack` + app wiring)

**Deploys:** `ApiStack` (Session Broker) plus the `WebStack` resource shell and
the CDK app wiring in `infra/bin/aisle.ts`, and the `/aisle/*` SSM parameter
plumbing across stacks.

## Build — Session Broker (the load-bearing auth piece)
- Python 3.12 arm64 Lambda + Function URL (`AuthType: NONE` for demo, CORS locked to
  the CloudFront origin).
- `GET /session?mode=home|store`:
  1. `session_id = uuid4()`
  2. mint a **SigV4 pre-signed `wss://` URL** via
     `AgentCoreRuntimeClient.generate_presigned_url(runtime_arn=AGENT_RUNTIME_ARN, expires=300)`,
     binding header `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id = session_id`.
  3. return `SessionResponse` (shape in `contracts.py`).
- **Browsers cannot set WS handshake headers → presigned URL is the ONLY viable auth.**
- IAM: `bedrock-agentcore:InvokeAgentRuntimeWithWebSocketStream` on the runtime ARN.
- Env: `AGENT_RUNTIME_ARN` (from `/aisle/agent/runtime_arn`), `ALLOWED_ORIGIN`.

## IaC glue
Wire every stack via SSM (no hard cross-stack refs). Deploy order:
`DataStack → ToolsStack → AgentStack → ApiStack → WebStack`.

## Exports (SSM)
`/aisle/session/url`, `/aisle/web/url`

## Verify
`curl {session_url}/session?mode=home` → valid `SessionResponse`; the `ws_url` connects
to AgentCore and survives a full voice turn.
