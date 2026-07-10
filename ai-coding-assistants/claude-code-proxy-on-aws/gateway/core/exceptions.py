"""FastAPI error handlers for exception types."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

class GatewayError(Exception):
    """Gateway error with HTTP response metadata."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        status_code: int,
        anthropic_type: str = "api_error",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.anthropic_type = anthropic_type
        self.retryable = retryable


from shared.exceptions import (  # noqa: E402,F401
    AnthropicError,
    AnthropicThrottlingError,
    AppError,
    AuthenticationError,
    BedrockClientBugError,
    BedrockError,
    BedrockThrottlingError,
    BudgetExceededError,
    InternalError,
    InvalidKeyError,
    KeyExpiredError,
    KeyRevokedError,
    ModelNotAllowedError,
    TeamInactiveError,
    UserInactiveError,
    UserNotFoundError,
    ValidationError,
)


class NotFoundError(GatewayError):
    """Requested resource was not found."""

    def __init__(self, message: str, *, code: str = "not_found", retryable: bool = False) -> None:
        super().__init__(
            message,
            code=code,
            status_code=404,
            anthropic_type="api_error",
            retryable=retryable,
        )


class ConflictError(GatewayError):
    """Operation conflicts with current state."""

    def __init__(self, message: str, *, code: str = "conflict", retryable: bool = False) -> None:
        super().__init__(
            message,
            code=code,
            status_code=409,
            anthropic_type="api_error",
            retryable=retryable,
        )


logger = logging.getLogger(__name__)


def _is_admin_or_auth_path(path: str) -> bool:
    return path.startswith("/v1/admin") or path.startswith("/v1/auth")


def _build_runtime_error(error: GatewayError, request_id: str) -> dict[str, Any]:
    return {
        "type": "error",
        "error": {"type": error.anthropic_type, "message": error.message},
        "request_id": request_id,
    }


def _build_admin_error(error: GatewayError, request_id: str) -> dict[str, Any]:
    return {
        "error": {
            "code": error.code,
            "message": error.message,
            "request_id": request_id,
            "retryable": error.retryable,
        }
    }


async def handle_gateway_error(request: Request, error: GatewayError) -> JSONResponse:
    """Render a gateway error for runtime or admin callers."""

    request_id = getattr(request.state, "request_id", "unknown")
    if _is_admin_or_auth_path(request.url.path):
        content = _build_admin_error(error, request_id)
    else:
        content = _build_runtime_error(error, request_id)
    return JSONResponse(status_code=error.status_code, content=content)


async def handle_shared_error(request: Request, error: Exception) -> JSONResponse:
    """Map shared exceptions to gateway errors with default status codes."""

    from shared.exceptions import (
        AnthropicError,
        AnthropicThrottlingError,
        AuthenticationError,
        BedrockClientBugError,
        BedrockError,
        BedrockThrottlingError,
        BudgetExceededError,
        InternalError,
        InvalidKeyError,
        KeyExpiredError,
        KeyRevokedError,
        ModelNotAllowedError,
        TeamInactiveError,
        UserInactiveError,
        UserNotFoundError,
        ValidationError,
    )

    is_auth_path = request.url.path.startswith("/v1/auth")
    error_type = type(error)
    if error_type is AuthenticationError:
        code, status_code, anthropic_type, retryable = (
            "authentication_failed",
            401,
            "authentication_error",
            False,
        )
    elif error_type is InvalidKeyError:
        code, status_code, anthropic_type, retryable = (
            "invalid_virtual_key",
            401,
            "authentication_error",
            False,
        )
    elif error_type is KeyRevokedError:
        code, status_code, anthropic_type, retryable = (
            "virtual_key_revoked",
            401,
            "authentication_error",
            False,
        )
    elif error_type is KeyExpiredError:
        code, status_code, anthropic_type, retryable = (
            "virtual_key_expired",
            401,
            "authentication_error",
            False,
        )
    elif error_type is UserNotFoundError:
        code, status_code, anthropic_type, retryable = (
            "user_not_synced",
            403,
            "permission_error",
            False,
        )
    elif error_type is UserInactiveError:
        code, status_code, anthropic_type, retryable = (
            "user_inactive",
            409 if is_auth_path else 403,
            "permission_error",
            False,
        )
    elif error_type is TeamInactiveError:
        code, status_code, anthropic_type, retryable = (
            "team_inactive",
            403,
            "permission_error",
            False,
        )
    elif error_type is ModelNotAllowedError:
        code, status_code, anthropic_type, retryable = (
            "model_not_allowed",
            403,
            "permission_error",
            False,
        )
    elif error_type is BudgetExceededError:
        code, status_code, anthropic_type, retryable = (
            "budget_exceeded",
            403,
            "permission_error",
            False,
        )
    elif error_type is BedrockClientBugError:
        code, status_code, anthropic_type, retryable = (
            "bedrock_request_invalid",
            400,
            "invalid_request_error",
            False,
        )
    elif error_type is BedrockError:
        code, status_code, anthropic_type, retryable = (
            "bedrock_error",
            502,
            "api_error",
            True,
        )
    elif error_type is BedrockThrottlingError:
        code, status_code, anthropic_type, retryable = (
            "bedrock_throttling",
            429,
            "rate_limit_error",
            True,
        )
    elif error_type is AnthropicError:
        code, status_code, anthropic_type, retryable = (
            "anthropic_error",
            502,
            "api_error",
            True,
        )
    elif error_type is AnthropicThrottlingError:
        code, status_code, anthropic_type, retryable = (
            "anthropic_throttling",
            429,
            "rate_limit_error",
            True,
        )
    elif error_type is ValidationError:
        code, status_code, anthropic_type, retryable = (
            "validation_error",
            400,
            "invalid_request_error",
            False,
        )
    elif error_type is InternalError:
        code, status_code, anthropic_type, retryable = (
            "internal_error",
            500,
            "api_error",
            False,
        )
    else:
        code, status_code, anthropic_type, retryable = (
            "internal_error",
            500,
            "api_error",
            False,
        )
    gateway_error = GatewayError(
        str(error),
        code=code,
        status_code=status_code,
        anthropic_type=anthropic_type,
        retryable=retryable,
    )
    return await handle_gateway_error(request, gateway_error)


async def handle_unexpected_error(request: Request, _: Exception) -> JSONResponse:
    """Render unexpected failures as internal errors."""

    request_id = getattr(request.state, "request_id", "unknown")
    logger.exception(
        "Unhandled exception request_id=%s method=%s path=%s",
        request_id,
        request.method,
        request.url.path,
    )
    gateway_error = GatewayError(
        "Internal server error",
        code="internal_error",
        status_code=500,
        anthropic_type="api_error",
        retryable=False,
    )
    return await handle_gateway_error(request, gateway_error)


def register_exception_handlers(app: FastAPI) -> None:
    """Register FastAPI exception handlers."""

    app.add_exception_handler(GatewayError, handle_gateway_error)
    app.add_exception_handler(AppError, handle_shared_error)
    app.add_exception_handler(Exception, handle_unexpected_error)
