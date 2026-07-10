"""Regression tests for translate.py.

Each test is keyed to a concrete Bedrock-shape rejection or translation bug we
hit in the field. Inputs are hand-built minimal Anthropic/Bedrock payloads
(no captured session data) so the public repo carries no PII.
"""

from bedrock_bridge import translate
from bedrock_bridge.translate import (
    _EMPTY_TEXT_PLACEHOLDER,
    anthropic_to_converse,
    converse_stream_to_anthropic_events,
)


def _converted_messages(body: dict) -> list[dict]:
    kwargs, _ = anthropic_to_converse(body)
    return kwargs["messages"]


# Bedrock rejects blank text blocks ("text field ... is blank"). An interrupted
# assistant turn can arrive as nothing but empty text blocks; every real block
# is dropped, so the all-empty fallback must emit a non-blank placeholder.
def test_empty_turn_uses_nonblank_placeholder() -> None:
    body = {
        "model": "m",
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": ""},
                    {"type": "text", "text": ""},
                ],
            },
        ],
    }
    msgs = _converted_messages(body)
    assert msgs[0]["content"] == [{"text": _EMPTY_TEXT_PLACEHOLDER}]
    assert _EMPTY_TEXT_PLACEHOLDER != ""


# A real text block alongside empties keeps the real text and drops the empties;
# no placeholder is added.
def test_empty_text_blocks_dropped_when_real_content_present() -> None:
    body = {
        "model": "m",
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": ""},
                    {"type": "text", "text": "actual answer"},
                ],
            },
        ],
    }
    msgs = _converted_messages(body)
    assert msgs[0]["content"] == [{"text": "actual answer"}]


# An empty tool_result (string or list) must fall back to a non-blank
# placeholder; Bedrock rejects an empty toolResult.content list.
def test_empty_tool_result_uses_nonblank_placeholder() -> None:
    body = {
        "model": "m",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "abc", "content": ""},
                ],
            },
        ],
    }
    msgs = _converted_messages(body)
    tr = msgs[0]["content"][0]["toolResult"]
    assert tr["content"] == [{"text": _EMPTY_TEXT_PLACEHOLDER}]


# Some Bedrock models reject images nested in toolResult. We hoist the image to
# a sibling block in the same user message and leave a text marker behind.
def test_image_hoisted_out_of_tool_result() -> None:
    png_b64 = "aGVsbG8="  # "hello"
    body = {
        "model": "m",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "abc",
                        "content": [
                            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": png_b64}},
                        ],
                    },
                ],
            },
        ],
    }
    msgs = _converted_messages(body)
    content = msgs[0]["content"]
    tr = next(b["toolResult"] for b in content if "toolResult" in b)
    # The image is gone from the tool result, replaced by a text marker.
    assert all("image" not in sub for sub in tr["content"])
    assert any("text" in sub for sub in tr["content"])
    # And it now appears as a sibling image block.
    assert any("image" in b for b in content)


# image/jpg is not a valid Bedrock format; it must be normalized to jpeg.
def test_jpg_media_type_normalized_to_jpeg() -> None:
    body = {
        "model": "m",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpg", "data": "aGk="}},
                ],
            },
        ],
    }
    msgs = _converted_messages(body)
    assert msgs[0]["content"][0]["image"]["format"] == "jpeg"


# stop_sequences is rejected by every non-Anthropic Bedrock model; drop it.
def test_stop_sequences_dropped() -> None:
    body = {"model": "m", "messages": [], "stop_sequences": ["X"], "max_tokens": 10}
    kwargs, _ = anthropic_to_converse(body)
    assert "stopSequences" not in kwargs.get("inferenceConfig", {})


# Anthropic server-side tools have no Bedrock equivalent and must be dropped;
# a tools list that is only server tools yields no toolConfig (Bedrock rejects
# an empty tools list).
def test_server_tools_dropped_no_toolconfig() -> None:
    body = {
        "model": "m",
        "messages": [],
        "tools": [{"type": "web_search_20250101", "name": "web_search"}],
    }
    kwargs, _ = anthropic_to_converse(body)
    assert "toolConfig" not in kwargs


# Tool names over Bedrock's 64-char cap are shortened deterministically and the
# mapping round-trips on the response leg.
def test_long_tool_name_shortened_and_restored() -> None:
    long_name = "mcp__some_server__" + "x" * 80
    short = translate._shorten_tool_name(long_name)
    assert len(short) <= 64
    assert translate._restore_tool_name(short) == long_name


# Some models (Kimi) emit tool names/IDs with chat-template tokens leaked in
# (spaces, "<|...|>"), violating Bedrock's charset even when under the length
# cap. A short-but-illegal value must still be rewritten, and round-trip.
def test_illegal_charset_tool_name_shortened_even_when_short() -> None:
    bad_name = "tool with spaces"  # short, but space is illegal for names
    short = translate._shorten_tool_name(bad_name)
    assert translate._NAME_ILLEGAL.search(short) is None
    assert translate._restore_tool_name(short) == bad_name


def test_illegal_charset_tool_use_id_shortened_even_when_short() -> None:
    bad_id = "call<|tool|> 7"  # short, but "<", "|", ">", " " are illegal for IDs
    short = translate._shorten_tool_use_id(bad_id)
    assert translate._ID_ILLEGAL.search(short) is None
    assert translate._restore_tool_use_id(short) == bad_id


# A tool_use and its matching tool_result must resolve to the same shortened
# id, so Bedrock can pair them. (messages.N.toolUse / messages.N+1.toolResult)
def test_tool_use_and_result_share_shortened_id() -> None:
    raw_id = "functions.mcp__server__some_tool:" + "1" * 80
    body = {
        "model": "m",
        "messages": [
            {"role": "assistant", "content": [{"type": "tool_use", "id": raw_id, "name": "t", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": raw_id, "content": "ok"}]},
        ],
    }
    msgs = _converted_messages(body)
    use_id = msgs[0]["content"][0]["toolUse"]["toolUseId"]
    res_id = msgs[1]["content"][0]["toolResult"]["toolUseId"]
    assert use_id == res_id
    assert len(use_id) <= 64


# A real image's base64 payload must decode to the raw bytes Bedrock expects.
# 1x1 red PNG; we know exactly what it is.
_RED_PIXEL_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


def test_image_base64_decoded_to_bytes() -> None:
    import base64 as _b64

    body = {
        "model": "m",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": _RED_PIXEL_PNG_B64},
                    }
                ],
            },
        ],
    }
    msgs = _converted_messages(body)
    img = msgs[0]["content"][0]["image"]
    assert img["format"] == "png"
    assert img["source"]["bytes"] == _b64.b64decode(_RED_PIXEL_PNG_B64)
    assert isinstance(img["source"]["bytes"], (bytes, bytearray))


# kimi-k2 and similar skip contentBlockStart for text/reasoning. The translator
# must synthesize a content_block_start on the first delta of an unseen index,
# else Claude Code renders reasoning as plain text.
def test_stream_synthesizes_content_block_start_for_text() -> None:
    state = {}
    event = {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "hello"}}}
    out = list(converse_stream_to_anthropic_events(event, {"model": "m"}, state))
    types = [e[0] for e in out]
    assert types == ["content_block_start", "content_block_delta"]
    assert out[0][1]["content_block"]["type"] == "text"


# Some models prefix the first delta with a single space (chat-template
# artifact). Strip exactly one leading space on the first delta, keep the rest.
def test_stream_strips_single_leading_space_once() -> None:
    state = {}
    e1 = {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": " hi"}}}
    e2 = {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "  there"}}}
    out1 = list(converse_stream_to_anthropic_events(e1, {"model": "m"}, state))
    out2 = list(converse_stream_to_anthropic_events(e2, {"model": "m"}, state))
    d1 = next(e[1] for e in out1 if e[0] == "content_block_delta")
    d2 = next(e[1] for e in out2 if e[0] == "content_block_delta")
    assert d1["delta"]["text"] == "hi"  # one leading space stripped
    assert d2["delta"]["text"] == "  there"  # later deltas untouched


# The trailing Converse metadata event carries inputTokens and outputTokens.
# We forward both on the final message_delta so the client can report the real
# per-response input count instead of a hardcoded 0.
def test_stream_metadata_forwards_input_tokens() -> None:
    event = {"metadata": {"usage": {"inputTokens": 1500, "outputTokens": 800}}}
    out = list(converse_stream_to_anthropic_events(event, {"model": "m"}, {}))
    usage = next(e[1]["usage"] for e in out if e[0] == "message_delta")
    assert usage["input_tokens"] == 1500
    assert usage["output_tokens"] == 800
    # Non-Claude models have no prompt cache, so these are always 0.
    assert usage["cache_read_input_tokens"] == 0
    assert usage["cache_creation_input_tokens"] == 0
