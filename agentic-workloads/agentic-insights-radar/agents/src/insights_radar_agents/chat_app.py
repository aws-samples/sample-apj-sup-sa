# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""AgentCore entry point of the chat runtime (``python -m insights_radar_agents.chat_app``)."""

from __future__ import annotations

import json
import logging
import random
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

import boto3
import httpx
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
from bedrock_agentcore.memory.integrations.strands.session_manager import AgentCoreMemorySessionManager
from bedrock_agentcore.runtime import BedrockAgentCoreApp

from insights_radar_agents.chat_runtime import ChatRunner
from insights_radar_agents.models import ModelFactory
from insights_radar_agents.settings import RuntimeSettings, configure_logging
from insights_radar_agents.tool_api import TIMEOUT, SigV4HttpxAuth, credentials_of

configure_logging()
log = logging.getLogger("air.chat")


SETTLE_ATTEMPTS = 4
# A reservation is not idempotent once it has run, so it is retried only when it never reached the
# backend: a throttled call (429) or a connection that was never made.
RESERVE_ATTEMPTS = 3
RETRY_BASE_SECONDS = 0.5


class ChatApp:
    def __init__(
        self,
        settings: RuntimeSettings,
        session: Any = None,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self.settings = settings
        self._sleep = sleep
        self._jitter = jitter
        self._session = session or boto3.Session(region_name=settings.region)
        # The chat-usage route is its own IAM-authenticated function URL (signed for "lambda"); older
        # stacks without one reached it on the HTTP API.
        self._usage_url = settings.chat_usage_url or settings.api_url
        service = "lambda" if settings.chat_usage_url else "execute-api"
        self._usage_client = http or httpx.Client(
            timeout=TIMEOUT, auth=SigV4HttpxAuth(credentials_of(self._session), settings.region, service)
        )
        self.runner = ChatRunner(
            api_url=settings.api_url,
            models=ModelFactory(settings.region, settings.guardrail),
            model_key=settings.chat_model_key,
            ledger=self,
            session_manager=self.memory if settings.memory_id else None,
        )
        self.app = BedrockAgentCoreApp()
        self.app.entrypoint(self.invoke)

    def reserve(self, subject: str, turn_id: str, amount: int) -> int:
        body = self._post(
            {"subject": subject, "turn_id": turn_id, "reserve_tokens": amount}, RESERVE_ATTEMPTS, idempotent=False
        )
        return int(body.get("reserved", 0))

    def settle(self, subject: str, turn_id: str, usage: dict[str, int]) -> None:
        # Settling is idempotent on the backend (keyed by turn_id), so retrying a lost reply is safe.
        self._post({"subject": subject, "turn_id": turn_id, **usage}, SETTLE_ATTEMPTS, idempotent=True)

    def _post(self, body: dict[str, Any], attempts: int, idempotent: bool) -> dict[str, Any]:
        for attempt in range(1, attempts + 1):
            try:
                res = self._usage_client.post(
                    f"{self._usage_url}/agent/chat/usage",
                    content=json.dumps(body).encode("utf-8"),
                    headers={"content-type": "application/json"},
                )
                retry = res.status_code == 429 or (idempotent and res.status_code >= 500)
                if not retry or attempt == attempts:
                    res.raise_for_status()
                    return dict(res.json())
            except httpx.TransportError as err:
                # A request that was sent may have run: only an idempotent call is retried after it.
                never_sent = isinstance(err, httpx.ConnectError | httpx.ConnectTimeout)
                if attempt == attempts or not (idempotent or never_sent):
                    raise
            # Exponential backoff with jitter, so turns throttled together do not retry together.
            self._sleep(RETRY_BASE_SECONDS * 2 ** (attempt - 1) * (0.5 + self._jitter()))
        raise AssertionError("unreachable")  # pragma: no cover

    def memory(self, subject: str, session_id: str) -> Any:
        config = AgentCoreMemoryConfig(
            memory_id=str(self.settings.memory_id),
            session_id=session_id,
            actor_id=subject,
            filter_restored_tool_context=True,
        )
        return AgentCoreMemorySessionManager(config, region_name=self.settings.region)

    async def invoke(self, payload: dict[str, Any], context: Any = None) -> AsyncIterator[dict[str, Any]]:
        headers = dict(getattr(context, "request_headers", None) or {})
        session_id = getattr(context, "session_id", None)
        async for frame in self.runner.stream(payload if isinstance(payload, dict) else {}, headers, session_id):
            yield frame


def main() -> None:  # pragma: no cover - process entry point
    ChatApp(RuntimeSettings.from_env()).app.run()


if __name__ == "__main__":  # pragma: no cover
    main()
