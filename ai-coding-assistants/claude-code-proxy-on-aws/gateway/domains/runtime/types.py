"""Anthropic-compatible runtime request/response types."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MessageParam(BaseModel):
    """Incoming message payload."""

    role: str
    content: str | list[dict[str, Any]]


class MessageRequest(BaseModel):
    """Anthropic-compatible runtime request."""

    model_config = ConfigDict(extra="ignore")

    model: str
    max_tokens: int = Field(gt=0)
    system: str | list[dict[str, Any]] | None = None
    messages: list[MessageParam]
    tools: list[dict[str, Any]] | None = None
    tool_choice: dict[str, Any] | None = None
    stream: bool = False
    temperature: float | None = None
    top_p: float | None = None
    stop_sequences: list[str] | None = None
    thinking: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class UsagePayload(BaseModel):
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


class MessageResponse(BaseModel):
    id: str
    type: str = "message"
    role: str = "assistant"
    model: str
    content: list[dict[str, Any]]
    stop_reason: str | None = None
    stop_sequence: str | None = None
    usage: UsagePayload


class ModelData(BaseModel):
    id: str
    family: str | None = None
    supports_streaming: bool
    supports_tools: bool
    supports_prompt_cache: bool


class ModelListResponse(BaseModel):
    data: list[ModelData]


@dataclass(slots=True)
class UsageInfo:
    """Resolved usage and upstream metadata."""

    input_tokens: int = 0
    output_tokens: int = 0
    cached_read_tokens: int = 0
    cached_write_tokens: int = 0
    stop_reason: str | None = None
    latency_ms: int | None = None
    bedrock_invocation_id: str | None = None
    cache_details: dict[str, Any] | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(slots=True)
class SSEEvent:
    """Anthropic-compatible SSE event."""

    event: str
    data: dict[str, Any]

    def encode(self) -> str:
        return f"event: {self.event}\ndata: {json.dumps(self.data, separators=(',', ':'))}\n\n"


@dataclass(slots=True)
class StreamState:
    """Per-stream converter state."""

    message_id: str = "msg_unknown"
    model_name: str = "unknown"
    message_started: bool = False
    content_index: int = 0
    saw_message_stop: bool = False
    message_stop_emitted: bool = False
    message_delta_emitted: bool = False
    stop_reason: str | None = None
    pending_usage: UsageInfo | None = None
    saw_tool_use: bool = False
    saw_reasoning: bool = False
    started_blocks: set[int] = field(default_factory=set)
    text_chunks: list[str] = field(default_factory=list)
    unhandled_event_types: dict[str, int] = field(default_factory=dict)
    tool_name_map: dict[str, str] = field(default_factory=dict)


END_OF_STREAM = object()
