"""A *cell* = the unit of measurement: one model, on one transport, at one tier.

The benchmark matrix is the set of cells; each cell yields ``n`` samples that
become one column of percentiles. This module defines the ``Cell`` value object,
expands a config + registry into the full cell list, and builds the concrete
llmeter endpoint for a cell.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .auth import AuthBroker
from .config import BenchmarkConfig, PayloadStyle, Tier, Transport
from .endpoints import (
    JMESPATH_NOVA,
    JMESPATH_OPENAI,
    FlexBedrockInvokeStream,
    MantleChatStream,
)
from .payloads import build_invoke_payload, build_mantle_payload
from .registry import ModelSpec, select


@dataclass(frozen=True)
class Cell:
    """One (model, transport, tier) experiment in a concrete region."""

    spec: ModelSpec
    transport: Transport
    tier: Tier
    region: str

    @property
    def model_id(self) -> str:
        return self.spec.id_for(self.transport)  # type: ignore[return-value]

    @property
    def domain(self) -> str:
        """Pacing domain key — requests within a domain are serialized at 1/min.

        A domain is (transport, model) so that a model's default+flex samples
        share one cadence (interleaved), while different models run in parallel.
        """
        return f"{self.transport.value}:{self.spec.key}:{self.region}"

    @property
    def label(self) -> str:
        return f"{self.spec.key}|{self.transport.value}|{self.tier.value}|{self.region}"

    def tier_request(self) -> str | None:
        """Wire value to request: ``"flex"`` for flex, ``None`` for Standard."""
        return None if self.tier.is_default else self.tier.value


def expand_cells(config: BenchmarkConfig, specs: list[ModelSpec] | None = None) -> list[Cell]:
    """Build every cell implied by ``config`` and the (filtered) registry.

    A cell is created only when the model supports the (transport, tier)
    combination — the generated registry records exactly which tiers each
    transport serves, so we never schedule, say, a flex cell for a model that
    doesn't serve flex. ``default`` is always benchmarked alongside whichever of
    flex/priority the config requests *and* the model supports, so every
    non-default tier has its default baseline to compare against.
    """
    if specs is None:
        specs = select(families=config.families)

    requested = set(config.tiers)
    cells: list[Cell] = []
    for spec in specs:
        for transport in config.transports:
            if not spec.supports(transport):
                continue
            region = spec.resolve_region(transport, config.regions)
            if region is None:
                continue
            supported = set(spec.tiers_for(transport))
            # Always include default as the baseline if the transport serves it.
            tiers = [t for t in spec.tiers_for(transport) if t in requested or t.is_default]
            # Only keep default if at least one non-default tier will also run,
            # otherwise there is nothing to compare it against.
            non_default = [t for t in tiers if not t.is_default]
            if not non_default:
                continue
            if Tier.DEFAULT in supported and Tier.DEFAULT not in tiers:
                tiers.insert(0, Tier.DEFAULT)
            for tier in tiers:
                cells.append(Cell(spec=spec, transport=transport, tier=tier, region=region))
    return cells


def build_endpoint(cell: Cell, broker: AuthBroker) -> FlexBedrockInvokeStream | MantleChatStream:
    """Construct the llmeter endpoint that will invoke ``cell``."""
    if cell.transport is Transport.INVOKE:
        jmespath = (
            JMESPATH_NOVA if cell.spec.payload_style is PayloadStyle.NOVA else JMESPATH_OPENAI
        )
        return FlexBedrockInvokeStream(
            model_id=cell.model_id,
            region=cell.region,
            bedrock_boto3_client=broker.bedrock_runtime(cell.region),
            service_tier=cell.tier_request(),
            endpoint_name=cell.spec.display_name,
            **jmespath,
        )
    return MantleChatStream(
        model_id=cell.model_id,
        broker=broker,
        region=cell.region,
        service_tier=cell.tier_request(),
        endpoint_name=cell.spec.display_name,
    )


def build_payload(cell: Cell, config: BenchmarkConfig) -> dict[str, Any]:
    """The request payload for ``cell`` (excluding model id / tier / stream flags)."""
    if cell.transport is Transport.INVOKE:
        return build_invoke_payload(cell.spec.payload_style, config.prompt, config.max_tokens)
    return build_mantle_payload(config.prompt, config.max_tokens)
