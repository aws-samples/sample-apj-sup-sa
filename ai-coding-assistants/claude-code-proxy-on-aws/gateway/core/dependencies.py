"""FastAPI dependency wiring for Unit 4 gateway."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from functools import lru_cache

import boto3
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.config import Settings, get_settings
from gateway.core.database import ReadSessionFactory, SessionFactory
from gateway.core.exceptions import GatewayError
from gateway.domains.admin.auth import AdminRequestContext
from gateway.repositories import (
    AuditEventRepository,
    BudgetPolicyRepository,
    IdentitySyncRunRepository,
    ModelAliasMappingRepository,
    ModelCatalogRepository,
    ModelPricingRepository,
    TeamMembershipRepository,
    TeamModelPolicyRepository,
    TeamRepository,
    UsageAggRepository,
    UsageEventRepository,
    UserModelPolicyRepository,
    UserRepository,
    VirtualKeyRepository,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionFactory() as session:
        yield session


async def get_read_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with ReadSessionFactory() as session:
        yield session


def get_read_user_repository(
    session: AsyncSession = Depends(get_read_db_session),
) -> UserRepository:
    return UserRepository(session)


def get_read_team_repository(
    session: AsyncSession = Depends(get_read_db_session),
) -> TeamRepository:
    return TeamRepository(session)


def get_read_team_membership_repository(
    session: AsyncSession = Depends(get_read_db_session),
) -> TeamMembershipRepository:
    return TeamMembershipRepository(session)


def get_read_virtual_key_repository(
    session: AsyncSession = Depends(get_read_db_session),
) -> VirtualKeyRepository:
    return VirtualKeyRepository(session)


def get_read_model_catalog_repository(
    session: AsyncSession = Depends(get_read_db_session),
) -> ModelCatalogRepository:
    return ModelCatalogRepository(session)


def get_read_model_alias_mapping_repository(
    session: AsyncSession = Depends(get_read_db_session),
) -> ModelAliasMappingRepository:
    return ModelAliasMappingRepository(session)


def get_read_budget_policy_repository(
    session: AsyncSession = Depends(get_read_db_session),
) -> BudgetPolicyRepository:
    return BudgetPolicyRepository(session)


def get_user_repository(session: AsyncSession = Depends(get_db_session)) -> UserRepository:
    return UserRepository(session)


def get_team_repository(session: AsyncSession = Depends(get_db_session)) -> TeamRepository:
    return TeamRepository(session)


def get_team_membership_repository(
    session: AsyncSession = Depends(get_db_session),
) -> TeamMembershipRepository:
    return TeamMembershipRepository(session)


def get_virtual_key_repository(
    session: AsyncSession = Depends(get_db_session),
) -> VirtualKeyRepository:
    return VirtualKeyRepository(session)


def get_model_catalog_repository(
    session: AsyncSession = Depends(get_db_session),
) -> ModelCatalogRepository:
    return ModelCatalogRepository(session)


def get_model_alias_mapping_repository(
    session: AsyncSession = Depends(get_db_session),
) -> ModelAliasMappingRepository:
    return ModelAliasMappingRepository(session)


def get_model_pricing_repository(
    session: AsyncSession = Depends(get_db_session),
) -> ModelPricingRepository:
    return ModelPricingRepository(session)


def get_read_user_model_policy_repository(
    session: AsyncSession = Depends(get_read_db_session),
) -> UserModelPolicyRepository:
    return UserModelPolicyRepository(session)


def get_read_team_model_policy_repository(
    session: AsyncSession = Depends(get_read_db_session),
) -> TeamModelPolicyRepository:
    return TeamModelPolicyRepository(session)


def get_user_model_policy_repository(
    session: AsyncSession = Depends(get_db_session),
) -> UserModelPolicyRepository:
    return UserModelPolicyRepository(session)


def get_team_model_policy_repository(
    session: AsyncSession = Depends(get_db_session),
) -> TeamModelPolicyRepository:
    return TeamModelPolicyRepository(session)


def get_budget_policy_repository(
    session: AsyncSession = Depends(get_db_session),
) -> BudgetPolicyRepository:
    return BudgetPolicyRepository(session)


def get_usage_event_repository(
    session: AsyncSession = Depends(get_db_session),
) -> UsageEventRepository:
    return UsageEventRepository(session)


def get_usage_agg_repository(session: AsyncSession = Depends(get_db_session)) -> UsageAggRepository:
    return UsageAggRepository(session)


def get_audit_event_repository(
    session: AsyncSession = Depends(get_db_session),
) -> AuditEventRepository:
    return AuditEventRepository(session)


def get_identity_sync_run_repository(
    session: AsyncSession = Depends(get_db_session),
) -> IdentitySyncRunRepository:
    return IdentitySyncRunRepository(session)


def get_auth_principal(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> str:
    """Extract IAM principal ARN from the auth header set by API Gateway."""
    principal = request.headers.get(settings.auth_principal_header)
    if not principal:
        raise GatewayError(
            "Missing auth principal",
            code="auth_principal_missing",
            status_code=401,
            anthropic_type="authentication_error",
        )
    return principal


def get_token_issuance_service(
    user_repo: UserRepository = Depends(get_user_repository),
    key_repo: VirtualKeyRepository = Depends(get_virtual_key_repository),
    audit_repo: AuditEventRepository = Depends(get_audit_event_repository),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
):
    from gateway.domains.auth.service import TokenIssuanceService

    return TokenIssuanceService(user_repo, key_repo, audit_repo, session, settings)


def get_admin_request_context(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> AdminRequestContext:
    principal = request.headers.get(settings.admin_principal_header)
    if not principal:
        raise GatewayError(
            "Missing admin principal",
            code="admin_principal_missing",
            status_code=401,
            anthropic_type="authentication_error",
        )
    return AdminRequestContext(principal=principal, request_id=request.state.request_id)


def get_policy_chain(
    key_repo: VirtualKeyRepository = Depends(get_read_virtual_key_repository),
    user_repo: UserRepository = Depends(get_read_user_repository),
    team_repo: TeamRepository = Depends(get_read_team_repository),
    mapping_repo: ModelAliasMappingRepository = Depends(get_read_model_alias_mapping_repository),
    user_policy_repo: UserModelPolicyRepository = Depends(get_read_user_model_policy_repository),
    team_policy_repo: TeamModelPolicyRepository = Depends(get_read_team_model_policy_repository),
    budget_repo: BudgetPolicyRepository = Depends(get_read_budget_policy_repository),
):
    from gateway.domains.policy.engine import PolicyChain
    from gateway.domains.policy.handlers import (
        CachePolicyHandler,
        ModelBudgetPreCheckHandler,
        ModelResolverHandler,
        TeamBudgetPreCheckHandler,
        TeamModelPolicyHandler,
        TeamStatusHandler,
        UserBudgetPreCheckHandler,
        UserModelPolicyHandler,
        UserStatusHandler,
        VirtualKeyHandler,
    )

    return PolicyChain(
        [
            VirtualKeyHandler(key_repo),
            UserStatusHandler(user_repo),
            TeamStatusHandler(team_repo),
            ModelResolverHandler(mapping_repo),
            UserModelPolicyHandler(user_policy_repo),
            TeamModelPolicyHandler(team_policy_repo),
            UserBudgetPreCheckHandler(budget_repo),
            TeamBudgetPreCheckHandler(budget_repo),
            ModelBudgetPreCheckHandler(budget_repo),
            CachePolicyHandler(),
        ]
    )


@lru_cache(maxsize=1)
def get_metrics_service():
    from gateway.domains.usage.metrics import MetricsService

    return MetricsService()


@lru_cache(maxsize=4)
def _get_bedrock_client(settings: Settings):
    from gateway.domains.runtime.bedrock_client import BedrockClient

    return BedrockClient(settings)


def get_bedrock_client(settings: Settings = Depends(get_settings)):
    return _get_bedrock_client(settings)


@lru_cache(maxsize=4)
def _get_anthropic_client(settings: Settings):
    from gateway.domains.runtime.anthropic_client import AnthropicClient

    if not settings.anthropic_api_key_secret_arn:
        return None
    return AnthropicClient(settings)


def get_anthropic_client(settings: Settings = Depends(get_settings)):
    return _get_anthropic_client(settings)


@lru_cache(maxsize=4)
def _get_circuit_breaker(open_seconds: float):
    from gateway.domains.runtime.circuit_breaker import CircuitBreaker

    return CircuitBreaker(open_seconds=open_seconds)


def get_circuit_breaker(settings: Settings = Depends(get_settings)):
    return _get_circuit_breaker(settings.bedrock_breaker_open_seconds)


def get_usage_service(
    pricing_repo: ModelPricingRepository = Depends(get_model_pricing_repository),
    budget_repo: BudgetPolicyRepository = Depends(get_budget_policy_repository),
    event_repo: UsageEventRepository = Depends(get_usage_event_repository),
    metrics=Depends(get_metrics_service),  # type: ignore[assignment]
    session: AsyncSession = Depends(get_db_session),
):
    from gateway.domains.usage.services import UsageService

    return UsageService(pricing_repo, budget_repo, event_repo, metrics, session)


def get_gateway_service(
    policy_chain=Depends(get_policy_chain),  # type: ignore[assignment]
    usage_service=Depends(get_usage_service),  # type: ignore[assignment]
    model_catalog_repo: ModelCatalogRepository = Depends(get_read_model_catalog_repository),
    session: AsyncSession = Depends(get_db_session),
    metrics=Depends(get_metrics_service),  # type: ignore[assignment]
    bedrock_client=Depends(get_bedrock_client),  # type: ignore[assignment]
    anthropic_client=Depends(get_anthropic_client),  # type: ignore[assignment]
    circuit_breaker=Depends(get_circuit_breaker),  # type: ignore[assignment]
    settings: Settings = Depends(get_settings),
):
    from gateway.domains.runtime.converter import (
        AnthropicToBedrockConverter,
        BedrockToAnthropicConverter,
    )
    from gateway.domains.runtime.services import GatewayService
    from gateway.domains.runtime.streaming import StreamProcessor

    response_converter = BedrockToAnthropicConverter()
    stream_processor = StreamProcessor(
        response_converter,
        usage_service,
        metrics,
        log_full_payloads=settings.runtime_log_full_payloads,
        log_stream_events=settings.runtime_log_stream_events,
    )
    return GatewayService(
        policy_chain,
        AnthropicToBedrockConverter(),
        response_converter,
        bedrock_client,
        stream_processor,
        usage_service,
        session,
        model_catalog_repo,
        metrics,
        log_full_payloads=settings.runtime_log_full_payloads,
        anthropic_client=anthropic_client,
        circuit_breaker=circuit_breaker,
    )


def get_admin_user_service(
    user_repo: UserRepository = Depends(get_user_repository),
    user_policy_repo: UserModelPolicyRepository = Depends(get_user_model_policy_repository),
    model_repo: ModelCatalogRepository = Depends(get_model_catalog_repository),
    audit_repo: AuditEventRepository = Depends(get_audit_event_repository),
    admin_ctx: AdminRequestContext = Depends(get_admin_request_context),
    session: AsyncSession = Depends(get_db_session),
):
    from gateway.domains.admin.users import AdminUserService

    return AdminUserService(user_repo, user_policy_repo, model_repo, audit_repo, admin_ctx, session)


def get_admin_team_service(
    team_repo: TeamRepository = Depends(get_team_repository),
    membership_repo: TeamMembershipRepository = Depends(get_team_membership_repository),
    user_repo: UserRepository = Depends(get_user_repository),
    team_policy_repo: TeamModelPolicyRepository = Depends(get_team_model_policy_repository),
    model_repo: ModelCatalogRepository = Depends(get_model_catalog_repository),
    audit_repo: AuditEventRepository = Depends(get_audit_event_repository),
    admin_ctx: AdminRequestContext = Depends(get_admin_request_context),
    session: AsyncSession = Depends(get_db_session),
):
    from gateway.domains.admin.teams import AdminTeamService

    return AdminTeamService(
        team_repo,
        membership_repo,
        user_repo,
        team_policy_repo,
        model_repo,
        audit_repo,
        admin_ctx,
        session,
    )


def get_admin_model_service(
    model_repo: ModelCatalogRepository = Depends(get_model_catalog_repository),
    mapping_repo: ModelAliasMappingRepository = Depends(get_model_alias_mapping_repository),
    pricing_repo: ModelPricingRepository = Depends(get_model_pricing_repository),
    audit_repo: AuditEventRepository = Depends(get_audit_event_repository),
    admin_ctx: AdminRequestContext = Depends(get_admin_request_context),
    session: AsyncSession = Depends(get_db_session),
):
    from gateway.domains.admin.models import AdminModelService

    return AdminModelService(model_repo, mapping_repo, pricing_repo, audit_repo, admin_ctx, session)


def get_admin_budget_service(
    budget_repo: BudgetPolicyRepository = Depends(get_budget_policy_repository),
    agg_repo: UsageAggRepository = Depends(get_usage_agg_repository),
    audit_repo: AuditEventRepository = Depends(get_audit_event_repository),
    admin_ctx: AdminRequestContext = Depends(get_admin_request_context),
    session: AsyncSession = Depends(get_db_session),
):
    from gateway.domains.admin.budgets import AdminBudgetService

    return AdminBudgetService(budget_repo, agg_repo, audit_repo, admin_ctx, session)


def get_admin_virtual_key_service(
    key_repo: VirtualKeyRepository = Depends(get_virtual_key_repository),
    audit_repo: AuditEventRepository = Depends(get_audit_event_repository),
    admin_ctx: AdminRequestContext = Depends(get_admin_request_context),
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
):
    from gateway.domains.admin.virtual_keys import AdminVirtualKeyService

    return AdminVirtualKeyService(key_repo, audit_repo, admin_ctx, session, settings)


def get_admin_usage_service(
    event_repo: UsageEventRepository = Depends(get_usage_event_repository),
    agg_repo: UsageAggRepository = Depends(get_usage_agg_repository),
):
    from gateway.domains.admin.usage import AdminUsageService
    from gateway.domains.usage.rollup import UsageRollupService

    return AdminUsageService(event_repo, agg_repo, UsageRollupService(agg_repo))


@lru_cache(maxsize=4)
def _get_identity_store_client(region_name: str):
    return boto3.client("identitystore", region_name=region_name)


def get_identity_store_gateway(settings: Settings = Depends(get_settings)):
    from gateway.domains.sync.identity_store import IdentityStoreGateway

    client = _get_identity_store_client(settings.identity_store_region or settings.aws_region)
    return IdentityStoreGateway(client, settings.identity_store_id)


def get_identity_sync_service(
    sync_repo: IdentitySyncRunRepository = Depends(get_identity_sync_run_repository),
    user_repo: UserRepository = Depends(get_user_repository),
    audit_repo: AuditEventRepository = Depends(get_audit_event_repository),
    identity_store=Depends(get_identity_store_gateway),  # type: ignore[assignment]
    admin_ctx: AdminRequestContext = Depends(get_admin_request_context),
    session: AsyncSession = Depends(get_db_session),
):
    from gateway.domains.sync.services import IdentitySyncService

    return IdentitySyncService(sync_repo, user_repo, audit_repo, identity_store, admin_ctx, session)
