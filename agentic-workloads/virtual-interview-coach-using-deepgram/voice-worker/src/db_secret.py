"""Rotation-proof DB password provider.

The RDS master password is RDS-managed and auto-rotates. A service that bakes the password into its
DSN at container start breaks on every NEW DB connection after a rotation until it restarts — the
report-worker's hourly retention sweep was the canary (46 consecutive InvalidAuthorizationSpecification
Error failures starting 2026-06-09, 23 min after the first rotation).

asyncpg evaluates a *callable* password on every connection attempt (connect_utils._connect_addr
re-invokes it per connect, and create_pool opens each pooled connection through that same path), so
when DB_SECRET_ARN is configured we hand asyncpg a provider that fetches the LIVE password from
Secrets Manager instead of a frozen string. A rotation is then transparent: the next connection
simply reads the new password. A short TTL cache coalesces the burst of fetches a pool open can cause
without ever holding a value long enough to matter against a 7-day rotation.

Falls back to None (use the static DB_PASSWORD / DATABASE_URL) when no secret ARN is set, so local
dev and the harness are unchanged.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Awaitable, Callable

PasswordProvider = Callable[[], Awaitable[str]]


def make_password_provider(secret_arn: str, region: str, ttl_s: float = 60.0) -> PasswordProvider:
    """Return an async callable asyncpg can use as its `password`. Fetches the secret's `password`
    field from Secrets Manager, cached for `ttl_s` so a pool-fill burst makes one call, not N. The
    boto3 call runs in a thread so it never blocks the event loop on a new-connection open."""
    cache: dict = {"pw": None, "at": 0.0}

    async def provider() -> str:
        now = time.monotonic()
        if cache["pw"] is not None and (now - cache["at"]) < ttl_s:
            return cache["pw"]
        pw = await asyncio.to_thread(_fetch, secret_arn, region)
        cache["pw"] = pw
        cache["at"] = now
        return pw

    return provider


def _fetch(secret_arn: str, region: str) -> str:
    import boto3

    client = boto3.client("secretsmanager", region_name=region)
    resp = client.get_secret_value(SecretId=secret_arn)
    return json.loads(resp["SecretString"])["password"]
