# Claude Code Proxy on AWS Docs

This directory is the code-aligned documentation set for the current repository state.

## Scope

- These docs describe the implementation that exists in this repo today.
- If code, migration, and docs disagree, trust them in this order:
  1. `gateway/`, `shared/`, `infra/`
  2. `migrations/versions/001_initial_schema.py`
  3. `docs/`
  4. `aidlc-docs/`

## Read Order

1. [`SYSTEM_ARCHITECTURE.md`](./SYSTEM_ARCHITECTURE.md)
   Runtime topology, auth boundaries, implemented flows, and current gaps.
2. [`API_SPEC.md`](./API_SPEC.md)
   Implemented HTTP surfaces only.
3. [`DATA_MODEL.md`](./DATA_MODEL.md)
   Current relational model, constraints, and state semantics.
4. [`RUNTIME_TRANSLATION.md`](./RUNTIME_TRANSLATION.md)
   Anthropic-compatible request/response conversion rules actually implemented by the gateway.
5. [`BEDROCK_FALLBACK.md`](./BEDROCK_FALLBACK.md)
   Bedrock-to-Anthropic-1P fallback: triggers, circuit breaker, streaming vs non-streaming, and limits.

## Current Status

- Implemented:
  - Token issuance and reuse via gateway path: `POST /v1/auth/token`
  - Runtime APIs: `GET /v1/healthz`, `GET /v1/models`, `POST /v1/messages`
  - Admin APIs for users, virtual keys, teams, model catalog/mappings/pricing, budgets, usage queries
  - Manual Identity Center user sync trigger/status endpoints
- Partial:
  - Gateway can emit metrics over OTLP when `OTLP_GRPC_ENDPOINT` is configured, but local compose disables export by default and does not provision a local collector
  - Aggregate usage tables and query endpoints exist, but there is no implemented writer/rollup path populating them
- Known repo issue:
  - `pytest -q` currently fails CDK stack tests because the configured Aurora engine constant is unavailable in the installed CDK version

## Historical Material

- Product planning, reference systems, and AI-DLC generation history are not SSoT.
- Use `aidlc-docs/` only when you need rationale or archived intent.
