# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Daily token budget for chat: one shared ceiling plus a per-user cap.

Usage is reported only by the chat runtime over an IAM-authenticated route, never by users,
so no user can drain the shared budget by reporting fake usage. Each turn first *reserves* its
ceiling from both ledgers atomically, so parallel turns cannot together spend past a limit, then
*settles*: the hold is swapped for the real usage, once (a retried report is a no-op). A turn
whose report never arrives keeps its hold charged, so lost reports cannot make chat free.
Ledgers reuse the module budget rows (``BUDGET#chat`` and ``BUDGET#chat-user-<sub>``); ``chat`` is
a reserved slug, so no module can collide with them.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from botocore.exceptions import ClientError

from insights_radar.core.clock import Clock, SystemClock
from insights_radar.core.errors import ValidationError
from insights_radar.core.tokens import DEFAULT_DAILY_TOKENS, BudgetSnapshot, BudgetState, TokenUsage, TokenWeights
from insights_radar.modules.repository import ModuleRepository

CHAT_SLUG = "chat"
DEFAULT_PER_USER_TOKENS = 300_000
MAX_REPORT_TOKENS = 5_000_000
MAX_TURN_RESERVE = 1_000_000
TURN_TTL_SECONDS = 2 * 86400
_TURN_ID = re.compile(r"[A-Za-z0-9-]{8,64}")
log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ChatLimits:
    daily_tokens: int = DEFAULT_DAILY_TOKENS
    per_user_daily_tokens: int = DEFAULT_PER_USER_TOKENS

    def to_public(self) -> dict[str, int]:
        return {"daily_tokens": self.daily_tokens, "per_user_daily_tokens": self.per_user_daily_tokens}


class ChatBudget:
    def __init__(
        self, modules: ModuleRepository, ops_table: Any, clock: Clock | None = None, tier: str = "balanced"
    ) -> None:
        """``tier`` is the chat model's (a deploy option): usage is weighted at it, so the daily budget
        buys the same dollars whichever model chat runs on (B3-029, as B2-041 for modules)."""
        self._modules = modules
        self._t = ops_table
        self._clock = clock or SystemClock()
        self._weights = TokenWeights.for_tier(tier)

    @staticmethod
    def _user_slug(subject: str) -> str:
        # Hashed so the ledger key never carries the raw subject.
        return "chat-user-" + hashlib.sha256(subject.encode("utf-8")).hexdigest()[:24]

    def limits(self) -> ChatLimits:
        item = self._t.get_item(Key={"pk": "CHAT", "sk": "SETTINGS"}).get("Item") or {}
        return ChatLimits(
            int(item.get("daily_tokens", DEFAULT_DAILY_TOKENS)),
            int(item.get("per_user_daily_tokens", DEFAULT_PER_USER_TOKENS)),
        )

    def set_limits(self, daily_tokens: int, per_user_daily_tokens: int) -> ChatLimits:
        BudgetSnapshot.validate_limit(daily_tokens)
        BudgetSnapshot.validate_limit(per_user_daily_tokens)
        if per_user_daily_tokens > daily_tokens:
            raise ValidationError("the per-user limit cannot exceed the shared daily limit")
        self._t.put_item(
            Item={
                "pk": "CHAT",
                "sk": "SETTINGS",
                "daily_tokens": daily_tokens,
                "per_user_daily_tokens": per_user_daily_tokens,
            }
        )
        return ChatLimits(daily_tokens, per_user_daily_tokens)

    def status(self, subject: str) -> dict[str, Any]:
        limits = self.limits()
        shared = self._modules.budget(CHAT_SLUG, limits.daily_tokens)
        mine = self._modules.budget(self._user_slug(subject), limits.per_user_daily_tokens)
        closed = BudgetState.CLOSED in (shared.state, mine.state)
        return {
            "allowed": not closed,
            "shared": {"state": shared.state.value, "remaining": shared.remaining},
            "user": {"state": mine.state.value, "remaining": mine.remaining},
        }

    def charge(self, subject: str, usage: TokenUsage) -> int:
        for value in (usage.input_tokens, usage.output_tokens, usage.cache_read_tokens, usage.cache_write_tokens):
            if value > MAX_REPORT_TOKENS:
                raise ValidationError("usage report is implausibly large")
        weighted = self._modules.charge(CHAT_SLUG, usage, self._weights)
        self._modules.charge(self._user_slug(subject), usage, self._weights)
        return weighted

    @staticmethod
    def _check_turn(turn_id: str) -> None:
        if not _TURN_ID.fullmatch(turn_id):
            raise ValidationError("turn_id must be 8-64 letters, digits or hyphens")

    def reserve(self, subject: str, turn_id: str, amount: int) -> int:
        """Hold up to ``amount`` weighted tokens for one turn from both ledgers; 0 means refused."""
        self._check_turn(turn_id)
        if not 1 <= amount <= MAX_TURN_RESERVE:
            raise ValidationError(f"reserve_tokens must be 1-{MAX_TURN_RESERVE}")
        limits = self.limits()
        user = self._user_slug(subject)
        day = self._modules.reserve(CHAT_SLUG, amount, limits.daily_tokens)
        if day is None:
            return 0
        if self._modules.reserve(user, amount, limits.per_user_daily_tokens) is None:
            self._modules.release(CHAT_SLUG, amount, day)
            return 0
        try:
            self._t.put_item(
                Item={
                    "pk": f"CHATTURN#{turn_id}",
                    "sk": "HOLD",
                    "user": user,
                    "amount": amount,
                    "day": day,
                    "ttl": int(self._clock.now().timestamp()) + TURN_TTL_SECONDS,
                },
                ConditionExpression="attribute_not_exists(pk)",
            )
        except ClientError as err:
            if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            # The turn was already settled (its give-back overtook this slow reserve): hold nothing.
            self._modules.release(CHAT_SLUG, amount, day)
            self._modules.release(user, amount, day)
            return 0
        return amount

    def settle(self, subject: str, turn_id: str, usage: TokenUsage) -> int:
        """Swap a turn's hold for its real usage. Idempotent: only the first report for a turn counts.

        One conditional update does all three cases. It replaces the hold with a settled tombstone,
        which a slow ``reserve`` then bounces off (giving its hold straight back), so a retried report
        is refused by the tombstone rather than looking like a turn that was never reserved. A turn
        with no hold at all -- the runtime skipped or lost its reservation -- is still *charged*: it
        used to write a tombstone, charge nothing and make chat free whenever a report went missing.
        """
        self._check_turn(turn_id)
        try:
            previous = (
                self._t.update_item(
                    Key={"pk": f"CHATTURN#{turn_id}", "sk": "HOLD"},
                    UpdateExpression="SET settled = :t, #ttl = :ttl REMOVE amount, #d, #u",
                    # An absent item satisfies both: the tombstone is created and nothing is released.
                    ConditionExpression="attribute_not_exists(settled) AND (attribute_not_exists(#u) OR #u = :u)",
                    ExpressionAttributeNames={"#u": "user", "#d": "day", "#ttl": "ttl"},
                    ExpressionAttributeValues={
                        ":t": True,
                        ":u": self._user_slug(subject),
                        ":ttl": int(self._clock.now().timestamp()) + TURN_TTL_SECONDS,
                    },
                    ReturnValues="ALL_OLD",
                ).get("Attributes")
                or {}
            )
        except ClientError as err:
            if err.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            return 0  # already settled (a retried report), or not this user's turn
        self.charge(subject, usage)
        if "amount" not in previous:
            log.warning(json.dumps({"event": "chat.settled_without_hold", "turn": turn_id}))
            return self._weights.weigh(usage)
        amount, day = int(previous["amount"]), str(previous["day"])
        self._modules.release(CHAT_SLUG, amount, day)
        self._modules.release(str(previous["user"]), amount, day)
        return self._weights.weigh(usage)  # this turn's charge
