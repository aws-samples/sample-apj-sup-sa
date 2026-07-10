"""Credential & client brokering for both transports.

* InvokeModel: a per-region ``bedrock-runtime`` boto3 client built from the
  configured named profile.
* Mantle: a short-lived **bearer token** minted from the same profile's IAM
  credentials via ``aws-bedrock-token-generator``, cached per region and
  refreshed before expiry.

We deliberately mint the token from the profile's *frozen* credentials rather
than mutating ``os.environ`` (which would leak into the whole process and race
across threads).
"""

from __future__ import annotations

import threading
import time
from typing import Any

import boto3
from botocore.config import Config

# 12h is the max token lifetime; refresh well before that. A benchmark run is
# ~1h, so a single mint would suffice, but refresh keeps long/retried runs safe.
_TOKEN_TTL_SECONDS = 11 * 3600
_MANTLE_HOST = "bedrock-mantle.{region}.api.aws"


class AuthBroker:
    """Thread-safe provider of boto3 clients and Mantle bearer tokens.

    A single instance is shared across the scheduler's worker threads.
    """

    def __init__(
        self,
        profile: str | None = None,
        max_attempts: int = 1,
        max_pool_connections: int = 128,
    ):
        # profile=None lets boto3 resolve via the default credential chain.
        # max_attempts=1 keeps latency measurements honest: botocore retries
        # would silently fold a throttle+retry into one slow sample. The
        # scheduler handles pacing so we should not be throttled anyway.
        self._session = boto3.Session(profile_name=profile)
        self._profile = profile
        self._client_config = Config(
            retries={"max_attempts": max_attempts, "mode": "standard"},
            # Generous client-side timeouts; the scheduler enforces the real cap.
            read_timeout=180,
            connect_timeout=15,
            # All invoke domains in a region share one client; each round fires
            # many requests near-simultaneously. The default pool (10) overflows
            # and recreates connections, adding latency that would distort the
            # measurement — so size the pool to cover the concurrency.
            max_pool_connections=max_pool_connections,
        )
        # boto3 clients are dynamically generated, so there is no static type to
        # annotate them with; Any is the honest annotation here.
        self._clients: dict[str, Any] = {}
        self._tokens: dict[str, tuple[str, float]] = {}  # region -> (token, expiry_epoch)
        self._lock = threading.Lock()
        self._token_gen: Any = None  # lazily imported BedrockTokenGenerator

    # --- InvokeModel -------------------------------------------------------
    def bedrock_runtime(self, region: str) -> Any:
        """Return a cached ``bedrock-runtime`` client for ``region``."""
        with self._lock:
            client = self._clients.get(region)
            if client is None:
                client = self._session.client(
                    "bedrock-runtime", region_name=region, config=self._client_config
                )
                self._clients[region] = client
            return client

    # --- Mantle ------------------------------------------------------------
    def mantle_base_url(self, region: str) -> str:
        return f"https://{_MANTLE_HOST.format(region=region)}/v1"

    def mantle_token(self, region: str, *, force: bool = False) -> str:
        """A valid Mantle bearer token for ``region``, minting/refreshing as needed."""
        now = time.time()
        with self._lock:
            cached = self._tokens.get(region)
            if cached and not force and cached[1] > now:
                return cached[0]
            token = self._mint_token(region)
            self._tokens[region] = (token, now + _TOKEN_TTL_SECONDS)
            return token

    def _mint_token(self, region: str) -> str:
        from aws_bedrock_token_generator import BedrockTokenGenerator

        if self._token_gen is None:
            self._token_gen = BedrockTokenGenerator()
        creds = self._session.get_credentials()
        if creds is None:
            raise RuntimeError(f"No AWS credentials resolved for profile {self._profile!r}")
        # Frozen snapshot avoids a refresh mid-sign for assumed-role creds.
        return self._token_gen.get_token(creds.get_frozen_credentials(), region)

    def account_id(self) -> str:
        """Best-effort caller account id (for stamping into results)."""
        try:
            return self._session.client("sts").get_caller_identity()["Account"]
        except Exception:  # pragma: no cover - identity is non-essential
            return "unknown"
