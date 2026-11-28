"""Web UI backend Lambda (invoked via a Lambda Function URL, fronted by
CloudFront). Serves the demo chat UI's API: /api/config, /api/chat,
/api/memory, /api/attack.

Design notes:
  - The browser NEVER holds a tenant/agent JWT. It only ever holds which
    tenant/agent/shopper it's currently viewing. This Lambda looks up the
    right Cognito app client for that (tenant, agent), mints (and caches)
    the JWT server-side, and uses it to call the Gateway's MCP endpoint.
    This satisfies "the browser must never hold another tenant's
    credentials" trivially -- it never holds ANY tenant's credentials.
  - Chat turns run on AgentCore Runtime when RUNTIME_ARN is set: this
    Lambda invokes the Runtime over HTTPS with that same minted JWT as the
    bearer token (the Runtime's CUSTOM_JWT authorizer verifies it), and the
    Runtime agent forwards it unchanged to the Gateway -- one identity, end
    to end. If the Runtime call fails, the turn falls back to running the
    identical loop (lambda_src/chat_core/chat_core.py) right here, and the
    response says which one served it (`served_by`).
  - Secrets (Cognito client secrets) are read from SSM Parameter Store
    (SecureString), never hardcoded or passed as plain Lambda environment
    variables.
  - The site is public (no sign-in), so abuse is bounded in layers:
    a WAF web ACL on CloudFront (per-IP rate limit on /api/* + AWS managed
    common rules), reserved concurrency on this function (see
    deploy/13_abuse_controls.py), and the per-request caps below
    (MAX_MESSAGE_CHARS / MAX_HISTORY_TURNS), enforced here, server-side.
"""
from __future__ import annotations

import hashlib
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

import boto3

import chat_core

REGION = os.environ.get("AWS_REGION", "us-east-1")
TENANT_AGENTS = json.loads(os.environ["TENANT_AGENTS_JSON"])
# [{"tenant_id":, "agent_id":, "tenant_display":, "agent_display":, "client_id":,
#   "scope":, "token_endpoint":, "secret_param": }]
DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL_ID", "")
AVAILABLE_MODELS = json.loads(os.environ.get("AVAILABLE_MODELS_JSON", "[]"))
GUARDRAIL_ID = os.environ.get("GUARDRAIL_ID", "")
# AgentCore Runtime hosting the agent (deploy/15_runtime.py). Empty = run
# every turn in this Lambda.
RUNTIME_ARN = os.environ.get("RUNTIME_ARN", "")
RUNTIME_TIMEOUT_S = int(os.environ.get("RUNTIME_TIMEOUT_S", "20"))
# Server-side request caps (the browser enforces the same limits for UX,
# but only these count): one shopper message, and how many prior turns of
# the current chat the client may send back as context.
MAX_MESSAGE_CHARS = int(os.environ.get("MAX_MESSAGE_CHARS", "1000"))
MAX_HISTORY_TURNS = int(os.environ.get("MAX_HISTORY_TURNS", "4"))

_ssm = boto3.client("ssm", region_name=REGION)
_ssl_ctx = ssl.create_default_context()

_param_cache: dict[str, tuple[float, str]] = {}
_token_cache: dict[str, tuple[float, str]] = {}

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


def _get_param(name: str, ttl_s: int = 300) -> str:
    cached = _param_cache.get(name)
    if cached and cached[0] > time.time():
        return cached[1]
    resp = _ssm.get_parameter(Name=name, WithDecryption=True)
    value = resp["Parameter"]["Value"]
    _param_cache[name] = (time.time() + ttl_s, value)
    return value


def _http_post_form(url: str, fields: dict, timeout: int = 20) -> dict:
    import urllib.parse

    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx) as resp:
        raw = resp.read().decode()
    return json.loads(raw)


class UnknownTenantAgent(ValueError):
    pass


def _tenant_agent_info(tenant_id: str, agent_id: str) -> dict:
    for info in TENANT_AGENTS:
        if info["tenant_id"] == tenant_id and info["agent_id"] == agent_id:
            return info
    raise UnknownTenantAgent(f"unknown tenant/agent: {tenant_id}/{agent_id}")


def _mint_token(tenant_id: str, agent_id: str) -> str:
    key = f"{tenant_id}:{agent_id}"
    cached = _token_cache.get(key)
    if cached and cached[0] > time.time():
        return cached[1]

    info = _tenant_agent_info(tenant_id, agent_id)
    secret = _get_param(info["secret_param"])
    resp = _http_post_form(
        info["token_endpoint"],
        {
            "grant_type": "client_credentials",
            "client_id": info["client_id"],
            "client_secret": secret,
            "scope": f"agentcore-mt-demo/{info['scope']}",
        },
    )
    token = resp["access_token"]
    # Cognito M2M tokens here are valid 60 minutes; refresh a bit early.
    _token_cache[key] = (time.time() + 50 * 60, token)
    return token


def _runtime_session_id(tenant_id: str, agent_id: str, shopper_id: str) -> str:
    """One Runtime session (its own microVM) per brand + agent + shopper.
    Session ids must be 33+ chars."""
    digest = hashlib.sha256(f"{tenant_id}/{agent_id}/{shopper_id}".encode()).hexdigest()[:24]
    return f"{tenant_id}-{agent_id}-{shopper_id}-{digest}"


def _invoke_runtime(token: str, session_id: str, payload: dict) -> dict:
    """Invoke the AgentCore Runtime with the brand+agent JWT as the bearer
    token (inbound CUSTOM_JWT auth -- no SigV4, so this Lambda needs no
    bedrock-agentcore:InvokeAgentRuntime permission at all)."""
    url = (
        f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/"
        f"{urllib.parse.quote(RUNTIME_ARN, safe='')}/invocations?qualifier=DEFAULT"
    )
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=RUNTIME_TIMEOUT_S, context=_ssl_ctx) as resp:
        return json.loads(resp.read().decode())


def _run_chat_turn(
    tenant_id: str, agent_id: str, shopper_id: str, message: str, model_id: str, history: list | None = None
) -> dict:
    token = _mint_token(tenant_id, agent_id)
    info = _tenant_agent_info(tenant_id, agent_id)
    runtime_error = None
    if RUNTIME_ARN:
        session_id = _runtime_session_id(tenant_id, agent_id, shopper_id)
        started = time.time()
        try:
            # Note what is NOT sent: no tenant_id / agent_id. The Runtime
            # agent derives both from the verified JWT.
            out = _invoke_runtime(
                token,
                session_id,
                {"message": message, "shopper_id": shopper_id, "model_id": model_id, "history": history or []},
            )
            if "reply" not in out:
                raise RuntimeError(f"unexpected Runtime response: {str(out)[:300]}")
            out["served_by"] = "agentcore-runtime"
            out["runtime_session_id"] = session_id
            out["runtime_latency_ms"] = int((time.time() - started) * 1000)
            return out
        except urllib.error.HTTPError as exc:
            runtime_error = f"HTTP {exc.code}: {exc.read().decode()[:300]}"
        except Exception as exc:  # noqa: BLE001
            runtime_error = f"{type(exc).__name__}: {exc}"
    out = chat_core.run_chat_turn(
        token,
        info["tenant_display"],
        info["agent_display"],
        shopper_id,
        message,
        model_id,
        history,
        tools_cache_key=f"{tenant_id}:{agent_id}",
    )
    out["served_by"] = "lambda-fallback" if RUNTIME_ARN else "lambda"
    if runtime_error:
        out["runtime_error"] = runtime_error
    return out


def _approve_pending_action(tenant_id: str, agent_id: str, shopper_id: str, proposed_action: dict) -> dict:
    """Re-invokes the SAME tool the model proposed, now with approved=True,
    only after the shopper has explicitly confirmed in the UI. The backend
    (not the model) controls this re-invocation -- the model never sees or
    sets `approved` itself in the first call."""
    token = _mint_token(tenant_id, agent_id)
    tool_name = proposed_action.get("tool")
    tool_input = {k: v for k, v in proposed_action.items() if k not in ("tool", "tenant_id")}
    tool_input["approved"] = True
    result = chat_core.mcp_tool_call(token, tool_name, tool_input)
    return {"approved_result": result, "tool": tool_name, "input": tool_input}


def _get_memory_snapshot(tenant_id: str, agent_id: str, shopper_id: str) -> dict:
    token = _mint_token(tenant_id, agent_id)
    shared = chat_core.mcp_tool_call(token, "list_memories", {"scope": "shared", "shopper_id": shopper_id})
    private = chat_core.mcp_tool_call(token, "list_memories", {"scope": "private", "shopper_id": shopper_id})
    # Namespaces/actorId as derived server-side (interceptor + tools Lambda),
    # so the UI shows what was actually used rather than recomputing it.
    trace = shared.get("_trace", {})
    return {
        "shared": shared.get("records", []),
        "private": private.get("records", []),
        "namespaces": {
            "shared": (shared.get("namespaces_searched") or [None])[0],
            "private": (private.get("namespaces_searched") or [None])[0],
        },
        "actor_id": trace.get("server_actor_id"),
        "tenant_role_arn": trace.get("tenant_role_arn"),
    }


def _run_attack_demo(tenant_id: str, agent_id: str, shopper_id: str, target_tenant_id: str) -> dict:
    """Demonstrates the cross-tenant attack from the UI: use the CURRENT
    tenant's JWT but smuggle the TARGET tenant's id into the tool
    arguments. The interceptor strips it server-side, so the result still
    only ever contains the caller's OWN tenant's data. ALSO runs a direct
    IAM-layer probe (via the tools Lambda, using the SAME already-assumed
    current-tenant role) against the target tenant's own namespace, so the
    demo shows both independent layers that would stop a real attacker,
    not just the one that happens to run first."""
    if target_tenant_id == tenant_id:
        raise ValueError("pick another brand: target_tenant_id must differ from the current tenant_id")
    token = _mint_token(tenant_id, agent_id)
    spoofed_args = {
        "scope": "both",
        "shopper_id": shopper_id,
        "tenant_id": target_tenant_id,
        "namespace": f"/tenant/{target_tenant_id}/shared/{shopper_id}/",
        "iam_probe_target_tenant_id": target_tenant_id,
    }
    result = chat_core.mcp_tool_call(token, "list_memories", spoofed_args)
    namespaces_actually_searched = result.get("namespaces_searched", [])
    blocked = all(f"/tenant/{tenant_id}/" in ns for ns in namespaces_actually_searched) and not any(
        f"/tenant/{target_tenant_id}/" in ns for ns in namespaces_actually_searched
    )
    trace = result.get("_trace", {})
    iam_probe = result.get("iam_probe", {})
    iam_layer_detail = (
        f"direct call using {trace.get('tenant_role_arn')} against the TARGET's own namespace "
        f"({iam_probe.get('namespace_probed')}) returned {iam_probe.get('error', 'no error')} -- IAM "
        f"would have denied this even if the interceptor above had a bug"
        if iam_probe.get("denied")
        else "UNEXPECTED: direct IAM-layer probe against the target's namespace was NOT denied -- this "
        "would be a real bug"
    )
    return {
        "attempted_spoofed_args": spoofed_args,
        "namespaces_actually_searched": namespaces_actually_searched,
        "records_returned": result.get("records", []),
        "blocked": blocked,
        "layer_trace": [
            {"layer": "1. AuthN (Cognito JWT + Gateway CUSTOM_JWT authorizer)",
             "detail": f"token accepted for client_id={trace.get('jwt_client_id')} -> mapped to tenant={tenant_id}"},
            {"layer": "2. AuthZ / context derivation (REQUEST interceptor)",
             "detail": f"stripped caller-supplied fields: {trace.get('stripped_fields')} "
                       f"(the spoofed tenant_id='{target_tenant_id}' and namespace were in this list); "
                       f"injected server_tenant_id='{tenant_id}', server_shopper_id='{shopper_id}'"},
            {"layer": "3. IAM (per-tenant role + namespace condition)",
             "detail": f"tools Lambda assumed {trace.get('tenant_role_arn')} and could only ever touch "
                       f"/tenant/{tenant_id}/* -- the role for '{target_tenant_id}' was never assumed"},
            {"layer": "4. IAM, proven directly (not just inferred)",
             "detail": iam_layer_detail},
        ],
        "verdict": (
            f"BLOCKED: the forged tenant_id/namespace pointing at '{target_tenant_id}' was silently "
            f"stripped by the Gateway's REQUEST interceptor. Only '{tenant_id}' own namespaces were "
            "ever searched, server-side -- the caller's JWT determines tenant identity, not any "
            "argument it sends. A direct IAM-layer probe independently confirms the same tenant's role "
            f"cannot read '{target_tenant_id}' data either."
            if blocked and iam_probe.get("denied")
            else "UNEXPECTED: the spoofed tenant namespace was NOT blocked -- this would be a real bug."
        ),
    }


def _public_config() -> dict:
    tenants: dict[str, dict] = {}
    for info in TENANT_AGENTS:
        t = tenants.setdefault(
            info["tenant_id"], {"tenant_id": info["tenant_id"], "tenant_display": info["tenant_display"], "agents": []}
        )
        t["agents"].append({"agent_id": info["agent_id"], "agent_display": info["agent_display"]})
    return {
        "tenants": list(tenants.values()),
        "models": AVAILABLE_MODELS,
        "default_model": DEFAULT_MODEL,
        "guardrail_enabled": bool(GUARDRAIL_ID),
        "chat_host": "agentcore-runtime" if RUNTIME_ARN else "lambda",
    }


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", **CORS_HEADERS},
        "body": json.dumps(body, default=str),
    }


def _require_fields(body: dict, fields: list) -> str | None:
    """Returns an error message if any required field is missing/empty,
    else None. Used to turn a would-be KeyError (-> 500) into a clean 400."""
    missing = [f for f in fields if not body.get(f)]
    if missing:
        return f"missing required field(s): {', '.join(missing)}"
    return None


def _validate_chat_body(body: dict) -> str | None:
    """Server-side abuse caps for /api/chat (the site is public). Returns an
    error message for a 400, else None."""
    if not isinstance(body.get("message"), str):
        return "message must be a string"
    if len(body["message"]) > MAX_MESSAGE_CHARS:
        return f"message too long: {len(body['message'])} chars (max {MAX_MESSAGE_CHARS})"
    model_id = body.get("model_id")
    if model_id and model_id not in {m["model_id"] for m in AVAILABLE_MODELS}:
        return f"unknown model_id: {model_id}"
    history = body.get("history") or []
    if not isinstance(history, list):
        return "history must be a list"
    if len(history) > MAX_HISTORY_TURNS:
        return f"history too long: {len(history)} turns (max {MAX_HISTORY_TURNS})"
    for turn in history:
        if not isinstance(turn, dict) or turn.get("role") not in ("user", "assistant"):
            return "each history turn must be {role: user|assistant, text: str}"
        if not isinstance(turn.get("text"), str) or len(turn["text"]) > MAX_MESSAGE_CHARS:
            return f"each history turn's text must be a string of at most {MAX_MESSAGE_CHARS} chars"
    return None


def _history_to_messages(history: list) -> list:
    """Turns validated client history into Converse messages. Converse
    needs strictly alternating roles starting with 'user', so consecutive
    same-role turns are merged and a leading assistant turn is dropped."""
    messages: list = []
    for turn in history:
        if not turn["text"].strip():
            continue
        if not messages and turn["role"] == "assistant":
            continue
        if messages and messages[-1]["role"] == turn["role"]:
            messages[-1]["content"][0]["text"] += "\n" + turn["text"]
        else:
            messages.append({"role": turn["role"], "content": [{"text": turn["text"]}]})
    # The new message is appended as a user turn, so history must end on an
    # assistant turn.
    if messages and messages[-1]["role"] == "user":
        messages.pop()
    return messages


def handler(event, context):  # noqa: ANN001, ARG001
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath", "/")
    query = event.get("queryStringParameters") or {}

    if method == "OPTIONS":
        return {"statusCode": 204, "headers": CORS_HEADERS, "body": ""}

    try:
        parsed_body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        parsed_body = {}

    if path == "/api/config" and method == "GET":
        return _response(200, _public_config())

    try:
        if path == "/api/chat" and method == "POST":
            if parsed_body.get("approve_pending"):
                err = _require_fields(parsed_body, ["tenant_id", "agent_id"])
                if err:
                    return _response(400, {"error": err})
                out = _approve_pending_action(
                    parsed_body["tenant_id"],
                    parsed_body["agent_id"],
                    parsed_body.get("shopper_id", "anonymous"),
                    parsed_body["approve_pending"],
                )
                return _response(200, out)
            err = _require_fields(parsed_body, ["tenant_id", "agent_id", "message"]) or _validate_chat_body(parsed_body)
            if err:
                return _response(400, {"error": err})
            out = _run_chat_turn(
                parsed_body["tenant_id"],
                parsed_body["agent_id"],
                parsed_body.get("shopper_id", "anonymous"),
                parsed_body["message"],
                parsed_body.get("model_id") or DEFAULT_MODEL,
                _history_to_messages(parsed_body.get("history") or []),
            )
            return _response(200, out)

        if path == "/api/memory" and method == "GET":
            err = _require_fields(query, ["tenant_id", "agent_id"])
            if err:
                return _response(400, {"error": err})
            out = _get_memory_snapshot(
                query.get("tenant_id", ""), query.get("agent_id", ""), query.get("shopper_id", "anonymous")
            )
            return _response(200, out)

        if path == "/api/attack" and method == "POST":
            err = _require_fields(parsed_body, ["tenant_id", "agent_id", "target_tenant_id"])
            if err:
                return _response(400, {"error": err})
            if parsed_body["target_tenant_id"] == parsed_body["tenant_id"]:
                return _response(
                    400,
                    {"error": "pick another brand: target_tenant_id must differ from the current tenant_id "
                     "(this isn't a real cross-tenant attempt otherwise)"},
                )
            out = _run_attack_demo(
                parsed_body["tenant_id"],
                parsed_body["agent_id"],
                parsed_body.get("shopper_id", "anonymous"),
                parsed_body["target_tenant_id"],
            )
            return _response(200, out)

        return _response(404, {"error": f"no route for {method} {path}"})
    except UnknownTenantAgent as exc:
        return _response(400, {"error": str(exc)})
    except urllib.error.HTTPError as exc:
        return _response(502, {"error": f"gateway HTTPError {exc.code}: {exc.read().decode()[:500]}"})
    except Exception as exc:  # noqa: BLE001
        return _response(500, {"error": f"{type(exc).__name__}: {exc}"})
