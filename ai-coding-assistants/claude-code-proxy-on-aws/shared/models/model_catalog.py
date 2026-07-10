"""Model catalog and pricing models."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, Numeric, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.models.base import TIMESTAMPTZ_SQL, UUID_SQL, Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from shared.models.policy import BudgetPolicy, TeamModelPolicy, UserModelPolicy
    from shared.models.usage import UsageDailyAgg, UsageEvent, UsageMonthlyAgg


class ModelCatalog(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "model_catalog"

    canonical_name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    bedrock_model_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    bedrock_region: Mapped[str | None] = mapped_column(Text)
    anthropic_model_id: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    family: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'ACTIVE'"))
    supports_streaming: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    supports_tools: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    supports_prompt_cache: Mapped[bool] = mapped_column(
        nullable=False,
        server_default=text("false"),
    )
    default_max_tokens: Mapped[int | None]

    alias_mappings: Mapped[list["ModelAliasMapping"]] = relationship(back_populates="target_model")
    pricings: Mapped[list["ModelPricing"]] = relationship(back_populates="model")
    user_policies: Mapped[list["UserModelPolicy"]] = relationship(back_populates="model")
    team_policies: Mapped[list["TeamModelPolicy"]] = relationship(back_populates="model")
    budget_policies: Mapped[list["BudgetPolicy"]] = relationship(back_populates="model")
    usage_events: Mapped[list["UsageEvent"]] = relationship(back_populates="resolved_model")
    daily_aggregates: Mapped[list["UsageDailyAgg"]] = relationship(back_populates="model")
    monthly_aggregates: Mapped[list["UsageMonthlyAgg"]] = relationship(back_populates="model")


class ModelAliasMapping(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "model_alias_mappings"

    selected_model_pattern: Mapped[str] = mapped_column(Text, nullable=False)
    target_model_id: Mapped[UUID] = mapped_column(
        UUID_SQL,
        ForeignKey("model_catalog.id", ondelete="CASCADE"),
        nullable=False,
    )
    priority: Mapped[int]
    is_fallback: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))

    target_model: Mapped["ModelCatalog"] = relationship(back_populates="alias_mappings")

    __table_args__ = (
        UniqueConstraint(
            "selected_model_pattern",
            "priority",
            name="uq_model_alias_mappings_pattern_priority",
        ),
        Index(
            "uq_model_alias_mappings_fallback",
            is_fallback,
            unique=True,
            postgresql_where=text("is_fallback = true"),
        ),
    )


class ModelPricing(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "model_pricing"

    model_id: Mapped[UUID] = mapped_column(
        UUID_SQL,
        ForeignKey("model_catalog.id", ondelete="CASCADE"),
        nullable=False,
    )
    input_price_per_1k: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    output_price_per_1k: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    cache_read_price_per_1k: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    cache_write_5m_price_per_1k: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    cache_write_1h_price_per_1k: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'USD'"))
    effective_from: Mapped[datetime] = mapped_column(TIMESTAMPTZ_SQL, nullable=False)
    active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))

    model: Mapped["ModelCatalog"] = relationship(back_populates="pricings")

    __table_args__ = (
        UniqueConstraint("model_id", "effective_from", name="uq_model_pricing_model_effective"),
        CheckConstraint("input_price_per_1k >= 0", name="input_price_non_negative"),
        CheckConstraint("output_price_per_1k >= 0", name="output_price_non_negative"),
        CheckConstraint("cache_read_price_per_1k >= 0", name="cache_read_price_non_negative"),
        CheckConstraint(
            "cache_write_5m_price_per_1k >= 0",
            name="cache_write_5m_price_non_negative",
        ),
        CheckConstraint(
            "cache_write_1h_price_per_1k >= 0",
            name="cache_write_1h_price_non_negative",
        ),
    )
