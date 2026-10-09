"""Bedrock-to-Anthropic response conversion."""

from __future__ import annotations

import json
from typing import Any

from gateway.domains.runtime.types import (
    MessageResponse,
    SSEEvent,
    StreamState,
    UsageInfo,
    UsagePayload,
)


class BedrockToAnthropicConverter:
    """Convert Converse and ConverseStream responses to Anthropic-compatible payloads."""

    def convert_response(
        self,
        bedrock_resp: dict[str, Any],
        response_model: str,
        tool_name_map: dict[str, str] | None = None,
    ) -> MessageResponse:
        output = bedrock_resp.get("output", {}).get("message", {})
        usage_info = self.extract_usage(bedrock_resp)
        content = self._convert_output_content(output.get("content", []), tool_name_map)
        return MessageResponse(
            id=bedrock_resp.get("id")
            or bedrock_resp.get("ResponseMetadata", {}).get("RequestId", "msg_unknown"),
            model=response_model,
            content=content,
            stop_reason=self._map_stop_reason(bedrock_resp.get("stopReason")),
            usage=UsagePayload(
                input_tokens=usage_info.input_tokens,
                output_tokens=usage_info.output_tokens,
                cache_creation_input_tokens=usage_info.cached_write_tokens,
                cache_read_input_tokens=usage_info.cached_read_tokens,
            ),
        )

    def convert_stream_event(self, event: dict[str, Any], state: StreamState) -> list[SSEEvent]:
        event = self._normalize_stream_event(event)
        events = self._maybe_emit_message_start(event, state)
        if "contentBlockStart" in event:
            events.extend(self._handle_content_block_start(event["contentBlockStart"], state))
        if "contentBlockDelta" in event:
            events.extend(self._handle_content_block_delta(event["contentBlockDelta"], state))
        if "contentBlockStop" in event:
            events.extend(self._handle_content_block_stop(event["contentBlockStop"], state))
        if "messageStop" in event:
            self._handle_message_stop(event, state)
        if "metadata" in event:
            self._handle_metadata(event["metadata"], state)
        events.extend(self._emit_message_termination_events(state))
        self._record_unhandled_event_types(event, state)
        return events

    def finalize_stream(self, state: StreamState) -> list[SSEEvent]:
        if state.message_delta_emitted:
            return []

        if state.saw_message_stop:
            usage = state.pending_usage or UsageInfo(stop_reason=state.stop_reason)
            state.message_delta_emitted = True
            state.message_stop_emitted = True
            return [
                SSEEvent(
                    "message_delta",
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": state.stop_reason or usage.stop_reason},
                        "usage": {
                            "input_tokens": usage.input_tokens,
                            "output_tokens": usage.output_tokens,
                            "cache_creation_input_tokens": usage.cached_write_tokens,
                            "cache_read_input_tokens": usage.cached_read_tokens,
                        },
                    },
                ),
                SSEEvent("message_stop", {"type": "message_stop"}),
            ]

        if state.message_started and not state.message_stop_emitted:
            state.message_stop_emitted = True
            return [SSEEvent("message_stop", {"type": "message_stop"})]

        return []

    def extract_usage(self, response_or_metadata: dict[str, Any]) -> UsageInfo:
        metadata = response_or_metadata.get("metadata") or {}
        usage = response_or_metadata.get("usage")
        if usage is None:
            usage = metadata.get("usage")
        usage = usage or {}

        metrics = response_or_metadata.get("metrics")
        if metrics is None:
            metrics = metadata.get("metrics")
        metrics = metrics or {}

        return UsageInfo(
            input_tokens=self._coerce_usage_int(usage.get("inputTokens")),
            output_tokens=self._coerce_usage_int(usage.get("outputTokens")),
            cached_read_tokens=self._coerce_usage_int(usage.get("cacheReadInputTokens")),
            cached_write_tokens=self._coerce_usage_int(usage.get("cacheWriteInputTokens")),
            stop_reason=self._map_stop_reason(response_or_metadata.get("stopReason")),
            latency_ms=metrics.get("latencyMs"),
            bedrock_invocation_id=response_or_metadata.get("ResponseMetadata", {}).get("RequestId"),
            cache_details=usage or None,
        )

    @staticmethod
    def _coerce_usage_int(value: Any) -> int:
        return int(value or 0)

    def _convert_output_content(
        self, content: list[dict[str, Any]], tool_name_map: dict[str, str] | None = None
    ) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for block in content:
            if "text" in block:
                converted.append({"type": "text", "text": block["text"]})
            elif "toolUse" in block:
                tool_use = block["toolUse"]
                converted.append(
                    {
                        "type": "tool_use",
                        "id": tool_use.get("toolUseId"),
                        "name": self._restore_tool_name(tool_use.get("name"), tool_name_map),
                        "input": tool_use.get("input", {}),
                    }
                )
            elif "toolResult" in block:
                tool_result = block["toolResult"]
                converted.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_result.get("toolUseId"),
                        "content": tool_result.get("content", []),
                        "status": tool_result.get("status"),
                    }
                )
            elif "reasoningContent" in block:
                thinking_block = self._convert_reasoning_content_start(block["reasoningContent"])
                if thinking_block is not None:
                    converted.append(thinking_block)
            else:
                converted.append(block)
        return converted

    def _map_stop_reason(self, stop_reason: str | None) -> str | None:
        mapping = {
            "end_turn": "end_turn",
            "tool_use": "tool_use",
            "max_tokens": "max_tokens",
            "stop_sequence": "stop_sequence",
            "content_filtered": "end_turn",
        }
        return mapping.get(stop_reason, stop_reason)

    def _resolve_content_block_index(
        self, block_event: dict[str, Any], state: StreamState
    ) -> int:
        content_block_index = block_event.get("contentBlockIndex")
        if isinstance(content_block_index, int):
            return content_block_index
        return state.content_index

    def _maybe_emit_message_start(
        self, event: dict[str, Any], state: StreamState
    ) -> list[SSEEvent]:
        if state.message_started or "metadata" in event:
            return []

        state.message_started = True
        return [
            SSEEvent(
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": state.message_id,
                        "type": "message",
                        "role": "assistant",
                        "content": [],
                        "model": state.model_name,
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 0, "output_tokens": 0},
                    },
                },
            )
        ]

    def _handle_content_block_start(
        self, start_event: dict[str, Any], state: StreamState
    ) -> list[SSEEvent]:
        content_index = self._resolve_content_block_index(start_event, state)
        state.content_index = content_index
        state.started_blocks.add(content_index)
        return [
            SSEEvent(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": content_index,
                    "content_block": self._convert_stream_content_block_start(
                        start_event.get("start", {}), state
                    ),
                },
            )
        ]

    def _handle_content_block_delta(
        self, delta_event: dict[str, Any], state: StreamState
    ) -> list[SSEEvent]:
        events: list[SSEEvent] = []
        content_index = self._resolve_content_block_index(delta_event, state)
        if content_index not in state.started_blocks:
            content_block = self._convert_stream_content_block_start_from_delta(
                delta_event.get("delta", {}), state
            )
            if content_block is not None:
                state.started_blocks.add(content_index)
                events.append(
                    SSEEvent(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": content_index,
                            "content_block": content_block,
                        },
                    )
                )

        delta = self._convert_stream_content_block_delta(delta_event.get("delta", {}), state)
        if delta is not None:
            events.append(
                SSEEvent(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": content_index,
                        "delta": delta,
                    },
                )
            )
        return events

    def _handle_content_block_stop(
        self, stop_event: dict[str, Any], state: StreamState
    ) -> list[SSEEvent]:
        content_index = self._resolve_content_block_index(stop_event, state)
        state.started_blocks.discard(content_index)
        state.content_index = content_index + 1
        return [
            SSEEvent(
                "content_block_stop",
                {"type": "content_block_stop", "index": content_index},
            )
        ]

    def _handle_message_stop(self, event: dict[str, Any], state: StreamState) -> None:
        state.saw_message_stop = True
        stop_event = event["messageStop"]
        if isinstance(stop_event, dict):
            state.stop_reason = self._map_stop_reason(stop_event.get("stopReason"))
        elif event.get("stopReason") is not None:
            state.stop_reason = self._map_stop_reason(event.get("stopReason"))

    def _handle_metadata(self, metadata_event: dict[str, Any], state: StreamState) -> None:
        state.pending_usage = self.extract_usage(metadata_event)

    @staticmethod
    def _restore_tool_name(
        name: str | None, tool_name_map: dict[str, str] | None
    ) -> str | None:
        if name is not None and tool_name_map:
            return tool_name_map.get(name, name)
        return name

    def _convert_stream_content_block_start(
        self, start: dict[str, Any], state: StreamState
    ) -> dict[str, Any]:
        tool_use = start.get("toolUse")
        if isinstance(tool_use, dict):
            state.saw_tool_use = True
            return {
                "type": "tool_use",
                "id": tool_use.get("toolUseId"),
                "name": self._restore_tool_name(tool_use.get("name"), state.tool_name_map),
                "input": {},
            }

        reasoning_content = start.get("reasoningContent")
        if isinstance(reasoning_content, dict):
            thinking_block = self._convert_reasoning_content_start(reasoning_content)
            if thinking_block is not None:
                state.saw_reasoning = True
                return thinking_block

        return {"type": "text", "text": ""}

    def _convert_stream_content_block_delta(
        self, delta: dict[str, Any], state: StreamState
    ) -> dict[str, Any] | None:
        text = delta.get("text")
        if isinstance(text, str):
            state.text_chunks.append(text)
            return {"type": "text_delta", "text": text}

        tool_use = delta.get("toolUse")
        if isinstance(tool_use, dict):
            state.saw_tool_use = True
            partial_json = self._stringify_partial_json(tool_use.get("input", ""))
            if partial_json == "":
                return None
            return {
                "type": "input_json_delta",
                "partial_json": partial_json,
            }

        reasoning_content = delta.get("reasoningContent")
        if isinstance(reasoning_content, dict):
            reasoning_delta = self._convert_reasoning_content_delta(reasoning_content)
            if reasoning_delta is not None:
                state.saw_reasoning = True
                return reasoning_delta

        return None

    def _convert_reasoning_content_start(
        self, reasoning_content: dict[str, Any]
    ) -> dict[str, Any] | None:
        if "redactedContent" in reasoning_content:
            return {
                "type": "redacted_thinking",
                "data": reasoning_content.get("redactedContent", ""),
            }

        source, from_wrapped_reasoning_text = self._resolve_reasoning_source(reasoning_content)
        if not from_wrapped_reasoning_text and "text" not in source and "signature" not in source:
            return None
        return self._build_thinking_block(source)

    def _convert_stream_content_block_start_from_delta(
        self, delta: dict[str, Any], state: StreamState
    ) -> dict[str, Any] | None:
        tool_use = delta.get("toolUse")
        if isinstance(tool_use, dict):
            partial_json = self._stringify_partial_json(tool_use.get("input", ""))
            if not tool_use.get("toolUseId") and not tool_use.get("name") and partial_json == "":
                return None
            state.saw_tool_use = True
            return {
                "type": "tool_use",
                "id": tool_use.get("toolUseId"),
                "name": self._restore_tool_name(tool_use.get("name"), state.tool_name_map),
                "input": {},
            }

        reasoning_content = delta.get("reasoningContent")
        if isinstance(reasoning_content, dict):
            thinking_block = self._convert_reasoning_content_start(reasoning_content)
            if thinking_block is not None:
                state.saw_reasoning = True
                return thinking_block

        if isinstance(delta.get("text"), str):
            return {"type": "text", "text": ""}

        return None

    def _convert_reasoning_content_delta(
        self, reasoning_content: dict[str, Any]
    ) -> dict[str, Any] | None:
        source, _ = self._resolve_reasoning_source(reasoning_content)
        if isinstance(source.get("text"), str):
            return {
                "type": "thinking_delta",
                "thinking": source["text"],
            }

        if isinstance(source.get("signature"), str):
            return {
                "type": "signature_delta",
                "signature": source["signature"],
            }

        return None

    def _resolve_reasoning_source(
        self, reasoning_content: dict[str, Any]
    ) -> tuple[dict[str, Any], bool]:
        reasoning_text = reasoning_content.get("reasoningText")
        if isinstance(reasoning_text, dict):
            return reasoning_text, True
        return reasoning_content, False

    def _build_thinking_block(self, source: dict[str, Any]) -> dict[str, Any]:
        block: dict[str, Any] = {
            "type": "thinking",
            "thinking": source.get("text", ""),
        }
        signature = source.get("signature")
        if signature is not None:
            block["signature"] = signature
        return block

    def _emit_message_termination_events(self, state: StreamState) -> list[SSEEvent]:
        if (
            state.pending_usage is None
            or not state.saw_message_stop
            or state.message_delta_emitted
        ):
            return []

        usage = state.pending_usage
        state.message_delta_emitted = True
        state.message_stop_emitted = True
        return [
            SSEEvent(
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": state.stop_reason or usage.stop_reason},
                    "usage": {
                        "input_tokens": usage.input_tokens,
                        "output_tokens": usage.output_tokens,
                        "cache_creation_input_tokens": usage.cached_write_tokens,
                        "cache_read_input_tokens": usage.cached_read_tokens,
                    },
                },
            ),
            SSEEvent("message_stop", {"type": "message_stop"}),
        ]

    def _stringify_partial_json(self, partial_json: Any) -> str:
        if isinstance(partial_json, str):
            return partial_json
        return json.dumps(partial_json, separators=(",", ":"))

    def _record_unhandled_event_types(
        self, event: dict[str, Any], state: StreamState
    ) -> None:
        if "contentBlockDelta" in event:
            delta = event["contentBlockDelta"].get("delta", {})
            known_delta_keys = {"text", "toolUse", "reasoningContent"}
            extra_delta_keys = [key for key in delta if key not in known_delta_keys]
            if extra_delta_keys:
                self._record_unhandled_event(
                    state, f"contentBlockDelta:{','.join(sorted(extra_delta_keys))}"
                )

        known_top_level_keys = {
            "messageStart",
            "contentBlockStart",
            "contentBlockDelta",
            "contentBlockStop",
            "messageStop",
            "metadata",
        }
        extra_top_level_keys = [key for key in event if key not in known_top_level_keys]
        if extra_top_level_keys:
            self._record_unhandled_event(
                state, f"event:{','.join(sorted(extra_top_level_keys))}"
            )

    def _record_unhandled_event(self, state: StreamState, event_type: str) -> None:
        state.unhandled_event_types[event_type] = (
            state.unhandled_event_types.get(event_type, 0) + 1
        )

    def _normalize_stream_event(self, event: dict[str, Any]) -> dict[str, Any]:
        if "p" not in event:
            return event
        if any(
            key in event
            for key in (
                "messageStart",
                "contentBlockStart",
                "contentBlockDelta",
                "contentBlockStop",
                "messageStop",
                "metadata",
            )
        ):
            return event
        if "role" in event and "contentBlockIndex" not in event and "delta" not in event:
            return {"messageStart": {"role": event.get("role")}}
        if "contentBlockIndex" in event and "start" in event:
            return {
                "contentBlockStart": {
                    "contentBlockIndex": event.get("contentBlockIndex", 0),
                    "start": event.get("start", {}),
                }
            }
        if "contentBlockIndex" in event and "delta" in event:
            return {
                "contentBlockDelta": {
                    "contentBlockIndex": event.get("contentBlockIndex", 0),
                    "delta": event.get("delta", {}),
                }
            }
        if "contentBlockIndex" in event:
            return {
                "contentBlockStop": {
                    "contentBlockIndex": event.get("contentBlockIndex", 0),
                }
            }
        if "stopReason" in event:
            return {"messageStop": {"stopReason": event.get("stopReason")}}
        if "usage" in event or "metrics" in event:
            metadata: dict[str, Any] = {}
            if "usage" in event:
                metadata["usage"] = event.get("usage", {})
            if "metrics" in event:
                metadata["metrics"] = event.get("metrics", {})
            return {"metadata": metadata}
        return event
