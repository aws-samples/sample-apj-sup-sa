#!/usr/bin/env python3
"""Local-only bootstrap for containerized Gateway smoke tests.

This script seeds the minimum runtime state the Gateway needs:
user, an initial ACTIVE virtual key, model catalog, alias mapping, and pricing.
Subsequent local helper runs still go through /v1/auth/token.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from gateway.core.config import get_settings
from shared.models import Base, ModelAliasMapping, ModelCatalog, ModelPricing, User, VirtualKey
from shared.utils.constants import ModelStatus, UserStatus, VirtualKeyStatus
from shared.utils.hashing import sha256_hex
from shared.utils.kms import KmsHelper


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


LOCAL_API_KEY = _env("LOCAL_BOOTSTRAP_API_KEY", "sk-local-dev")
LOCAL_USER_ID = uuid.UUID(
    _env("LOCAL_BOOTSTRAP_USER_ID", "11111111-1111-1111-1111-111111111111")
)
LOCAL_VIRTUAL_KEY_ID = uuid.UUID(
    _env("LOCAL_BOOTSTRAP_VIRTUAL_KEY_ID", "55555555-5555-5555-5555-555555555555")
)
DEFAULT_LOCAL_CACHE_POLICY = "5m"
LOCAL_BEDROCK_REGION = _env("LOCAL_BOOTSTRAP_BEDROCK_REGION", _env("AWS_REGION", "ap-northeast-2"))
LOCAL_GLM_BEDROCK_REGION = _env("LOCAL_BOOTSTRAP_GLM_BEDROCK_REGION", "ap-northeast-1")


@dataclass(frozen=True, slots=True)
class LocalModelSeed:
    id: uuid.UUID
    pricing_id: uuid.UUID
    canonical_name: str
    bedrock_model_id: str
    bedrock_region: str
    provider: str
    family: str
    supports_prompt_cache: bool
    default_max_tokens: int
    input_price_per_1k: Decimal
    output_price_per_1k: Decimal
    supports_streaming: bool = True
    supports_tools: bool = True
    anthropic_model_id: str | None = None
    cache_read_price_per_1k: Decimal = Decimal("0")
    cache_write_5m_price_per_1k: Decimal = Decimal("0")
    cache_write_1h_price_per_1k: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class LocalMappingSeed:
    id: uuid.UUID
    pattern: str
    target_canonical_name: str
    priority: int
    is_fallback: bool = False


LOCAL_MODEL_SEEDS = (
    LocalModelSeed(
        id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        pricing_id=uuid.UUID("44444444-4444-4444-4444-444444444444"),
        canonical_name="claude-opus-4-8",
        anthropic_model_id="claude-opus-4-8",
        bedrock_model_id="global.anthropic.claude-opus-4-8",
        bedrock_region=LOCAL_BEDROCK_REGION,
        provider="anthropic",
        family="claude-opus-4-8",
        supports_streaming=True,
        supports_tools=True,
        supports_prompt_cache=True,
        default_max_tokens=8192,
        input_price_per_1k=Decimal("0.005"),
        output_price_per_1k=Decimal("0.025"),
        cache_read_price_per_1k=Decimal("0.0005"),
        cache_write_5m_price_per_1k=Decimal("0.00625"),
        cache_write_1h_price_per_1k=Decimal("0.010"),
    ),
    LocalModelSeed(
        id=uuid.UUID("66666666-6666-6666-6666-666666666666"),
        pricing_id=uuid.UUID("77777777-7777-7777-7777-777777777777"),
        canonical_name="claude-sonnet-4-6",
        anthropic_model_id="claude-sonnet-4-6",
        bedrock_model_id="global.anthropic.claude-sonnet-4-6",
        bedrock_region=LOCAL_BEDROCK_REGION,
        provider="anthropic",
        family="claude-sonnet-4-6",
        supports_streaming=True,
        supports_tools=True,
        supports_prompt_cache=True,
        default_max_tokens=8192,
        input_price_per_1k=Decimal("0.003"),
        output_price_per_1k=Decimal("0.015"),
        cache_read_price_per_1k=Decimal("0.0003"),
        cache_write_5m_price_per_1k=Decimal("0.00375"),
        cache_write_1h_price_per_1k=Decimal("0.006"),
    ),
    LocalModelSeed(
        id=uuid.UUID("88888888-8888-8888-8888-888888888888"),
        pricing_id=uuid.UUID("99999999-9999-9999-9999-999999999999"),
        canonical_name="claude-haiku-4-5",
        anthropic_model_id="claude-haiku-4-5",
        bedrock_model_id="global.anthropic.claude-haiku-4-5-20251001-v1:0",
        bedrock_region=LOCAL_BEDROCK_REGION,
        provider="anthropic",
        family="claude-haiku-4-5",
        supports_streaming=True,
        supports_tools=True,
        supports_prompt_cache=True,
        default_max_tokens=8192,
        input_price_per_1k=Decimal("0.001"),
        output_price_per_1k=Decimal("0.005"),
        cache_read_price_per_1k=Decimal("0.0001"),
        cache_write_5m_price_per_1k=Decimal("0.00125"),
        cache_write_1h_price_per_1k=Decimal("0.002"),
    ),
    LocalModelSeed(
        id=uuid.UUID("12121212-1212-1212-1212-121212121212"),
        pricing_id=uuid.UUID("34343434-3434-3434-3434-343434343434"),
        canonical_name="glm-5",
        bedrock_model_id="zai.glm-5",
        bedrock_region=LOCAL_GLM_BEDROCK_REGION,
        provider="zai",
        family="glm-5",
        supports_streaming=True,
        supports_tools=True,
        supports_prompt_cache=False,
        default_max_tokens=131072,
        input_price_per_1k=Decimal("0.0012"),
        output_price_per_1k=Decimal("0.00384"),
    ),
    LocalModelSeed(
        id=uuid.UUID("56565656-5656-5656-5656-565656565656"),
        pricing_id=uuid.UUID("78787878-7878-7878-7878-787878787878"),
        canonical_name="minimax.minimax-m2.5",
        bedrock_model_id="minimax.minimax-m2.5",
        bedrock_region="ap-northeast-1",
        provider="minimax",
        family="minimax-m2.5",
        supports_streaming=True,
        supports_tools=True,
        supports_prompt_cache=False,
        default_max_tokens=8192,
        input_price_per_1k=Decimal("0.00036"),
        output_price_per_1k=Decimal("0.00144"),
    ),
)

LOCAL_MAPPING_SEEDS = (
    LocalMappingSeed(
        id=uuid.UUID("33333333-3333-3333-3333-333333333333"),
        pattern="claude-opus-4-8*",
        target_canonical_name="claude-opus-4-8",
        priority=400,
    ),
    LocalMappingSeed(
        id=uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        pattern="claude-sonnet-4-6*",
        target_canonical_name="claude-sonnet-4-6",
        priority=300,
    ),
    LocalMappingSeed(
        id=uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        pattern="claude-haiku-4-5*",
        target_canonical_name="glm-5",
        priority=200,
    ),
    LocalMappingSeed(
        id=uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
        pattern="*",
        target_canonical_name="claude-sonnet-4-6",
        priority=100,
        is_fallback=True,
    ),
)


async def _ensure_user(session) -> User:
    user = await session.get(User, LOCAL_USER_ID)
    if user is not None:
        return user

    stmt = select(User).where(User.user_name == _env("LOCAL_BOOTSTRAP_USER_NAME", "local-user"))
    user = (await session.execute(stmt)).scalar_one_or_none()
    if user is not None:
        if user.status != UserStatus.ACTIVE:
            user.status = UserStatus.ACTIVE
        user.default_team_id = None
        return user

    user = User(
        id=LOCAL_USER_ID,
        identity_store_user_id=_env("LOCAL_BOOTSTRAP_IDENTITY_STORE_USER_ID", "local-user"),
        user_name=_env("LOCAL_BOOTSTRAP_USER_NAME", "local-user"),
        display_name=_env("LOCAL_BOOTSTRAP_DISPLAY_NAME", "Local User"),
        email=_env("LOCAL_BOOTSTRAP_EMAIL", "local@example.com"),
        status=UserStatus.ACTIVE,
        default_team_id=None,
        last_synced_at=datetime.now(timezone.utc),
    )
    session.add(user)
    return user


async def _ensure_model(session, seed: LocalModelSeed) -> ModelCatalog:
    model = await session.get(ModelCatalog, seed.id)
    if model is not None:
        model.canonical_name = seed.canonical_name
        model.bedrock_model_id = seed.bedrock_model_id
        model.bedrock_region = seed.bedrock_region
        model.provider = seed.provider
        model.family = seed.family
        model.status = ModelStatus.ACTIVE
        model.supports_streaming = seed.supports_streaming
        model.supports_tools = seed.supports_tools
        model.supports_prompt_cache = seed.supports_prompt_cache
        model.default_max_tokens = seed.default_max_tokens
        return model

    stmt = select(ModelCatalog).where(
        ModelCatalog.canonical_name == seed.canonical_name
    )
    model = (await session.execute(stmt)).scalar_one_or_none()
    if model is not None:
        model.bedrock_model_id = seed.bedrock_model_id
        model.bedrock_region = seed.bedrock_region
        model.provider = seed.provider
        model.family = seed.family
        model.status = ModelStatus.ACTIVE
        model.supports_streaming = seed.supports_streaming
        model.supports_tools = seed.supports_tools
        model.supports_prompt_cache = seed.supports_prompt_cache
        model.default_max_tokens = seed.default_max_tokens
        return model

    model = ModelCatalog(
        id=seed.id,
        canonical_name=seed.canonical_name,
        bedrock_model_id=seed.bedrock_model_id,
        bedrock_region=seed.bedrock_region,
        anthropic_model_id=seed.anthropic_model_id,
        provider=seed.provider,
        family=seed.family,
        status=ModelStatus.ACTIVE,
        supports_streaming=seed.supports_streaming,
        supports_tools=seed.supports_tools,
        supports_prompt_cache=seed.supports_prompt_cache,
        default_max_tokens=seed.default_max_tokens,
    )
    session.add(model)
    return model


async def _ensure_mapping(
    session, seed: LocalMappingSeed, target_model: ModelCatalog
) -> None:
    mapping = await session.get(ModelAliasMapping, seed.id)
    if mapping is None:
        stmt = select(ModelAliasMapping).where(
            ModelAliasMapping.selected_model_pattern == seed.pattern,
            ModelAliasMapping.priority == seed.priority,
        )
        mapping = (await session.execute(stmt)).scalar_one_or_none()
    if mapping is None and seed.is_fallback:
        stmt = select(ModelAliasMapping).where(ModelAliasMapping.is_fallback.is_(True))
        mapping = (await session.execute(stmt)).scalar_one_or_none()

    if mapping is None:
        session.add(
            ModelAliasMapping(
                id=seed.id,
                selected_model_pattern=seed.pattern,
                target_model_id=target_model.id,
                priority=seed.priority,
                is_fallback=seed.is_fallback,
                active=True,
            )
        )
        return

    mapping.selected_model_pattern = seed.pattern
    mapping.target_model_id = target_model.id
    mapping.priority = seed.priority
    mapping.is_fallback = seed.is_fallback
    mapping.active = True


async def _ensure_pricing(session, seed: LocalModelSeed, model: ModelCatalog) -> None:
    stmt = select(ModelPricing).where(
        ModelPricing.model_id == model.id,
        ModelPricing.active.is_(True),
    )
    pricing = (await session.execute(stmt)).scalars().first()
    if pricing is not None:
        pricing.input_price_per_1k = seed.input_price_per_1k
        pricing.output_price_per_1k = seed.output_price_per_1k
        pricing.cache_read_price_per_1k = seed.cache_read_price_per_1k
        pricing.cache_write_5m_price_per_1k = seed.cache_write_5m_price_per_1k
        pricing.cache_write_1h_price_per_1k = seed.cache_write_1h_price_per_1k
        pricing.currency = "USD"
        return

    session.add(
        ModelPricing(
            id=seed.pricing_id,
            model_id=model.id,
            input_price_per_1k=seed.input_price_per_1k,
            output_price_per_1k=seed.output_price_per_1k,
            cache_read_price_per_1k=seed.cache_read_price_per_1k,
            cache_write_5m_price_per_1k=seed.cache_write_5m_price_per_1k,
            cache_write_1h_price_per_1k=seed.cache_write_1h_price_per_1k,
            currency="USD",
            effective_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
            active=True,
        )
    )


async def _ensure_virtual_key(session, user: User, ttl_ms: int) -> None:
    now = datetime.now(timezone.utc)
    ciphertext = KmsHelper.encrypt_key(LOCAL_API_KEY)
    expires_at = _calculate_expires_at(now, ttl_ms)
    fingerprint = sha256_hex(LOCAL_API_KEY)

    stmt = select(VirtualKey).where(VirtualKey.key_fingerprint == fingerprint)
    key = (await session.execute(stmt)).scalar_one_or_none()
    if key is not None:
        key.status = VirtualKeyStatus.ACTIVE
        key.user_id = user.id
        key.key_last4 = LOCAL_API_KEY[-4:]
        key.kms_ciphertext = ciphertext
        key.issued_at = now
        key.expires_at = expires_at
        key.last_used_at = now
        key.revoked_at = None
        return

    stmt = select(VirtualKey).where(
        VirtualKey.user_id == user.id,
        VirtualKey.status == VirtualKeyStatus.ACTIVE,
    )
    active_key = (await session.execute(stmt)).scalar_one_or_none()
    if active_key is not None:
        active_key.key_fingerprint = fingerprint
        active_key.key_last4 = LOCAL_API_KEY[-4:]
        active_key.kms_ciphertext = ciphertext
        active_key.issued_at = now
        active_key.expires_at = expires_at
        active_key.last_used_at = now
        active_key.revoked_at = None
        return

    session.add(
        VirtualKey(
            id=LOCAL_VIRTUAL_KEY_ID,
            user_id=user.id,
            key_fingerprint=fingerprint,
            key_last4=LOCAL_API_KEY[-4:],
            kms_ciphertext=ciphertext,
            status=VirtualKeyStatus.ACTIVE,
            issued_at=now,
            expires_at=expires_at,
            last_used_at=now,
            revoked_at=None,
        )
    )


def _calculate_expires_at(now: datetime, ttl_ms: int) -> datetime | None:
    return now + timedelta(milliseconds=ttl_ms) if ttl_ms > 0 else None


def _resolve_virtual_key_ttl_ms(settings) -> int:
    ttl_ms = getattr(settings, "virtual_key_ttl_ms", None)
    if ttl_ms is not None:
        return ttl_ms

    ttl_hours = getattr(settings, "virtual_key_ttl_hours", None)
    if ttl_hours is not None:
        return ttl_hours * 60 * 60 * 1000

    return 14_400_000


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        user = await _ensure_user(session)
        models_by_name: dict[str, ModelCatalog] = {}
        for seed in LOCAL_MODEL_SEEDS:
            model = await _ensure_model(session, seed)
            models_by_name[seed.canonical_name] = model
            await _ensure_pricing(session, seed, model)
        for seed in LOCAL_MAPPING_SEEDS:
            await _ensure_mapping(session, seed, models_by_name[seed.target_canonical_name])
        await _ensure_virtual_key(session, user, _resolve_virtual_key_ttl_ms(settings))
        await session.commit()

    await engine.dispose()
    print(
        "local bootstrap complete "
        f"api_key=...{LOCAL_API_KEY[-4:]} "
        f"models={','.join(seed.canonical_name for seed in LOCAL_MODEL_SEEDS)} "
        f"default_cache_policy={DEFAULT_LOCAL_CACHE_POLICY} "
        "fallback_model=claude-sonnet-4-6"
    )


if __name__ == "__main__":
    asyncio.run(main())
