"""Regression tests for server.py error mapping and image handling.

These cover field failures we saw and fixed: context-window overflow (mapped to
Claude Code's compact path), images reaching a non-vision model, and lost
`[Image #N]` history chips that made small models confabulate. server.py's boto
client is lazy (get_client is only called inside the request path), so importing
and exercising these helpers needs no AWS creds.
"""

from bedrock_bridge.server import (
    _format_error,
    _has_image_content,
    _replace_lost_image_chips,
    _strip_images_from_body,
)


# Context-window overflow must be rewritten to "prompt is too long", the
# substring Claude Code's getAssistantMessageFromError keys on to fire reactive
# compaction. Observed verbatim from a Mantle-wrapped model.
def test_context_length_maps_to_prompt_too_long() -> None:
    err = (
        "An error occurred (ValidationException) when calling the Converse "
        "operation: ... \"Input length (131075) exceeds model's maximum "
        'context length (131072)." ...'
    )
    status, etype, msg = _format_error(err, None)
    assert status == 400
    assert "prompt is too long" in msg
    # The two large numbers become the gap; limit < actual so the gap is positive.
    assert "131072" in msg and "131075" in msg


# Mantle wraps errors with a status code ("Some(400)"). That 400 must NOT be
# mistaken for a token count; only numbers >= 1000 are considered.
def test_context_length_ignores_status_code_noise() -> None:
    err = (
        'ErrorEvent { error: APIError { type: "BadRequestError", '
        "code: Some(400), message: \"Input length (131075) exceeds model's "
        'maximum context length (131072)." } }'
    )
    _, _, msg = _format_error(err, None)
    # 400 must not appear as the "maximum" token figure.
    assert "> 400 maximum" not in msg
    assert "131072" in msg


# The "total tokens" phrasing (input + requested output) also carries
# "maximum context length"; it must hit the same compact path.
def test_total_token_overflow_maps_to_prompt_too_long() -> None:
    err = (
        "This model's maximum context length is 262144 tokens. However, "
        "you requested 32000 output tokens and your prompt contains at "
        "least 230145 input tokens, for a total of at least 262145 tokens."
    )
    status, _, msg = _format_error(err, None)
    assert status == 400
    assert "prompt is too long" in msg


# Output-token cap has no auto-recovery; surface plainly with the bridge prefix.
def test_output_token_cap_passthrough() -> None:
    err = "The maximum tokens you requested exceeds the model limit of 8192."
    status, _, msg = _format_error(err, None)
    assert status == 400
    assert "[bedrock-bridge]" in msg
    assert "prompt is too long" not in msg


# Unknown errors fall through to 500 + bridge prefix + issue-tracker pointer.
def test_unknown_error_falls_through_to_500() -> None:
    status, etype, msg = _format_error("some brand new bedrock error", None)
    assert status == 500
    assert etype == "api_error"
    assert "[bedrock-bridge]" in msg


# Non-vision model + image in body: every image block is replaced with a text
# marker so the request validates ("model doesn't support the image content
# block"). Covers both top-level and tool_result-nested images.
def test_strip_images_replaces_all_image_blocks() -> None:
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "x"}},
                    {"type": "text", "text": "what is this"},
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "a",
                        "content": [
                            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "y"}},
                        ],
                    },
                ],
            },
        ]
    }
    n = _strip_images_from_body(body)
    assert n == 2
    assert not _has_image_content(body)
    # The text block alongside the image is preserved.
    assert any(b.get("text") == "what is this" for b in body["messages"][0]["content"])


# A lost `[Image #N]` chip (recalled from history with no bytes) must be
# rewritten to an explicit instruction so small models don't confabulate
# image contents they cannot see.
def test_lost_image_chip_rewritten() -> None:
    body = {
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "[Image #1]"}]},
        ]
    }
    n = _replace_lost_image_chips(body)
    assert n == 1
    new_text = body["messages"][0]["content"][0]["text"]
    assert "[Image #1]" not in new_text
    assert "did not preserve" in new_text or "did not come through" in new_text


# A live paste (chip text PLUS a real image block in the same message) must be
# left alone; the chip is just a label there.
def test_live_paste_chip_not_rewritten() -> None:
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "[Image #1]"},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "x"}},
                ],
            },
        ]
    }
    n = _replace_lost_image_chips(body)
    assert n == 0
    assert body["messages"][0]["content"][0]["text"] == "[Image #1]"
