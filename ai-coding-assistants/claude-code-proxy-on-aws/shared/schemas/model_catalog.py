"""Model catalog DTOs."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from shared.utils.constants import ModelStatus


class ModelCreate(BaseModel):
    canonical_name: str
    bedrock_model_id: str
    bedrock_region: str | None = None
    anthropic_model_id: str | None = None
    provider: str
    family: str | None = None
    status: ModelStatus | None = None
    supports_streaming: bool = True
    supports_tools: bool = True
    supports_prompt_cache: bool = False
    default_max_tokens: int | None = None


class ModelUpdate(BaseModel):
    canonical_name: str | None = None
    bedrock_model_id: str | None = None
    bedrock_region: str | None = None
    anthropic_model_id: str | None = None
    provider: str | None = None
    family: str | None = None
    status: ModelStatus | None = None
    supports_streaming: bool | None = None
    supports_tools: bool | None = None
    supports_prompt_cache: bool | None = None
    default_max_tokens: int | None = None


class ModelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    canonical_name: str
    bedrock_model_id: str
    bedrock_region: str | None
    anthropic_model_id: str | None
    provider: str
    family: str | None
    status: ModelStatus
    supports_streaming: bool
    supports_tools: bool
    supports_prompt_cache: bool
    default_max_tokens: int | None
    created_at: datetime
    updated_at: datetime


class AliasMappingCreate(BaseModel):
    selected_model_pattern: str
    target_model_id: UUID
    priority: int
    is_fallback: bool = False
    active: bool = True


class AliasMappingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    selected_model_pattern: str
    target_model_id: UUID
    priority: int
    is_fallback: bool
    active: bool
    created_at: datetime
    updated_at: datetime


class PricingCreate(BaseModel):
    model_id: UUID
    input_price_per_1k: Decimal
    output_price_per_1k: Decimal
    cache_read_price_per_1k: Decimal
    cache_write_5m_price_per_1k: Decimal
    cache_write_1h_price_per_1k: Decimal
    currency: str = "USD"
    effective_from: datetime
    active: bool = True


class PricingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    model_id: UUID
    input_price_per_1k: Decimal
    output_price_per_1k: Decimal
    cache_read_price_per_1k: Decimal
    cache_write_5m_price_per_1k: Decimal
    cache_write_1h_price_per_1k: Decimal
    currency: str
    effective_from: datetime
    active: bool
    created_at: datetime
    updated_at: datetime
