# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Client for the backend's ``/agent/tools/<name>`` endpoint.

Two credentials travel on every call, and the backend requires both:

* a SigV4 signature from the runtime's execution role. The tool gate is a Lambda function URL with
  ``AWS_IAM`` authorization, not an API Gateway route (the agent routes left the HTTP API), and
  Lambda refuses a caller from another account before any code runs;
* the run token (``X-MR-Run-Token``), which names the module and run and lists the tools the
  run may use. The model never sees it.

Credentials are read live on each request (not frozen once) so a long run survives a role
credential refresh.
"""

from __future__ import annotations

import json
import logging
import random
import time
from collections.abc import Callable, Generator
from typing import Any, cast

import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials

log = logging.getLogger(__name__)

RUN_HEADER = "x-mr-run-token"  # the header that carries the run token
TIMEOUT = httpx.Timeout(30.0, connect=5.0)
MAX_RESULT_CHARS = 100_000
MAX_ATTEMPTS = 4
BACKOFF_SECONDS = 0.5


class SigV4HttpxAuth(httpx.Auth):
    """Signs each request (SigV4 covers the body, so it cannot be signed in advance)."""

    requires_request_body = True

    def __init__(self, credentials: Credentials, region: str, service: str) -> None:
        self._credentials = credentials
        self._region = region
        self._service = service

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        aws = AWSRequest(
            method=request.method,
            url=str(request.url),
            data=request.content or b"",
            headers={k: v for k, v in request.headers.items() if k.lower() != "host"},
        )
        SigV4Auth(self._credentials, self._service, self._region).add_auth(aws)
        for key, value in aws.headers.items():
            request.headers[key] = value
        yield request


def credentials_of(session: Any) -> Credentials:
    """The session's refreshable credentials (botocore re-reads them on every signature)."""
    credentials = session.get_credentials()
    if credentials is None:
        raise RuntimeError("no AWS credentials for SigV4 signing")
    return cast(Credentials, credentials)


class ToolCallError(Exception):
    """A tool call the backend refused or failed. The message is safe to show the model."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


class ToolApi:
    def __init__(
        self,
        base_url: str,
        run_token: str,
        auth: httpx.Auth,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._sleep = sleep
        self._base = base_url.rstrip("/")
        self._token = run_token
        self._client = client or httpx.Client(timeout=TIMEOUT, auth=auth)
        self.calls: dict[str, int] = {}

    def call(self, tool: str, args: dict[str, Any]) -> Any:
        self.calls[tool] = self.calls.get(tool, 0) + 1
        res = self._post(tool, json.dumps(args).encode("utf-8"))
        try:
            body = res.json()
        except ValueError:
            body = {}
        if res.status_code >= 400:
            if not isinstance(body, dict):
                body = {}
            message = str(body.get("message", res.text[:300]))
            if body.get("details"):
                message += " " + json.dumps(body["details"], default=str)[:2000]
            raise ToolCallError(res.status_code, str(body.get("code", "error")), message)
        return body.get("result") if isinstance(body, dict) else None

    def _post(self, tool: str, body: bytes) -> httpx.Response:
        """One call, retried only when it never ran: Lambda answered 429 (the AgentTools function is at its
        reserved concurrency, and the sweep starts every due module together) or the connection was never
        made. Writes are not idempotent, so nothing that may have reached the backend is retried (P3-088)."""
        for attempt in range(MAX_ATTEMPTS):
            last = attempt + 1 == MAX_ATTEMPTS
            try:
                res = self._client.post(
                    f"{self._base}/agent/tools/{tool}",
                    content=body,
                    headers={"content-type": "application/json", RUN_HEADER: self._token},
                )
            except (httpx.ConnectError, httpx.ConnectTimeout):
                if last:
                    raise
            else:
                if res.status_code != 429 or last:
                    return res
            log.warning("tool %s was not run (attempt %d); retrying", tool, attempt + 1)
            self._sleep(BACKOFF_SECONDS * 2**attempt * (0.5 + random.random() / 2))  # noqa: S311  # nosec B311 - backoff jitter, not a secret
        raise AssertionError("unreachable")  # pragma: no cover - the last attempt returns or raises

    def close(self) -> None:
        self._client.close()
