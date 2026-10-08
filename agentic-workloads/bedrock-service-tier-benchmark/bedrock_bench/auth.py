"""Credential and client brokering.

* ``bedrock-runtime`` boto3 clients per region (SigV4), retries disabled so a
  throttle is recorded as one error rather than a silently slow sample.
* Short-lived Bedrock **bearer tokens** for the OpenAI-compatible and Messages
  APIs, minted from the resolved IAM credentials with
  ``aws-bedrock-token-generator``, cached per region, refreshed in memory and
  never written to disk or logged.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import boto3
from botocore.config import Config

# Tokens live up to 12 h; refresh well before that.
_TOKEN_TTL_SECONDS = 11 * 3600
#: Re-mint this long before temporary credentials expire.
_CRED_MARGIN_SECONDS = 300


class AuthBroker:
    """Thread-safe provider of boto3 clients and bearer tokens."""

    def __init__(
        self,
        profile: str | None = None,
        max_pool_connections: int = 128,
        read_timeout: float = 630,
    ):
        self._session = boto3.Session(profile_name=profile)
        self._profile = profile
        self._config = Config(
            retries={"max_attempts": 1, "mode": "standard"},
            # Must exceed the longest tier timeout: botocore would otherwise cut a
            # queued flex request before the benchmark's own deadline does.
            read_timeout=read_timeout,
            connect_timeout=15,
            max_pool_connections=max_pool_connections,
        )
        self._clients: dict[tuple[str, str], Any] = {}
        self._tokens: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()
        self._gen: Any = None

    def client(self, service: str, region: str) -> Any:
        """Cached boto3 client for ``service`` in ``region``."""
        with self._lock:
            key = (service, region)
            if key not in self._clients:
                self._clients[key] = self._session.client(
                    service, region_name=region, config=self._config
                )
            return self._clients[key]

    def bedrock_runtime(self, region: str) -> Any:
        return self.client("bedrock-runtime", region)

    def token(self, region: str, *, force: bool = False) -> str:
        """A valid bearer token for ``region``, minting or refreshing as needed.

        A token minted from temporary credentials (SSO, assumed role) stops working
        when those credentials expire, so the cache lifetime is the earlier of
        11 hours and the credentials' own expiry minus a safety margin.
        """
        now = time.time()
        with self._lock:
            cached = self._tokens.get(region)
            if cached and not force and cached[1] > now:
                return cached[0]
            from aws_bedrock_token_generator import BedrockTokenGenerator

            if self._gen is None:
                self._gen = BedrockTokenGenerator()
            creds = self._session.get_credentials()
            if creds is None:
                raise RuntimeError("No AWS credentials resolved (check profile / environment)")
            frozen = creds.get_frozen_credentials()  # refreshes temporary credentials if due
            tok = self._gen.get_token(frozen, region)
            expiry = now + _TOKEN_TTL_SECONDS
            cred_expiry = getattr(creds, "_expiry_time", None)
            if cred_expiry is not None:
                expiry = min(expiry, cred_expiry.timestamp() - _CRED_MARGIN_SECONDS)
            self._tokens[region] = (tok, max(expiry, now + 60))
            return tok

    def token_provider(self, region: str) -> Callable[[], str]:
        return lambda: self.token(region)

    def account_id(self) -> str:
        """Best-effort caller account id (for run metadata)."""
        try:
            return self._session.client("sts").get_caller_identity()["Account"]
        except Exception:  # pragma: no cover - identity is non-essential
            return "unknown"
