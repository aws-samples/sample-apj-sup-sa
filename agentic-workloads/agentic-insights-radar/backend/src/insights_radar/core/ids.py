# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Identifiers.

UUIDv7 (RFC 9562) gives ids whose lexical order is creation order, which the graph table
relies on for "newest first" without a separate sort key. Python 3.12's ``uuid`` module has
no ``uuid7``, so it is built here from the RFC layout. Randomness comes from ``secrets``.
"""

from __future__ import annotations

import re
import secrets
import uuid

from insights_radar.core.clock import Clock, SystemClock

_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")


class IdGenerator:
    def __init__(self, clock: Clock | None = None) -> None:
        self._clock = clock or SystemClock()

    def uuid7(self) -> str:
        ms = int(self._clock.now().timestamp() * 1000) & ((1 << 48) - 1)
        rand_a = secrets.randbits(12)
        rand_b = secrets.randbits(62)
        value = (ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
        return str(uuid.UUID(int=value))

    def short(self, length: int = 10) -> str:
        """URL-safe random id for user-facing objects (sources, documents)."""
        return secrets.token_hex((length + 1) // 2)[:length]


class Slug:
    """Lower-case URL-safe identifier, 1-64 chars, no leading/trailing hyphen."""

    RESERVED = frozenset(
        {
            "admin",
            "api",
            "agent",
            "chat",
            "create",
            "docs",
            "entries",  # GET /api/radars/entries is the cross-radar entry search
            "help",
            "graph",
            "insights",
            "login",
            "logout",
            "modules",
            "new",
            "products",
            "radars",
            "radar",
            "search",
            "settings",
            "users",
        }
    )

    @staticmethod
    def is_valid(value: str) -> bool:
        return bool(_SLUG_RE.fullmatch(value))

    # "chat-" names the per-user chat ledgers (BUDGET#chat-user-...): a module slug there would share
    # them. Radars have no budget rows, so the prefix is checked only where it matters.
    RESERVED_PREFIXES = ("chat-",)

    @classmethod
    def is_reserved(cls, value: str, prefixes: bool = True) -> bool:
        return value in cls.RESERVED or (prefixes and value.startswith(cls.RESERVED_PREFIXES))

    @staticmethod
    def from_name(name: str) -> str:
        """Derive a slug from a display name (best effort; caller still validates)."""
        lowered = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        return lowered[:64].rstrip("-") or "item"
