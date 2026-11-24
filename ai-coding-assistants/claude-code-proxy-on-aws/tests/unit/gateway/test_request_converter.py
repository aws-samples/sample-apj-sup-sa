"""Tests for Anthropic-to-Bedrock request conversion."""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from gateway.domains.runtime.converter.request_converter import AnthropicToBedrockConverter
from gateway.domains.runtime.types import MessageRequest
from shared.exceptions import ValidationError

_BEDROCK_TOOL_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _tool(name: str) -> dict:
    return {
        "name": name,
        "description": "x",
        "input_schema": {"type": "object", "properties": {}},
    }


def _convert_with_tools(tools: list[dict], tool_choice: dict | None = None):
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-6",
        max_tokens=64,
        messages=[{"role": "user", "content": "hi"}],
        tools=tools,
        tool_choice=tool_choice,
    )
    converted = converter.convert_request(
        request, SimpleNamespace(bedrock_model_id="bedrock-model"), "none"
    )
    return converter, converted


def _tool_names(converted: dict) -> list[str]:
    return [t["toolSpec"]["name"] for t in converted["toolConfig"]["tools"]]


# --- #1/#2: tool name length + charset sanitization with reverse mapping ---

def test_long_tool_name_is_truncated_to_64_and_reverse_mapped() -> None:
    long_name = "mcp__aws-sentral-mcp__get_customer_influences_by_account_and_service"  # 68
    assert len(long_name) > 64
    converter, converted = _convert_with_tools([_tool(long_name)])
    (sanitized,) = _tool_names(converted)
    assert _BEDROCK_TOOL_NAME_RE.match(sanitized)
    assert len(sanitized) <= 64
    # reverse map lets the response side restore the original name
    assert converter.tool_name_map[sanitized] == long_name


def test_invalid_charset_tool_name_is_sanitized() -> None:
    converter, converted = _convert_with_tools([_tool("aws.sentral.get.customer")])
    (sanitized,) = _tool_names(converted)
    assert _BEDROCK_TOOL_NAME_RE.match(sanitized)
    assert "." not in sanitized
    assert converter.tool_name_map[sanitized] == "aws.sentral.get.customer"


def test_colliding_sanitized_tool_names_stay_unique_and_reversible() -> None:
    # both sanitize to "a_b" before dedupe
    converter, converted = _convert_with_tools([_tool("a.b"), _tool("a/b")])
    names = _tool_names(converted)
    assert len(set(names)) == 2, names
    for n in names:
        assert _BEDROCK_TOOL_NAME_RE.match(n)
    # every sanitized name maps back to its distinct original
    assert converter.tool_name_map[names[0]] == "a.b"
    assert converter.tool_name_map[names[1]] == "a/b"


def test_valid_tool_names_pass_through_without_map_entries() -> None:
    converter, converted = _convert_with_tools([_tool("read_file"), _tool("get-weather")])
    assert _tool_names(converted) == ["read_file", "get-weather"]
    assert converter.tool_name_map == {}


def test_tool_choice_tool_name_is_sanitized_to_match_spec() -> None:
    long_name = "mcp__server__" + "x" * 60
    converter, converted = _convert_with_tools(
        [_tool(long_name)], tool_choice={"type": "tool", "name": long_name}
    )
    (spec_name,) = _tool_names(converted)
    assert converted["toolConfig"]["toolChoice"] == {"tool": {"name": spec_name}}


# --- #3: tool_choice none / unsupported type must not break Bedrock ---

def test_tool_choice_none_omits_toolchoice() -> None:
    _converter, converted = _convert_with_tools(
        [_tool("read_file")], tool_choice={"type": "none"}
    )
    assert "toolChoice" not in converted["toolConfig"]


def test_tool_choice_unknown_type_is_dropped() -> None:
    _converter, converted = _convert_with_tools(
        [_tool("read_file")], tool_choice={"type": "totally_unknown"}
    )
    assert "toolChoice" not in converted["toolConfig"]


def _resolved_model(
    bedrock_model_id: str,
    *,
    family: str | None = None,
    canonical_name: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        bedrock_model_id=bedrock_model_id,
        family=family,
        canonical_name=canonical_name,
    )


def test_convert_request_applies_max_tokens_override() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": "hello"}],
    )

    converted = converter.convert_request(
        request,
        SimpleNamespace(bedrock_model_id="bedrock-model"),
        "none",
        max_tokens_override=1024,
    )

    assert converted["inferenceConfig"]["maxTokens"] == 1024


def test_convert_request_wraps_tools_for_bedrock_converse() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": "hello"}],
        tools=[
            {
                "name": "read_file",
                "description": "Read a file",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            }
        ],
        tool_choice={"type": "tool", "name": "read_file"},
    )

    converted = converter.convert_request(
        request,
        SimpleNamespace(bedrock_model_id="bedrock-model"),
        "none",
    )

    assert converted["toolConfig"] == {
        "tools": [
            {
                "toolSpec": {
                    "name": "read_file",
                    "description": "Read a file",
                    "inputSchema": {
                        "json": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        }
                    },
                }
            }
        ],
        "toolChoice": {"tool": {"name": "read_file"}},
    }


def test_convert_request_maps_auto_tool_choice_to_bedrock_union() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": "hello"}],
        tools=[{"name": "noop", "input_schema": {"type": "object"}}],
        tool_choice={"type": "auto"},
    )

    converted = converter.convert_request(
        request,
        SimpleNamespace(bedrock_model_id="bedrock-model"),
        "none",
    )

    assert converted["toolConfig"]["toolChoice"] == {"auto": {}}


def test_convert_request_defaults_missing_tool_input_schema_to_empty_object() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": "hello"}],
        tools=[{"name": "noop"}],
    )

    converted = converter.convert_request(
        request,
        SimpleNamespace(bedrock_model_id="bedrock-model"),
        "none",
    )

    assert converted["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"] == {
        "json": {"type": "object", "properties": {}}
    }


def test_convert_request_infers_object_type_for_tool_schema_without_type() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": "hello"}],
        tools=[
            {
                "name": "read_file",
                "input_schema": {
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            }
        ],
    )

    converted = converter.convert_request(
        request,
        SimpleNamespace(bedrock_model_id="bedrock-model"),
        "none",
    )

    assert converted["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"] == {
        "json": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        }
    }


def test_convert_request_rejects_non_object_tool_schema() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": "hello"}],
        tools=[
            {
                "name": "search",
                "input_schema": {"type": "string"},
            }
        ],
    )

    with pytest.raises(
        ValidationError,
        match="Tool 'search' input_schema type must be 'object' for Bedrock Converse.",
    ):
        converter.convert_request(
            request,
            SimpleNamespace(bedrock_model_id="bedrock-model"),
            "none",
        )


def test_convert_request_normalizes_system_blocks_and_cache_control() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=[
            {"type": "text", "text": "You are Claude Code."},
            {
                "type": "text",
                "text": "Repository instructions.",
                "cache_control": {"type": "ephemeral"},
            },
        ],
        messages=[{"role": "user", "content": "hello"}],
    )

    converted = converter.convert_request(
        request,
        SimpleNamespace(bedrock_model_id="bedrock-model"),
        "5m",
    )

    assert converted["system"] == [
        {"text": "You are Claude Code."},
        {"text": "Repository instructions."},
        {"cachePoint": {"type": "default", "ttl": "5m"}},
    ]


def test_convert_request_inserts_message_cache_points_for_block_and_policy() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "stable context"},
                    {
                        "type": "text",
                        "text": "cache me",
                        "cache_control": {"type": "ephemeral"},
                    },
                ],
            }
        ],
    )

    converted = converter.convert_request(
        request,
        SimpleNamespace(bedrock_model_id="bedrock-model"),
        "1h",
    )

    assert converted["messages"] == [
        {
            "role": "user",
            "content": [
                {"text": "stable context"},
                {"text": "cache me"},
                {"cachePoint": {"type": "default", "ttl": "1h"}},
            ],
        }
    ]


def test_convert_request_omits_cache_points_when_policy_is_none() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=[
            {
                "type": "text",
                "text": "do not cache",
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "hello",
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            }
        ],
    )

    converted = converter.convert_request(
        request,
        SimpleNamespace(bedrock_model_id="bedrock-model"),
        "none",
    )

    assert converted["system"] == [{"text": "do not cache"}]
    assert converted["messages"] == [{"role": "user", "content": [{"text": "hello"}]}]


def test_convert_request_drops_blank_system_and_message_text_blocks() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-haiku-4-5",
        max_tokens=4096,
        system="   ",
        messages=[
            {"role": "assistant", "content": "   "},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "keep me"},
                    {"type": "text", "text": "   "},
                    {"type": "tool_use", "id": "toolu_123", "name": "read_file", "input": {}},
                ],
            },
        ],
    )

    converted = converter.convert_request(
        request,
        SimpleNamespace(bedrock_model_id="bedrock-model"),
        "none",
    )

    assert "system" not in converted
    assert converted["messages"] == [
        {
            "role": "user",
            "content": [
                {"text": "keep me"},
                {"toolUse": {"toolUseId": "toolu_123", "name": "read_file", "input": {}}},
            ],
        }
    ]


def test_convert_request_maps_any_tool_choice_and_strict_tool_spec() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": "hello"}],
        tools=[
            {
                "name": "apply_patch",
                "description": "Edit files",
                "input_schema": {"type": "object"},
                "strict": True,
            }
        ],
        tool_choice={"type": "any"},
    )

    converted = converter.convert_request(
        request,
        SimpleNamespace(bedrock_model_id="bedrock-model"),
        "none",
    )

    assert converted["toolConfig"] == {
        "tools": [
            {
                "toolSpec": {
                    "name": "apply_patch",
                    "description": "Edit files",
                    "inputSchema": {"json": {"type": "object"}},
                    "strict": True,
                }
            }
        ],
        "toolChoice": {"any": {}},
    }


def test_convert_request_wraps_string_tool_result_content_for_bedrock() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_123",
                        "content": "<persisted-output>\nOutput too large\n</persisted-output>",
                    }
                ],
            }
        ],
    )

    converted = converter.convert_request(
        request,
        SimpleNamespace(bedrock_model_id="bedrock-model"),
        "none",
    )

    assert converted["messages"][0]["content"] == [
        {
            "toolResult": {
                "toolUseId": "toolu_123",
                "content": [{"text": "<persisted-output>\nOutput too large\n</persisted-output>"}],
                "status": "success",
            }
        }
    ]


def test_convert_request_wraps_object_tool_result_content_as_json_block() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_123",
                        "content": {"stdout": "ok", "exit_code": 0},
                    }
                ],
            }
        ],
    )

    converted = converter.convert_request(
        request,
        SimpleNamespace(bedrock_model_id="bedrock-model"),
        "none",
    )

    assert converted["messages"][0]["content"] == [
        {
            "toolResult": {
                "toolUseId": "toolu_123",
                "content": [{"json": {"stdout": "ok", "exit_code": 0}}],
                "status": "success",
            }
        }
    ]


def test_convert_request_normalizes_tool_result_content_lists() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_123",
                        "content": [
                            "stdout line 1",
                            {"type": "text", "text": "stdout line 2"},
                            {"type": "json", "json": {"exit_code": 0}},
                        ],
                    }
                ],
            }
        ],
    )

    converted = converter.convert_request(
        request,
        SimpleNamespace(bedrock_model_id="bedrock-model"),
        "none",
    )

    assert converted["messages"][0]["content"] == [
        {
            "toolResult": {
                "toolUseId": "toolu_123",
                "content": [
                    {"text": "stdout line 1"},
                    {"text": "stdout line 2"},
                    {"json": {"exit_code": 0}},
                ],
                "status": "success",
            }
        }
    ]


def test_convert_request_preserves_existing_bedrock_tool_result_union_blocks() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_123",
                        "content": [
                            {"text": "already normalized"},
                            {"json": {"stdout": "ok"}},
                        ],
                    }
                ],
            }
        ],
    )

    converted = converter.convert_request(
        request,
        SimpleNamespace(bedrock_model_id="bedrock-model"),
        "none",
    )

    assert converted["messages"][0]["content"] == [
        {
            "toolResult": {
                "toolUseId": "toolu_123",
                "content": [
                    {"text": "already normalized"},
                    {"json": {"stdout": "ok"}},
                ],
                "status": "success",
            }
        }
    ]


def test_convert_request_defaults_missing_tool_result_content_to_empty_list() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_123",
                    }
                ],
            }
        ],
    )

    converted = converter.convert_request(
        request,
        SimpleNamespace(bedrock_model_id="bedrock-model"),
        "none",
    )

    assert converted["messages"][0]["content"] == [
        {
            "toolResult": {
                "toolUseId": "toolu_123",
                "content": [],
                "status": "success",
            }
        }
    ]


def test_convert_request_drops_blank_tool_result_text_content() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-haiku-4-5",
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_123",
                        "content": [
                            "",
                            {"type": "text", "text": "  "},
                            {"text": "\n"},
                            "kept",
                        ],
                    }
                ],
            }
        ],
    )

    converted = converter.convert_request(
        request,
        SimpleNamespace(bedrock_model_id="bedrock-model"),
        "none",
    )

    assert converted["messages"][0]["content"] == [
        {
            "toolResult": {
                "toolUseId": "toolu_123",
                "content": [{"text": "kept"}],
                "status": "success",
            }
        }
    ]


def test_convert_request_converts_thinking_history_to_bedrock_reasoning_content() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "step by step",
                        "signature": "sig_123",
                    },
                    {
                        "type": "tool_use",
                        "id": "toolu_123",
                        "name": "read_file",
                        "input": {"path": "a"},
                    },
                ],
            }
        ],
    )

    converted = converter.convert_request(
        request,
        SimpleNamespace(bedrock_model_id="bedrock-model"),
        "none",
    )

    assert converted["messages"][0]["content"] == [
        {
            "reasoningContent": {
                "reasoningText": {"text": "step by step", "signature": "sig_123"}
            }
        },
        {
            "toolUse": {
                "toolUseId": "toolu_123",
                "name": "read_file",
                "input": {"path": "a"},
            }
        },
    ]


def test_convert_request_converts_redacted_thinking_history_to_bedrock_reasoning_content() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[
            {
                "role": "assistant",
                "content": [
                    {"type": "redacted_thinking", "data": "encrypted-reasoning"},
                ],
            }
        ],
    )

    converted = converter.convert_request(
        request,
        SimpleNamespace(bedrock_model_id="bedrock-model"),
        "none",
    )

    assert converted["messages"][0]["content"] == [
        {"reasoningContent": {"redactedContent": "encrypted-reasoning"}}
    ]


def test_convert_request_drops_thinking_when_tool_history_has_no_thinking_blocks() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        thinking={"type": "adaptive"},
        messages=[
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_123",
                        "name": "read_file",
                        "input": {"path": "a"},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_123", "content": "ok"},
                ],
            },
        ],
    )

    converted = converter.convert_request(
        request,
        _resolved_model(
            "anthropic.claude-sonnet-4-6",
            family="claude-sonnet-4-6",
            canonical_name="claude-sonnet-4-6",
        ),
        "none",
    )

    assert "additionalModelRequestFields" not in converted


def test_convert_request_forces_auto_tool_choice_when_thinking_is_enabled() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": "hello"}],
        tools=[{"name": "read_file", "input_schema": {"type": "object"}}],
        tool_choice={"type": "tool", "name": "read_file"},
        thinking={"type": "adaptive"},
    )

    converted = converter.convert_request(
        request,
        _resolved_model(
            "anthropic.claude-sonnet-4-6",
            family="claude-sonnet-4-6",
            canonical_name="claude-sonnet-4-6",
        ),
        "none",
    )

    assert converted["toolConfig"]["toolChoice"] == {"auto": {}}


def test_convert_request_normalizes_non_object_tool_use_input() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "toolu_json", "name": "a", "input": '{"x":1}'},
                    {"type": "tool_use", "id": "toolu_bad", "name": "b", "input": '"oops"'},
                ],
            }
        ],
    )

    converted = converter.convert_request(
        request,
        SimpleNamespace(bedrock_model_id="bedrock-model"),
        "none",
    )

    assert converted["messages"][0]["content"] == [
        {"toolUse": {"toolUseId": "toolu_json", "name": "a", "input": {"x": 1}}},
        {"toolUse": {"toolUseId": "toolu_bad", "name": "b", "input": {}}},
    ]


def test_convert_request_deduplicates_duplicate_tool_block_ids_within_message() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "toolu_123", "name": "first", "input": {"a": 1}},
                    {"type": "tool_use", "id": "toolu_123", "name": "second", "input": {"a": 2}},
                    {"type": "text", "text": "after tool"},
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_123", "content": "ok"},
                    {"type": "tool_result", "tool_use_id": "toolu_123", "content": "duplicate"},
                ],
            },
        ],
    )

    converted = converter.convert_request(
        request,
        SimpleNamespace(bedrock_model_id="bedrock-model"),
        "none",
    )

    assert converted["messages"][0]["content"] == [
        {"toolUse": {"toolUseId": "toolu_123", "name": "first", "input": {"a": 1}}},
        {"text": "after tool"},
    ]
    assert converted["messages"][1]["content"] == [
        {
            "toolResult": {
                "toolUseId": "toolu_123",
                "content": [{"text": "ok"}],
                "status": "success",
            }
        }
    ]


def test_convert_request_preserves_fixed_budget_thinking_for_pre_46_models() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-5",
        max_tokens=4096,
        messages=[{"role": "user", "content": "hello"}],
        thinking={"type": "enabled", "budget_tokens": 2048},
    )

    converted = converter.convert_request(
        request,
        _resolved_model(
            "anthropic.claude-sonnet-4-5-20250929-v1:0",
            family="claude-sonnet-4-5",
            canonical_name="claude-sonnet-4-5",
        ),
        "none",
    )

    assert converted["additionalModelRequestFields"] == {
        "thinking": {"type": "enabled", "budget_tokens": 2048}
    }


def test_convert_request_upgrades_legacy_thinking_to_adaptive_for_46_models() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": "hello"}],
        thinking={"type": "enabled", "budget_tokens": 2048},
    )

    converted = converter.convert_request(
        request,
        _resolved_model(
            "anthropic.claude-sonnet-4-6",
            family="claude-sonnet-4-6",
            canonical_name="claude-sonnet-4-6",
        ),
        "none",
    )

    assert converted["additionalModelRequestFields"] == {"thinking": {"type": "adaptive"}}


def test_convert_request_keeps_adaptive_thinking_for_opus_48() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-opus-4-8",
        max_tokens=4096,
        messages=[{"role": "user", "content": "hello"}],
        thinking={"type": "adaptive"},
    )

    converted = converter.convert_request(
        request,
        _resolved_model(
            "global.anthropic.claude-opus-4-8",
            family="claude-opus",
            canonical_name="claude-opus-4-8",
        ),
        "none",
    )

    assert converted["additionalModelRequestFields"] == {"thinking": {"type": "adaptive"}}


def test_convert_request_upgrades_legacy_thinking_to_adaptive_for_opus_48() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-opus-4-8",
        max_tokens=4096,
        messages=[{"role": "user", "content": "hello"}],
        thinking={"type": "enabled", "budget_tokens": 2048},
    )

    converted = converter.convert_request(
        request,
        _resolved_model(
            "global.anthropic.claude-opus-4-8",
            family="claude-opus",
            canonical_name="claude-opus-4-8",
        ),
        "none",
    )

    assert converted["additionalModelRequestFields"] == {"thinking": {"type": "adaptive"}}


def test_convert_request_normalizes_camel_case_budget_tokens() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-5",
        max_tokens=4096,
        messages=[{"role": "user", "content": "hello"}],
        thinking={"type": "enabled", "budgetTokens": 2048},
    )

    converted = converter.convert_request(
        request,
        _resolved_model(
            "anthropic.claude-sonnet-4-5-20250929-v1:0",
            family="claude-sonnet-4-5",
            canonical_name="claude-sonnet-4-5",
        ),
        "none",
    )

    assert converted["additionalModelRequestFields"] == {
        "thinking": {"type": "enabled", "budget_tokens": 2048}
    }


def test_convert_request_clamps_pre_46_thinking_budget_to_bedrock_minimum() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-5",
        max_tokens=4096,
        messages=[{"role": "user", "content": "hello"}],
        thinking={"type": "enabled", "budget_tokens": 512},
    )

    converted = converter.convert_request(
        request,
        _resolved_model(
            "anthropic.claude-sonnet-4-5-20250929-v1:0",
            family="claude-sonnet-4-5",
            canonical_name="claude-sonnet-4-5",
        ),
        "none",
    )

    assert converted["additionalModelRequestFields"] == {
        "thinking": {"type": "enabled", "budget_tokens": 1024}
    }


def test_convert_request_strips_budget_from_adaptive_46_thinking() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": "hello"}],
        thinking={"type": "adaptive", "budget_tokens": 2048},
    )

    converted = converter.convert_request(
        request,
        _resolved_model(
            "anthropic.claude-sonnet-4-6",
            family="claude-sonnet-4-6",
            canonical_name="claude-sonnet-4-6",
        ),
        "none",
    )

    assert converted["additionalModelRequestFields"] == {"thinking": {"type": "adaptive"}}


def test_convert_request_clamps_fixed_budget_to_stay_below_max_tokens() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-5",
        max_tokens=2048,
        messages=[{"role": "user", "content": "hello"}],
        thinking={"type": "enabled", "budget_tokens": 4096},
    )

    converted = converter.convert_request(
        request,
        _resolved_model(
            "anthropic.claude-sonnet-4-5-20250929-v1:0",
            family="claude-sonnet-4-5",
            canonical_name="claude-sonnet-4-5",
        ),
        "none",
    )

    assert converted["additionalModelRequestFields"] == {
        "thinking": {"type": "enabled", "budget_tokens": 2047}
    }


def test_convert_request_rejects_fixed_budget_when_max_tokens_too_small() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": "hello"}],
        thinking={"type": "enabled", "budget_tokens": 1024},
    )

    with pytest.raises(ValidationError, match="max_tokens greater than 1024"):
        converter.convert_request(
            request,
            _resolved_model(
                "anthropic.claude-sonnet-4-5-20250929-v1:0",
                family="claude-sonnet-4-5",
                canonical_name="claude-sonnet-4-5",
            ),
            "none",
        )


def test_convert_request_ignores_metadata_for_bedrock_thinking_payload() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-5",
        max_tokens=4096,
        messages=[{"role": "user", "content": "hello"}],
        thinking={"type": "enabled", "budget_tokens": 2048},
        metadata={"prior_reasoning": {"foo": "bar"}, "trace_id": "abc"},
    )

    converted = converter.convert_request(
        request,
        _resolved_model(
            "anthropic.claude-sonnet-4-5-20250929-v1:0",
            family="claude-sonnet-4-5",
            canonical_name="claude-sonnet-4-5",
        ),
        "none",
    )

    assert converted["additionalModelRequestFields"] == {
        "thinking": {"type": "enabled", "budget_tokens": 2048}
    }


def test_apply_cache_points_skips_when_cache_point_already_exists() -> None:
    """Regression: duplicate cachePoint caused Bedrock 'ttl conflicts' error."""
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "cached content",
                        "cache_control": {"type": "ephemeral"},
                    },
                ],
            }
        ],
    )

    converted = converter.convert_request(
        request,
        SimpleNamespace(bedrock_model_id="bedrock-model"),
        "5m",
    )

    # cache_control block produces one cachePoint; apply_cache_points should NOT add another
    cache_points = [b for b in converted["messages"][0]["content"] if "cachePoint" in b]
    assert len(cache_points) == 1, f"Expected 1 cachePoint, got {len(cache_points)}"


def test_adaptive_thinking_downgrades_to_fixed_budget_on_pre_46_models() -> None:
    """Pre-4.6 Claude models require fixed-budget thinking even if clients send adaptive."""
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-20250514",
        max_tokens=8192,
        messages=[{"role": "user", "content": "hello"}],
        thinking={"type": "adaptive"},
    )

    converted = converter.convert_request(
        request,
        SimpleNamespace(bedrock_model_id="apac.anthropic.claude-sonnet-4-20250514-v1:0"),
        "none",
    )

    thinking = converted["additionalModelRequestFields"]["thinking"]
    assert thinking["type"] == "enabled"
    assert thinking["budget_tokens"] == 1024


def test_thinking_budget_tokens_clamped_below_max_tokens() -> None:
    """Regression: budget_tokens must be less than max_tokens or Bedrock rejects the request."""
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        messages=[{"role": "user", "content": "hello"}],
        thinking={"type": "enabled", "budget_tokens": 50000},
    )

    converted = converter.convert_request(
        request,
        SimpleNamespace(bedrock_model_id="apac.anthropic.claude-sonnet-4-20250514-v1:0"),
        "none",
    )

    thinking = converted["additionalModelRequestFields"]["thinking"]
    assert thinking["budget_tokens"] < 4096


def test_cache_point_preserves_policy_ttl_for_bedrock() -> None:
    """Cache policy TTL should be carried on the generated Bedrock cachePoint block."""
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hello", "cache_control": {"type": "ephemeral"}},
                ],
            },
        ],
    )

    converted = converter.convert_request(
        request,
        SimpleNamespace(bedrock_model_id="apac.anthropic.claude-sonnet-4-20250514-v1:0"),
        "5m",
    )

    for msg in converted["messages"]:
        for block in msg.get("content", []):
            if "cachePoint" in block:
                assert block["cachePoint"]["ttl"] == "5m"


def test_convert_request_caps_cache_points_at_four_across_request() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        system=[
            {"type": "text", "text": "system", "cache_control": {"type": "ephemeral"}},
        ],
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "one", "cache_control": {"type": "ephemeral"}},
                    {"type": "text", "text": "two", "cache_control": {"type": "ephemeral"}},
                ],
            }
        ],
        tools=[
            {
                "name": "tool_a",
                "input_schema": {"type": "object"},
                "cache_control": {"type": "ephemeral"},
            },
            {
                "name": "tool_b",
                "input_schema": {"type": "object"},
                "cache_control": {"type": "ephemeral"},
            },
        ],
    )

    converted = converter.convert_request(
        request,
        SimpleNamespace(bedrock_model_id="apac.anthropic.claude-sonnet-4-20250514-v1:0"),
        "5m",
    )

    cache_points = 0
    for block in converted.get("system", []):
        if "cachePoint" in block:
            cache_points += 1
    for message in converted["messages"]:
        for block in message["content"]:
            if "cachePoint" in block:
                cache_points += 1
    for block in converted["toolConfig"]["tools"]:
        if "cachePoint" in block:
            cache_points += 1

    assert cache_points == 4


def test_convert_request_lifts_trailing_system_message_to_system_field() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-opus-4-8",
        max_tokens=16,
        messages=[
            {"role": "user", "content": "hello"},
            {"role": "system", "content": "The following skills are available"},
        ],
    )

    converted = converter.convert_request(
        request,
        _resolved_model("global.anthropic.claude-opus-4-8"),
        "none",
    )

    # System message must not remain in the conversation; the turn must end as user.
    roles = [m["role"] for m in converted["messages"]]
    assert roles == ["user"]
    assert converted["system"] == [{"text": "The following skills are available"}]


def test_convert_request_merges_inline_system_with_top_level_system() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-opus-4-8",
        max_tokens=16,
        system="base system prompt",
        messages=[
            {"role": "user", "content": "hi"},
            {"role": "system", "content": "extra mid-conversation guidance"},
        ],
    )

    converted = converter.convert_request(
        request,
        _resolved_model("global.anthropic.claude-opus-4-8"),
        "none",
    )

    assert [m["role"] for m in converted["messages"]] == ["user"]
    assert converted["system"] == [
        {"text": "base system prompt"},
        {"text": "extra mid-conversation guidance"},
    ]


def _image_block(media_type: str = "image/png", data: str = "aGVsbG8=") -> dict:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }


def test_convert_request_converts_tool_result_image_to_bedrock_format() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-6",
        max_tokens=16,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": [_image_block()],
                    }
                ],
            }
        ],
    )

    converted = converter.convert_request(
        request, _resolved_model("bedrock-model"), "none"
    )

    image = converted["messages"][0]["content"][0]["toolResult"]["content"][0]["image"]
    assert image == {"format": "png", "source": {"bytes": b"hello"}}


def test_convert_request_converts_user_image_to_bedrock_format() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-6",
        max_tokens=16,
        messages=[{"role": "user", "content": [_image_block("image/jpeg")]}],
    )

    converted = converter.convert_request(
        request, _resolved_model("bedrock-model"), "none"
    )

    image = converted["messages"][0]["content"][0]["image"]
    assert image == {"format": "jpeg", "source": {"bytes": b"hello"}}


def test_convert_request_rejects_url_image_source() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-6",
        max_tokens=16,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "url", "url": "https://example.com/a.png"},
                    }
                ],
            }
        ],
    )

    with pytest.raises(ValidationError):
        converter.convert_request(request, _resolved_model("bedrock-model"), "none")


def test_convert_request_converts_document_to_bedrock_format() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-6",
        max_tokens=16,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "title": "Q3 Report: final/v2*",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": "aGVsbG8=",
                        },
                    }
                ],
            }
        ],
    )

    converted = converter.convert_request(
        request, _resolved_model("bedrock-model"), "none"
    )

    document = converted["messages"][0]["content"][0]["document"]
    assert document == {
        "format": "pdf",
        "name": "Q3 Report final v2",
        "source": {"bytes": b"hello"},
    }


def test_convert_request_marks_tool_result_with_is_error_as_error_status() -> None:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-6",
        max_tokens=16,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": "command failed",
                        "is_error": True,
                    }
                ],
            }
        ],
    )

    converted = converter.convert_request(
        request, _resolved_model("bedrock-model"), "none"
    )

    assert converted["messages"][0]["content"][0]["toolResult"]["status"] == "error"


# --- #4: document name uniqueness within a request ---

def _doc(title: str | None) -> dict:
    import base64 as _b64

    data = _b64.b64encode(b"x").decode()
    block: dict = {
        "type": "document",
        "source": {"type": "base64", "media_type": "text/plain", "data": data},
    }
    if title is not None:
        block["title"] = title
    return block


def _doc_names(tools_msg_content: list[dict]) -> list[str]:
    return [b["document"]["name"] for b in tools_msg_content if "document" in b]


def _convert_docs(blocks: list[dict]) -> dict:
    converter = AnthropicToBedrockConverter()
    request = MessageRequest(
        model="claude-sonnet-4-6",
        max_tokens=64,
        messages=[{"role": "user", "content": blocks}],
    )
    return converter.convert_request(
        request, SimpleNamespace(bedrock_model_id="bedrock-model"), "none"
    )


def test_duplicate_document_titles_get_unique_names() -> None:
    converted = _convert_docs([_doc("report"), _doc("report"), {"type": "text", "text": "sum"}])
    names = _doc_names(converted["messages"][0]["content"])
    assert names == ["report", "report (2)"]


def test_untitled_documents_get_unique_names() -> None:
    converted = _convert_docs([_doc(None), _doc(None)])
    names = _doc_names(converted["messages"][0]["content"])
    assert names == ["document", "document (2)"]
    assert len(set(names)) == 2
