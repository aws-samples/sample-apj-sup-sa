"""Tests for AnthropicClient (1P fallback)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

from gateway.domains.runtime.anthropic_client import AnthropicClient
from shared.exceptions import AnthropicError, AnthropicThrottlingError


def _settings(secret_arn: str = "arn:aws:secretsmanager:test:123:secret:k") -> SimpleNamespace:
    return SimpleNamespace(
        anthropic_api_key_secret_arn=secret_arn,
        anthropic_api_base_url="https://api.anthropic.com",
        anthropic_api_version="2023-06-01",
        anthropic_request_timeout_seconds=10.0,
        aws_region="us-east-1",
    )


class _FakeSecretsManager:
    def __init__(self, secret_string: str) -> None:
        self._secret_string = secret_string
        self.calls: list[str] = []

    def get_secret_value(self, SecretId: str) -> dict:
        self.calls.append(SecretId)
        return {"SecretString": self._secret_string}


def _patch_boto3(secret_string: str) -> _FakeSecretsManager:
    fake = _FakeSecretsManager(secret_string)

    def _factory(*args, **kwargs):
        return fake

    return fake, _factory


def _install_mock_transport(client: AnthropicClient, handler) -> None:
    transport = httpx.MockTransport(handler)
    client._client = httpx.AsyncClient(
        base_url=client._settings.anthropic_api_base_url,
        timeout=client._settings.anthropic_request_timeout_seconds,
        transport=transport,
    )


@pytest.mark.asyncio
async def test_messages_posts_with_required_headers_and_body() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["url"] = str(request.url)
        captured["body"] = request.read()
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-5-20250929",
                "content": [{"type": "text", "text": "hi"}],
                "usage": {"input_tokens": 1, "output_tokens": 2},
            },
        )

    fake, factory = _patch_boto3("test-key-fixture")
    with patch("gateway.domains.runtime.anthropic_client.boto3.client", factory):
        client = AnthropicClient(_settings())
        _install_mock_transport(client, handler)
        result = await client.messages(
            {"model": "claude-sonnet-4-6", "max_tokens": 50, "messages": []},
            "claude-sonnet-4-5-20250929",
        )

    assert result["id"] == "msg_1"
    assert captured["headers"]["x-api-key"] == "test-key-fixture"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert captured["url"].endswith("/v1/messages")
    assert b'"model":"claude-sonnet-4-5-20250929"' in captured["body"]
    assert b'"stream":false' in captured["body"]
    assert fake.calls == ["arn:aws:secretsmanager:test:123:secret:k"]


@pytest.mark.asyncio
async def test_messages_raises_throttling_on_429() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limit")

    _, factory = _patch_boto3("test-key-fixture")
    with patch("gateway.domains.runtime.anthropic_client.boto3.client", factory):
        client = AnthropicClient(_settings())
        _install_mock_transport(client, handler)
        with pytest.raises(AnthropicThrottlingError):
            await client.messages({"messages": []}, "claude-sonnet-4-5-20250929")


@pytest.mark.asyncio
async def test_messages_raises_anthropic_error_on_5xx() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream down")

    _, factory = _patch_boto3("test-key-fixture")
    with patch("gateway.domains.runtime.anthropic_client.boto3.client", factory):
        client = AnthropicClient(_settings())
        _install_mock_transport(client, handler)
        with pytest.raises(AnthropicError):
            await client.messages({"messages": []}, "claude-sonnet-4-5-20250929")


@pytest.mark.asyncio
async def test_messages_stream_yields_sse_bytes_and_sets_stream_true() -> None:
    captured: dict = {}
    sse_body = (
        b'event: message_start\ndata: {"type":"message_start"}\n\n'
        b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read()
        return httpx.Response(200, content=sse_body)

    _, factory = _patch_boto3("test-key-fixture")
    with patch("gateway.domains.runtime.anthropic_client.boto3.client", factory):
        client = AnthropicClient(_settings())
        _install_mock_transport(client, handler)
        chunks = await client.messages_stream(
            {"model": "claude-sonnet-4-6", "max_tokens": 50, "messages": []},
            "claude-sonnet-4-5-20250929",
        )
        received = b"".join([chunk async for chunk in chunks])

    assert b"message_start" in received
    assert b"message_stop" in received
    assert b'"stream":true' in captured["body"]
    assert b'"model":"claude-sonnet-4-5-20250929"' in captured["body"]


@pytest.mark.asyncio
async def test_messages_stream_raises_before_first_chunk_on_429() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate limit")

    _, factory = _patch_boto3("test-key-fixture")
    with patch("gateway.domains.runtime.anthropic_client.boto3.client", factory):
        client = AnthropicClient(_settings())
        _install_mock_transport(client, handler)
        with pytest.raises(AnthropicThrottlingError):
            await client.messages_stream({"messages": []}, "claude-sonnet-4-5-20250929")


@pytest.mark.asyncio
async def test_messages_stream_raises_before_first_chunk_on_5xx() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream down")

    _, factory = _patch_boto3("test-key-fixture")
    with patch("gateway.domains.runtime.anthropic_client.boto3.client", factory):
        client = AnthropicClient(_settings())
        _install_mock_transport(client, handler)
        with pytest.raises(AnthropicError):
            await client.messages_stream({"messages": []}, "claude-sonnet-4-5-20250929")


def test_load_api_key_accepts_json_envelope() -> None:
    _, factory = _patch_boto3('{"api_key": "json-key-fixture"}')
    with patch("gateway.domains.runtime.anthropic_client.boto3.client", factory):
        client = AnthropicClient(_settings())
        assert client._load_api_key() == "json-key-fixture"


def test_load_api_key_raises_when_secret_arn_missing() -> None:
    client = AnthropicClient(_settings(secret_arn=""))
    with pytest.raises(AnthropicError):
        client._load_api_key()
