"""Streaming response processor."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator, Callable
from typing import Any

from gateway.domains.runtime.types import END_OF_STREAM, SSEEvent, StreamState, UsageInfo
from shared.exceptions import BedrockError

logger = logging.getLogger(__name__)


def next_event(iterator: Any) -> Any:
    """Read the next item from a sync Bedrock stream iterator."""

    try:
        return next(iterator)
    except StopIteration:
        return END_OF_STREAM


def close_stream(stream: Any) -> None:
    """Close a Bedrock event stream when the transport exposes a close hook."""

    close = getattr(stream, "close", None)
    if callable(close):
        close()


def extract_stream_metadata(event: dict[str, Any]) -> dict[str, Any] | None:
    """Extract canonical metadata from either standard or compact patch-style stream events."""

    metadata = event.get("metadata")
    if isinstance(metadata, dict):
        return metadata

    if "p" not in event or ("usage" not in event and "metrics" not in event):
        return None

    compact_metadata: dict[str, Any] = {}
    if "usage" in event:
        compact_metadata["usage"] = event.get("usage", {})
    if "metrics" in event:
        compact_metadata["metrics"] = event.get("metrics", {})
    return compact_metadata


class StreamProcessor:
    """Bridge Bedrock ConverseStream events into Anthropic SSE."""

    def __init__(
        self,
        converter,
        usage_service,
        metrics=None,
        log_full_payloads: bool = False,
        log_stream_events: bool = False,
    ) -> None:  # type: ignore[no-untyped-def]
        self._converter = converter
        self._usage_service = usage_service
        self._metrics = metrics
        self._log_full_payloads = log_full_payloads
        self._log_stream_events = log_stream_events

    @staticmethod
    def _response_model_name(context) -> str:  # type: ignore[no-untyped-def]
        resolved_model = getattr(context, "resolved_model", None)
        bedrock_model_id = getattr(resolved_model, "bedrock_model_id", None)
        if bedrock_model_id:
            return bedrock_model_id
        return getattr(getattr(context, "request", None), "model", "unknown")

    @staticmethod
    def _serialize_payload(payload: object) -> str:
        return json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))

    async def stream_response(
        self,
        bedrock_stream: dict[str, Any],
        context,  # type: ignore[no-untyped-def]
        on_done: Callable[[], None] | None = None,
        tool_name_map: dict[str, str] | None = None,
    ) -> AsyncGenerator[str, None]:
        state = StreamState(
            message_id=f"msg_{getattr(context, 'request_id', 'unknown')}",
            model_name=self._response_model_name(context),
            tool_name_map=tool_name_map or {},
        )
        stream = bedrock_stream["stream"]
        iterator = iter(stream)
        loop = asyncio.get_running_loop()
        usage_info: UsageInfo | None = None
        usage_persisted = False
        first_chunk_emitted = False
        t0 = time.monotonic()
        try:
            while True:
                event = await loop.run_in_executor(None, next_event, iterator)
                if event is END_OF_STREAM:
                    finalize_stream = getattr(self._converter, "finalize_stream", None)
                    if callable(finalize_stream):
                        finalized_events = finalize_stream(state)
                        if isinstance(finalized_events, list):
                            for sse in finalized_events:
                                yield sse.encode()
                        elif state.message_started and not state.message_stop_emitted:
                            yield SSEEvent("message_stop", {"type": "message_stop"}).encode()
                    elif state.message_started and not state.message_stop_emitted:
                        yield SSEEvent("message_stop", {"type": "message_stop"}).encode()
                    break
                if self._log_full_payloads and self._log_stream_events:
                    logger.info(
                        "runtime bedrock stream event payload request_id=%s payload=%s",
                        getattr(context, "request_id", "unknown"),
                        self._serialize_payload(event),
                    )
                for sse in self._converter.convert_stream_event(event, state):
                    if not first_chunk_emitted and self._metrics:
                        ttfb_ms = (time.monotonic() - t0) * 1000
                        self._metrics.emit_ttfb(context, ttfb_ms)
                        first_chunk_emitted = True
                    yield sse.encode()
                metadata = extract_stream_metadata(event)
                if metadata is not None:
                    usage_info = self._converter.extract_usage(metadata)
                    try:
                        await self._usage_service.record_success(context, usage_info)
                        usage_persisted = True
                    except Exception:
                        logger.exception("Usage persistence failed after stream metadata")
        except Exception as error:
            logger.exception("Stream error")
            if not usage_persisted:
                try:
                    await self._usage_service.record_error(
                        context,
                        BedrockError(str(error)),
                        usage_info or UsageInfo(),
                    )
                except Exception:
                    logger.exception("Usage persistence failed after stream error")
            yield SSEEvent(
                "error",
                {"type": "error", "error": {"type": "api_error", "message": str(error)}},
            ).encode()
            return
        finally:
            try:
                close_stream(stream)
            except Exception:
                logger.exception("Failed to close Bedrock stream")
            if on_done:
                on_done()
