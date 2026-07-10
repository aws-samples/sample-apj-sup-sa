# API Specification

This document covers only the HTTP surfaces implemented in the repository today.

## API Families

| Family | Base path | Auth | Ingress |
| --- | --- | --- | --- |
| Auth | `/v1/auth` | IAM + SigV4 | API Gateway -> ALB -> FastAPI |
| Runtime | `/v1` | `x-api-key` | Public ALB -> FastAPI |
| Admin | `/v1/admin` | IAM + SigV4 externally, forwarded as headers internally | API Gateway -> ALB -> FastAPI |

## Shared Conventions

### Request IDs

- Gateway-managed APIs (`/v1/auth/*`, `/v1/admin/*`, `/v1/messages`, `/v1/models`, `/v1/healthz`):
  - Use inbound `x-request-id` if present.
  - Otherwise generate `req_<uuid>`.

### Error envelopes

Auth and admin APIs:

```json
{
  "error": {
    "code": "internal_error",
    "message": "Internal server error",
    "request_id": "req_xxx",
    "retryable": false
  }
}
```

Runtime APIs:

```json
{
  "type": "error",
  "error": {
    "type": "api_error",
    "message": "Internal server error"
  },
  "request_id": "req_xxx"
}
```

## Auth API

### `POST /v1/auth/token`

Returns the caller's active virtual key or creates one.

If the caller already has an `ACTIVE` key row:

- unexpired key: the gateway reuses the existing secret
- expired key: the gateway refreshes the key material in the same row and returns the new secret

Admin rotation is different: it marks the old row `ROTATED` and creates a new `ACTIVE` row.

Request body fields accepted by the current gateway:

- `client_name`
- `client_version`
- `aws_profile`

Successful response:

```json
{
  "user": {
    "id": "uuid",
    "identity_store_user_id": "string",
    "display_name": "string|null",
    "email": "string|null",
    "default_team_id": "uuid|null"
  },
  "virtual_key": {
    "id": "uuid",
    "secret": "ak_live_xxx",
    "status": "ACTIVE",
    "issued_at": "2026-04-01T00:00:00+00:00",
    "expires_at": null
  }
}
```

`expires_at` is the runtime TTL boundary for the returned key. `null` means TTL is disabled.

Current error codes:

- `auth_origin_invalid` -> `403`
- `auth_principal_missing` -> `401`
- `user_not_synced` -> `403`
- `user_inactive` -> `409`
- `internal_error` -> `500`

Not implemented:

- `POST /v1/auth/virtual-key`
- `POST /v1/auth/virtual-key/refresh`

## Runtime API

### `GET /v1/healthz`

Response:

```json
{
  "status": "ok"
}
```

### `GET /v1/models`

Requires header:

- `x-api-key`

Response shape:

```json
{
  "data": [
    {
      "id": "canonical_name",
      "family": "string|null",
      "supports_streaming": true,
      "supports_tools": true,
      "supports_prompt_cache": false
    }
  ]
}
```

Important behavior:

- The gateway authenticates virtual key, user status, and default team status before serving this endpoint.
- It does not currently filter models by per-user/team model policy.
- It returns all active rows from `model_catalog`.

### `POST /v1/messages`

Requires header:

- `x-api-key`

Recognized request fields:

- `model`
- `max_tokens`
- `system`
- `messages`
- `tools`
- `tool_choice`
- `stream`
- `temperature`
- `top_p`
- `stop_sequences`
- `thinking`
- `metadata`

Extra top-level request fields are accepted by validation but ignored unless the runtime converter uses them.

Current converter behavior notes:

- Client-supplied `metadata` is accepted by validation but not forwarded directly to Bedrock.
- The gateway injects Bedrock `requestMetadata` with `request_id`, `user_id`, and `team_id` (when a team is resolved) for invocation-log filtering.

Non-streaming response:

```json
{
  "id": "msg_or_request_id",
  "type": "message",
  "role": "assistant",
  "model": "client_selected_model",
  "content": [],
  "stop_reason": "end_turn",
  "stop_sequence": null,
  "usage": {
    "input_tokens": 0,
    "output_tokens": 0,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0
  }
}
```

Streaming response:

- Content type: `text/event-stream`
- Implemented events:
  - `message_start`
  - `content_block_start`
  - `content_block_delta`
  - `content_block_stop`
  - `message_delta`
  - `message_stop`
  - `error` on stream failures after headers have already been sent

Current stream notes:

- Stream normalization preserves text blocks, tool-use JSON deltas, and reasoning deltas.
- Final usage is attached on `message_delta` once both Bedrock `messageStop` and `metadata` have arrived.
- Ping-style keepalive events are not currently surfaced.

Runtime error mappings:

- `invalid_virtual_key` -> `401`, `authentication_error`
- `virtual_key_expired` -> `401`, `authentication_error`
- `virtual_key_revoked` -> `401`, `authentication_error`
- `user_inactive` -> `403`, `permission_error`
- `team_inactive` -> `403`, `permission_error`
- `model_not_allowed` -> `403`, `permission_error`
- `budget_exceeded` -> `403`, `permission_error`
- `validation_error` -> `400`, `invalid_request_error`
- `bedrock_throttling` -> `429`, `rate_limit_error`
- `bedrock_error` -> `502`, `api_error`
- `internal_error` -> `500`, `api_error`

Validation caveat:

- `validation_error` here refers to runtime/business validation raised inside gateway code, such as model alias resolution failure.
- Malformed request bodies rejected by FastAPI/Pydantic still use the framework's default `422` validation response.

## Admin API

All admin routes are mounted under `/v1/admin`.

Internal gateway requirements:

- `x-admin-origin` must match configured trusted origin, default `apigw`
- `x-admin-principal` must be present

### Users

- `GET /v1/admin/users`
  - Query: `status`, `team_id`, `q`, `page`, `page_size`
- `GET /v1/admin/users/{user_id}`
- `PATCH /v1/admin/users/{user_id}`
- `PUT /v1/admin/users/{user_id}/runtime-policy`

Runtime policy payload:

```json
{
  "allowed_models": ["canonical_name"],
  "cache_policy": "none|5m|1h|null",
  "max_tokens_overrides": {
    "canonical_name": 4096
  }
}
```

### Virtual keys

- `GET /v1/admin/virtual-keys`
  - Query: `user_id`, `status=ACTIVE|REVOKED|ROTATED|EXPIRED`, `page`, `page_size`
  - TTL-based automatic refresh does not persist `EXPIRED`; that value is reserved for compatibility with existing enum/status filters.
- `GET /v1/admin/virtual-keys/{key_id}`
- `POST /v1/admin/virtual-keys/{key_id}/rotate`
- `POST /v1/admin/virtual-keys/{key_id}/revoke`

### Teams

- `GET /v1/admin/teams`
  - Query: `page`, `page_size`
- `POST /v1/admin/teams`
- `GET /v1/admin/teams/{team_id}`
- `PATCH /v1/admin/teams/{team_id}`
- `PUT /v1/admin/teams/{team_id}/runtime-policy`
- `POST /v1/admin/teams/{team_id}/members`
  - Body: `user_id`, optional `role` defaulting to `MEMBER`, optional `is_default`
- `DELETE /v1/admin/teams/{team_id}/members/{user_id}`

### Models

- `GET /v1/admin/models`
  - Query: `page`, `page_size`
- `POST /v1/admin/models`
  - Body: `canonical_name`, `bedrock_model_id`, optional `bedrock_region`, optional `anthropic_model_id`, `provider`, optional capability fields
- `PATCH /v1/admin/models/{model_id}`
  - Body: any subset of model fields, including optional `bedrock_region` and `anthropic_model_id`
- `DELETE /v1/admin/models/{model_id}`
- `GET /v1/admin/model-mappings`
  - Query: `page`, `page_size`
- `POST /v1/admin/model-mappings`
- `PUT /v1/admin/model-mappings/{mapping_id}`
- `DELETE /v1/admin/model-mappings/{mapping_id}`
- `GET /v1/admin/model-pricing`
  - Query: `model_id` (optional filter), `page`, `page_size`
- `POST /v1/admin/model-pricing`
- `PATCH /v1/admin/model-pricing/{pricing_id}`
- `DELETE /v1/admin/model-pricing/{pricing_id}`

### Budgets

- `GET /v1/admin/budgets`
  - Query: `scope_type`, `scope_id`, `period`, `model_id`, `page`, `page_size`
- `POST /v1/admin/budgets`
- `PATCH /v1/admin/budgets/{budget_id}`
- `GET /v1/admin/budget-status`

Budget status behavior:

- Computes estimated spend from `usage_daily_agg`.
- Because aggregate tables are not maintained by runtime, this endpoint is only useful if those tables are populated externally.
- The response currently hardcodes `status: WITHIN_LIMIT`; it does not derive a threshold-specific status from the computed remainder.

### Usage queries

- `GET /v1/admin/usage/events`
  - Query: `user_id`, `team_id`, `resolved_model_id`, `status`, `from`, `to`, `page`, `page_size`
- `GET /v1/admin/usage/aggregates`
  - Query: `period=daily|monthly`, `user_id`, `team_id`, `model_id`, `from`, `to`

### Manual sync

- `POST /v1/admin/sync/identity-center`
- `GET /v1/admin/sync/identity-center/runs/{run_id}`

Current behavior:

- The trigger endpoint lists users from the configured IAM Identity Center identity store and syncs them into the local `users` table.
- Users are matched by `identity_store_user_id`.
- Missing users are marked `INACTIVE` and get `source_deleted_at`.
- Successful trigger response:

```json
{
  "sync_run_id": "uuid",
  "status": "SUCCEEDED",
  "sync_scope": "USERS_ONLY",
  "users_scanned": 12,
  "users_created": 2,
  "users_updated": 3,
  "users_inactivated": 1
}
```

## API Gaps

- No OpenAI-compatible surface
- No token refresh endpoint
- No admin endpoint for explicit usage rollup
- No public contract for identity import beyond the manual trigger/status endpoints
