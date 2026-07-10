"""Tests for Bedrock -> Anthropic 1P fallback in GatewayService."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from gateway.domains.runtime.services import GatewayService
from gateway.domains.runtime.types import MessageRequest, UsageInfo
from shared.exceptions import (
    AnthropicError,
    BedrockClientBugError,
    BedrockError,
    BedrockThrottlingError,
)


def _build_request() -> MessageRequest:
    return MessageRequest(
        model="claude-sonnet-4-6",
        max_tokens=128,
        messages=[{"role": "user", "content": "Hello"}],
    )


def _stub_metrics():
    return SimpleNamespace(
        emit_token_usage=Mock(),
        emit_cost_usage=Mock(),
        emit_request_duration=Mock(),
        emit_request_count=Mock(),
        emit_throttle=Mock(),
        emit_policy_block=Mock(),
        emit_active_request_start=Mock(),
        emit_active_request_end=Mock(),
        emit_budget_utilization=Mock(),
        emit_ttfb=Mock(),
    )


def _build_resolved_model(anthropic_id: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        id="model-id",
        bedrock_model_id="anthropic.claude-sonnet-4-6",
        bedrock_region="ap-northeast-2",
        anthropic_model_id=anthropic_id,
    )


# Circuit-breaker key the service derives for the model built above:
# "{bedrock_region}|{bedrock_model_id}".
_BREAKER_KEY = "ap-northeast-2|anthropic.claude-sonnet-4-6"


def _evaluate_with_resolved(resolved):
    async def _evaluate(context):
        context.user = SimpleNamespace(id="user-123")
        context.virtual_key = SimpleNamespace(id="vk-123")
        context.resolved_model = resolved

    return _evaluate


def _anthropic_response() -> dict:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-4-5-20250929",
        "content": [{"type": "text", "text": "ok"}],
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": 11,
            "output_tokens": 22,
            "cache_read_input_tokens": 3,
            "cache_creation_input_tokens": 4,
        },
    }


def _make_service(
    *,
    bedrock_side_effect,
    anthropic_client,
    resolved_model,
    response_converter=None,
    circuit_breaker=None,
    bedrock_client=None,
):
    policy_chain = SimpleNamespace(evaluate=AsyncMock())
    policy_chain.evaluate.side_effect = _evaluate_with_resolved(resolved_model)
    if response_converter is None:
        response_converter = SimpleNamespace(
            convert_response=Mock(return_value=SimpleNamespace()),
            extract_usage=Mock(return_value=UsageInfo()),
        )
    usage_service = SimpleNamespace(
        record_success=AsyncMock(),
        record_blocked_request=AsyncMock(),
        record_error=AsyncMock(),
    )
    if bedrock_client is None:
        bedrock_client = SimpleNamespace(
            converse=AsyncMock(side_effect=bedrock_side_effect)
        )
    return GatewayService(
        policy_chain=policy_chain,
        request_converter=SimpleNamespace(convert_request=Mock(return_value={})),
        response_converter=response_converter,
        bedrock_client=bedrock_client,
        stream_processor=SimpleNamespace(stream_response=Mock()),
        usage_service=usage_service,
        session=SimpleNamespace(commit=AsyncMock()),
        model_catalog_repo=SimpleNamespace(),
        metrics=_stub_metrics(),
        anthropic_client=anthropic_client,
        circuit_breaker=circuit_breaker,
    ), usage_service


@pytest.mark.asyncio
async def test_bedrock_failure_falls_back_to_anthropic_when_configured() -> None:
    anthropic_client = SimpleNamespace(messages=AsyncMock(return_value=_anthropic_response()))
    service, usage_service = _make_service(
        bedrock_side_effect=BedrockError("bedrock down"),
        anthropic_client=anthropic_client,
        resolved_model=_build_resolved_model("claude-sonnet-4-5-20250929"),
    )

    response = await service.process_message(_build_request(), "sk-test", "req-fallback")

    anthropic_client.messages.assert_awaited_once()
    body, model_id = anthropic_client.messages.await_args.args
    assert model_id == "claude-sonnet-4-5-20250929"
    assert body["model"] == "claude-sonnet-4-6"
    assert response.id == "msg_test"
    # 1P fallback usage is intentionally not recorded: cost is tracked on the
    # Anthropic console, and the gateway only meters Bedrock spend.
    usage_service.record_success.assert_not_awaited()


@pytest.mark.asyncio
async def test_fallback_lifts_inline_system_messages_to_top_level() -> None:
    """1P fallback payload must move system-role messages to top-level system.

    Mirrors the Bedrock converter normalization so the fallback shape is valid
    for the Anthropic Messages API (which rejects system roles inside messages).
    """
    anthropic_client = SimpleNamespace(messages=AsyncMock(return_value=_anthropic_response()))
    service, _ = _make_service(
        bedrock_side_effect=BedrockError("bedrock down"),
        anthropic_client=anthropic_client,
        resolved_model=_build_resolved_model("claude-sonnet-4-5-20250929"),
    )
    request = MessageRequest(
        model="claude-sonnet-4-6",
        max_tokens=128,
        system="base",
        messages=[
            {"role": "user", "content": "Hi"},
            {"role": "system", "content": "skill context"},
        ],
    )

    await service.process_message(request, "sk-test", "req-system-lift")

    body, _ = anthropic_client.messages.await_args.args
    assert all(m["role"] != "system" for m in body["messages"])
    assert {"type": "text", "text": "base"} in body["system"]
    assert {"type": "text", "text": "skill context"} in body["system"]


@pytest.mark.asyncio
async def test_bedrock_throttling_falls_back_to_anthropic() -> None:
    anthropic_client = SimpleNamespace(messages=AsyncMock(return_value=_anthropic_response()))
    service, _ = _make_service(
        bedrock_side_effect=BedrockThrottlingError("rate limited"),
        anthropic_client=anthropic_client,
        resolved_model=_build_resolved_model("claude-sonnet-4-5-20250929"),
    )

    response = await service.process_message(_build_request(), "sk-test", "req-throttle")

    anthropic_client.messages.assert_awaited_once()
    assert response.id == "msg_test"


@pytest.mark.asyncio
async def test_no_fallback_when_anthropic_model_id_missing() -> None:
    anthropic_client = SimpleNamespace(messages=AsyncMock())
    service, _ = _make_service(
        bedrock_side_effect=BedrockError("bedrock down"),
        anthropic_client=anthropic_client,
        resolved_model=_build_resolved_model(None),
    )

    with pytest.raises(BedrockError):
        await service.process_message(_build_request(), "sk-test", "req-no-fallback")
    anthropic_client.messages.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_fallback_on_bedrock_client_bug_error() -> None:
    anthropic_client = SimpleNamespace(messages=AsyncMock())
    service, _ = _make_service(
        bedrock_side_effect=BedrockClientBugError("ValidationException: bad shape"),
        anthropic_client=anthropic_client,
        resolved_model=_build_resolved_model("claude-sonnet-4-5-20250929"),
    )

    with pytest.raises(BedrockClientBugError):
        await service.process_message(_build_request(), "sk-test", "req-bug")
    anthropic_client.messages.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_fallback_when_anthropic_client_unconfigured() -> None:
    service, _ = _make_service(
        bedrock_side_effect=BedrockError("bedrock down"),
        anthropic_client=None,
        resolved_model=_build_resolved_model("claude-sonnet-4-5-20250929"),
    )

    with pytest.raises(BedrockError):
        await service.process_message(_build_request(), "sk-test", "req-no-client")


@pytest.mark.asyncio
async def test_anthropic_failure_during_fallback_propagates() -> None:
    anthropic_client = SimpleNamespace(
        messages=AsyncMock(side_effect=AnthropicError("1p down"))
    )
    service, usage_service = _make_service(
        bedrock_side_effect=BedrockError("bedrock down"),
        anthropic_client=anthropic_client,
        resolved_model=_build_resolved_model("claude-sonnet-4-5-20250929"),
    )

    with pytest.raises(AnthropicError):
        await service.process_message(_build_request(), "sk-test", "req-1p-down")
    usage_service.record_blocked_request.assert_awaited_once()


@pytest.mark.asyncio
async def test_bedrock_success_does_not_call_anthropic() -> None:
    anthropic_client = SimpleNamespace(messages=AsyncMock())
    response_converter = SimpleNamespace(
        convert_response=Mock(return_value=SimpleNamespace(id="from-bedrock")),
        extract_usage=Mock(return_value=UsageInfo(input_tokens=1, output_tokens=2)),
    )
    policy_chain = SimpleNamespace(evaluate=AsyncMock())
    policy_chain.evaluate.side_effect = _evaluate_with_resolved(
        _build_resolved_model("claude-sonnet-4-5-20250929")
    )
    service = GatewayService(
        policy_chain=policy_chain,
        request_converter=SimpleNamespace(convert_request=Mock(return_value={})),
        response_converter=response_converter,
        bedrock_client=SimpleNamespace(
            converse=AsyncMock(return_value={"output": {}, "usage": {}})
        ),
        stream_processor=SimpleNamespace(stream_response=Mock()),
        usage_service=SimpleNamespace(
            record_success=AsyncMock(),
            record_blocked_request=AsyncMock(),
            record_error=AsyncMock(),
        ),
        session=SimpleNamespace(commit=AsyncMock()),
        model_catalog_repo=SimpleNamespace(),
        metrics=_stub_metrics(),
        anthropic_client=anthropic_client,
    )

    response = await service.process_message(_build_request(), "sk-test", "req-ok")

    anthropic_client.messages.assert_not_awaited()
    assert response.id == "from-bedrock"


@pytest.mark.asyncio
async def test_breaker_trip_skips_bedrock_on_subsequent_request() -> None:
    from gateway.domains.runtime.circuit_breaker import CircuitBreaker

    breaker = CircuitBreaker(open_seconds=300.0)
    anthropic_client = SimpleNamespace(messages=AsyncMock(return_value=_anthropic_response()))
    bedrock_client = SimpleNamespace(
        converse=AsyncMock(side_effect=BedrockError("bedrock down"))
    )
    service, _ = _make_service(
        bedrock_side_effect=None,
        anthropic_client=anthropic_client,
        resolved_model=_build_resolved_model("claude-sonnet-4-5-20250929"),
        circuit_breaker=breaker,
        bedrock_client=bedrock_client,
    )

    await service.process_message(_build_request(), "sk-test", "req-1")
    await service.process_message(_build_request(), "sk-test", "req-2")
    await service.process_message(_build_request(), "sk-test", "req-3")

    assert bedrock_client.converse.await_count == 1
    assert anthropic_client.messages.await_count == 3


@pytest.mark.asyncio
async def test_breaker_does_not_trip_on_client_bug_error() -> None:
    from gateway.domains.runtime.circuit_breaker import BreakerState, CircuitBreaker

    breaker = CircuitBreaker(open_seconds=300.0)
    anthropic_client = SimpleNamespace(messages=AsyncMock())
    bedrock_client = SimpleNamespace(
        converse=AsyncMock(side_effect=BedrockClientBugError("ValidationException"))
    )
    service, _ = _make_service(
        bedrock_side_effect=None,
        anthropic_client=anthropic_client,
        resolved_model=_build_resolved_model("claude-sonnet-4-5-20250929"),
        circuit_breaker=breaker,
        bedrock_client=bedrock_client,
    )

    with pytest.raises(BedrockClientBugError):
        await service.process_message(_build_request(), "sk-test", "req-bug")

    assert breaker.state(_BREAKER_KEY) == BreakerState.CLOSED
    anthropic_client.messages.assert_not_awaited()


@pytest.mark.asyncio
async def test_breaker_recovers_to_closed_on_bedrock_success() -> None:
    from gateway.domains.runtime.circuit_breaker import BreakerState, CircuitBreaker

    breaker = CircuitBreaker(open_seconds=300.0)
    monotonic_values = iter([0.0, 400.0])
    breaker._now = lambda: next(monotonic_values)  # type: ignore[method-assign]
    breaker.record_failure(_BREAKER_KEY)

    anthropic_client = SimpleNamespace(messages=AsyncMock(return_value=_anthropic_response()))
    response_converter = SimpleNamespace(
        convert_response=Mock(return_value=SimpleNamespace(id="from-bedrock")),
        extract_usage=Mock(return_value=UsageInfo()),
    )
    bedrock_client = SimpleNamespace(
        converse=AsyncMock(return_value={"output": {}, "usage": {}})
    )
    service, _ = _make_service(
        bedrock_side_effect=None,
        anthropic_client=anthropic_client,
        resolved_model=_build_resolved_model("claude-sonnet-4-5-20250929"),
        response_converter=response_converter,
        circuit_breaker=breaker,
        bedrock_client=bedrock_client,
    )

    response = await service.process_message(_build_request(), "sk-test", "req-probe")

    assert response.id == "from-bedrock"
    assert breaker.state(_BREAKER_KEY) == BreakerState.CLOSED
    bedrock_client.converse.assert_awaited_once()
    anthropic_client.messages.assert_not_awaited()


@pytest.mark.asyncio
async def test_breaker_open_for_one_model_does_not_divert_another() -> None:
    """An open breaker for model A must not route model B in the same region.

    Breaker keys are (region, model), so an Opus outage in ap-northeast-2 leaves
    Sonnet requests in the same region hitting Bedrock normally.
    """
    from gateway.domains.runtime.circuit_breaker import CircuitBreaker

    breaker = CircuitBreaker(open_seconds=300.0)
    # Open the breaker for a different model in the same region.
    breaker.record_failure("ap-northeast-2|anthropic.claude-opus-4-6")

    anthropic_client = SimpleNamespace(messages=AsyncMock(return_value=_anthropic_response()))
    response_converter = SimpleNamespace(
        convert_response=Mock(return_value=SimpleNamespace(id="from-bedrock")),
        extract_usage=Mock(return_value=UsageInfo()),
    )
    bedrock_client = SimpleNamespace(
        converse=AsyncMock(return_value={"output": {}, "usage": {}})
    )
    # Resolved model is Sonnet (the model built by _build_resolved_model).
    service, _ = _make_service(
        bedrock_side_effect=None,
        anthropic_client=anthropic_client,
        resolved_model=_build_resolved_model("claude-sonnet-4-5-20250929"),
        response_converter=response_converter,
        circuit_breaker=breaker,
        bedrock_client=bedrock_client,
    )

    response = await service.process_message(_build_request(), "sk-test", "req-other-model")

    assert response.id == "from-bedrock"
    bedrock_client.converse.assert_awaited_once()
    anthropic_client.messages.assert_not_awaited()


# --- Streaming fallback (stream-not-yet-started window) ---


def _sse_stream_chunks() -> list[bytes]:
    return [
        b'event: message_start\ndata: {"type":"message_start","message":'
        b'{"id":"msg_1p","usage":{"input_tokens":7,"output_tokens":0}}}\n\n',
        b'event: content_block_delta\ndata: {"type":"content_block_delta",'
        b'"index":0,"delta":{"type":"text_delta","text":"ok"}}\n\n',
        b'event: message_delta\ndata: {"type":"message_delta","delta":'
        b'{"stop_reason":"end_turn"},"usage":{"output_tokens":5}}\n\n',
        b'event: message_stop\ndata: {"type":"message_stop"}\n\n',
    ]


async def _async_chunks(chunks: list[bytes]):
    for chunk in chunks:
        yield chunk


def _make_stream_service(
    *,
    converse_stream_side_effect,
    anthropic_client,
    resolved_model,
    circuit_breaker=None,
    converse_stream_return=None,
):
    policy_chain = SimpleNamespace(evaluate=AsyncMock())
    policy_chain.evaluate.side_effect = _evaluate_with_resolved(resolved_model)
    usage_service = SimpleNamespace(
        record_success=AsyncMock(),
        record_blocked_request=AsyncMock(),
        record_error=AsyncMock(),
    )
    converse_stream = AsyncMock(
        side_effect=converse_stream_side_effect,
        return_value=converse_stream_return,
    )
    bedrock_client = SimpleNamespace(converse_stream=converse_stream)
    stream_processor = SimpleNamespace(stream_response=Mock(return_value="bedrock-gen"))
    service = GatewayService(
        policy_chain=policy_chain,
        request_converter=SimpleNamespace(convert_request=Mock(return_value={})),
        response_converter=SimpleNamespace(
            convert_response=Mock(), extract_usage=Mock(return_value=UsageInfo())
        ),
        bedrock_client=bedrock_client,
        stream_processor=stream_processor,
        usage_service=usage_service,
        session=SimpleNamespace(commit=AsyncMock()),
        model_catalog_repo=SimpleNamespace(),
        metrics=_stub_metrics(),
        anthropic_client=anthropic_client,
        circuit_breaker=circuit_breaker,
    )
    return service, usage_service, bedrock_client, stream_processor


@pytest.mark.asyncio
async def test_stream_bedrock_failure_falls_back_to_anthropic_stream() -> None:
    anthropic_client = SimpleNamespace(
        messages_stream=AsyncMock(return_value=_async_chunks(_sse_stream_chunks()))
    )
    service, usage_service, _, _ = _make_stream_service(
        converse_stream_side_effect=BedrockError("bedrock stream down"),
        anthropic_client=anthropic_client,
        resolved_model=_build_resolved_model("claude-sonnet-4-5-20250929"),
    )

    generator = await service.process_message_stream(_build_request(), "sk-test", "req-s1")
    received = b"".join([chunk async for chunk in generator])

    anthropic_client.messages_stream.assert_awaited_once()
    assert b"message_start" in received
    assert b"message_stop" in received
    # 1P fallback usage is intentionally not recorded; the SSE bytes pass through
    # to the client but no usage/cost is metered for the 1P leg.
    usage_service.record_success.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_no_fallback_when_anthropic_model_id_missing() -> None:
    anthropic_client = SimpleNamespace(messages_stream=AsyncMock())
    service, _, _, _ = _make_stream_service(
        converse_stream_side_effect=BedrockError("bedrock stream down"),
        anthropic_client=anthropic_client,
        resolved_model=_build_resolved_model(None),
    )

    with pytest.raises(BedrockError):
        await service.process_message_stream(_build_request(), "sk-test", "req-s2")

    anthropic_client.messages_stream.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_breaker_open_skips_bedrock_stream() -> None:
    from gateway.domains.runtime.circuit_breaker import CircuitBreaker

    breaker = CircuitBreaker(open_seconds=300.0)
    breaker.record_failure(_BREAKER_KEY)
    anthropic_client = SimpleNamespace(
        messages_stream=AsyncMock(return_value=_async_chunks(_sse_stream_chunks()))
    )
    service, _, bedrock_client, _ = _make_stream_service(
        converse_stream_side_effect=None,
        converse_stream_return={"stream": iter([])},
        anthropic_client=anthropic_client,
        resolved_model=_build_resolved_model("claude-sonnet-4-5-20250929"),
        circuit_breaker=breaker,
    )

    generator = await service.process_message_stream(_build_request(), "sk-test", "req-s3")
    _ = [chunk async for chunk in generator]

    bedrock_client.converse_stream.assert_not_awaited()
    anthropic_client.messages_stream.assert_awaited_once()


@pytest.mark.asyncio
async def test_stream_bedrock_success_does_not_call_anthropic() -> None:
    anthropic_client = SimpleNamespace(messages_stream=AsyncMock())
    service, _, bedrock_client, stream_processor = _make_stream_service(
        converse_stream_side_effect=None,
        converse_stream_return={"stream": iter([])},
        anthropic_client=anthropic_client,
        resolved_model=_build_resolved_model("claude-sonnet-4-5-20250929"),
    )

    result = await service.process_message_stream(_build_request(), "sk-test", "req-s4")

    assert result == "bedrock-gen"
    bedrock_client.converse_stream.assert_awaited_once()
    anthropic_client.messages_stream.assert_not_awaited()
    stream_processor.stream_response.assert_called_once()
