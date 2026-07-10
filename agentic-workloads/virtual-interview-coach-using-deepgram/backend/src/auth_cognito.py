"""Cognito JWT validation (T012) — minimal for G1.

Validates the bearer access token against the Cognito user pool JWKS and enforces 18+
eligibility. For the POC the age gate is satisfied at sign-up (account is 18+ only); here we
verify the token is valid and extract the subject. JWKS keys are cached after first fetch.
"""

from __future__ import annotations

import time

import httpx
from jose import jwt
from jose.utils import base64url_decode  # noqa: F401  (kept for signature verification path)

from .config import settings

_jwks_cache: dict | None = None
_jwks_fetched_at: float = 0.0
_JWKS_TTL_S = 3600


class AuthError(Exception):
    """Raised when a token is missing, malformed, or fails validation."""


def _issuer() -> str:
    return (
        f"https://cognito-idp.{settings.cognito_region}.amazonaws.com/"
        f"{settings.cognito_user_pool_id}"
    )


async def _get_jwks() -> dict:
    global _jwks_cache, _jwks_fetched_at
    now = time.monotonic()
    if _jwks_cache is not None and (now - _jwks_fetched_at) < _JWKS_TTL_S:
        return _jwks_cache
    url = f"{_issuer()}/.well-known/jwks.json"
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        _jwks_cache = resp.json()
        _jwks_fetched_at = now
    return _jwks_cache


def _extract_bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthError("missing or malformed Authorization header")
    return authorization.split(" ", 1)[1].strip()


async def validate_token(authorization: str | None) -> str:
    """Validate the bearer token and return the Cognito subject (user_sub).

    Raises AuthError on any validation failure.
    """
    token = _extract_bearer(authorization)

    if not settings.cognito_user_pool_id:
        # Local/dev fallback: no pool configured. Accept an unverified token's `sub`
        # claim ONLY so the loop can be exercised locally. Never use in production.
        try:
            claims = jwt.get_unverified_claims(token)
        except Exception as exc:  # noqa: BLE001
            raise AuthError("invalid token (no pool configured)") from exc
        sub = claims.get("sub")
        if not sub:
            raise AuthError("token missing sub")
        return sub

    jwks = await _get_jwks()
    try:
        headers = jwt.get_unverified_header(token)
    except Exception as exc:  # noqa: BLE001
        raise AuthError("invalid token header") from exc

    kid = headers.get("kid")
    key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
    if key is None:
        raise AuthError("signing key not found")

    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=settings.cognito_app_client_id,
            issuer=_issuer(),
        )
    except Exception as exc:  # noqa: BLE001
        raise AuthError("token verification failed") from exc

    sub = claims.get("sub")
    if not sub:
        raise AuthError("token missing sub")
    return sub
