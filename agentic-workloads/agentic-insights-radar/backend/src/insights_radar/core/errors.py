# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Typed domain errors with an HTTP mapping.

Every error the API can return is one of these. The router turns them into
``{"code", "message", "details"}`` with the class's status; anything else is a 500.
Bad input is always a 400 subclass, never a 500 (the internal radar's F-015 lesson:
500s for client mistakes pollute alarms and give the caller nothing to act on).
"""

from __future__ import annotations

from typing import Any, ClassVar


class DomainError(Exception):
    """Base class. Subclasses set ``status`` and ``code``."""

    status: ClassVar[int] = 500
    code: ClassVar[str] = "internal"
    # False for a fault only an operator can clear: no Retry-After is sent (scan cycle 44).
    retryable: ClassVar[bool] = True
    # The Retry-After a 503 sends (scan cycle 47).
    retry_after_seconds: ClassVar[int] = 1

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def to_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            body["details"] = self.details
        return body


class BadRequestError(DomainError):
    status = 400
    code = "bad_request"


class ValidationError(BadRequestError, ValueError):
    """Bad input. Also a ``ValueError`` so domain validators can be used outside the API."""

    code = "validation_failed"


class UnauthorizedError(DomainError):
    status = 401
    code = "unauthorized"


class AccountDisabledError(UnauthorizedError):
    """The account was disabled or deleted: signing in again does not help, so clients tell the user
    that instead of "your session expired"."""

    code = "account_disabled"


class NoAccountError(UnauthorizedError):
    """A signed (IAM) caller whose person has no account yet: one sign-in to the web app creates it."""

    code = "no_account"


class ForbiddenError(DomainError):
    status = 403
    code = "forbidden"


class NotFoundError(DomainError):
    status = 404
    code = "not_found"


class ConflictError(DomainError):
    status = 409
    code = "conflict"


class PayloadTooLargeError(DomainError):
    status = 413
    code = "payload_too_large"


class RateLimitedError(DomainError):
    status = 429
    code = "rate_limited"


class ServiceUnavailableError(DomainError):
    status = 503
    code = "service_unavailable"


class UnsubscribeRetryError(ServiceUnavailableError):
    """The unsubscribe key could not be read just now. Retry-After says when to try again; the message names no
    wait, because the app adds one from Retry-After (scan cycles 47 to 49)."""

    code = "unsubscribe_retry"
    retry_after_seconds = 60


class UnsubscribeUnavailableError(ServiceUnavailableError):
    """The unsubscribe key is missing or too short: only an operator can fix it, so no Retry-After is sent. A read
    refused for a lasting reason answers UnsubscribeRetryError instead, since IAM settling looks the same (scan
    cycles 43 to 50)."""

    code = "unsubscribe_unavailable"
    retryable = False


class BudgetExhaustedError(ServiceUnavailableError):
    code = "budget_exhausted"


class GenAiDisabledError(ServiceUnavailableError):
    code = "genai_disabled"
    retryable = False  # switched off by an administrator: nothing to wait for


class RunFinishedError(ConflictError):
    """A module run's tools were called after the run was finalized (for example its task timed out)."""

    code = "run_finished"


# AWS error codes that mean "busy, retry shortly": answered 503, not 500.
THROTTLING_CODES = frozenset(
    {
        "ThrottlingException",
        "Throttling",
        "TooManyRequestsException",
        "ProvisionedThroughputExceededException",
        "RequestLimitExceeded",
        "SlowDown",
    }
)
