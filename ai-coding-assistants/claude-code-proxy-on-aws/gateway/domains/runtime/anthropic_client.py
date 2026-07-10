"""Anthropic 1P Messages API client used as Bedrock fallback."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

import boto3
import httpx

from gateway.core.config import Settings
from shared.exceptions import AnthropicError, AnthropicThrottlingError

logger = logging.getLogger(__name__)


class AnthropicClient:
    """Thin wrapper over Anthropic 1P Messages API for fallback."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._api_key: str | None = None
        self._client: httpx.AsyncClient | None = None

    def _load_api_key(self) -> str:
        if self._api_key is not None:
            return self._api_key
        secret_arn = self._settings.anthropic_api_key_secret_arn
        if not secret_arn:
            raise AnthropicError("ANTHROPIC_API_KEY_SECRET_ARN is not configured")
        sm = boto3.client("secretsmanager", region_name=self._settings.aws_region)
        resp = sm.get_secret_value(SecretId=secret_arn)
        secret_string = resp.get("SecretString")
        if not secret_string:
            raise AnthropicError("Anthropic API key secret has no SecretString")
        # Allow either a raw key or a JSON envelope {"api_key": "..."}.
        try:
            parsed = json.loads(secret_string)
            api_key = parsed["api_key"] if isinstance(parsed, dict) else secret_string
        except (json.JSONDecodeError, KeyError):
            api_key = secret_string
        if not isinstance(api_key, str) or not api_key.strip():
            raise AnthropicError("Anthropic API key secret is empty")
        self._api_key = api_key.strip()
        return self._api_key

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._settings.anthropic_api_base_url,
                timeout=self._settings.anthropic_request_timeout_seconds,
            )
        return self._client

    async def messages(
        self,
        body: dict[str, Any],
        anthropic_model_id: str,
    ) -> dict[str, Any]:
        """POST /v1/messages with the Anthropic-native body and return parsed JSON.

        The caller must pass an Anthropic-native request body (the original
        MessageRequest payload). The `model` field is set/overridden to the 1P
        model id, and `stream` is forced to False (streaming fallback is not
        supported in this stage).
        """
        api_key = self._load_api_key()
        client = await self._get_client()
        payload = dict(body)
        payload["model"] = anthropic_model_id
        payload["stream"] = False
        headers = {
            "x-api-key": api_key,
            "anthropic-version": self._settings.anthropic_api_version,
            "content-type": "application/json",
        }
        try:
            response = await client.post("/v1/messages", json=payload, headers=headers)
        except httpx.RequestError as exc:
            raise AnthropicError(f"Anthropic 1P request failed: {exc}") from exc

        if response.status_code == 429:
            raise AnthropicThrottlingError(
                f"Anthropic 1P throttled: {response.text[:500]}"
            )
        if response.status_code >= 400:
            raise AnthropicError(
                f"Anthropic 1P returned {response.status_code}: {response.text[:500]}"
            )
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise AnthropicError(
                f"Anthropic 1P returned non-JSON body: {response.text[:500]}"
            ) from exc

    async def messages_stream(
        self,
        body: dict[str, Any],
        anthropic_model_id: str,
    ) -> AsyncGenerator[bytes, None]:
        """Stream POST /v1/messages and yield the raw Anthropic SSE bytes.

        1P already emits Anthropic-native SSE, so the bytes pass through to the
        client unchanged. The upstream status is checked before the first chunk
        is yielded: a non-2xx response raises (no SSE has been sent yet, so the
        caller can still fall through), which keeps fallback confined to the
        stream-not-yet-started window.
        """
        api_key = self._load_api_key()
        client = await self._get_client()
        payload = dict(body)
        payload["model"] = anthropic_model_id
        payload["stream"] = True
        headers = {
            "x-api-key": api_key,
            "anthropic-version": self._settings.anthropic_api_version,
            "content-type": "application/json",
        }
        request = client.build_request(
            "POST", "/v1/messages", json=payload, headers=headers
        )
        try:
            response = await client.send(request, stream=True)
        except httpx.RequestError as exc:
            raise AnthropicError(f"Anthropic 1P stream request failed: {exc}") from exc

        if response.status_code >= 400:
            body_bytes = await response.aread()
            await response.aclose()
            text = body_bytes.decode(errors="ignore")[:500]
            if response.status_code == 429:
                raise AnthropicThrottlingError(f"Anthropic 1P throttled: {text}")
            raise AnthropicError(f"Anthropic 1P returned {response.status_code}: {text}")

        async def _iter() -> AsyncGenerator[bytes, None]:
            try:
                async for chunk in response.aiter_bytes():
                    yield chunk
            finally:
                await response.aclose()

        return _iter()
