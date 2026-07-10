"""Gateway orchestration service."""

from __future__ import annotations

import json
import logging

from gateway.domains.policy.context import PolicyContext
from gateway.domains.policy.engine import HandlerType
from gateway.domains.runtime.circuit_breaker import CircuitBreaker
from gateway.domains.runtime.types import (
    MessageRequest,
    MessageResponse,
    ModelData,
    ModelListResponse,
)
from gateway.domains.usage.metrics import MetricsService
from shared.exceptions import (
    AnthropicError,
    AppError,
    BedrockClientBugError,
    BedrockError,
    BedrockThrottlingError,
    BudgetExceededError,
    ModelNotAllowedError,
    TeamInactiveError,
    UserInactiveError,
)

logger = logging.getLogger(__name__)


class GatewayService:
    """Orchestrate runtime requests."""

    def __init__(
        self,
        policy_chain,
        request_converter,
        response_converter,
        bedrock_client,
        stream_processor,
        usage_service,
        session,
        model_catalog_repo,
        metrics: MetricsService | None = None,
        log_full_payloads: bool = False,
        anthropic_client=None,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:  # type: ignore[no-untyped-def]
        self._policy_chain = policy_chain
        self._request_converter = request_converter
        self._response_converter = response_converter
        self._bedrock_client = bedrock_client
        self._stream_processor = stream_processor
        self._usage_service = usage_service
        self._session = session
        self._model_catalog_repo = model_catalog_repo
        self._metrics = metrics
        self._log_full_payloads = log_full_payloads
        self._anthropic_client = anthropic_client
        self._circuit_breaker = circuit_breaker

    def _log_bedrock_error(self, context: PolicyContext, error: AppError) -> None:
        level = logging.WARNING if isinstance(error, BedrockThrottlingError) else logging.ERROR
        logger.log(
            level,
            "bedrock runtime request failed request_id=%s "
            "selected_model=%s resolved_model=%s user_id=%s error=%s",
            context.request_id,
            context.selected_model,
            context.resolved_model.bedrock_model_id if context.resolved_model else None,
            str(context.user.id) if context.user else None,
            str(error),
        )

    @staticmethod
    def _response_model_name(context: PolicyContext, fallback_model: str) -> str:
        if context.resolved_model and context.resolved_model.bedrock_model_id:
            return context.resolved_model.bedrock_model_id
        return fallback_model

    @staticmethod
    def _serialize_payload(payload: object) -> str:
        return json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))

    def _log_request_payload(self, request_id: str, request: MessageRequest) -> None:
        if not self._log_full_payloads:
            return
        logger.info(
            "runtime anthropic request payload request_id=%s payload=%s",
            request_id,
            self._serialize_payload(request.model_dump(exclude_none=True)),
        )

    def _log_bedrock_request_payload(self, request_id: str, payload: dict[str, object]) -> None:
        if not self._log_full_payloads:
            return
        logger.info(
            "runtime bedrock request payload request_id=%s payload=%s",
            request_id,
            self._serialize_payload(payload),
        )

    def _log_bedrock_response_payload(self, request_id: str, payload: dict[str, object]) -> None:
        if not self._log_full_payloads:
            return
        logger.info(
            "runtime bedrock response payload request_id=%s payload=%s",
            request_id,
            self._serialize_payload(payload),
        )

    def _can_fallback_to_anthropic(self, context: PolicyContext) -> bool:
        if self._anthropic_client is None:
            return False
        resolved = context.resolved_model
        anthropic_id = getattr(resolved, "anthropic_model_id", None) if resolved else None
        return bool(anthropic_id)

    @staticmethod
    def _breaker_key(context: PolicyContext) -> str | None:
        """Build the circuit-breaker key as (region, model).

        Keying by model as well as region keeps one model's outage or throttle
        from diverting unrelated models in the same region to 1P. Returns None
        when the resolved model has no region, in which case the breaker is
        skipped for the request.
        """
        resolved = context.resolved_model
        if resolved is None:
            return None
        region = getattr(resolved, "bedrock_region", None)
        if region is None:
            return None
        return f"{region}|{resolved.bedrock_model_id}"

    @staticmethod
    def _anthropic_fallback_payload(request: MessageRequest) -> dict:
        """Build the 1P payload, lifting inline system-role messages.

        Claude Code may inject system-role entries inside `messages`. The 1P
        Messages API (like Bedrock Converse) rejects system roles inside
        `messages` and only accepts a top-level `system`, so they are moved
        there to keep the fallback payload shape consistent with the Bedrock
        path. Order is preserved.
        """
        payload = request.model_dump(exclude_none=True)
        messages = payload.get("messages", [])
        conversation = [m for m in messages if m.get("role") != "system"]
        inline_system = [m for m in messages if m.get("role") == "system"]
        if not inline_system:
            return payload
        system_blocks: list[dict] = []
        existing = payload.get("system")
        if isinstance(existing, str):
            system_blocks.append({"type": "text", "text": existing})
        elif isinstance(existing, list):
            system_blocks.extend(existing)
        for message in inline_system:
            content = message.get("content")
            if isinstance(content, str):
                system_blocks.append({"type": "text", "text": content})
            elif isinstance(content, list):
                system_blocks.extend(content)
        payload["system"] = system_blocks
        payload["messages"] = conversation
        return payload

    async def _call_anthropic_fallback(
        self,
        request: MessageRequest,
        context: PolicyContext,
        request_id: str,
        reason: str,
    ) -> MessageResponse:
        logger.warning(
            "falling back to anthropic 1p request_id=%s reason=%s",
            request_id,
            reason,
        )
        anthropic_response = await self._anthropic_client.messages(
            self._anthropic_fallback_payload(request),
            context.resolved_model.anthropic_model_id,
        )
        self._log_bedrock_response_payload(request_id, anthropic_response)
        # 1P fallback usage is intentionally NOT recorded: cost and token usage
        # land on the Anthropic console, and this gateway only tracks Bedrock
        # spend. Skipping record_success also means budget is not decremented for
        # requests served by 1P during a Bedrock outage.
        return MessageResponse.model_validate(anthropic_response)

    async def _stream_anthropic_fallback(
        self,
        request: MessageRequest,
        context: PolicyContext,
        request_id: str,
        reason: str,
    ):
        """Yield 1P SSE bytes unchanged.

        1P emits Anthropic-native SSE, so the bytes pass through to the client
        untouched. 1P fallback usage is intentionally NOT recorded (cost lands on
        the Anthropic console; this gateway only tracks Bedrock spend), so no
        usage is parsed off the stream and budget is not decremented.
        """
        logger.warning(
            "falling back to anthropic 1p stream request_id=%s reason=%s",
            request_id,
            reason,
        )
        chunks = await self._anthropic_client.messages_stream(
            self._anthropic_fallback_payload(request),
            context.resolved_model.anthropic_model_id,
        )

        async def _generator():
            try:
                async for chunk in chunks:
                    yield chunk
            finally:
                if self._metrics:
                    self._metrics.emit_active_request_end(context)

        return _generator()

    @staticmethod
    def _inject_bedrock_request_metadata(
        request: dict[str, object], context: PolicyContext
    ) -> dict[str, object]:
        existing = request.get("requestMetadata")
        request_metadata = dict(existing) if isinstance(existing, dict) else {}
        request_metadata["request_id"] = context.request_id
        if context.user is not None and getattr(context.user, "id", None) is not None:
            request_metadata["user_id"] = str(context.user.id)
        if context.team is not None and getattr(context.team, "id", None) is not None:
            request_metadata["team_id"] = str(context.team.id)
        request["requestMetadata"] = request_metadata
        return request

    async def process_message(
        self,
        request: MessageRequest,
        api_key: str,
        request_id: str,
    ) -> MessageResponse:
        context = PolicyContext(api_key=api_key, request_id=request_id, request=request)
        self._log_request_payload(request_id, request)
        if self._metrics:
            self._metrics.emit_active_request_start(context)
        try:
            await self._policy_chain.evaluate(context)
            await self._session.commit()
            resolved = context.resolved_model
            region = getattr(resolved, "bedrock_region", None) if resolved else None
            breaker_key = self._breaker_key(context)
            breaker_open = (
                self._circuit_breaker is not None
                and breaker_key is not None
                and not self._circuit_breaker.allow_bedrock(breaker_key)
            )
            if breaker_open and self._can_fallback_to_anthropic(context):
                logger.info(
                    "bedrock circuit breaker open, skipping bedrock "
                    "request_id=%s region=%s",
                    request_id,
                    region,
                )
                return await self._call_anthropic_fallback(
                    request, context, request_id, "circuit_open"
                )

            bedrock_request = self._request_converter.convert_request(
                request,
                context.resolved_model,
                context.cache_policy,
                context.max_tokens_override,
            )
            bedrock_request = self._inject_bedrock_request_metadata(bedrock_request, context)
            self._log_bedrock_request_payload(request_id, bedrock_request)
            try:
                response = await self._bedrock_client.converse(
                    bedrock_request, context.resolved_model
                )
            except (BedrockError, BedrockThrottlingError) as bedrock_error:
                if self._circuit_breaker is not None and breaker_key is not None:
                    self._circuit_breaker.record_failure(breaker_key)
                if not self._can_fallback_to_anthropic(context):
                    raise
                self._log_bedrock_error(context, bedrock_error)
                if isinstance(bedrock_error, BedrockThrottlingError) and self._metrics:
                    self._metrics.emit_throttle(context)
                return await self._call_anthropic_fallback(
                    request, context, request_id, type(bedrock_error).__name__
                )
            if self._circuit_breaker is not None and breaker_key is not None:
                self._circuit_breaker.record_success(breaker_key)
            self._log_bedrock_response_payload(request_id, response)
            message = self._response_converter.convert_response(
                response,
                self._response_model_name(context, request.model),
            )
            usage = self._response_converter.extract_usage(response)
            await self._usage_service.record_success(context, usage)
            return message
        except AppError as error:
            if isinstance(error, BedrockThrottlingError):
                self._log_bedrock_error(context, error)
                if self._metrics:
                    self._metrics.emit_throttle(context)
            elif isinstance(error, (BedrockError, BedrockClientBugError)):
                self._log_bedrock_error(context, error)
            elif isinstance(error, AnthropicError):
                logger.error(
                    "anthropic 1p fallback failed request_id=%s error=%s",
                    request_id,
                    str(error),
                )
            if self._metrics and isinstance(
                error,
                (BudgetExceededError, ModelNotAllowedError, UserInactiveError, TeamInactiveError),
            ):
                self._metrics.emit_policy_block(context, type(error).__name__)
            if context.resolved_model and context.user and context.virtual_key:
                await self._usage_service.record_blocked_request(context, error)
            raise
        except Exception as error:
            if context.resolved_model and context.user and context.virtual_key:
                await self._usage_service.record_error(context, BedrockError(str(error)))
            raise
        finally:
            if self._metrics:
                self._metrics.emit_active_request_end(context)

    async def process_message_stream(
        self,
        request: MessageRequest,
        api_key: str,
        request_id: str,
    ):
        context = PolicyContext(api_key=api_key, request_id=request_id, request=request)
        self._log_request_payload(request_id, request)
        if self._metrics:
            self._metrics.emit_active_request_start(context)
        try:
            await self._policy_chain.evaluate(context)
            await self._session.commit()
            resolved = context.resolved_model
            region = getattr(resolved, "bedrock_region", None) if resolved else None
            breaker_key = self._breaker_key(context)
            breaker_open = (
                self._circuit_breaker is not None
                and breaker_key is not None
                and not self._circuit_breaker.allow_bedrock(breaker_key)
            )
            if breaker_open and self._can_fallback_to_anthropic(context):
                logger.info(
                    "bedrock circuit breaker open, skipping bedrock stream "
                    "request_id=%s region=%s",
                    request_id,
                    region,
                )
                return await self._stream_anthropic_fallback(
                    request, context, request_id, "circuit_open"
                )

            bedrock_request = self._request_converter.convert_request(
                request,
                context.resolved_model,
                context.cache_policy,
                context.max_tokens_override,
            )
            bedrock_request = self._inject_bedrock_request_metadata(bedrock_request, context)
            self._log_bedrock_request_payload(request_id, bedrock_request)
            try:
                bedrock_stream = await self._bedrock_client.converse_stream(
                    bedrock_request,
                    context.resolved_model,
                )
            except (BedrockError, BedrockThrottlingError) as bedrock_error:
                if self._circuit_breaker is not None and breaker_key is not None:
                    self._circuit_breaker.record_failure(breaker_key)
                if not self._can_fallback_to_anthropic(context):
                    raise
                self._log_bedrock_error(context, bedrock_error)
                if isinstance(bedrock_error, BedrockThrottlingError) and self._metrics:
                    self._metrics.emit_throttle(context)
                return await self._stream_anthropic_fallback(
                    request, context, request_id, type(bedrock_error).__name__
                )
            if self._circuit_breaker is not None and breaker_key is not None:
                self._circuit_breaker.record_success(breaker_key)
            return self._stream_processor.stream_response(
                bedrock_stream,
                context,
                on_done=(
                    lambda: self._metrics.emit_active_request_end(context)
                    if self._metrics
                    else None
                ),
            )
        except AppError as error:
            if isinstance(error, BedrockThrottlingError):
                self._log_bedrock_error(context, error)
                if self._metrics:
                    self._metrics.emit_throttle(context)
            elif isinstance(error, (BedrockError, BedrockClientBugError)):
                self._log_bedrock_error(context, error)
            elif isinstance(error, AnthropicError):
                logger.error(
                    "anthropic 1p stream fallback failed request_id=%s error=%s",
                    request_id,
                    str(error),
                )
            if self._metrics and isinstance(
                error,
                (BudgetExceededError, ModelNotAllowedError, UserInactiveError, TeamInactiveError),
            ):
                self._metrics.emit_policy_block(context, type(error).__name__)
            if self._metrics:
                self._metrics.emit_active_request_end(context)
            if context.resolved_model and context.user and context.virtual_key:
                await self._usage_service.record_blocked_request(context, error)
            raise

    async def list_models(self, api_key: str, request_id: str) -> ModelListResponse:
        request = MessageRequest(
            model="models", max_tokens=1, messages=[{"role": "user", "content": ""}]
        )
        context = PolicyContext(api_key=api_key, request_id=request_id, request=request)
        auth_handlers = self._policy_chain.get_handlers_by_type(
            [
                HandlerType.VIRTUAL_KEY,
                HandlerType.USER_STATUS,
                HandlerType.TEAM_STATUS,
            ]
        )
        for handler in auth_handlers:
            await handler.handle(context)  # type: ignore[attr-defined]
        models = await self._model_catalog_repo.get_active_list()
        return ModelListResponse(
            data=[
                ModelData(
                    id=model.canonical_name,
                    family=model.family,
                    supports_streaming=model.supports_streaming,
                    supports_tools=model.supports_tools,
                    supports_prompt_cache=model.supports_prompt_cache,
                )
                for model in models
            ]
        )
