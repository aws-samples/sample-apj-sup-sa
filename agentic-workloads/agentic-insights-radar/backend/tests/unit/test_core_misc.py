# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from insights_radar.core import errors
from insights_radar.core.clock import FixedClock, SystemClock, iso, parse_iso
from insights_radar.core.ids import IdGenerator, Slug
from insights_radar.core.names import NameNormalizer
from insights_radar.core.tokens import (
    HARD_MAX_DAILY_TOKENS,
    BudgetSnapshot,
    BudgetState,
    TokenUsage,
    TokenWeights,
)

T0 = datetime(2026, 10, 2, 8, 14, 0, 123456, tzinfo=UTC)


def test_iso_format() -> None:
    assert iso(T0) == "2026-10-02T08:14:00.123Z"
    assert iso(datetime(2026, 1, 1, 8, tzinfo=timezone(timedelta(hours=8)))) == "2026-01-01T00:00:00.000Z"


@pytest.mark.parametrize("text", ["2026-10-02T08:14:00Z", "2026-10-02T16:14:00+08:00", "2026-10-02T08:14:00.000Z"])
def test_parse_iso(text: str) -> None:
    assert parse_iso(text) == datetime(2026, 10, 2, 8, 14, tzinfo=UTC)


def test_parse_iso_requires_tz() -> None:
    with pytest.raises(ValueError):
        parse_iso("2026-10-02T08:14:00")


def test_fixed_clock() -> None:
    c = FixedClock(T0)
    c.advance(timedelta(minutes=5))
    assert c.now() == T0 + timedelta(minutes=5)
    with pytest.raises(ValueError):
        FixedClock(datetime(2026, 1, 1))
    assert SystemClock().now().tzinfo is not None


def test_uuid7_layout_and_order() -> None:
    c = FixedClock(T0)
    gen = IdGenerator(c)
    a = gen.uuid7()
    c.advance(timedelta(milliseconds=2))
    b = gen.uuid7()
    ua = uuid.UUID(a)
    assert ua.version == 7 and ua.variant == uuid.RFC_4122
    assert a < b
    assert len(gen.short(8)) == 8


@pytest.mark.parametrize("slug", ["a", "abc", "a-b", "a1-2b", "x" * 64])
def test_valid_slugs(slug: str) -> None:
    assert Slug.is_valid(slug)


@pytest.mark.parametrize("slug", ["", "-a", "a-", "A", "a_b", "a b", "x" * 65, "\u00e4"])
def test_invalid_slugs(slug: str) -> None:
    assert not Slug.is_valid(slug)


def test_reserved_and_from_name() -> None:
    assert Slug.is_reserved("admin") and not Slug.is_reserved("desk-trends")
    assert Slug.from_name("Desk Trends 2026!") == "desk-trends-2026"
    assert Slug.from_name("!!!") == "item"


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Acme Motors", "acme-motors"),
        ("ACME_MOTORS", "acme motors"),
        ("n8n", "N8N"),
        ("O\u2019Reilly", "O'Reilly"),
        ("Caf\u00e9", "Cafe\u0301"),
    ],
)
def test_name_keys_collapse(a: str, b: str) -> None:
    assert NameNormalizer.key(a) == NameNormalizer.key(b)


def test_name_key_distinguishes_and_rejects_empty() -> None:
    assert NameNormalizer.key("Acme") != NameNormalizer.key("Acmes")
    with pytest.raises(ValueError):
        NameNormalizer.key(" - _ ")
    assert NameNormalizer.display("  a   b ") == "a b"
    assert NameNormalizer.title_key("  Hello   World ") == "hello world"


@given(st.text(min_size=1, max_size=40).filter(lambda s: any(c.isalnum() for c in s)))
def test_name_key_idempotent(name: str) -> None:
    k = NameNormalizer.key(name)
    assert NameNormalizer.key(k) == k


def test_token_weights() -> None:
    w = TokenWeights()
    assert w.weigh(TokenUsage(1000, 100, 10_000, 400)) == 1000 + 500 + 1000 + 500
    total = TokenUsage(1, 2, 3, 4) + TokenUsage(1, 1, 1, 1)
    assert total == TokenUsage(2, 3, 4, 5) and total.raw_total == 14


@pytest.mark.parametrize("bad", [-1, True, 1.5])
def test_token_usage_validation(bad: object) -> None:
    with pytest.raises(ValueError):
        TokenUsage(input_tokens=bad)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("consumed", "limit", "state"),
    [
        (0, 100, BudgetState.OPEN),
        (69, 100, BudgetState.OPEN),
        (70, 100, BudgetState.WARN),
        (89, 100, BudgetState.WARN),
        (90, 100, BudgetState.HALF_OPEN),
        (99, 100, BudgetState.HALF_OPEN),
        (100, 100, BudgetState.CLOSED),
        (150, 100, BudgetState.CLOSED),
        (0, 0, BudgetState.CLOSED),
    ],
)
def test_budget_states(consumed: int, limit: int, state: BudgetState) -> None:
    snap = BudgetSnapshot(consumed, limit)
    assert snap.state is state
    assert snap.remaining == max(0, limit - consumed)


@pytest.mark.parametrize("limit", [-1, HARD_MAX_DAILY_TOKENS + 1, "10", 1.0, True])
def test_budget_limit_validation(limit: object) -> None:
    with pytest.raises(ValueError):
        BudgetSnapshot.validate_limit(limit)
    assert BudgetSnapshot.validate_limit(HARD_MAX_DAILY_TOKENS) == HARD_MAX_DAILY_TOKENS


@pytest.mark.parametrize(
    ("cls", "status", "code"),
    [
        (errors.BadRequestError, 400, "bad_request"),
        (errors.ValidationError, 400, "validation_failed"),
        (errors.UnauthorizedError, 401, "unauthorized"),
        (errors.ForbiddenError, 403, "forbidden"),
        (errors.NotFoundError, 404, "not_found"),
        (errors.ConflictError, 409, "conflict"),
        (errors.PayloadTooLargeError, 413, "payload_too_large"),
        (errors.RateLimitedError, 429, "rate_limited"),
        (errors.ServiceUnavailableError, 503, "service_unavailable"),
        (errors.BudgetExhaustedError, 503, "budget_exhausted"),
        (errors.GenAiDisabledError, 503, "genai_disabled"),
    ],
)
def test_error_mapping(cls: type[errors.DomainError], status: int, code: str) -> None:
    err = cls("msg", {"k": 1})
    assert err.status == status and err.to_body() == {"code": code, "message": "msg", "details": {"k": 1}}
    assert cls("m").to_body() == {"code": code, "message": "m"}


def test_sources_are_ascii_only() -> None:
    base = Path(__file__).resolve().parents[2]
    files = [*(base / "src").rglob("*.py"), *(base / "tests").rglob("*.py")]
    offenders = [str(p) for p in files if any(ord(c) > 127 for c in p.read_text("utf-8"))]
    assert offenders == []
