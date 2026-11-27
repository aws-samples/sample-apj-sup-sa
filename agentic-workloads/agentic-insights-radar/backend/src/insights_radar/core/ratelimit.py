# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Per-user fixed-window rate limit for routes that spend money on every call.

Search, insight queries and product ask each call Bedrock (embeddings or Knowledge Base
retrieval) with no budget of their own, so one viewer's script could spend without limit. Each
call adds one to a per-user, per-minute counter item in the ops table (one atomic update,
expired by the table's TTL); past the limit the caller gets a 429 with ``Retry-After``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from botocore.exceptions import ClientError

from insights_radar.core.clock import Clock, SystemClock
from insights_radar.core.errors import RateLimitedError

WINDOW_SECONDS = 60
DEFAULT_PER_MINUTE = 60
# The whole deployment's ceiling for a bucket: N viewers could each spend their own per-minute
# allowance, so the bill scaled with the number of signed-in people and nothing capped the total.
DEFAULT_WORKSPACE_PER_MINUTE = 600
WORKSPACE = "*"
log = logging.getLogger(__name__)


class RateLimiter:
    def __init__(
        self,
        table: Any,
        clock: Clock | None = None,
        per_minute: int | None = None,
        workspace_per_minute: int | None = None,
    ) -> None:
        self._t = table
        self._clock = clock or SystemClock()
        self._limit = per_minute or DEFAULT_PER_MINUTE
        self._workspace = DEFAULT_WORKSPACE_PER_MINUTE if workspace_per_minute is None else workspace_per_minute

    def hit(self, subject: str, bucket: str, *, admin: bool = False, role: str = "viewer") -> int:
        """Count one call; raise ``RateLimitedError`` once ``subject`` passes the limit, or once the
        whole deployment does.

        An administrator is held to their own limit only and does not count towards the workspace's:
        a few viewers at their own allowance used to hold the shared counter at its ceiling and lock
        everyone out, administrators included, with no alarm (SR2-11). The ceiling is kept per role,
        so ten viewers at their own allowance no longer lock every editor out either (B3-054).
        Reaching the ceiling logs a ``rate.workspace_limit`` event, which the stack's alarm counts.

        The window slides: with fixed one-minute buckets a caller could spend the whole allowance at
        :59 and the whole next one at :00, twice the limit in about a second. The previous window's
        count is weighted by how much of it still overlaps now.
        """
        now = int(self._clock.now().timestamp())
        window = now - now % WINDOW_SECONDS
        count = self._count(bucket, subject, now, window)
        if count > self._limit:
            raise RateLimitedError(
                "too many requests; wait a minute and try again",
                {"limit_per_minute": self._limit, "retry_after": window + WINDOW_SECONDS - now},
            )
        if not admin and self._workspace and self._count(bucket, f"{WORKSPACE}{role}", now, window) > self._workspace:
            log.warning(
                json.dumps({"event": "rate.workspace_limit", "bucket": bucket, "role": role, "limit": self._workspace})
            )
            raise RateLimitedError(
                "the workspace is busy; retry shortly",
                {"limit_per_minute": self._workspace, "retry_after": window + WINDOW_SECONDS - now},
            )
        return count

    def _count(self, bucket: str, subject: str, now: int, window: int) -> int:
        key = f"RATE#{bucket}#{subject}"
        res = self._t.update_item(
            Key={"pk": key, "sk": str(window)},
            UpdateExpression="ADD n :one SET #ttl = :ttl",
            ExpressionAttributeNames={"#ttl": "ttl"},
            ExpressionAttributeValues={":one": 1, ":ttl": window + 2 * WINDOW_SECONDS},
            ReturnValues="UPDATED_NEW",
        )
        current = int(res["Attributes"]["n"])
        previous = self._t.get_item(Key={"pk": key, "sk": str(window - WINDOW_SECONDS)}).get("Item") or {}
        overlap = (WINDOW_SECONDS - (now - window)) / WINDOW_SECONDS
        return current + int(int(previous.get("n", 0)) * overlap)


DAY_SECONDS = 86_400


class DailyQuota:
    """A per-user, per-UTC-day allowance (drafts): one atomic counter item per user and day, so a busy
    day cannot push a user's earlier requests out of view, and the 429 says when the day ends."""

    def __init__(self, table: Any, clock: Clock | None = None) -> None:
        self._t = table
        self._clock = clock or SystemClock()

    def _key(self, subject: str, bucket: str) -> dict[str, str]:
        return {"pk": f"QUOTA#{bucket}#{subject}", "sk": self._clock.now().strftime("%Y-%m-%d")}

    def take(self, subject: str, bucket: str, limit: int, message: str) -> str:
        """Count one use; raise ``RateLimitedError`` (Retry-After: until UTC midnight) once ``limit`` is
        used. Returns the day the use was counted against, so it is given back on that same day."""
        now = int(self._clock.now().timestamp())
        key = self._key(subject, bucket)
        try:
            self._t.update_item(
                Key=key,
                UpdateExpression="ADD n :one SET #ttl = :ttl",
                ConditionExpression="attribute_not_exists(n) OR n < :limit",
                ExpressionAttributeNames={"#ttl": "ttl"},
                ExpressionAttributeValues={":one": 1, ":limit": limit, ":ttl": now + 2 * DAY_SECONDS},
            )
        except ClientError as err:
            if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            raise RateLimitedError(message, {"limit": limit, "retry_after": DAY_SECONDS - now % DAY_SECONDS}) from err
        return key["sk"]

    def give_back(self, subject: str, bucket: str, day: str) -> None:
        """Undo a ``take`` whose request did not go ahead, on the day it was counted against.

        The day used to come from the clock at give-back time, so a take at 23:59:59 whose start
        failed after midnight decremented the *next* day's counter to -1 (an extra draft that day)
        and left a counter item with no TTL. Conditioned on there being something to give back, and
        it writes the TTL.
        """
        try:
            self._t.update_item(
                Key={"pk": f"QUOTA#{bucket}#{subject}", "sk": day},
                UpdateExpression="ADD n :minus SET #ttl = if_not_exists(#ttl, :ttl)",
                ConditionExpression="n > :zero",
                ExpressionAttributeNames={"#ttl": "ttl"},
                ExpressionAttributeValues={
                    ":minus": -1,
                    ":zero": 0,
                    ":ttl": int(self._clock.now().timestamp()) + 2 * DAY_SECONDS,
                },
            )
        except ClientError as err:
            if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
