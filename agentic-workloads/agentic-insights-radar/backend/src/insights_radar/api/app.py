# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Lambda entrypoint for the HTTP API (``/api/*`` user routes and ``/agent/tools/*``)."""

from __future__ import annotations

import functools
import json
import logging
import os
import re
from typing import Any

from botocore.exceptions import ClientError

from insights_radar.api.agent_tools import AgentTools
from insights_radar.api.body import Body
from insights_radar.api.context import AppContext, Settings
from insights_radar.api.http import Request, Response, Router, error_response, ok
from insights_radar.api.routes import PUBLIC_ROUTES, build_router, unsubscribe_page_answer
from insights_radar.chat.budget import MAX_REPORT_TOKENS
from insights_radar.core.errors import THROTTLING_CODES, DomainError, ForbiddenError, UnauthorizedError, ValidationError
from insights_radar.core.tokens import TokenUsage
from insights_radar.identity.principal import RoleCaller

log = logging.getLogger()
log.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


class Application:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.router: Router = build_router(ctx)
        self.tools = AgentTools(ctx)

    def _chat_usage(self, req: Request) -> dict[str, Any]:
        """Chat runtime reports a turn's usage. IAM route; only the chat runtime role may call it."""
        if not RoleCaller.matches(self.ctx.settings.chat_runtime_role_arn, req.source_arn):
            raise ForbiddenError("only the chat runtime may report chat usage")
        b = Body(req.json())
        b.only(
            "subject",
            "turn_id",
            "reserve_tokens",
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
        )
        subject = b.text("subject", max_len=200)
        turn_id = b.opt_text("turn_id", 64)
        if b.has("reserve_tokens"):  # start of a turn: hold its ceiling
            if turn_id is None:
                raise ValidationError("a reservation needs a turn_id")
            return {"reserved": self.ctx.chat_budget.reserve(subject, turn_id, b.integer("reserve_tokens"))}
        usage = TokenUsage(
            *(
                b.integer(k, required=False, low=0, high=MAX_REPORT_TOKENS)
                for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
            )
        )
        if turn_id is None:  # without a turn id a retried report would be charged twice
            raise ValidationError("a usage report needs a turn_id")
        # End of a reserved turn: swap the hold for the real usage, once.
        return {"weighted_tokens": self.ctx.chat_budget.settle(subject, turn_id, usage)}

    def _event_cors(self, event: dict[str, Any]) -> str | None:
        """The allowed origin from the raw event, before the body is parsed: a body over the limit
        raised in from_event, so the 413 carried no Access-Control-Allow-Origin and the browser
        reported a network error instead of "too large"."""
        allowed = self.ctx.settings.allowed_origin
        headers = event.get("headers") or {}
        origin = next((str(v) for k, v in headers.items() if str(k).lower() == "origin"), None)
        return origin if allowed and origin == allowed else None

    def handle(self, event: dict[str, Any]) -> dict[str, Any]:
        return unsubscribe_page_answer(event, self._handle(event), self.ctx.settings.digest.app_url)

    def _handle(self, event: dict[str, Any]) -> dict[str, Any]:
        cors = self._event_cors(event)
        try:
            req = Request.from_event(event)
            if req.method == "OPTIONS":
                return Response(
                    204,
                    raw="",
                    headers={
                        "Access-Control-Allow-Methods": "GET,POST,PUT,PATCH,DELETE",
                        "Access-Control-Allow-Headers": "authorization,content-type",
                        "Access-Control-Max-Age": "600",
                    },
                ).to_event(cors)
            if req.path == "/agent/chat/usage" and req.method == "POST":
                return ok(self._chat_usage(req)).to_event()
            if req.path.startswith("/agent/tools/"):
                tool = req.path.removeprefix("/agent/tools/")
                result = self.tools.call(tool, req.headers.get("x-mr-run-token"), req.json(), req.source_arn)
                return ok({"result": result}).to_event()
            if req.via_iam:
                # The deploy options mcpIssuer and mcpAudience: a SigV4 caller must be a session of the stack's MCP
                # role (HTTP APIs have no resource policy, so any account's signature passes the
                # authorizer). Off -> no role ARN -> every such request is refused.
                if RoleCaller.session_name(self.ctx.settings.mcp_role_arn, req.source_arn) is None:
                    raise UnauthorizedError("sign in required")
            elif not req.claims and (req.method, req.path) not in PUBLIC_ROUTES:
                # Before routing: an anonymous caller must not learn which routes and methods exist.
                raise UnauthorizedError("sign in required")
            handler, params, _template = self.router.resolve(req)
            req = Request(
                req.method, req.path, req.headers, req.query, req.body, req.claims, params, req.source_arn, req.via_iam
            )
            return handler(req).to_event(cors)
        except DomainError as err:
            if err.status >= 500:
                log.warning("service error %s: %s", err.code, err.message)
            return error_response(err).to_event(cors)
        except ClientError as err:
            if err.response.get("Error", {}).get("Code") in THROTTLING_CODES:
                # Retryable: tell the client to back off rather than report a fault.
                log.warning("throttled by AWS: %s", err.response["Error"]["Code"])
                return Response(
                    503, {"code": "throttled", "message": "the service is busy; retry shortly"}, {"Retry-After": "2"}
                ).to_event(cors)
            log.exception("unhandled error")
            return Response(500, {"code": "internal", "message": "something went wrong"}).to_event(cors)
        except Exception:
            log.exception("unhandled error")
            return Response(500, {"code": "internal", "message": "something went wrong"}).to_event(cors)


# An error code is server-chosen (validation_failed, not_found); anything else is logged as "error".
_CODE = re.compile(r"[a-z_]{2,40}")


@functools.cache
def _app() -> Application:
    return Application(AppContext(Settings.from_env(), request_scoped=True))


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    res = _app().handle(event)
    path = (event.get("rawPath") or "")[:200]
    line: dict[str, Any] = {"event": "request", "status": res.get("statusCode"), "path": path}
    if path.startswith("/agent/tools/") and 400 <= int(res.get("statusCode") or 0) < 500:
        # Why an agent's write was refused: the model is told, but the log only had the status, so six
        # refused kg_add_evidence calls of one Scout run could not be explained afterwards (2026-10-31).
        line["refusal"] = _refusal(res.get("body"))
    log.info(json.dumps(line))
    return res


def _refusal(body: Any) -> str:
    """Why a tool call was refused: the error code, and how many linked nodes were missing. No free text: error
    messages carry what the caller sent, quoted or not, and page text can steer that (code review r18)."""
    try:
        parsed = json.loads(body) if isinstance(body, str) else {}
    except ValueError:
        return ""
    if not isinstance(parsed, dict) or not parsed:
        return ""
    raw_code = parsed.get("code")
    code = raw_code if isinstance(raw_code, str) and _CODE.fullmatch(raw_code) else "error"
    raw_details = parsed.get("details")
    details: dict[str, Any] = raw_details if isinstance(raw_details, dict) else {}
    raw_missing = details.get("missing")
    missing = raw_missing if isinstance(raw_missing, list) else []
    return f"{code} ({len(missing)} missing)" if missing else code
