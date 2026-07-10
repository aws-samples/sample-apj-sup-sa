"""Tests for the Bedrock runtime client wrapper."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from botocore.exceptions import (
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ParamValidationError,
    ReadTimeoutError,
)

from gateway.core.config import Settings
from gateway.domains.runtime.bedrock_client import BedrockClient
from shared.exceptions import (
    BedrockClientBugError,
    BedrockError,
    BedrockThrottlingError,
)


def _client_error(code: str, message: str = "boom") -> ClientError:
    return ClientError(
        error_response={"Error": {"Code": code, "Message": message}},
        operation_name="Converse",
    )


@pytest.mark.asyncio
async def test_converse_stream_classifies_param_validation_as_client_bug(monkeypatch) -> None:
    def fake_client(*_args, **_kwargs):
        return SimpleNamespace(
            converse_stream=lambda **_: (_ for _ in ()).throw(
                ParamValidationError(report="bad payload")
            )
        )

    monkeypatch.setattr("gateway.domains.runtime.bedrock_client.boto3.client", fake_client)
    client = BedrockClient(Settings(aws_region="ap-northeast-2"))

    with pytest.raises(BedrockClientBugError, match="bad payload"):
        await client.converse_stream({"messages": []})


@pytest.mark.parametrize(
    "error_code",
    [
        "ValidationException",
        "AccessDeniedException",
        "ResourceNotFoundException",
        "FTUFormNotFilled",
    ],
)
@pytest.mark.asyncio
async def test_converse_classifies_client_bug_codes(monkeypatch, error_code: str) -> None:
    def fake_client(*_args, **_kwargs):
        return SimpleNamespace(
            converse=lambda **_: (_ for _ in ()).throw(_client_error(error_code))
        )

    monkeypatch.setattr("gateway.domains.runtime.bedrock_client.boto3.client", fake_client)
    client = BedrockClient(Settings(aws_region="ap-northeast-2"))

    with pytest.raises(BedrockClientBugError):
        await client.converse({"messages": []})


@pytest.mark.parametrize(
    "error_code",
    ["ThrottlingException", "TooManyRequestsException"],
)
@pytest.mark.asyncio
async def test_converse_classifies_throttle_codes(monkeypatch, error_code: str) -> None:
    def fake_client(*_args, **_kwargs):
        return SimpleNamespace(
            converse=lambda **_: (_ for _ in ()).throw(_client_error(error_code))
        )

    monkeypatch.setattr("gateway.domains.runtime.bedrock_client.boto3.client", fake_client)
    client = BedrockClient(Settings(aws_region="ap-northeast-2"))

    with pytest.raises(BedrockThrottlingError):
        await client.converse({"messages": []})


@pytest.mark.parametrize(
    "error_code",
    ["ServiceUnavailableException", "InternalServerException", "ModelTimeoutException"],
)
@pytest.mark.asyncio
async def test_converse_classifies_provider_outage_codes(monkeypatch, error_code: str) -> None:
    def fake_client(*_args, **_kwargs):
        return SimpleNamespace(
            converse=lambda **_: (_ for _ in ()).throw(_client_error(error_code))
        )

    monkeypatch.setattr("gateway.domains.runtime.bedrock_client.boto3.client", fake_client)
    client = BedrockClient(Settings(aws_region="ap-northeast-2"))

    with pytest.raises(BedrockError) as exc_info:
        await client.converse({"messages": []})
    # Must NOT be classified as client-bug or throttle.
    assert not isinstance(exc_info.value, BedrockClientBugError)
    assert not isinstance(exc_info.value, BedrockThrottlingError)


@pytest.mark.parametrize(
    "exc",
    [
        EndpointConnectionError(endpoint_url="https://bedrock-runtime.ap-northeast-2.amazonaws.com"),
        ConnectTimeoutError(endpoint_url="https://bedrock-runtime.ap-northeast-2.amazonaws.com"),
        ReadTimeoutError(endpoint_url="https://bedrock-runtime.ap-northeast-2.amazonaws.com"),
    ],
)
@pytest.mark.asyncio
async def test_converse_classifies_connection_failures_as_fallback_eligible(
    monkeypatch, exc: Exception
) -> None:
    def fake_client(*_args, **_kwargs):
        return SimpleNamespace(converse=lambda **_: (_ for _ in ()).throw(exc))

    monkeypatch.setattr("gateway.domains.runtime.bedrock_client.boto3.client", fake_client)
    client = BedrockClient(Settings(aws_region="ap-northeast-2"))

    # Connection/DNS/timeout failures must surface as BedrockError so the
    # gateway falls back to Anthropic 1P, not as an unhandled botocore error.
    with pytest.raises(BedrockError) as exc_info:
        await client.converse({"messages": []})
    assert not isinstance(exc_info.value, BedrockClientBugError)
    assert not isinstance(exc_info.value, BedrockThrottlingError)


@pytest.mark.asyncio
async def test_converse_uses_resolved_model_bedrock_region(monkeypatch) -> None:
    captured_regions: list[str] = []

    def fake_client(*_args, **kwargs):
        captured_regions.append(kwargs["region_name"])
        return SimpleNamespace(converse=lambda **_: {"output": {}})

    monkeypatch.setattr("gateway.domains.runtime.bedrock_client.boto3.client", fake_client)
    client = BedrockClient(Settings(aws_region="ap-northeast-2"))

    await client.converse(
        {"messages": []},
        SimpleNamespace(bedrock_region="us-east-1"),
    )
    await client.converse(
        {"messages": []},
        SimpleNamespace(bedrock_region="us-east-1"),
    )

    assert captured_regions == ["us-east-1"]


@pytest.mark.asyncio
async def test_converse_falls_back_to_settings_region_when_model_region_missing(
    monkeypatch,
) -> None:
    captured_regions: list[str] = []

    def fake_client(*_args, **kwargs):
        captured_regions.append(kwargs["region_name"])
        return SimpleNamespace(converse=lambda **_: {"output": {}})

    monkeypatch.setattr("gateway.domains.runtime.bedrock_client.boto3.client", fake_client)
    client = BedrockClient(Settings(aws_region="ap-northeast-1"))

    await client.converse({"messages": []}, SimpleNamespace(bedrock_region=None))

    assert captured_regions == ["ap-northeast-1"]
