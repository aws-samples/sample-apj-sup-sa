"""Shared exception hierarchy for the Claude Code Proxy application."""

from __future__ import annotations


class AppError(Exception):
    """Base application error."""


class AuthenticationError(AppError):
    """Authentication failed."""


class InvalidKeyError(AuthenticationError):
    """Virtual Key invalid or not found."""


class KeyRevokedError(AuthenticationError):
    """Virtual Key revoked."""


class KeyExpiredError(AuthenticationError):
    """Virtual Key expired."""


class AuthorizationError(AppError):
    """Authorization failed."""


class UserNotFoundError(AuthorizationError):
    """User not found in DB."""


class UserInactiveError(AuthorizationError):
    """User status is not ACTIVE."""


class TeamInactiveError(AuthorizationError):
    """Team status is not ACTIVE."""


class ModelNotAllowedError(AuthorizationError):
    """Model not allowed by policy."""


class BudgetExceededError(AppError):
    """Budget limit exceeded."""


class UpstreamError(AppError):
    """Upstream service error."""


class BedrockError(UpstreamError):
    """Bedrock invocation error eligible for fallback (provider-side, transient)."""


class BedrockThrottlingError(UpstreamError):
    """Bedrock throttling error eligible for fallback."""


class BedrockClientBugError(UpstreamError):
    """Bedrock rejection caused by request shape, auth, or policy.

    Not eligible for 1P fallback because the same payload would fail upstream too.
    Examples: ValidationException, AccessDeniedException, ResourceNotFoundException.
    """


class AnthropicError(UpstreamError):
    """Anthropic 1P invocation error."""


class AnthropicThrottlingError(UpstreamError):
    """Anthropic 1P throttling error."""


class ValidationError(AppError):
    """Request validation error."""


class InternalError(AppError):
    """Internal server error."""
