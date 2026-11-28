"""AgentCore Gateway REQUEST interceptor (MCP target).

Runs AFTER the Gateway's CUSTOM_JWT authorizer has already cryptographically
verified the bearer token (signature, expiry, issuer, allowed client).  This
Lambda's job is narrower but critical: derive the tenant/agent identity from
the verified token's `client_id` claim, and rewrite the MCP `tools/call`
request so that:

  1. Any caller-supplied tenant/agent/namespace/actor fields are stripped --
     an agent can send `{"tenant_id": "someone-elses-tenant", ...}` and it
     will be silently removed.
  2. The server-derived `server_tenant_id` / `server_agent_id` /
     `server_actor_id` are injected into the tool arguments. These are the
     ONLY fields the downstream tools Lambda trusts for tenant scoping.
  3. A caller-supplied `shopper_id` (identifying the individual shopper
     using the assistant -- NOT a tenant-isolation boundary, just a
     business-level partition within the already tenant/agent-locked
     namespace) is sanitized against a strict allowlist pattern and
     re-injected as `server_shopper_id`, so the tools Lambda can trust it
     the same way it trusts the other `server_*` fields without having to
     re-validate caller input itself.

We do not re-verify the JWT signature here (that's the authorizer's job and
duplicating JWKS verification in every interceptor invocation would add
latency for no additional security benefit here, since the interceptor
cannot execute at all unless the authorizer already accepted the token).
We DO still sanity-check `exp` defensively and fail closed on any decode
error or unrecognized client_id.

See docs: gateway-interceptors-types.html for the request interceptor
contract (interceptorInputVersion/interceptorOutputVersion "1.0",
mcp.gatewayRequest.{headers,body}, mcp.transformedGatewayRequest/Response).
"""
from __future__ import annotations

import base64
import json
import os
import re
import time

# client_id -> {"tenant_id": ..., "agent_id": ...}, injected as a Lambda
# environment variable (JSON) by deploy/05_lambdas.py, built from the
# Cognito app clients created in deploy/01_cognito.py. The attacker/no-scope
# client is intentionally absent from the Gateway's allowedClients list, so
# it never reaches this Lambda at all -- but we still fail closed below if
# an unrecognized client_id ever shows up.
CLIENT_MAP: dict = json.loads(os.environ.get("CLIENT_MAP_JSON", "{}"))

# Kept in sync with config/tenants.py (not imported directly -- this Lambda
# ships as a standalone zip with no repo-relative imports).
SHOPPER_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
DEFAULT_SHOPPER_ID = "anonymous"

# Argument keys a caller might try to use to spoof tenant/agent/namespace
# context. All of these are stripped unconditionally before the request
# reaches the tools Lambda. shopper_id is NOT in this list -- it's a
# legitimate business parameter, sanitized (not stripped) below.
SPOOFABLE_ARG_KEYS = {
    "tenant_id",
    "tenantid",
    "tenant",
    "agent_id",
    "agentid",
    "agent",
    "namespace",
    "namespace_path",
    "namespacepath",
    "actor_id",
    "actorid",
    "actor",
    "server_tenant_id",
    "server_agent_id",
    "server_actor_id",
    "server_shopper_id",
    "_trace_client_id",
    "_trace_stripped_fields",
    # kb_search: the index and metadata filter are derived from the tenant
    # server-side, never accepted from the caller.
    "filter",
    "metadata_filter",
    "index",
    "index_name",
    "kb_index",
    "vector_bucket",
}


def _b64url_decode(segment: str) -> bytes:
    segment += "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment)


def _decode_jwt_claims(token: str) -> dict:
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("not a JWT")
    return json.loads(_b64url_decode(parts[1]))


def _deny(request_id, message: str, status_code: int = 403) -> dict:
    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {
            "transformedGatewayResponse": {
                "statusCode": status_code,
                "body": {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32001, "message": f"tenant-isolation: {message}"},
                },
            }
        },
    }


def _passthrough(body: dict) -> dict:
    return {"interceptorOutputVersion": "1.0", "mcp": {"transformedGatewayRequest": {"body": body}}}


def handler(event, context):  # noqa: ANN001, ARG001
    mcp = event.get("mcp", {})
    gw_req = mcp.get("gatewayRequest", {})
    headers = gw_req.get("headers", {}) or {}
    body = gw_req.get("body", {}) or {}
    request_id = body.get("id")
    method = body.get("method")

    # Methods other than tools/call (initialize, tools/list, ping, ...) carry
    # no tenant-scoped arguments to rewrite; pass them through untouched.
    if method != "tools/call":
        return _passthrough(body)

    auth_header = headers.get("Authorization") or headers.get("authorization") or ""
    token = auth_header[7:] if auth_header.lower().startswith("bearer ") else auth_header
    if not token:
        return _deny(request_id, "missing bearer token at interceptor stage", 401)

    try:
        claims = _decode_jwt_claims(token)
    except Exception:  # noqa: BLE001
        return _deny(request_id, "unparseable token at interceptor stage", 401)

    if claims.get("exp") and time.time() > float(claims["exp"]):
        return _deny(request_id, "expired token", 401)

    client_id = claims.get("client_id") or claims.get("sub")
    mapping = CLIENT_MAP.get(client_id)
    if not mapping:
        return _deny(request_id, "unrecognized client_id for tenant mapping", 403)

    tenant_id = mapping["tenant_id"]
    agent_id = mapping["agent_id"]

    params = body.get("params", {}) or {}
    arguments = dict(params.get("arguments") or {})

    raw_shopper_id = arguments.get("shopper_id")

    stripped = [k for k in list(arguments.keys()) if k.lower() in SPOOFABLE_ARG_KEYS]
    for k in stripped:
        arguments.pop(k, None)

    shopper_id = (
        raw_shopper_id
        if isinstance(raw_shopper_id, str) and SHOPPER_ID_PATTERN.match(raw_shopper_id)
        else DEFAULT_SHOPPER_ID
    )

    arguments["server_tenant_id"] = tenant_id
    arguments["server_agent_id"] = agent_id
    arguments["server_shopper_id"] = shopper_id
    arguments["server_actor_id"] = f"tenant-{tenant_id}-{shopper_id}"
    # Trace fields for the UI's "What happened" panel (deploy-level
    # transparency feature, not a security control): which JWT client
    # authenticated this call, and exactly which caller-supplied fields
    # were stripped above. The tools Lambda echoes these back verbatim in
    # its response's _trace block so the UI can show the full chain.
    arguments["_trace_client_id"] = client_id
    arguments["_trace_stripped_fields"] = stripped

    params["arguments"] = arguments
    body["params"] = params
    return _passthrough(body)
