"""Per-user invocation quota stored in DynamoDB.

DynamoDB table schema (provisioned by CloudFormation):
  PK = user_sub (string)
  SK = day (string, "YYYY-MM-DD")
  count = number (atomically incremented)
  ttl = number (epoch seconds; 8 days from write so old rows auto-purge)

In development (DEPLOY_MODE != production), this is a no-op.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from dataclasses import dataclass

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from config import CONFIG


@dataclass
class QuotaCheck:
    allowed: bool
    used: int
    limit: int
    reason: str = ""


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _table():
    return boto3.resource("dynamodb").Table(CONFIG.quota_table_name)


def check_and_increment(user_sub: str, n_invocations: int) -> QuotaCheck:
    """Atomically increment the user's daily counter, refusing if over the limit.

    `n_invocations` is the number of Bedrock calls about to be made (models * runs_per_model)
    so multi-run requests are correctly accounted for.
    """
    if not CONFIG.is_production or CONFIG.daily_invocation_limit <= 0:
        return QuotaCheck(allowed=True, used=0, limit=0)

    if not CONFIG.quota_table_name:
        # Misconfigured — fail closed in production.
        return QuotaCheck(allowed=False, used=0, limit=CONFIG.daily_invocation_limit, reason="quota table not configured")

    today = _today_utc()
    ttl = int(time.time()) + 8 * 24 * 3600
    limit = CONFIG.daily_invocation_limit

    if n_invocations <= 0:
        return QuotaCheck(allowed=False, used=0, limit=limit, reason="invalid invocation count")

    try:
        # Atomic conditional increment: only succeeds if count + n <= limit.
        # Uses DynamoDB's ADD with a ConditionExpression to prevent race conditions
        # where concurrent requests both read count=0 and both pass the check.
        _table().update_item(
            Key={"user_sub": user_sub, "day": today},
            UpdateExpression="ADD #c :n SET #t = :ttl",
            ConditionExpression="attribute_not_exists(#c) OR #c <= :max_before",
            ExpressionAttributeNames={"#c": "count", "#t": "ttl"},
            ExpressionAttributeValues={
                ":n": n_invocations,
                ":ttl": ttl,
                ":max_before": limit - n_invocations,
            },
        )
        resp = _table().get_item(Key={"user_sub": user_sub, "day": today}, ConsistentRead=True)
        current = int(resp.get("Item", {}).get("count", 0))
        return QuotaCheck(allowed=True, used=current, limit=limit)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            resp = _table().get_item(Key={"user_sub": user_sub, "day": today}, ConsistentRead=True)
            current = int(resp.get("Item", {}).get("count", 0))
            return QuotaCheck(
                allowed=False,
                used=current,
                limit=limit,
                reason=f"daily quota exhausted ({current}/{limit} used)",
            )
        return QuotaCheck(allowed=False, used=0, limit=limit, reason=f"quota service unavailable: {e}")
    except BotoCoreError as e:
        # Fail closed — don't let a DynamoDB outage become an unbounded-cost incident.
        return QuotaCheck(allowed=False, used=0, limit=limit, reason=f"quota service unavailable: {e}")


def current_usage(user_sub: str) -> tuple[int, int]:
    """Return (used_today, daily_limit). Used for sidebar display."""
    if not CONFIG.is_production or CONFIG.daily_invocation_limit <= 0 or not CONFIG.quota_table_name:
        return 0, 0
    try:
        resp = _table().get_item(Key={"user_sub": user_sub, "day": _today_utc()})
        return int(resp.get("Item", {}).get("count", 0)), CONFIG.daily_invocation_limit
    except (ClientError, BotoCoreError):
        return 0, CONFIG.daily_invocation_limit
