# Silo 5 — Session Broker + IaC glue (`ApiStack` + app wiring)

**Owns (writes only here):** `api/**`, `infra/lib/api-stack.ts`, `infra/lib/web-stack.ts`
(S3 + CloudFront resources), `infra/bin/aisle.ts` (CDK app wiring), all `/aisle/*` SSM
param plumbing across stacks.
**Deploys:** `ApiStack` (and owns the `WebStack` resource shell that Agent 4 deploys into).

## Build — Session Broker (the load-bearing auth piece)
- Python 3.12 arm64 Lambda + Function URL (`AuthType: NONE` for demo, CORS locked to
  the CloudFront origin).
- `GET /session?mode=home|store`:
  1. `session_id = uuid4()`
  2. mint a **SigV4 pre-signed `wss://` URL** via
     `AgentCoreRuntimeClient.generate_presigned_url(runtime_arn=AGENT_RUNTIME_ARN, expires=300)`,
     binding header `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id = session_id`.
  3. return `SessionResponse` (`contracts.py` / `contracts.ts`).
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
