"""Tests for BedrockToAnthropicConverter prompt-cache token handling."""

from __future__ import annotations

from gateway.domains.runtime.converter.response_converter import BedrockToAnthropicConverter
from gateway.domains.runtime.types import StreamState


def test_extract_usage_maps_cache_tokens() -> None:
    converter = BedrockToAnthropicConverter()
    response = {
        "usage": {
            "inputTokens": 100,
            "outputTokens": 50,
            "cacheReadInputTokens": 80,
            "cacheWriteInputTokens": 20,
        },
        "stopReason": "end_turn",
    }
    usage = converter.extract_usage(response)
    assert usage.cached_read_tokens == 80
    assert usage.cached_write_tokens == 20


def test_convert_response_includes_cache_usage_in_payload() -> None:
    converter = BedrockToAnthropicConverter()
    response = {
        "output": {"message": {"role": "assistant", "content": [{"text": "Hello!"}]}},
        "usage": {
            "inputTokens": 100,
            "outputTokens": 50,
            "cacheReadInputTokens": 80,
            "cacheWriteInputTokens": 20,
        },
        "stopReason": "end_turn",
        "ResponseMetadata": {"RequestId": "req-123"},
    }
    message = converter.convert_response(response, "zai.glm-5")
    assert message.model == "zai.glm-5"
    assert message.usage.cache_read_input_tokens == 80
    assert message.usage.cache_creation_input_tokens == 20


def test_convert_response_maps_reasoning_text_to_thinking_block() -> None:
    converter = BedrockToAnthropicConverter()
    response = {
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "reasoningContent": {
                            "reasoningText": {
                                "text": "step by step",
                                "signature": "sig_123",
                            }
                        }
                    }
                ],
            }
        },
        "stopReason": "end_turn",
        "ResponseMetadata": {"RequestId": "req-123"},
    }

    message = converter.convert_response(response, "zai.glm-5")

    assert message.content == [
        {
            "type": "thinking",
            "thinking": "step by step",
            "signature": "sig_123",
        }
    ]


def test_convert_response_maps_redacted_reasoning_to_redacted_thinking_block() -> None:
    converter = BedrockToAnthropicConverter()
    response = {
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "reasoningContent": {
                            "redactedContent": "encrypted-reasoning",
                        }
                    }
                ],
            }
        },
        "stopReason": "end_turn",
        "ResponseMetadata": {"RequestId": "req-123"},
    }

    message = converter.convert_response(response, "zai.glm-5")

    assert message.content == [
        {
            "type": "redacted_thinking",
            "data": "encrypted-reasoning",
        }
    ]


def test_extract_usage_defaults_cache_tokens_to_zero() -> None:
    converter = BedrockToAnthropicConverter()
    response = {
        "usage": {"inputTokens": 100, "outputTokens": 50},
        "stopReason": "end_turn",
    }
    usage = converter.extract_usage(response)
    assert usage.cached_read_tokens == 0
    assert usage.cached_write_tokens == 0


def test_extract_usage_treats_null_token_values_as_zero() -> None:
    converter = BedrockToAnthropicConverter()
    response = {
        "usage": {
            "inputTokens": None,
            "outputTokens": None,
            "cacheReadInputTokens": None,
            "cacheWriteInputTokens": None,
        },
        "stopReason": "end_turn",
    }

    usage = converter.extract_usage(response)

    assert usage.input_tokens == 0
    assert usage.output_tokens == 0
    assert usage.cached_read_tokens == 0
    assert usage.cached_write_tokens == 0


def test_extract_usage_handles_null_usage_and_metrics_objects() -> None:
    converter = BedrockToAnthropicConverter()

    usage = converter.extract_usage({"usage": None, "metrics": None})

    assert usage.input_tokens == 0
    assert usage.output_tokens == 0
    assert usage.cached_read_tokens == 0
    assert usage.cached_write_tokens == 0
    assert usage.latency_ms is None


def test_convert_stream_metadata_includes_cache_tokens() -> None:
    converter = BedrockToAnthropicConverter()
    state = StreamState()
    state.message_started = True
    state.saw_message_stop = True
    state.stop_reason = "end_turn"
    event = {
        "metadata": {
            "usage": {
                "inputTokens": 100,
                "outputTokens": 50,
                "cacheReadInputTokens": 80,
                "cacheWriteInputTokens": 20,
            },
            "metrics": {"latencyMs": 500},
        },
        "stopReason": None,
    }
    events = converter.convert_stream_event(event, state)
    delta_event = [e for e in events if e.event == "message_delta"][0]
    assert delta_event.data["usage"]["cache_read_input_tokens"] == 80
    assert delta_event.data["usage"]["cache_creation_input_tokens"] == 20
    assert delta_event.data["delta"]["stop_reason"] == "end_turn"
    assert [event.event for event in events] == ["message_delta", "message_stop"]


def test_convert_stream_tool_use_events() -> None:
    converter = BedrockToAnthropicConverter()
    state = StreamState()

    start_events = converter.convert_stream_event(
        {
            "contentBlockStart": {
                "contentBlockIndex": 1,
                "start": {
                    "toolUse": {
                        "toolUseId": "toolu_123",
                        "name": "get_weather",
                    }
                },
            }
        },
        state,
    )
    assert [event.event for event in start_events] == [
        "message_start",
        "content_block_start",
    ]
    assert start_events[1].data["content_block"] == {
        "type": "tool_use",
        "id": "toolu_123",
        "name": "get_weather",
        "input": {},
    }

    delta_events = converter.convert_stream_event(
        {
            "contentBlockDelta": {
                "contentBlockIndex": 1,
                "delta": {"toolUse": {"input": '{"city":"Seoul"}'}},
            }
        },
        state,
    )
    assert [event.event for event in delta_events] == ["content_block_delta"]
    assert delta_events[0].data["delta"] == {
        "type": "input_json_delta",
        "partial_json": '{"city":"Seoul"}',
    }

    stop_events = converter.convert_stream_event(
        {"contentBlockStop": {"contentBlockIndex": 1}},
        state,
    )
    assert [event.event for event in stop_events] == ["content_block_stop"]
    assert stop_events[0].data["index"] == 1


def test_convert_stream_skips_empty_tool_use_delta() -> None:
    converter = BedrockToAnthropicConverter()
    state = StreamState(message_started=True)

    delta_events = converter.convert_stream_event(
        {
            "contentBlockDelta": {
                "contentBlockIndex": 0,
                "delta": {"toolUse": {"input": ""}},
            }
        },
        state,
    )

    assert delta_events == []


def test_convert_stream_reasoning_events() -> None:
    converter = BedrockToAnthropicConverter()
    state = StreamState()

    start_events = converter.convert_stream_event(
        {
            "contentBlockStart": {
                "contentBlockIndex": 0,
                "start": {"reasoningContent": {"reasoningText": {"text": ""}}},
            }
        },
        state,
    )
    assert start_events[1].data["content_block"] == {
        "type": "thinking",
        "thinking": "",
    }

    delta_events = converter.convert_stream_event(
        {
            "contentBlockDelta": {
                "contentBlockIndex": 0,
                "delta": {"reasoningContent": {"text": "step by step"}},
            }
        },
        state,
    )
    assert delta_events[0].data["delta"] == {
        "type": "thinking_delta",
        "thinking": "step by step",
    }


def test_convert_stream_emits_message_delta_when_message_stop_arrives_after_metadata() -> None:
    converter = BedrockToAnthropicConverter()
    state = StreamState(message_started=True)

    metadata_events = converter.convert_stream_event(
        {
            "metadata": {
                "usage": {"inputTokens": 100, "outputTokens": 50},
                "metrics": {"latencyMs": 100},
            }
        },
        state,
    )
    assert metadata_events == []

    stop_events = converter.convert_stream_event(
        {"messageStop": {"stopReason": "tool_use"}},
        state,
    )
    assert [event.event for event in stop_events] == ["message_delta", "message_stop"]
    assert stop_events[0].data["delta"]["stop_reason"] == "tool_use"
    assert stop_events[0].data["usage"]["input_tokens"] == 100


def test_convert_stream_emits_message_delta_when_metadata_arrives_after_message_stop() -> None:
    converter = BedrockToAnthropicConverter()
    state = StreamState(message_started=True)

    stop_events = converter.convert_stream_event(
        {"messageStop": {"stopReason": "end_turn"}},
        state,
    )
    assert stop_events == []

    metadata_events = converter.convert_stream_event(
        {
            "metadata": {
                "usage": {"inputTokens": 120, "outputTokens": 60},
                "metrics": {"latencyMs": 90},
            }
        },
        state,
    )
    assert [event.event for event in metadata_events] == ["message_delta", "message_stop"]
    assert metadata_events[0].data["delta"]["stop_reason"] == "end_turn"
    assert metadata_events[0].data["usage"]["output_tokens"] == 60


def test_message_start_includes_usage_stub() -> None:
    """Regression: missing usage in message_start caused 'H.input_tokens' undefined error."""
    converter = BedrockToAnthropicConverter()
    state = StreamState(message_id="msg_test", model_name="claude-sonnet-4-20250514")
    event = {"contentBlockStart": {"contentBlockIndex": 0, "start": {}}}
    events = converter.convert_stream_event(event, state)
    msg_start = [e for e in events if e.event == "message_start"]
    assert len(msg_start) == 1
    message = msg_start[0].data["message"]
    assert message["id"] == "msg_test"
    assert message["type"] == "message"
    assert message["role"] == "assistant"
    assert message["model"] == "claude-sonnet-4-20250514"
    assert message["stop_reason"] is None
    assert message["stop_sequence"] is None
    usage = msg_start[0].data["message"]["usage"]
    assert "input_tokens" in usage
    assert "output_tokens" in usage


def test_convert_stream_normalizes_patch_events() -> None:
    converter = BedrockToAnthropicConverter()
    state = StreamState(message_id="msg_patch", model_name="claude-test")

    start_events = converter.convert_stream_event({"p": "/message", "role": "assistant"}, state)
    assert [event.event for event in start_events] == ["message_start"]

    delta_events = converter.convert_stream_event(
        {
            "p": "/message/content/0",
            "contentBlockIndex": 0,
            "delta": {"toolUse": {"input": '{"city":"Seoul"}'}},
        },
        state,
    )
    assert [event.event for event in delta_events] == ["content_block_start", "content_block_delta"]
    assert delta_events[1].data["delta"] == {
        "type": "input_json_delta",
        "partial_json": '{"city":"Seoul"}',
    }

    stop_events = converter.convert_stream_event(
        {"p": "/message/stop", "stopReason": "tool_use"},
        state,
    )
    assert stop_events == []


def test_message_stop_comes_after_message_delta() -> None:
    """Regression: message_stop before message_delta caused client to close stream early."""
    converter = BedrockToAnthropicConverter()
    state = StreamState()
    state.message_started = True

    # Bedrock sends messageStop first
    events1 = converter.convert_stream_event({"messageStop": {"stopReason": "end_turn"}}, state)
    # messageStop should NOT emit message_stop yet
    assert not any(e.event == "message_stop" for e in events1)
    assert state.saw_message_stop is True

    # Then metadata arrives
    events2 = converter.convert_stream_event(
        {
            "metadata": {
                "usage": {"inputTokens": 10, "outputTokens": 5},
                "metrics": {"latencyMs": 100},
            }
        },
        state,
    )
    event_names = [e.event for e in events2]
    # message_delta must come before message_stop
    assert "message_delta" in event_names
    assert "message_stop" in event_names
    assert event_names.index("message_delta") < event_names.index("message_stop")


def test_finalize_stream_emits_zero_usage_message_delta_when_metadata_is_missing() -> None:
    converter = BedrockToAnthropicConverter()
    state = StreamState(message_started=True, saw_message_stop=True, stop_reason="tool_use")

    events = converter.finalize_stream(state)

    assert [event.event for event in events] == ["message_delta", "message_stop"]
    assert events[0].data["delta"]["stop_reason"] == "tool_use"
    assert events[0].data["usage"] == {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }


# --- #1: tool name reverse mapping (restore original client-facing name) ---

def _tool_use_response(name: str) -> dict:
    tool_use = {"toolUse": {"toolUseId": "t1", "name": name, "input": {"q": 1}}}
    return {
        "output": {"message": {"content": [tool_use]}},
        "stopReason": "tool_use",
        "usage": {"inputTokens": 1, "outputTokens": 1},
    }


def test_convert_response_restores_original_tool_name() -> None:
    converter = BedrockToAnthropicConverter()
    msg = converter.convert_response(
        _tool_use_response("mcp__srv__short"),
        "model",
        tool_name_map={"mcp__srv__short": "mcp__aws-sentral-mcp__really_long_original_name"},
    )
    tool_use = msg.content[0]
    assert tool_use["type"] == "tool_use"
    assert tool_use["name"] == "mcp__aws-sentral-mcp__really_long_original_name"


def test_convert_response_without_map_keeps_name() -> None:
    converter = BedrockToAnthropicConverter()
    msg = converter.convert_response(_tool_use_response("read_file"), "model")
    assert msg.content[0]["name"] == "read_file"


def test_convert_response_name_not_in_map_is_unchanged() -> None:
    converter = BedrockToAnthropicConverter()
    msg = converter.convert_response(
        _tool_use_response("read_file"), "model", tool_name_map={"other": "x"}
    )
    assert msg.content[0]["name"] == "read_file"


def test_stream_content_block_start_restores_original_tool_name() -> None:
    converter = BedrockToAnthropicConverter()
    state = StreamState(tool_name_map={"san_name": "orig.tool.name"})
    converter.convert_stream_event({"messageStart": {"role": "assistant"}}, state)
    events = converter.convert_stream_event(
        {
            "contentBlockStart": {
                "contentBlockIndex": 0,
                "start": {"toolUse": {"toolUseId": "t", "name": "san_name"}},
            }
        },
        state,
    )
    starts = [e for e in events if e.event == "content_block_start"]
    assert starts, events
    assert starts[0].data["content_block"]["name"] == "orig.tool.name"


def test_stream_tool_name_restored_from_delta_start() -> None:
    converter = BedrockToAnthropicConverter()
    state = StreamState(tool_name_map={"san_name": "orig.tool.name"})
    converter.convert_stream_event({"messageStart": {"role": "assistant"}}, state)
    events = converter.convert_stream_event(
        {
            "contentBlockDelta": {
                "contentBlockIndex": 0,
                "delta": {"toolUse": {"toolUseId": "t", "name": "san_name"}},
            }
        },
        state,
    )
    starts = [e for e in events if e.event == "content_block_start"]
    assert starts, events
    assert starts[0].data["content_block"]["name"] == "orig.tool.name"
