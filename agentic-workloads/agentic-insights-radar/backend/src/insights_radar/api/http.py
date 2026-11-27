# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Minimal typed HTTP layer for API Gateway HTTP API (payload v2) events.

Every response carries ``Cache-Control: no-store``, ``X-Content-Type-Options: nosniff``,
``Strict-Transport-Security`` and a restrictive CSP for JSON (the internal radar found
``Cache-Control`` missing on its API and 404s built by hand without headers; both fixed here by
construction). Bad JSON is a 400, never a 500.
"""

from __future__ import annotations

import base64
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from insights_radar.core.errors import BadRequestError, DomainError, NotFoundError, PayloadTooLargeError

MAX_BODY_BYTES = 6 * 1024 * 1024
# The deploy options mcpIssuer and mcpAudience adds the /api routes again under this prefix, behind API Gateway's
# IAM authorizer instead of the Cognito one (an HTTP API route has one authorizer).
IAM_PREFIX = "/iam"
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
}


# A lone UTF-16 surrogate is valid JSON ("\ud800") but cannot be encoded as UTF-8, so it reached
# every DynamoDB and S3 write as an UnicodeEncodeError: a 500 on radar save, import, rollback,
# feedback, product create and draft_save. Rejected once, here, for every route and agent tool.
_SURROGATE = re.compile("[\ud800-\udfff]")


def _reject_surrogates(data: Any) -> None:
    stack: list[Any] = [data]
    while stack:
        value = stack.pop()
        if isinstance(value, str):
            if _SURROGATE.search(value):
                raise BadRequestError("request body contains text that is not valid Unicode", {"code": "invalid_text"})
        elif isinstance(value, dict):
            stack.extend(value.keys())
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)


@dataclass(frozen=True, slots=True)
class Request:
    method: str
    path: str
    headers: dict[str, str]
    query: dict[str, str]
    body: bytes
    claims: dict[str, Any]
    path_params: dict[str, str] = field(default_factory=dict)
    source_arn: str | None = None
    # True for a request that came in under IAM_PREFIX: API Gateway checked a SigV4 signature, not a
    # token, so the caller is source_arn and there are no claims.
    via_iam: bool = False

    @classmethod
    def from_event(cls, event: dict[str, Any]) -> Request:
        ctx = event.get("requestContext", {}) or {}
        http = ctx.get("http", {}) or {}
        raw = event.get("body") or ""
        body = base64.b64decode(raw) if event.get("isBase64Encoded") else str(raw).encode("utf-8")
        if len(body) > MAX_BODY_BYTES:
            raise PayloadTooLargeError("request body is too large")
        auth = ctx.get("authorizer", {}) or {}
        claims = ((auth.get("jwt") or {}).get("claims")) or {}
        iam = auth.get("iam") or {}
        path = str(event.get("rawPath") or http.get("path") or "/")
        # The prefix alone proves nothing: only a route behind the IAM authorizer reports an IAM caller,
        # and app.py still checks that caller is the stack's MCP role before anything runs.
        via_iam = path.startswith(IAM_PREFIX + "/")
        if via_iam:
            path = path.removeprefix(IAM_PREFIX)
            claims = {}
        return cls(
            method=str(http.get("method") or event.get("httpMethod") or "GET").upper(),
            path=path,
            headers={str(k).lower(): str(v) for k, v in (event.get("headers") or {}).items()},
            query={str(k): str(v) for k, v in (event.get("queryStringParameters") or {}).items()},
            body=body,
            claims=dict(claims),
            source_arn=str(iam.get("userArn")) if iam.get("userArn") else None,
            via_iam=via_iam,
        )

    def json(self) -> dict[str, Any]:
        if not self.body:
            return {}
        try:
            data = json.loads(self.body)
        except (ValueError, RecursionError) as err:
            # JSONDecodeError and UnicodeDecodeError are ValueErrors; so is an integer past Python's
            # digit limit. A deeply nested body raises RecursionError. All are the client's mistake.
            raise BadRequestError("request body is not valid JSON", {"code": "invalid_json"}) from err
        if not isinstance(data, dict):
            raise BadRequestError("request body must be a JSON object")
        _reject_surrogates(data)
        return data

    def int_query(self, name: str, default: int, low: int, high: int) -> int:
        raw = self.query.get(name)
        if raw is None or raw == "":
            return default
        # lstrip("-") let "--5" through to int() and a 500; past 4,300 digits int() itself raises.
        if not re.fullmatch(r"-?[0-9]{1,12}", raw):
            raise BadRequestError(f"{name} must be an integer")
        return max(low, min(high, int(raw)))


@dataclass(frozen=True, slots=True)
class Response:
    status: int
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)
    raw: str | None = None
    content_type: str = "application/json"

    def to_event(self, cors_origin: str | None = None) -> dict[str, Any]:
        headers = {**SECURITY_HEADERS, "Content-Type": self.content_type, **self.headers}
        if cors_origin:
            headers["Access-Control-Allow-Origin"] = cors_origin
            headers["Vary"] = "Origin"
        text = self.raw if self.raw is not None else ("" if self.body is None else json.dumps(self.body, default=str))
        return {"statusCode": self.status, "headers": headers, "body": text}


def ok(body: Any = None, status: int = 200) -> Response:
    return Response(status, body if body is not None else {"ok": True})


def error_response(err: DomainError) -> Response:
    headers: dict[str, str] = {}
    if err.code == "budget_exhausted":
        # A closed daily budget reopens at UTC midnight: "retry in one second" had a client honouring
        # it retry every second until then (B3-063).
        headers["Retry-After"] = str(DAY_SECONDS - int(time.time()) % DAY_SECONDS)
    elif not err.retryable:
        pass  # switched off by an administrator, or waiting on one: nothing to wait for, so no Retry-After at all
    elif err.status == 503:
        headers["Retry-After"] = str(err.retry_after_seconds)
    elif err.status == 429:
        headers["Retry-After"] = str(err.details.get("retry_after", 60))
    return Response(err.status, err.to_body(), headers)


DAY_SECONDS = 86_400
Handler = Callable[[Request], Response]
_PARAM = re.compile(r"\{([a-z_]+)\}")


@dataclass(frozen=True, slots=True)
class Route:
    method: str
    template: str
    pattern: re.Pattern[str]
    handler: Handler


class Router:
    def __init__(self) -> None:
        self._routes: list[Route] = []

    def add(self, method: str, template: str, handler: Handler) -> None:
        regex = (
            "^"
            + _PARAM.sub(
                lambda m: f"(?P<{m.group(1)}>[A-Za-z0-9._~:@-]+)",
                re.escape(template).replace(r"\{", "{").replace(r"\}", "}"),
            )
            + "$"
        )
        self._routes.append(Route(method.upper(), template, re.compile(regex), handler))

    def route(self, method: str, template: str) -> Callable[[Handler], Handler]:
        def deco(fn: Handler) -> Handler:
            self.add(method, template, fn)
            return fn

        return deco

    def resolve(self, req: Request) -> tuple[Handler, dict[str, str], str]:
        allowed: set[str] = set()
        for r in self._routes:
            m = r.pattern.match(req.path)
            if m:
                if r.method == req.method:
                    return r.handler, m.groupdict(), r.template
                allowed.add(r.method)
        if allowed:
            raise DomainErrorMethod(sorted(allowed))
        raise NotFoundError(f"no route for {req.method} {req.path}")

    @property
    def templates(self) -> list[tuple[str, str]]:
        return [(r.method, r.template) for r in self._routes]


class DomainErrorMethod(DomainError):
    status = 405
    code = "method_not_allowed"

    def __init__(self, allowed: list[str]) -> None:
        super().__init__("method not allowed", {"allowed": allowed})
