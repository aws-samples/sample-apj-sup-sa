"""Bedrock runtime client wrapper."""

from __future__ import annotations

import asyncio
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, ParamValidationError

from gateway.core.config import Settings
from gateway.core.exceptions import (
    BedrockClientBugError,
    BedrockError,
    BedrockThrottlingError,
)

# Bedrock errors caused by request shape, auth, or policy. Re-raised as
# BedrockClientBugError so the gateway does NOT fall back to Anthropic 1P
# (the same payload would fail upstream too).
_CLIENT_BUG_ERROR_CODES = frozenset(
    {
        "ValidationException",
        "ValidationError",
        "AccessDeniedException",
        "ResourceNotFoundException",
        "IncompleteSignature",
        "InvalidClientTokenId",
        "InvalidSignatureException",
        "RequestExpired",
        "NotAuthorized",
        "FTUFormNotFilled",
        "MPAgreementBeingCreated",
    }
)

_THROTTLE_ERROR_CODES = frozenset({"ThrottlingException", "TooManyRequestsException"})


class BedrockClient:
    """Thin async wrapper over boto3 Bedrock runtime methods."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # connect_timeout is short so a blocked/unreachable Bedrock endpoint
        # fails fast into 1P fallback instead of hanging on the default 60s x
        # retries (~4min observed). read_timeout is the max silence BETWEEN
        # stream chunks, not total response time; it must stay high enough to
        # cover long thinking/tool gaps (e.g. Opus high/xhigh) or ConverseStream
        # is severed mid-response. 600s aligns with the ALB idle_timeout.
        self._config = Config(
            retries={"max_attempts": 1, "mode": "standard"},
            connect_timeout=20,
            read_timeout=600,
        )
        self._clients: dict[str, Any] = {}

    async def converse(
        self,
        request: dict[str, Any],
        resolved_model: Any | None = None,
    ) -> dict[str, Any]:
        return await self._call("converse", request, resolved_model)

    async def converse_stream(
        self,
        request: dict[str, Any],
        resolved_model: Any | None = None,
    ) -> dict[str, Any]:
        return await self._call("converse_stream", request, resolved_model)

    def _get_client(self, resolved_model: Any | None = None) -> Any:
        region_name = getattr(resolved_model, "bedrock_region", None) or self._settings.aws_region
        client = self._clients.get(region_name)
        if client is None:
            client = boto3.client(
                self._settings.bedrock_runtime_service,
                region_name=region_name,
                config=self._config,
            )
            self._clients[region_name] = client
        return client

    async def _call(
        self,
        method: str,
        request: dict[str, Any],
        resolved_model: Any | None = None,
    ) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        client = self._get_client(resolved_model)
        try:
            return await loop.run_in_executor(
                None, lambda: getattr(client, method)(**request)
            )
        except ParamValidationError as exc:
            raise BedrockClientBugError(str(exc)) from exc
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code in _THROTTLE_ERROR_CODES:
                raise BedrockThrottlingError(str(exc)) from exc
            if error_code in _CLIENT_BUG_ERROR_CODES:
                raise BedrockClientBugError(str(exc)) from exc
            raise BedrockError(str(exc)) from exc
        except BotoCoreError as exc:
            # Connection/DNS/timeout failures never reached Bedrock, so the
            # service may still be healthy elsewhere: treat as fallback-eligible.
            raise BedrockError(str(exc)) from exc
