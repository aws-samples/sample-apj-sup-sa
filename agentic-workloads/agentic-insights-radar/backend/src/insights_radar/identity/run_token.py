# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Signed capability token for one module run.

Format: ``base64url(claims_json) "." base64url(mac)``. The MAC is HMAC-SHA-256 computed by
AWS KMS (``GenerateMac``/``VerifyMac``) so the key never leaves KMS and every signature is
auditable in CloudTrail. The token travels in a header set by the runtime's Python code; it is
never placed in the model's context, so a prompt injection cannot read or forge it.

The backend trusts the token for exactly three things: which module is running, which run it
is, and which tools that run may call. Expiry is capped at 8 hours (the AgentCore session
limit).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any, Protocol

from insights_radar.core.clock import Clock, SystemClock
from insights_radar.core.errors import ForbiddenError, UnauthorizedError

MAX_TTL_SECONDS = 8 * 3600
_SEP = "."


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


class MacProvider(Protocol):
    def generate(self, message: bytes) -> bytes: ...

    def verify(self, message: bytes, mac: bytes) -> bool: ...


class LocalHmac:
    """In-process HMAC for tests and local development only."""

    def __init__(self, key: bytes) -> None:
        if len(key) < 32:
            raise ValueError("HMAC key must be at least 32 bytes")
        self._key = key

    def generate(self, message: bytes) -> bytes:
        return hmac.new(self._key, message, hashlib.sha256).digest()

    def verify(self, message: bytes, mac: bytes) -> bool:
        return hmac.compare_digest(self.generate(message), mac)


class KmsHmac:
    """HMAC via a KMS ``HMAC_256`` key."""

    def __init__(self, kms_client: Any, key_id: str) -> None:
        self._kms = kms_client
        self._key_id = key_id

    def generate(self, message: bytes) -> bytes:
        res = self._kms.generate_mac(KeyId=self._key_id, Message=message, MacAlgorithm="HMAC_SHA_256")
        return bytes(res["Mac"])

    def verify(self, message: bytes, mac: bytes) -> bool:
        try:
            res = self._kms.verify_mac(KeyId=self._key_id, Message=message, Mac=mac, MacAlgorithm="HMAC_SHA_256")
        except self._kms.exceptions.KMSInvalidMacException:
            return False
        return bool(res.get("MacValid"))


@dataclass(frozen=True, slots=True)
class RunClaims:
    module: str
    run_id: str
    tools: frozenset[str]
    exp: int
    product_id: str | None = None

    def to_json(self) -> bytes:
        body = {
            "module": self.module,
            "run_id": self.run_id,
            "tools": sorted(self.tools),
            "exp": self.exp,
            "product_id": self.product_id,
        }
        return json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")

    @classmethod
    def from_json(cls, raw: bytes) -> RunClaims:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("claims must be an object")
        tools = data.get("tools")
        if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
            raise ValueError("tools must be a list of strings")
        module, run_id, exp = data.get("module"), data.get("run_id"), data.get("exp")
        if not isinstance(module, str) or not isinstance(run_id, str) or not isinstance(exp, int):
            raise ValueError("claims are malformed")
        product = data.get("product_id")
        return cls(module, run_id, frozenset(tools), exp, product if isinstance(product, str) else None)


class RunTokenService:
    # How long a verified token may be trusted from the cache. Without a bound, disabling or rotating
    # the KMS key left already-verified tokens working in warm containers for the token's whole life.
    CACHE_SECONDS = 300

    def __init__(self, mac: MacProvider, clock: Clock | None = None, cache_size: int = 512) -> None:
        self._mac = mac
        self._clock = clock or SystemClock()
        self._verified: dict[str, tuple[int, RunClaims]] = {}
        self._cache_size = cache_size

    def issue(
        self, module: str, run_id: str, tools: frozenset[str], ttl_seconds: int, product_id: str | None = None
    ) -> str:
        if not 0 < ttl_seconds <= MAX_TTL_SECONDS:
            raise ValueError(f"ttl must be between 1 and {MAX_TTL_SECONDS} seconds")
        exp = int(self._clock.now().timestamp()) + ttl_seconds
        claims = RunClaims(module, run_id, tools, exp, product_id)
        payload = claims.to_json()
        return _b64(payload) + _SEP + _b64(self._mac.generate(payload))

    def verify(self, token: str | None) -> RunClaims:
        if not token or token.count(_SEP) != 1 or len(token) > 8192:
            raise UnauthorizedError("missing or malformed run token")
        now = int(self._clock.now().timestamp())
        entry = self._verified.get(token)
        cached = entry[1] if entry is not None and entry[0] > now else None
        if cached is None:
            body_b64, mac_b64 = token.split(_SEP)
            try:
                payload, mac = _unb64(body_b64), _unb64(mac_b64)
            except (ValueError, TypeError) as err:
                raise UnauthorizedError("malformed run token") from err
            if not self._mac.verify(payload, mac):
                raise UnauthorizedError("run token signature is invalid")
            try:
                cached = RunClaims.from_json(payload)
            except (ValueError, json.JSONDecodeError) as err:
                raise UnauthorizedError("run token claims are invalid") from err
            if len(self._verified) >= self._cache_size:
                self._verified.clear()
            self._verified[token] = (now + self.CACHE_SECONDS, cached)
        if cached.exp <= now:
            self._verified.pop(token, None)
            raise UnauthorizedError("run token has expired")
        return cached

    def authorize_tool(self, token: str | None, tool: str) -> RunClaims:
        claims = self.verify(token)
        if tool not in claims.tools:
            raise ForbiddenError(f"module {claims.module} is not granted tool {tool}", {"tool": tool})
        return claims
