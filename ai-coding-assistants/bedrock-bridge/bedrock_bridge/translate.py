"""Translate between Anthropic Messages API and Bedrock Converse API."""

from __future__ import annotations

import base64
import hashlib
import re
from collections.abc import Iterator
from typing import Any

# Bedrock Converse enforces a 64-char limit on both toolSpec.name and
# toolUse(Result).toolUseId, plus a charset constraint on each:
#   name:      [a-zA-Z0-9_-]+
#   toolUseId: [a-zA-Z0-9_.:-]+
# Claude Code's MCP tools easily exceed the length cap; some non-Anthropic
# models (e.g. Kimi) also emit malformed tool-use IDs with chat-template
# tokens leaked in (spaces, "<|...|>"), which violate the charset.
_BEDROCK_ID_LIMIT = 64
_NAME_ILLEGAL = re.compile(r"[^a-zA-Z0-9_-]")
_ID_ILLEGAL = re.compile(r"[^a-zA-Z0-9_.:-]")

# Bedrock rejects blank text blocks ("The text field in the ContentBlock
# object ... is blank"). When we must emit a placeholder for a turn that was
# otherwise empty (e.g. an interrupted assistant turn that arrived as nothing
# but empty text blocks), use a non-blank marker so the request still validates.
_EMPTY_TEXT_PLACEHOLDER = "[empty]"

# Bidirectional tool name mapping
_name_to_short: dict[str, str] = {}
_short_to_name: dict[str, str] = {}

# Bidirectional tool-use ID mapping (different namespace, looser charset)
_id_to_short: dict[str, str] = {}
_short_to_id: dict[str, str] = {}


def _shorten(value: str, fwd: dict[str, str], rev: dict[str, str], prefix_len: int, illegal: re.Pattern) -> str:
    """Map an arbitrary tool name/ID to a Bedrock-legal form (length + charset),
    remembering the mapping so the response leg can restore the original.

    A value is rewritten if it is too long OR contains characters Bedrock's
    pattern rejects. The rewrite is deterministic (sha256 of the original),
    so the toolUse and its matching toolResult resolve to the same short form.
    """
    if len(value) <= _BEDROCK_ID_LIMIT and not illegal.search(value):
        return value
    if value in fwd:
        return fwd[value]
    h = hashlib.sha256(value.encode()).hexdigest()[:8]
    clean_prefix = illegal.sub("_", value[:prefix_len])
    short = clean_prefix + "_" + h
    fwd[value] = short
    rev[short] = value
    return short


def _shorten_tool_name(name: str) -> str:
    return _shorten(name, _name_to_short, _short_to_name, 55, _NAME_ILLEGAL)


def _restore_tool_name(short: str) -> str:
    return _short_to_name.get(short, short)


def _shorten_tool_use_id(tool_use_id: str) -> str:
    return _shorten(tool_use_id, _id_to_short, _short_to_id, 55, _ID_ILLEGAL)


def _restore_tool_use_id(short: str) -> str:
    return _short_to_id.get(short, short)


def anthropic_to_converse(body: dict) -> tuple[dict, dict]:
    """Convert Anthropic Messages API request → Bedrock converse() kwargs.

    Returns (converse_kwargs, metadata) where metadata has info needed
    to build the Anthropic-shaped response.
    """
    kwargs: dict[str, Any] = {}

    # Messages
    messages = []
    for msg in body.get("messages", []):
        messages.append(_convert_message(msg))
    kwargs["messages"] = messages

    # System prompt
    if system := body.get("system"):
        if isinstance(system, str):
            kwargs["system"] = [{"text": system}]
        elif isinstance(system, list):
            kwargs["system"] = [{"text": block["text"]} for block in system if block.get("type") == "text"]

    # Inference config
    inf: dict[str, Any] = {}
    if (mt := body.get("max_tokens")) is not None:
        inf["maxTokens"] = mt
    if (temp := body.get("temperature")) is not None:
        inf["temperature"] = temp
    if (top_p := body.get("top_p")) is not None:
        inf["topP"] = top_p
    # stop_sequences intentionally dropped: every non-Anthropic Bedrock model
    # rejects the field with a ValidationException, and the bridge does not
    # serve Anthropic targets (preflight refuses anthropic.* main IDs).
    if inf:
        kwargs["inferenceConfig"] = inf

    # Tools. Server-side Anthropic tools (web_search_*, computer_*, bash_*,
    # text_editor_*) execute on Anthropic's servers; Bedrock Converse has no
    # equivalent, so drop them. Converse also rejects an empty tools list,
    # so only set toolConfig if at least one client tool remains.
    if tools := body.get("tools"):
        client_tools = [_convert_tool(t) for t in tools if _is_client_tool(t)]
        if client_tools:
            kwargs["toolConfig"] = {"tools": client_tools}

    metadata = {"model": body.get("model", "unknown")}
    return kwargs, metadata


def anthropic_image_to_bedrock(block: dict) -> dict | None:
    """Convert an Anthropic image block → Bedrock image block. None if unsupported."""
    source = block.get("source", {})
    if source.get("type") != "base64":
        return None
    media_type = source.get("media_type", "image/png")
    fmt = media_type.split("/")[-1].lower()
    if fmt == "jpg":
        fmt = "jpeg"
    raw = source.get("data", "")
    if isinstance(raw, str):
        raw = base64.b64decode(raw)
    return {"image": {"format": fmt, "source": {"bytes": raw}}}


def _convert_tool_result_content(content: Any) -> list[dict]:
    """Anthropic tool_result.content → Bedrock toolResult.content blocks.

    Bedrock requires non-empty content and supports text + image + json blocks
    inside a toolResult. Preserve images (e.g. screenshots from MCP tools) so
    the follow-up message doesn't end up with an empty content list.
    """
    if isinstance(content, str):
        return [{"text": content}] if content else [{"text": _EMPTY_TEXT_PLACEHOLDER}]

    out: list[dict] = []
    for b in content or []:
        btype = b.get("type")
        if btype == "text":
            text = b.get("text", "")
            if text:
                out.append({"text": text})
        elif btype == "image":
            img = anthropic_image_to_bedrock(b)
            if img is not None:
                out.append(img)
        elif btype == "json":
            out.append({"json": b.get("json", {})})
        # Unknown block types are dropped; Bedrock would reject them anyway.
    # Bedrock rejects an empty content list. Fall back to a single placeholder
    # text block so the request still validates.
    if not out:
        out.append({"text": _EMPTY_TEXT_PLACEHOLDER})
    return out


def _convert_message(msg: dict) -> dict:
    role = msg["role"]
    content = msg.get("content", "")

    if isinstance(content, str):
        return {"role": role, "content": [{"text": content}]}

    blocks: list[dict] = []
    for block in content:
        btype = block.get("type")
        if btype == "text":
            # Bedrock rejects empty text blocks inside multi-block messages
            # ("text field is blank"). Drop empties; the fallback at the end
            # handles the all-empty case with a single placeholder.
            text = block.get("text", "")
            if text:
                blocks.append({"text": text})
        elif btype == "tool_use":
            blocks.append(
                {
                    "toolUse": {
                        "toolUseId": _shorten_tool_use_id(block["id"]),
                        "name": _shorten_tool_name(block["name"]),
                        "input": block.get("input", {}),
                    }
                }
            )
        elif btype == "tool_result":
            blocks.append(
                {
                    "toolResult": {
                        "toolUseId": _shorten_tool_use_id(block["tool_use_id"]),
                        "content": _convert_tool_result_content(block.get("content", "")),
                        **({"status": "error"} if block.get("is_error") else {}),
                    }
                }
            )
        elif btype == "image":
            img = anthropic_image_to_bedrock(block)
            if img is not None:
                blocks.append(img)
        elif btype == "thinking":
            # Bedrock Converse accepts reasoning on input via reasoningContent.
            # For models that don't support signed reasoning the call will
            # ValidationException; prefer loud failure over silent loss.
            reasoning_text: dict[str, Any] = {"text": block.get("thinking", "")}
            if sig := block.get("signature"):
                reasoning_text["signature"] = sig
            blocks.append({"reasoningContent": {"reasoningText": reasoning_text}})
        elif btype == "redacted_thinking":
            data = block.get("data", "")
            if isinstance(data, str):
                try:
                    data = base64.b64decode(data)
                except Exception:
                    data = data.encode()
            blocks.append({"reasoningContent": {"redactedContent": data}})
        # Other unknown block types are dropped on the way to Bedrock.

    # Bedrock rejects messages with no content. If we dropped everything (e.g.
    # assistant turn that was nothing but a `thinking` block, or an interrupted
    # turn that arrived as only empty text blocks), fall back to a placeholder
    # so the request still validates.
    if not blocks:
        blocks.append({"text": _EMPTY_TEXT_PLACEHOLDER})

    # Some Bedrock-hosted models (Kimi K2.5, MiniMax) accept image blocks at
    # the top level of a user message but reject them inside toolResult.content.
    # Hoist any such images out of toolResult and append them as siblings in
    # the same user message, replacing them inside the toolResult with a text
    # marker so the tool result still has content.
    if role == "user":
        hoisted: list[dict] = []
        for b in blocks:
            if "toolResult" not in b:
                continue
            tr = b["toolResult"]
            new_content: list[dict] = []
            for sub in tr.get("content", []):
                if "image" in sub:
                    hoisted.append(sub)
                    new_content.append({"text": "[image attached to this message]"})
                else:
                    new_content.append(sub)
            if not new_content:
                new_content.append({"text": _EMPTY_TEXT_PLACEHOLDER})
            tr["content"] = new_content
        blocks.extend(hoisted)

    return {"role": role, "content": blocks}


_SERVER_TOOL_PREFIXES = ("web_search_", "computer_", "bash_", "text_editor_")


def _is_client_tool(tool: dict) -> bool:
    """True for regular client-executed tools. False for Anthropic server tools."""
    t = tool.get("type", "")
    if t and any(t.startswith(p) for p in _SERVER_TOOL_PREFIXES):
        return False
    # Client tools have a `name` + `input_schema`; server tools don't.
    return "name" in tool and "input_schema" in tool


def _convert_tool(tool: dict) -> dict:
    # Bedrock Converse requires toolSpec.description length >= 1. Some Claude
    # Code tools ship with an empty/missing description, so fall back to the
    # tool name as a placeholder.
    desc = tool.get("description") or tool["name"]
    return {
        "toolSpec": {
            "name": _shorten_tool_name(tool["name"]),
            "description": desc,
            "inputSchema": {"json": tool.get("input_schema") or {"type": "object"}},
        }
    }


def converse_to_anthropic(response: dict, metadata: dict) -> dict:
    """Convert Bedrock converse() response → Anthropic Messages API response."""
    output = response.get("output", {})
    message = output.get("message", {})
    usage = response.get("usage", {})
    stop = response.get("stopReason", "end_turn")

    content = []
    for block in message.get("content", []):
        if "text" in block:
            content.append({"type": "text", "text": block["text"]})
        elif "toolUse" in block:
            tu = block["toolUse"]
            content.append(
                {
                    "type": "tool_use",
                    "id": _restore_tool_use_id(tu["toolUseId"]),
                    "name": _restore_tool_name(tu["name"]),
                    "input": tu["input"],
                }
            )
        elif "reasoningContent" in block:
            rc = block["reasoningContent"]
            if "reasoningText" in rc:
                rt = rc["reasoningText"]
                content.append(
                    {
                        "type": "thinking",
                        "thinking": rt.get("text", ""),
                        "signature": rt.get("signature", ""),
                    }
                )
            elif "redactedContent" in rc:
                data = rc["redactedContent"]
                if isinstance(data, (bytes, bytearray)):
                    data = base64.b64encode(bytes(data)).decode()
                content.append({"type": "redacted_thinking", "data": data})

    return {
        "id": "msg_bridge_" + _short_id(),
        "type": "message",
        "role": "assistant",
        "content": content,
        "model": metadata.get("model", "unknown"),
        "stop_reason": _map_stop_reason(stop),
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("inputTokens", 0),
            "output_tokens": usage.get("outputTokens", 0),
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
    }


def converse_stream_to_anthropic_events(
    event: dict, metadata: dict, state: dict | None = None
) -> Iterator[tuple[str, dict]]:
    """Convert a single Bedrock converse-stream event to Anthropic SSE events.

    Yields (event_type, data_dict) tuples. `state` is a per-stream dict the
    caller threads through every call; the translator uses it to synthesize
    `content_block_start` events for indices Bedrock never opens explicitly
    (kimi-k2-thinking and other models skip contentBlockStart for text and
    reasoning blocks).
    """
    if state is None:
        state = {}
    seen = state.setdefault("seen", set())
    # Indices whose first non-empty text/thinking delta has already been emitted.
    # Used to strip a single leading whitespace character on the first delta,
    # an artifact of some Bedrock models' chat templates (e.g. kimi prefixes
    # assistant turns with a space) that doesn't appear via the native
    # Anthropic API. Stripping only the first char keeps legitimate intra-text
    # whitespace intact.
    primed = state.setdefault("primed", set())

    if "messageStart" in event:
        seen.clear()
        yield (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_bridge_" + _short_id(),
                    "type": "message",
                    "role": event["messageStart"].get("role", "assistant"),
                    "content": [],
                    "model": metadata.get("model", "unknown"),
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            },
        )

    elif "contentBlockStart" in event:
        cbs = event["contentBlockStart"]
        idx = cbs.get("contentBlockIndex", 0)
        start = cbs.get("start", {})

        if "toolUse" in start:
            tu = start["toolUse"]
            seen.add(idx)
            yield (
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": idx,
                    "content_block": {
                        "type": "tool_use",
                        "id": _restore_tool_use_id(tu.get("toolUseId", "")),
                        "name": _restore_tool_name(tu.get("name", "")),
                        "input": {},
                    },
                },
            )
        # For non-toolUse starts we defer until the first delta arrives,
        # since Bedrock doesn't tag the start with the block type. Some models
        # (kimi-k2-thinking) skip the start event entirely; the synthesis
        # below covers both paths.

    elif "contentBlockDelta" in event:
        cbd = event["contentBlockDelta"]
        idx = cbd.get("contentBlockIndex", 0)
        delta = cbd.get("delta", {})

        if idx not in seen:
            seen.add(idx)
            if "reasoningContent" in delta:
                yield (
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": idx,
                        "content_block": {"type": "thinking", "thinking": "", "signature": ""},
                    },
                )
            elif "toolUse" in delta:
                # Bedrock didn't send a typed start; we can't recover the tool
                # name / id from a delta alone, so emit a best-effort start.
                yield (
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": idx,
                        "content_block": {"type": "tool_use", "id": "", "name": "", "input": {}},
                    },
                )
            else:
                yield (
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": idx,
                        "content_block": {"type": "text", "text": ""},
                    },
                )

        if "text" in delta:
            text = delta["text"]
            if idx not in primed and text:
                if text[:1] == " ":
                    text = text[1:]
                primed.add(idx)
            yield (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": idx,
                    "delta": {"type": "text_delta", "text": text},
                },
            )
        elif "toolUse" in delta:
            yield (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": idx,
                    "delta": {"type": "input_json_delta", "partial_json": delta["toolUse"].get("input", "")},
                },
            )
        elif "reasoningContent" in delta:
            rc = delta["reasoningContent"]
            if "text" in rc:
                rtext = rc["text"]
                if idx not in primed and rtext:
                    if rtext[:1] == " ":
                        rtext = rtext[1:]
                    primed.add(idx)
                yield (
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": idx,
                        "delta": {"type": "thinking_delta", "thinking": rtext},
                    },
                )
            if "signature" in rc:
                yield (
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": idx,
                        "delta": {"type": "signature_delta", "signature": rc["signature"]},
                    },
                )

    elif "contentBlockStop" in event:
        idx = event["contentBlockStop"].get("contentBlockIndex", 0)
        yield (
            "content_block_stop",
            {
                "type": "content_block_stop",
                "index": idx,
            },
        )

    elif "messageStop" in event:
        stop = event["messageStop"].get("stopReason", "end_turn")
        yield (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": _map_stop_reason(stop), "stop_sequence": None},
                "usage": {
                    "output_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            },
        )

    elif "metadata" in event:
        usage = event["metadata"].get("usage", {})
        yield (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {
                    "input_tokens": usage.get("inputTokens", 0),
                    "output_tokens": usage.get("outputTokens", 0),
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                },
            },
        )


def _map_stop_reason(bedrock_reason: str) -> str:
    mapping = {
        "end_turn": "end_turn",
        "tool_use": "tool_use",
        "max_tokens": "max_tokens",
        "stop_sequence": "stop_sequence",
    }
    return mapping.get(bedrock_reason, "end_turn")


def _short_id() -> str:
    import secrets

    return secrets.token_hex(12)
