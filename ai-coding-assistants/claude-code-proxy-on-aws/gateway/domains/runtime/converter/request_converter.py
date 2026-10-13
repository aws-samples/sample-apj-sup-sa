"""Anthropic-to-Bedrock request conversion."""

from __future__ import annotations

import base64
import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from gateway.domains.runtime.types import MessageRequest
from shared.exceptions import ValidationError
from shared.models import ModelCatalog

MIN_BEDROCK_THINKING_BUDGET_TOKENS = 1024
MIN_REQUEST_MAX_TOKENS_FOR_FIXED_THINKING = MIN_BEDROCK_THINKING_BUDGET_TOKENS + 1
FIXED_THINKING_RESERVED_OUTPUT_TOKENS = 1
DEFAULT_FIXED_THINKING_BUDGET_TOKENS = MIN_BEDROCK_THINKING_BUDGET_TOKENS
MAX_CACHE_POINTS_PER_REQUEST = 4
ADAPTIVE_THINKING_MODEL_FAMILIES = frozenset(
    {"claude-opus-4-6", "claude-opus-4-8", "claude-sonnet-4-6"}
)
BEDROCK_TOOL_RESULT_CONTENT_KEYS = {
    "document",
    "image",
    "json",
    "searchResult",
    "text",
    "video",
}
IMAGE_MEDIA_TYPE_TO_BEDROCK_FORMAT = {
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/gif": "gif",
    "image/webp": "webp",
}
DOCUMENT_MEDIA_TYPE_TO_BEDROCK_FORMAT = {
    "application/pdf": "pdf",
    "text/csv": "csv",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.ms-excel": "xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "text/html": "html",
    "text/plain": "txt",
    "text/markdown": "md",
}


@dataclass(slots=True)
class _CachePointCounter:
    count: int = 0

    def can_insert(self) -> bool:
        return self.count < MAX_CACHE_POINTS_PER_REQUEST

    def increment(self) -> None:
        self.count += 1


class AnthropicToBedrockConverter:
    """Convert public runtime requests to Bedrock Converse payloads."""

    def convert_request(
        self,
        anthropic_req: MessageRequest,
        resolved_model: ModelCatalog,
        cache_policy: str,
        max_tokens_override: int | None = None,
    ) -> dict[str, Any]:
        cache_counter = _CachePointCounter()
        effective_max_tokens = (
            max_tokens_override if max_tokens_override is not None else anthropic_req.max_tokens
        )
        effective_thinking = anthropic_req.thinking
        if self._should_drop_thinking_from_history(anthropic_req.messages, effective_thinking):
            effective_thinking = None

        effective_tool_choice = anthropic_req.tool_choice
        if effective_thinking is not None:
            effective_tool_choice = self._normalize_tool_choice_for_thinking(
                effective_tool_choice
            )

        # Bedrock Converse only accepts user/assistant turns; any system-role
        # message in the conversation (e.g. Claude Code injecting skill context
        # mid-conversation) must be lifted into the top-level system field, or
        # Bedrock rejects the request (conversation must end with a user turn).
        conversation, inline_system = self._split_system_messages(anthropic_req.messages)

        request = {
            "modelId": resolved_model.bedrock_model_id,
            "messages": self.convert_messages(conversation, cache_policy, cache_counter),
            "inferenceConfig": {
                "maxTokens": effective_max_tokens,
            },
        }
        if anthropic_req.temperature is not None:
            request["inferenceConfig"]["temperature"] = anthropic_req.temperature
        if anthropic_req.top_p is not None:
            request["inferenceConfig"]["topP"] = anthropic_req.top_p
        if anthropic_req.stop_sequences:
            request["inferenceConfig"]["stopSequences"] = anthropic_req.stop_sequences
        converted_system: list[dict[str, Any]] = []
        if anthropic_req.system is not None:
            converted_system = self.convert_system(
                anthropic_req.system,
                cache_policy,
                cache_counter,
            )
        for system_message in inline_system:
            converted_system.extend(
                self.convert_system(system_message.content, cache_policy, cache_counter)
            )
        if converted_system:
            request["system"] = converted_system
        if anthropic_req.tools:
            request["toolConfig"] = self.convert_tools(
                anthropic_req.tools, effective_tool_choice, cache_policy, cache_counter
            )
        if effective_thinking:
            request["additionalModelRequestFields"] = self.convert_thinking(
                resolved_model,
                effective_thinking,
                effective_max_tokens,
            )
        return self.apply_cache_points(request, cache_policy)

    def convert_system(
        self,
        system: str | list[dict[str, Any]],
        cache_policy: str,
        cache_counter: _CachePointCounter | None = None,
    ) -> list[dict[str, Any]]:
        if isinstance(system, str):
            if self._is_blank_text_value(system):
                return []
            return [{"text": system}]
        return self._convert_blocks(system, cache_policy, cache_counter)

    @staticmethod
    def _split_system_messages(
        messages: list[Any],
    ) -> tuple[list[Any], list[Any]]:
        """Separate system-role messages from the user/assistant conversation.

        Bedrock Converse rejects system-role entries inside `messages`; they are
        returned separately so the caller can fold them into the top-level
        system field. Order is preserved for both lists.
        """
        conversation: list[Any] = []
        system_messages: list[Any] = []
        for message in messages:
            if getattr(message, "role", None) == "system":
                system_messages.append(message)
            else:
                conversation.append(message)
        return conversation, system_messages

    def convert_messages(
        self,
        messages: list[object],
        cache_policy: str,
        cache_counter: _CachePointCounter | None = None,
    ) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for message in messages:
            content = (
                message.content
                if not isinstance(message.content, str)
                else [{"type": "text", "text": message.content}]
            )
            converted_content = self._deduplicate_bedrock_content_ids(
                self._convert_blocks(content, cache_policy, cache_counter)
            )
            if not converted_content:
                continue
            converted.append(
                {
                    "role": message.role,
                    "content": converted_content,
                }
            )
        return converted

    def convert_tools(
        self,
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any] | None,
        cache_policy: str,
        cache_counter: _CachePointCounter | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "tools": self._convert_tools_with_cache_points(tools, cache_policy, cache_counter),
        }
        if tool_choice is not None:
            payload["toolChoice"] = self._convert_tool_choice(tool_choice)
        return payload

    def convert_thinking(
        self,
        resolved_model: ModelCatalog,
        thinking: dict[str, Any] | None,
        max_tokens: int,
    ) -> dict[str, Any] | None:
        if thinking is None:
            return None
        return {"thinking": self._normalize_thinking(resolved_model, thinking, max_tokens)}

    def apply_cache_points(self, request: dict[str, Any], cache_policy: str) -> dict[str, Any]:
        return request

    def _build_cache_point(self, cache_policy: str) -> dict[str, Any]:
        cache_point: dict[str, Any] = {"type": "default"}
        if cache_policy in {"5m", "1h"}:
            cache_point["ttl"] = cache_policy
        return {"cachePoint": cache_point}

    def _convert_blocks(
        self,
        blocks: list[dict[str, Any]],
        cache_policy: str,
        cache_counter: _CachePointCounter | None = None,
    ) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for block in blocks:
            if self._should_skip_content_block(block):
                continue
            converted.append(self._convert_block(block))
            if self._should_insert_cache_point(block, cache_policy, cache_counter):
                converted.append(self._build_cache_point(cache_policy))
                if cache_counter is not None:
                    cache_counter.increment()
        return converted

    def _convert_tools_with_cache_points(
        self,
        tools: list[dict[str, Any]],
        cache_policy: str,
        cache_counter: _CachePointCounter | None = None,
    ) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for tool in tools:
            converted.append(self._convert_tool(tool))
            if self._should_insert_cache_point(tool, cache_policy, cache_counter):
                converted.append(self._build_cache_point(cache_policy))
                if cache_counter is not None:
                    cache_counter.increment()
        return converted

    def _convert_tool(self, tool: dict[str, Any]) -> dict[str, Any]:
        input_schema = self._normalize_tool_input_schema(
            tool.get("input_schema"),
            tool_name=tool["name"],
        )
        tool_spec: dict[str, Any] = {
            "name": tool["name"],
            "inputSchema": {"json": input_schema},
        }
        description = tool.get("description")
        if description:
            tool_spec["description"] = description
        if "strict" in tool:
            tool_spec["strict"] = bool(tool["strict"])
        return {"toolSpec": tool_spec}

    def _convert_tool_choice(self, tool_choice: dict[str, Any]) -> dict[str, Any]:
        choice_type = tool_choice.get("type")
        if choice_type == "auto":
            return {"auto": {}}
        if choice_type == "any":
            return {"any": {}}
        if choice_type == "tool":
            return {"tool": {"name": tool_choice["name"]}}
        return deepcopy(tool_choice)

    def _normalize_thinking(
        self,
        resolved_model: ModelCatalog,
        thinking: dict[str, Any],
        max_tokens: int,
    ) -> dict[str, Any]:
        normalized = self._normalize_thinking_keys(thinking)
        if self._supports_adaptive_thinking(resolved_model):
            return self._normalize_adaptive_thinking(normalized)
        if normalized.get("type") in {"enabled", "adaptive"}:
            return self._normalize_fixed_budget_thinking(normalized, max_tokens)
        self._clamp_thinking_budget(normalized)
        return normalized

    def _normalize_adaptive_thinking(self, thinking: dict[str, Any]) -> dict[str, Any]:
        thinking_type = thinking.get("type")
        if thinking_type in {"adaptive", "enabled"}:
            return {"type": "adaptive"}
        return thinking

    def _normalize_fixed_budget_thinking(
        self,
        thinking: dict[str, Any],
        max_tokens: int,
    ) -> dict[str, Any]:
        if max_tokens < MIN_REQUEST_MAX_TOKENS_FOR_FIXED_THINKING:
            raise ValidationError(
                "Extended thinking requires max_tokens greater than 1024 for Bedrock Claude models."
            )

        budget = thinking.get("budget_tokens")
        if not isinstance(budget, int):
            budget = DEFAULT_FIXED_THINKING_BUDGET_TOKENS

        budget = max(budget, MIN_BEDROCK_THINKING_BUDGET_TOKENS)
        max_budget_tokens = max_tokens - FIXED_THINKING_RESERVED_OUTPUT_TOKENS
        if budget > max_budget_tokens:
            budget = max_budget_tokens

        return {"type": "enabled", "budget_tokens": budget}

    def _supports_adaptive_thinking(self, resolved_model: ModelCatalog) -> bool:
        family = getattr(resolved_model, "family", None)
        if family in ADAPTIVE_THINKING_MODEL_FAMILIES:
            return True

        canonical_name = getattr(resolved_model, "canonical_name", None)
        if canonical_name in ADAPTIVE_THINKING_MODEL_FAMILIES:
            return True

        bedrock_model_id = getattr(resolved_model, "bedrock_model_id", "")
        return any(
            model_family in bedrock_model_id
            for model_family in ADAPTIVE_THINKING_MODEL_FAMILIES
        )

    def _normalize_thinking_keys(self, thinking: dict[str, Any]) -> dict[str, Any]:
        normalized = deepcopy(thinking)
        if "budgetTokens" in normalized and "budget_tokens" not in normalized:
            normalized["budget_tokens"] = normalized.pop("budgetTokens")
        elif "budgetTokens" in normalized and "budget_tokens" in normalized:
            normalized.pop("budgetTokens")
        return normalized

    def _clamp_thinking_budget(self, thinking: dict[str, Any]) -> None:
        budget = thinking.get("budget_tokens")
        if isinstance(budget, int) and budget < MIN_BEDROCK_THINKING_BUDGET_TOKENS:
            thinking["budget_tokens"] = MIN_BEDROCK_THINKING_BUDGET_TOKENS

    def _normalize_tool_input_schema(
        self,
        input_schema: Any,
        *,
        tool_name: str,
    ) -> dict[str, Any]:
        if input_schema is None:
            return {"type": "object", "properties": {}}

        if not isinstance(input_schema, dict):
            raise ValidationError(
                f"Tool '{tool_name}' input_schema must be a JSON schema object."
            )

        schema = deepcopy(input_schema)
        schema_type = schema.get("type")

        if schema_type == "object":
            return schema

        if isinstance(schema_type, list):
            if "object" in schema_type:
                schema["type"] = "object"
                return schema
            raise ValidationError(
                f"Tool '{tool_name}' input_schema type must include 'object' for Bedrock Converse."
            )

        if schema_type is None:
            schema["type"] = "object"
            schema.setdefault("properties", {})
            return schema

        raise ValidationError(
            f"Tool '{tool_name}' input_schema type must be 'object' for Bedrock Converse."
        )

    def _convert_block(self, block: dict[str, Any]) -> dict[str, Any]:
        block_type = block.get("type")
        if block_type == "text":
            return {"text": block.get("text", "")}
        if block_type == "tool_use":
            return {
                "toolUse": {
                    "toolUseId": block.get("id"),
                    "name": block.get("name"),
                    "input": self._normalize_tool_use_input(block.get("input")),
                }
            }
        if block_type == "tool_result":
            # Anthropic marks failed tool calls with is_error; Bedrock uses status.
            status = "error" if block.get("is_error") else block.get("status", "success")
            return {
                "toolResult": {
                    "toolUseId": block.get("tool_use_id"),
                    "content": self._convert_tool_result_content(block.get("content", [])),
                    "status": status,
                }
            }
        if block_type == "image":
            return self._convert_image_block(block)
        if block_type == "document":
            return self._convert_document_block(block)
        if block_type == "thinking":
            reasoning_text: dict[str, Any] = {"text": block.get("thinking", "")}
            signature = block.get("signature")
            if signature is not None:
                reasoning_text["signature"] = signature
            return {"reasoningContent": {"reasoningText": reasoning_text}}
        if block_type == "redacted_thinking":
            return {
                "reasoningContent": {"redactedContent": deepcopy(block.get("data", ""))}
            }
        fallback = deepcopy(block)
        fallback.pop("cache_control", None)
        return fallback

    def _convert_tool_result_content(self, content: Any) -> list[dict[str, Any]]:
        if content is None:
            return []
        if isinstance(content, str):
            if self._is_blank_text_value(content):
                return []
            return [{"text": content}]
        if isinstance(content, list):
            converted = [self._convert_tool_result_content_block(block) for block in content]
            return [block for block in converted if not self._is_blank_text_content_block(block)]
        converted = self._convert_tool_result_content_block(content)
        if self._is_blank_text_content_block(converted):
            return []
        return [converted]

    def _convert_tool_result_content_block(self, block: Any) -> dict[str, Any]:
        if isinstance(block, str):
            return {"text": block}
        if isinstance(block, dict):
            if self._is_bedrock_tool_result_content_block(block):
                cleaned = deepcopy(block)
                cleaned.pop("cache_control", None)
                return cleaned

            block_type = block.get("type")
            if block_type == "text":
                return {"text": block.get("text", "")}
            if block_type == "image":
                return self._convert_image_block(block)
            if block_type == "document":
                return self._convert_document_block(block)
            if block_type == "json":
                return {"json": deepcopy(block.get("json", block.get("data", {})))}
            if block_type == "search_result":
                search_result = block.get("search_result", block.get("content"))
                if search_result is not None:
                    return {"searchResult": deepcopy(search_result)}
            return {"json": deepcopy(block)}
        return {"text": str(block)}

    def _convert_image_block(self, block: dict[str, Any]) -> dict[str, Any]:
        source = block.get("source")
        if not isinstance(source, dict):
            return {"image": source if source is not None else block}
        source_type = source.get("type")
        if source_type is None:
            # Already-Bedrock shapes ({"bytes": ...}, {"s3Location": ...}).
            return {"image": deepcopy(source)}
        if source_type != "base64":
            raise ValidationError(
                f"Unsupported image source type for Bedrock Converse: {source_type!r}"
            )
        media_type = source.get("media_type", "")
        image_format = IMAGE_MEDIA_TYPE_TO_BEDROCK_FORMAT.get(media_type)
        if image_format is None:
            raise ValidationError(
                f"Unsupported image media type for Bedrock Converse: {media_type!r}"
            )
        try:
            image_bytes = base64.b64decode(source.get("data", ""))
        except (ValueError, TypeError) as exc:
            raise ValidationError("Image data is not valid base64.") from exc
        return {"image": {"format": image_format, "source": {"bytes": image_bytes}}}

    def _convert_document_block(self, block: dict[str, Any]) -> dict[str, Any]:
        source = block.get("source")
        if not isinstance(source, dict):
            return {"document": source if source is not None else block}
        source_type = source.get("type")
        if source_type is None:
            # Already-Bedrock shapes ({"bytes": ...}, {"s3Location": ...}).
            return {"document": deepcopy(source)}
        media_type = source.get("media_type", "")
        if source_type == "base64":
            document_format = DOCUMENT_MEDIA_TYPE_TO_BEDROCK_FORMAT.get(media_type)
            if document_format is None:
                raise ValidationError(
                    f"Unsupported document media type for Bedrock Converse: {media_type!r}"
                )
            try:
                document_bytes = base64.b64decode(source.get("data", ""))
            except (ValueError, TypeError) as exc:
                raise ValidationError("Document data is not valid base64.") from exc
        elif source_type == "text":
            document_format = DOCUMENT_MEDIA_TYPE_TO_BEDROCK_FORMAT.get(media_type, "txt")
            document_bytes = str(source.get("data", "")).encode("utf-8")
        else:
            raise ValidationError(
                f"Unsupported document source type for Bedrock Converse: {source_type!r}"
            )
        return {
            "document": {
                "format": document_format,
                "name": self._sanitize_document_name(block.get("title")),
                "source": {"bytes": document_bytes},
            }
        }

    @staticmethod
    def _sanitize_document_name(title: Any) -> str:
        # Bedrock document names only allow alphanumerics, single spaces,
        # hyphens, parentheses, and square brackets.
        if not isinstance(title, str) or not title.strip():
            return "document"
        sanitized_chars = [
            ch if (ch.isalnum() or ch in " -()[]") else " " for ch in title
        ]
        sanitized = " ".join("".join(sanitized_chars).split())
        return sanitized or "document"

    def _is_bedrock_tool_result_content_block(self, block: dict[str, Any]) -> bool:
        matching_keys = [key for key in BEDROCK_TOOL_RESULT_CONTENT_KEYS if key in block]
        return len(matching_keys) == 1 and "type" not in block

    def _is_blank_text_value(self, value: Any) -> bool:
        return isinstance(value, str) and value.strip() == ""

    def _is_blank_text_content_block(self, block: dict[str, Any]) -> bool:
        return set(block.keys()) == {"text"} and self._is_blank_text_value(block.get("text"))

    def _should_skip_content_block(self, block: dict[str, Any]) -> bool:
        block_type = block.get("type")
        if block_type == "text":
            return self._is_blank_text_value(block.get("text"))
        if "type" not in block and set(block.keys()) == {"text"}:
            return self._is_blank_text_value(block.get("text"))
        return False

    def _normalize_tool_choice_for_thinking(
        self, tool_choice: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        if tool_choice is None:
            return None

        normalized = deepcopy(tool_choice)
        if normalized.get("type") in {"any", "tool"}:
            return {"type": "auto"}
        return normalized

    def _normalize_tool_use_input(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return deepcopy(value)
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return {}
            if isinstance(parsed, dict):
                return parsed
        return {}

    def _should_insert_cache_point(
        self,
        block: dict[str, Any],
        cache_policy: str,
        cache_counter: _CachePointCounter | None,
    ) -> bool:
        if cache_policy == "none":
            return False
        if cache_counter is not None and not cache_counter.can_insert():
            return False
        cache_control = block.get("cache_control")
        return isinstance(cache_control, dict) and cache_control.get("type") == "ephemeral"

    def _should_drop_thinking_from_history(
        self, messages: list[object], thinking: dict[str, Any] | None
    ) -> bool:
        """Disable request-level thinking for Bedrock tool histories that omit thinking blocks.

        Bedrock tool-use histories expect reasoning to be represented consistently across the
        conversation. When earlier assistant tool-use turns exist but none of the assistant turns
        carry thinking or redacted_thinking blocks, the runtime drops request-level thinking to
        avoid upstream validation failures.
        """
        if thinking is None:
            return False

        any_assistant_has_thinking = False
        last_assistant_with_tool_use_has_thinking: bool | None = None

        for message in messages:
            role = getattr(message, "role", None)
            content = getattr(message, "content", None)
            if role != "assistant" or isinstance(content, str) or not isinstance(content, list):
                continue

            has_tool_use = any(
                isinstance(block, dict) and block.get("type") == "tool_use"
                for block in content
            )
            has_thinking = any(
                isinstance(block, dict)
                and block.get("type") in {"thinking", "redacted_thinking"}
                for block in content
            )

            if has_thinking:
                any_assistant_has_thinking = True
            if has_tool_use:
                last_assistant_with_tool_use_has_thinking = has_thinking

        return (
            last_assistant_with_tool_use_has_thinking is False
            and not any_assistant_has_thinking
        )

    def _deduplicate_bedrock_content_ids(
        self, blocks: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        seen_tool_use_ids: set[str] = set()
        seen_tool_result_ids: set[str] = set()
        deduplicated: list[dict[str, Any]] = []

        for block in blocks:
            tool_use = block.get("toolUse")
            if isinstance(tool_use, dict):
                tool_use_id = tool_use.get("toolUseId")
                if isinstance(tool_use_id, str) and tool_use_id:
                    if tool_use_id in seen_tool_use_ids:
                        continue
                    seen_tool_use_ids.add(tool_use_id)

            tool_result = block.get("toolResult")
            if isinstance(tool_result, dict):
                tool_use_id = tool_result.get("toolUseId")
                if isinstance(tool_use_id, str) and tool_use_id:
                    if tool_use_id in seen_tool_result_ids:
                        continue
                    seen_tool_result_ids.add(tool_use_id)

            deduplicated.append(block)

        return deduplicated
