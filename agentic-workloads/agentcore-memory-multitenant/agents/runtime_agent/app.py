"""The shopping-assistant agent as an Amazon Bedrock AgentCore Runtime
container (HTTP protocol: GET /ping, POST /invocations on port 8080).

Identity propagates end to end with ONE token:

  webui backend --(Bearer brand+agent JWT)--> AgentCore Runtime
      Runtime's CUSTOM_JWT authorizer verifies signature/issuer/client
      before this container ever sees the request, then passes the
      Authorization header through (requestHeaderAllowlist).
  this agent --(the SAME Bearer JWT)--> AgentCore Gateway
      Gateway authorizer + interceptor derive the tenant from it again.

So the agent never receives, holds or chooses a tenant: it reads the
brand + agent only from the verified JWT's client_id (a fixed table,
CLIENT_MAP_JSON, same as the Gateway interceptor), for the persona in the
system prompt. The request payload carries no tenant field, and any it did
carry would be ignored. The agent holds no secrets: no Cognito client
secret, no SSM access, no Memory or KB permissions -- every data access
goes through the Gateway with the caller's JWT.
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import chat_core

# client_id -> {tenant_id, agent_id, tenant_display, agent_display}
CLIENT_MAP: dict = json.loads(os.environ.get("CLIENT_MAP_JSON", "{}"))
AVAILABLE_MODELS = {m["model_id"] for m in json.loads(os.environ.get("AVAILABLE_MODELS_JSON", "[]"))}
DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL_ID", "")
MAX_MESSAGE_CHARS = 1000
MAX_HISTORY_MESSAGES = 8
SHOPPER_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_started = int(time.time())


def _log(**fields) -> None:
    print(json.dumps({"ts": time.time(), **fields}), file=sys.stdout, flush=True)


def _claims(token: str) -> dict:
    """Decode (not verify) the JWT payload. Verification already happened
    in the Runtime's CUSTOM_JWT authorizer; a request with a bad token
    never reaches this container."""
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def _validate(payload: dict) -> str | None:
    message = payload.get("message")
    if not isinstance(message, str) or not message.strip() or len(message) > MAX_MESSAGE_CHARS:
        return f"message must be a non-empty string of at most {MAX_MESSAGE_CHARS} chars"
    history = payload.get("history") or []
    if not isinstance(history, list) or len(history) > MAX_HISTORY_MESSAGES:
        return f"history must be a list of at most {MAX_HISTORY_MESSAGES} Converse messages"
    for m in history:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant") or not isinstance(m.get("content"), list):
            return "each history entry must be a Converse message {role, content: [...]}"
    model_id = payload.get("model_id") or DEFAULT_MODEL
    if model_id not in AVAILABLE_MODELS:
        return f"unknown model_id: {model_id}"
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = "agentcore-mt-agent/1"

    def _send(self, status: int, body: dict) -> None:
        data = json.dumps(body, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):  # noqa: ANN001, ARG002 -- structured logs via _log instead
        return

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/ping"):
            self._send(200, {"status": "Healthy", "time_of_last_update": _started})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if not self.path.startswith("/invocations"):
            self._send(404, {"error": "not found"})
            return
        started = time.time()
        auth = self.headers.get("Authorization", "")
        token = auth[7:] if auth.lower().startswith("bearer ") else ""
        if not token:
            # Means the Runtime is not passing the header through
            # (requestHeaderConfiguration) -- fail closed.
            self._send(401, {"error": "no bearer token forwarded to the agent"})
            return
        try:
            client_id = _claims(token).get("client_id")
        except Exception:  # noqa: BLE001
            self._send(401, {"error": "unparseable bearer token"})
            return
        identity = CLIENT_MAP.get(client_id)
        if not identity:
            self._send(403, {"error": "client_id is not a known brand + agent"})
            return
        try:
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except json.JSONDecodeError:
            self._send(400, {"error": "body must be JSON"})
            return
        err = _validate(payload)
        if err:
            self._send(400, {"error": err})
            return
        shopper_id = payload.get("shopper_id") or "anonymous"
        if not SHOPPER_ID_PATTERN.match(shopper_id):
            shopper_id = "anonymous"
        try:
            out = chat_core.run_chat_turn(
                token,
                identity["tenant_display"],
                identity["agent_display"],
                shopper_id,
                payload["message"],
                payload.get("model_id") or DEFAULT_MODEL,
                payload.get("history") or [],
                tools_cache_key=client_id,
            )
        except Exception as exc:  # noqa: BLE001
            _log(event="turn_failed", client_id=client_id, error=f"{type(exc).__name__}: {exc}")
            self._send(500, {"error": f"{type(exc).__name__}: {exc}"})
            return
        out["runtime_identity"] = {
            "jwt_client_id": client_id,
            "tenant_id": identity["tenant_id"],
            "agent_id": identity["agent_id"],
            "source": "verified JWT (Runtime CUSTOM_JWT authorizer), forwarded unchanged to the Gateway",
        }
        _log(
            event="turn",
            tenant_id=identity["tenant_id"],
            agent_id=identity["agent_id"],
            model_id=out.get("model_id"),
            tools=[tc["name"] for tc in out.get("tool_calls", [])],
            input_tokens=out.get("input_tokens"),
            output_tokens=out.get("output_tokens"),
            cache_read_tokens=out.get("cache_read_tokens"),
            latency_ms=int((time.time() - started) * 1000),
        )
        self._send(200, out)


if __name__ == "__main__":
    _log(event="start", clients=len(CLIENT_MAP), otel_env=sorted(k for k in os.environ if k.startswith("OTEL_")))
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
