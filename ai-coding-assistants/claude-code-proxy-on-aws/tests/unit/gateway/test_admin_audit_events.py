"""Tests for audit events emitted by admin services."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from gateway.core.config import Settings
from gateway.domains.admin.budgets import AdminBudgetService
from gateway.domains.admin.models import AdminModelService, PricingUpdate
from gateway.domains.admin.teams import AdminTeamService, TeamMemberCreate, TeamRuntimePolicyUpdate
from gateway.domains.admin.virtual_keys import AdminVirtualKeyService
from shared.schemas import (
    AliasMappingCreate,
    BudgetPolicyCreate,
    BudgetPolicyUpdate,
    ModelCreate,
    ModelUpdate,
    PricingCreate,
    TeamUpdate,
)
from shared.utils.constants import (
    AuditEventType,
    AuditObjectType,
    BudgetPeriod,
    CachePolicy,
    ModelStatus,
    ScopeType,
    TeamStatus,
    VirtualKeyStatus,
)


def _admin_ctx() -> SimpleNamespace:
    return SimpleNamespace(principal="arn:aws:iam::123456789012:role/Admin", request_id="req-admin")


def _now() -> datetime:
    return datetime(2026, 4, 5, 12, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_team_admin_mutations_emit_audit_events() -> None:
    now = _now()
    team_id = uuid4()
    user_id = uuid4()
    membership_id = uuid4()
    model_id = uuid4()
    team = SimpleNamespace(
        id=team_id,
        name="Platform",
        description="Updated",
        status=TeamStatus.ACTIVE,
        created_at=now,
        updated_at=now,
    )
    user = SimpleNamespace(id=user_id, default_team_id=team_id)
    membership = SimpleNamespace(id=membership_id, user_id=user_id, team_id=team_id)
    audit_repo = SimpleNamespace(create_event=AsyncMock())
    service = AdminTeamService(
        team_repo=SimpleNamespace(
            get_by_id=AsyncMock(side_effect=[team, team, team]),
            update=AsyncMock(return_value=team),
        ),
        membership_repo=SimpleNamespace(
            get_by_user_and_team=AsyncMock(side_effect=[None, membership]),
            add_member=AsyncMock(return_value=membership),
            remove_member=AsyncMock(),
        ),
        user_repo=SimpleNamespace(
            get_by_id=AsyncMock(side_effect=[user, user]),
            update=AsyncMock(),
        ),
        team_policy_repo=SimpleNamespace(replace_policies=AsyncMock()),
        model_repo=SimpleNamespace(
            get_by_canonical_names=AsyncMock(
                return_value=[SimpleNamespace(id=model_id, canonical_name="claude-sonnet-4-6")]
            )
        ),
        audit_repo=audit_repo,
        admin_ctx=_admin_ctx(),
        session=SimpleNamespace(commit=AsyncMock()),
    )

    await service.update_team(team_id, TeamUpdate(description="Updated"))
    await service.set_runtime_policy(
        team_id,
        TeamRuntimePolicyUpdate(
            allowed_models=["claude-sonnet-4-6"],
            cache_policy=CachePolicy.ONE_HOUR,
            max_tokens_overrides={"claude-sonnet-4-6": 4096},
        ),
    )
    await service.add_member(
        team_id,
        TeamMemberCreate(user_id=user_id, role="ADMIN", is_default=True),
    )
    await service.remove_member(team_id, user_id)

    events = [call.args[0] for call in audit_repo.create_event.await_args_list]

    assert [event.event_type for event in events] == [
        AuditEventType.TEAM_UPDATED,
        AuditEventType.TEAM_RUNTIME_POLICY_SET,
        AuditEventType.TEAM_MEMBER_ADDED,
        AuditEventType.TEAM_MEMBER_REMOVED,
    ]
    assert [event.object_type for event in events] == [
        AuditObjectType.TEAM,
        AuditObjectType.TEAM,
        AuditObjectType.TEAM_MEMBERSHIP,
        AuditObjectType.TEAM_MEMBERSHIP,
    ]
    assert events[0].payload_json == {"description": "Updated"}
    assert events[1].payload_json == {
        "allowed_models": ["claude-sonnet-4-6"],
        "cache_policy": "1h",
        "max_tokens_overrides": {"claude-sonnet-4-6": 4096},
    }
    assert events[2].payload_json == {
        "user_id": str(user_id),
        "role": "ADMIN",
        "is_default": True,
    }
    assert events[3].payload_json == {
        "team_id": str(team_id),
        "user_id": str(user_id),
        "default_team_cleared": True,
    }


@pytest.mark.asyncio
async def test_virtual_key_revoke_emits_audit_event() -> None:
    key_id = uuid4()
    key = SimpleNamespace(id=key_id, user_id=uuid4())
    audit_repo = SimpleNamespace(create_event=AsyncMock())
    service = AdminVirtualKeyService(
        key_repo=SimpleNamespace(
            get_by_id=AsyncMock(return_value=key),
            update_status=AsyncMock(),
        ),
        audit_repo=audit_repo,
        admin_ctx=_admin_ctx(),
        session=SimpleNamespace(commit=AsyncMock()),
        settings=Settings(),
    )

    await service.revoke_key(key_id)

    event = audit_repo.create_event.await_args.args[0]
    assert event.event_type == AuditEventType.VIRTUAL_KEY_REVOKED
    assert event.object_type == AuditObjectType.VIRTUAL_KEY
    assert event.object_id == str(key_id)
    assert event.payload_json == {"status": VirtualKeyStatus.REVOKED}


@pytest.mark.asyncio
async def test_virtual_key_rotate_applies_ttl_to_new_key() -> None:
    key_id = uuid4()
    user_id = uuid4()
    key = SimpleNamespace(id=key_id, user_id=user_id)
    created_key_id = uuid4()

    async def create_key(new_key):  # type: ignore[no-untyped-def]
        new_key.id = created_key_id
        return new_key

    key_repo = SimpleNamespace(
        get_by_id=AsyncMock(return_value=key),
        update_status=AsyncMock(),
        create=AsyncMock(side_effect=create_key),
    )
    audit_repo = SimpleNamespace(create_event=AsyncMock())
    service = AdminVirtualKeyService(
        key_repo=key_repo,
        audit_repo=audit_repo,
        admin_ctx=_admin_ctx(),
        session=SimpleNamespace(commit=AsyncMock()),
        settings=Settings(virtual_key_ttl_ms=3_600_000),
    )

    with patch("gateway.domains.admin.virtual_keys.generate_api_key", return_value="vk-rotated"), patch(
        "gateway.domains.admin.virtual_keys.KmsHelper.encrypt_key",
        return_value=b"rotated-ciphertext",
    ), patch(
        "gateway.domains.admin.virtual_keys.KmsHelper.generate_fingerprint",
        return_value="rotated-fingerprint",
    ):
        result = await service.rotate_key(key_id)

    key_repo.update_status.assert_awaited_once()
    created_key = key_repo.create.await_args.args[0]
    assert created_key.expires_at is not None
    ttl_seconds = (created_key.expires_at - created_key.issued_at).total_seconds()
    assert 3595 <= ttl_seconds <= 3605

    event = audit_repo.create_event.await_args.args[0]
    assert event.event_type == AuditEventType.VIRTUAL_KEY_ROTATED
    assert event.payload_json == {"new_key_id": str(created_key_id)}
    assert result == {
        "old_key_id": str(key_id),
        "new_key_id": str(created_key_id),
        "status": VirtualKeyStatus.ROTATED,
        "request_id": "req-admin",
    }


@pytest.mark.asyncio
async def test_model_admin_mutations_emit_audit_events() -> None:
    now = _now()
    model_id = uuid4()
    mapping_id = uuid4()
    pricing_id = uuid4()
    model = SimpleNamespace(
        id=model_id,
        canonical_name="claude-sonnet-4-6",
        bedrock_model_id="anthropic.claude-sonnet-4-6",
        bedrock_region="ap-northeast-1",
        anthropic_model_id=None,
        provider="anthropic",
        family="claude-sonnet-4-6",
        status=ModelStatus.ACTIVE,
        supports_streaming=True,
        supports_tools=True,
        supports_prompt_cache=True,
        default_max_tokens=8192,
        created_at=now,
        updated_at=now,
    )
    mapping = SimpleNamespace(
        id=mapping_id,
        selected_model_pattern="claude-sonnet-4-6*",
        target_model_id=model_id,
        priority=100,
        is_fallback=False,
        active=True,
        created_at=now,
        updated_at=now,
    )
    pricing = SimpleNamespace(
        id=pricing_id,
        model_id=model_id,
        input_price_per_1k=Decimal("1.0"),
        output_price_per_1k=Decimal("2.0"),
        cache_read_price_per_1k=Decimal("0.1"),
        cache_write_5m_price_per_1k=Decimal("0.2"),
        cache_write_1h_price_per_1k=Decimal("0.3"),
        currency="USD",
        effective_from=now,
        active=False,
        created_at=now,
        updated_at=now,
    )
    audit_repo = SimpleNamespace(create_event=AsyncMock())
    service = AdminModelService(
        model_repo=SimpleNamespace(
            create=AsyncMock(return_value=model),
            get_by_id=AsyncMock(side_effect=[model, model]),
            update=AsyncMock(return_value=model),
            delete=AsyncMock(),
        ),
        mapping_repo=SimpleNamespace(
            create=AsyncMock(return_value=mapping),
            get_by_id=AsyncMock(return_value=mapping),
            update=AsyncMock(return_value=mapping),
        ),
        pricing_repo=SimpleNamespace(
            create=AsyncMock(return_value=pricing),
            get_by_id=AsyncMock(return_value=pricing),
            update=AsyncMock(return_value=pricing),
        ),
        audit_repo=audit_repo,
        admin_ctx=_admin_ctx(),
        session=SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock()),
    )

    create_payload = ModelCreate(
        canonical_name="claude-sonnet-4-6",
        bedrock_model_id="anthropic.claude-sonnet-4-6",
        bedrock_region="ap-northeast-1",
        provider="anthropic",
        family="claude-sonnet-4-6",
        status=ModelStatus.ACTIVE,
        supports_prompt_cache=True,
        default_max_tokens=8192,
    )
    update_payload = ModelUpdate(provider="anthropic-v2", bedrock_region="us-west-2")
    mapping_create_payload = AliasMappingCreate(
        selected_model_pattern="claude-sonnet-4-6*",
        target_model_id=model_id,
        priority=100,
    )
    mapping_update_payload = AliasMappingCreate(
        selected_model_pattern="claude-*",
        target_model_id=model_id,
        priority=200,
        is_fallback=True,
    )
    pricing_create_payload = PricingCreate(
        model_id=model_id,
        input_price_per_1k=Decimal("1.0"),
        output_price_per_1k=Decimal("2.0"),
        cache_read_price_per_1k=Decimal("0.1"),
        cache_write_5m_price_per_1k=Decimal("0.2"),
        cache_write_1h_price_per_1k=Decimal("0.3"),
        effective_from=now,
        active=True,
    )

    await service.create_model(create_payload)
    await service.update_model(model_id, update_payload)
    await service.delete_model(model_id)
    await service.create_mapping(mapping_create_payload)
    await service.update_mapping(mapping_id, mapping_update_payload)
    await service.create_pricing(pricing_create_payload)
    await service.update_pricing(pricing_id, PricingUpdate(active=False))

    events = [call.args[0] for call in audit_repo.create_event.await_args_list]

    assert [event.event_type for event in events] == [
        AuditEventType.MODEL_CREATED,
        AuditEventType.MODEL_UPDATED,
        AuditEventType.MODEL_DELETED,
        AuditEventType.MODEL_ALIAS_MAPPING_CREATED,
        AuditEventType.MODEL_ALIAS_MAPPING_UPDATED,
        AuditEventType.MODEL_PRICING_CREATED,
        AuditEventType.MODEL_PRICING_UPDATED,
    ]
    assert [event.object_type for event in events] == [
        AuditObjectType.MODEL,
        AuditObjectType.MODEL,
        AuditObjectType.MODEL,
        AuditObjectType.MODEL_ALIAS_MAPPING,
        AuditObjectType.MODEL_ALIAS_MAPPING,
        AuditObjectType.MODEL_PRICING,
        AuditObjectType.MODEL_PRICING,
    ]
    assert events[2].payload_json == {
        "canonical_name": "claude-sonnet-4-6",
        "bedrock_model_id": "anthropic.claude-sonnet-4-6",
        "bedrock_region": "ap-northeast-1",
    }
    assert events[6].payload_json == {"active": False}


@pytest.mark.asyncio
async def test_budget_admin_mutations_emit_audit_events() -> None:
    now = _now()
    budget_id = uuid4()
    user_id = uuid4()
    budget = SimpleNamespace(
        id=budget_id,
        scope_type=ScopeType.USER,
        scope_user_id=user_id,
        scope_team_id=None,
        model_id=None,
        period=BudgetPeriod.MONTHLY,
        soft_limit_usd=Decimal("100"),
        hard_limit_usd=Decimal("200"),
        current_used_usd=Decimal("0"),
        window_started_at=now,
        currency="USD",
        active=True,
        created_at=now,
        updated_at=now,
    )
    audit_repo = SimpleNamespace(create_event=AsyncMock())
    service = AdminBudgetService(
        budget_repo=SimpleNamespace(
            create=AsyncMock(return_value=budget),
            get_by_id=AsyncMock(return_value=budget),
            update=AsyncMock(return_value=budget),
        ),
        usage_agg_repo=SimpleNamespace(),
        audit_repo=audit_repo,
        admin_ctx=_admin_ctx(),
        session=SimpleNamespace(commit=AsyncMock()),
    )

    create_payload = BudgetPolicyCreate(
        scope_type=ScopeType.USER,
        scope_user_id=user_id,
        period=BudgetPeriod.MONTHLY,
        soft_limit_usd=Decimal("100"),
        hard_limit_usd=Decimal("200"),
    )
    update_payload = BudgetPolicyUpdate(active=False, hard_limit_usd=Decimal("250"))

    await service.create_budget(create_payload)
    await service.update_budget(budget_id, update_payload)

    events = [call.args[0] for call in audit_repo.create_event.await_args_list]

    assert [event.event_type for event in events] == [
        AuditEventType.BUDGET_CREATED,
        AuditEventType.BUDGET_UPDATED,
    ]
    assert [event.object_type for event in events] == [
        AuditObjectType.BUDGET_POLICY,
        AuditObjectType.BUDGET_POLICY,
    ]
    assert events[0].payload_json == {
        "scope_type": "USER",
        "scope_user_id": str(user_id),
        "period": "MONTHLY",
        "soft_limit_usd": "100",
        "hard_limit_usd": "200",
        "currency": "USD",
        "active": True,
    }
    assert events[1].payload_json == {"hard_limit_usd": "250", "active": False}
