"""Admin model endpoints and service."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel

from gateway.core.dependencies import get_admin_model_service
from gateway.core.exceptions import NotFoundError
from shared.models import AuditEvent, ModelAliasMapping, ModelCatalog, ModelPricing
from shared.schemas import (
    AliasMappingCreate,
    AliasMappingResponse,
    ModelCreate,
    ModelResponse,
    ModelUpdate,
    PricingCreate,
    PricingResponse,
)
from shared.utils.constants import AuditActorType, AuditEventType, AuditObjectType

router = APIRouter(tags=["admin-models"])


class PricingUpdate(BaseModel):
    active: bool | None = None


class AdminModelService:
    def __init__(
        self, model_repo, mapping_repo, pricing_repo, audit_repo, admin_ctx, session
    ) -> None:  # type: ignore[no-untyped-def]
        self._model_repo = model_repo
        self._mapping_repo = mapping_repo
        self._pricing_repo = pricing_repo
        self._audit_repo = audit_repo
        self._admin_ctx = admin_ctx
        self._session = session

    async def list_models(self, page: int, page_size: int) -> dict[str, Any]:
        items, next_page = await self._model_repo.list(page=page, page_size=page_size)
        return {
            "items": [ModelResponse.model_validate(item).model_dump() for item in items],
            "next_page": next_page,
        }

    async def create_model(self, payload: ModelCreate) -> ModelResponse:
        model = await self._model_repo.create(ModelCatalog(**payload.model_dump(exclude_none=True)))
        await self._audit_repo.create_event(
            AuditEvent(
                actor_type=AuditActorType.IAM_PRINCIPAL,
                actor_id=self._admin_ctx.principal,
                event_type=AuditEventType.MODEL_CREATED,
                object_type=AuditObjectType.MODEL,
                object_id=str(model.id),
                request_id=self._admin_ctx.request_id,
                payload_json=payload.model_dump(mode="json", exclude_none=True),
            )
        )
        await self._session.commit()
        return ModelResponse.model_validate(model)

    async def delete_model(self, model_id: UUID) -> None:
        model = await self._model_repo.get_by_id(model_id)
        if model is None:
            raise NotFoundError("Model not found", code="model_not_found")
        await self._model_repo.delete(model)
        await self._audit_repo.create_event(
            AuditEvent(
                actor_type=AuditActorType.IAM_PRINCIPAL,
                actor_id=self._admin_ctx.principal,
                event_type=AuditEventType.MODEL_DELETED,
                object_type=AuditObjectType.MODEL,
                object_id=str(model_id),
                request_id=self._admin_ctx.request_id,
                payload_json={
                    "canonical_name": model.canonical_name,
                    "bedrock_model_id": model.bedrock_model_id,
                    "bedrock_region": model.bedrock_region,
                },
            )
        )
        await self._session.commit()

    async def update_model(self, model_id: UUID, payload: ModelUpdate) -> ModelResponse:
        model = await self._model_repo.get_by_id(model_id)
        if model is None:
            raise NotFoundError("Model not found", code="model_not_found")
        updated = await self._model_repo.update(model, **payload.model_dump(exclude_unset=True))
        await self._audit_repo.create_event(
            AuditEvent(
                actor_type=AuditActorType.IAM_PRINCIPAL,
                actor_id=self._admin_ctx.principal,
                event_type=AuditEventType.MODEL_UPDATED,
                object_type=AuditObjectType.MODEL,
                object_id=str(model_id),
                request_id=self._admin_ctx.request_id,
                payload_json=payload.model_dump(mode="json", exclude_unset=True),
            )
        )
        await self._session.commit()
        # commit expires server-side onupdate columns (updated_at uses
        # onupdate=now()); refresh reloads them so model_validate does not
        # trigger a lazy load outside the async context (MissingGreenlet).
        await self._session.refresh(updated)
        return ModelResponse.model_validate(updated)

    async def list_mappings(self, page: int, page_size: int) -> dict[str, Any]:
        items, next_page = await self._mapping_repo.list(page=page, page_size=page_size)
        return {
            "items": [AliasMappingResponse.model_validate(item).model_dump() for item in items],
            "next_page": next_page,
        }

    async def create_mapping(self, payload: AliasMappingCreate) -> AliasMappingResponse:
        mapping = await self._mapping_repo.create(ModelAliasMapping(**payload.model_dump()))
        await self._audit_repo.create_event(
            AuditEvent(
                actor_type=AuditActorType.IAM_PRINCIPAL,
                actor_id=self._admin_ctx.principal,
                event_type=AuditEventType.MODEL_ALIAS_MAPPING_CREATED,
                object_type=AuditObjectType.MODEL_ALIAS_MAPPING,
                object_id=str(mapping.id),
                request_id=self._admin_ctx.request_id,
                payload_json=payload.model_dump(mode="json"),
            )
        )
        await self._session.commit()
        return AliasMappingResponse.model_validate(mapping)

    async def update_mapping(
        self, mapping_id: UUID, payload: AliasMappingCreate
    ) -> AliasMappingResponse:
        mapping = await self._mapping_repo.get_by_id(mapping_id)
        if mapping is None:
            raise NotFoundError("Mapping not found", code="mapping_not_found")
        updated = await self._mapping_repo.update(mapping, **payload.model_dump())
        await self._audit_repo.create_event(
            AuditEvent(
                actor_type=AuditActorType.IAM_PRINCIPAL,
                actor_id=self._admin_ctx.principal,
                event_type=AuditEventType.MODEL_ALIAS_MAPPING_UPDATED,
                object_type=AuditObjectType.MODEL_ALIAS_MAPPING,
                object_id=str(mapping_id),
                request_id=self._admin_ctx.request_id,
                payload_json=payload.model_dump(mode="json"),
            )
        )
        await self._session.commit()
        return AliasMappingResponse.model_validate(updated)

    async def delete_mapping(self, mapping_id: UUID) -> None:
        mapping = await self._mapping_repo.get_by_id(mapping_id)
        if mapping is None:
            raise NotFoundError("Mapping not found", code="mapping_not_found")
        await self._mapping_repo.delete(mapping)
        await self._audit_repo.create_event(
            AuditEvent(
                actor_type=AuditActorType.IAM_PRINCIPAL,
                actor_id=self._admin_ctx.principal,
                event_type=AuditEventType.MODEL_ALIAS_MAPPING_DELETED,
                object_type=AuditObjectType.MODEL_ALIAS_MAPPING,
                object_id=str(mapping_id),
                request_id=self._admin_ctx.request_id,
                payload_json={"selected_model_pattern": mapping.selected_model_pattern},
            )
        )
        await self._session.commit()

    async def list_pricing(
        self, model_id: UUID | None, page: int, page_size: int
    ) -> dict[str, Any]:
        items, next_page = await self._pricing_repo.list(
            model_id=model_id, page=page, page_size=page_size
        )
        return {
            "items": [PricingResponse.model_validate(item).model_dump() for item in items],
            "next_page": next_page,
        }

    async def create_pricing(self, payload: PricingCreate) -> PricingResponse:
        pricing = await self._pricing_repo.create(ModelPricing(**payload.model_dump()))
        await self._audit_repo.create_event(
            AuditEvent(
                actor_type=AuditActorType.IAM_PRINCIPAL,
                actor_id=self._admin_ctx.principal,
                event_type=AuditEventType.MODEL_PRICING_CREATED,
                object_type=AuditObjectType.MODEL_PRICING,
                object_id=str(pricing.id),
                request_id=self._admin_ctx.request_id,
                payload_json=payload.model_dump(mode="json"),
            )
        )
        await self._session.commit()
        return PricingResponse.model_validate(pricing)

    async def update_pricing(self, pricing_id: UUID, payload: PricingUpdate) -> PricingResponse:
        pricing = await self._pricing_repo.get_by_id(pricing_id)
        if pricing is None:
            raise NotFoundError("Pricing not found", code="pricing_not_found")
        updated = await self._pricing_repo.update(pricing, **payload.model_dump(exclude_unset=True))
        await self._audit_repo.create_event(
            AuditEvent(
                actor_type=AuditActorType.IAM_PRINCIPAL,
                actor_id=self._admin_ctx.principal,
                event_type=AuditEventType.MODEL_PRICING_UPDATED,
                object_type=AuditObjectType.MODEL_PRICING,
                object_id=str(pricing_id),
                request_id=self._admin_ctx.request_id,
                payload_json=payload.model_dump(mode="json", exclude_unset=True),
            )
        )
        await self._session.commit()
        return PricingResponse.model_validate(updated)

    async def delete_pricing(self, pricing_id: UUID) -> None:
        pricing = await self._pricing_repo.get_by_id(pricing_id)
        if pricing is None:
            raise NotFoundError("Pricing not found", code="pricing_not_found")
        await self._pricing_repo.delete(pricing)
        await self._audit_repo.create_event(
            AuditEvent(
                actor_type=AuditActorType.IAM_PRINCIPAL,
                actor_id=self._admin_ctx.principal,
                event_type=AuditEventType.MODEL_PRICING_DELETED,
                object_type=AuditObjectType.MODEL_PRICING,
                object_id=str(pricing_id),
                request_id=self._admin_ctx.request_id,
                payload_json={"model_id": str(pricing.model_id)},
            )
        )
        await self._session.commit()


@router.get("/models")
async def list_models(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=1000),
    service=Depends(get_admin_model_service),  # type: ignore[assignment]
) -> dict[str, Any]:
    return await service.list_models(page, page_size)


@router.post("/models", response_model=ModelResponse)
async def create_model(payload: ModelCreate, service=Depends(get_admin_model_service)):  # type: ignore[assignment]
    return await service.create_model(payload)


@router.patch("/models/{model_id}", response_model=ModelResponse)
async def update_model(
    model_id: UUID, payload: ModelUpdate, service=Depends(get_admin_model_service)
):  # type: ignore[assignment]
    return await service.update_model(model_id, payload)


@router.delete("/models/{model_id}", status_code=204)
async def delete_model(model_id: UUID, service=Depends(get_admin_model_service)):  # type: ignore[assignment]
    await service.delete_model(model_id)
    return Response(status_code=204)


@router.get("/model-mappings")
async def list_mappings(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=1000),
    service=Depends(get_admin_model_service),  # type: ignore[assignment]
) -> dict[str, Any]:
    return await service.list_mappings(page, page_size)


@router.post("/model-mappings", response_model=AliasMappingResponse)
async def create_mapping(payload: AliasMappingCreate, service=Depends(get_admin_model_service)):  # type: ignore[assignment]
    return await service.create_mapping(payload)


@router.put("/model-mappings/{mapping_id}", response_model=AliasMappingResponse)
async def update_mapping(
    mapping_id: UUID,
    payload: AliasMappingCreate,
    service=Depends(get_admin_model_service),  # type: ignore[assignment]
) -> AliasMappingResponse:
    return await service.update_mapping(mapping_id, payload)


@router.delete("/model-mappings/{mapping_id}", status_code=204)
async def delete_mapping(mapping_id: UUID, service=Depends(get_admin_model_service)):  # type: ignore[assignment]
    await service.delete_mapping(mapping_id)
    return Response(status_code=204)


@router.get("/model-pricing")
async def list_pricing(
    model_id: UUID | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=1000),
    service=Depends(get_admin_model_service),  # type: ignore[assignment]
) -> dict[str, Any]:
    return await service.list_pricing(model_id, page, page_size)


@router.post("/model-pricing", response_model=PricingResponse)
async def create_pricing(payload: PricingCreate, service=Depends(get_admin_model_service)):  # type: ignore[assignment]
    return await service.create_pricing(payload)


@router.patch("/model-pricing/{pricing_id}", response_model=PricingResponse)
async def update_pricing(
    pricing_id: UUID,
    payload: PricingUpdate,
    service=Depends(get_admin_model_service),  # type: ignore[assignment]
) -> PricingResponse:
    return await service.update_pricing(pricing_id, payload)


@router.delete("/model-pricing/{pricing_id}", status_code=204)
async def delete_pricing(pricing_id: UUID, service=Depends(get_admin_model_service)):  # type: ignore[assignment]
    await service.delete_pricing(pricing_id)
    return Response(status_code=204)
