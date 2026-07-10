"""The model registry — the source of truth for *what* gets benchmarked.

The registry is **generated**, not hand-curated: :mod:`bedrock_bench.discovery`
probes every Bedrock text model on both transports for which service tiers it
actually accepts *and is served*, then writes :data:`MODELS_FILE` (``models.json``).
This module loads that file into :class:`ModelSpec` objects.

Inclusion rule (per the benchmark's purpose): a model appears only if it supports
**flex and/or priority** on at least one transport. Default-only models are
excluded — there is nothing to compare. Each transport carries its own list of
supported tiers (``default`` plus whichever of ``flex``/``priority`` were served),
so the cell expander never schedules a tier a model can't serve.

Facts the generator bakes in (do not "simplify" away):

* InvokeModel IDs and Mantle IDs are **different strings** for the same logical
  model (e.g. ``openai.gpt-oss-120b-1:0`` vs ``openai.gpt-oss-120b``;
  ``moonshot.*`` vs ``moonshotai.*``; Mantle often appends ``-instruct``). The
  generator canonicalises them to one logical ``key`` and stores both ids.
* Availability is per-region; the generator records the region each id was found
  in, and :meth:`ModelSpec.resolve_region` honours the configured preference.
* Amazon Nova models use the Converse-native payload/stream shape
  (``payload_style == "nova"``); everything else is OpenAI-shaped.

To refresh after Bedrock's catalog changes, run::

    python -m bedrock_bench.discovery --profile my-aws-profile
"""

from __future__ import annotations

import functools
import json
from dataclasses import dataclass
from pathlib import Path

from .config import PayloadStyle, Tier, Transport

#: Generated catalog file (produced by :mod:`bedrock_bench.discovery`).
MODELS_FILE = Path(__file__).with_name("models.json")


@dataclass(frozen=True)
class ModelSpec:
    """One benchmarkable model and how to reach it on each transport.

    Attributes:
        key: Stable logical slug shared across transports (used in result keys).
        family: Provider/family label for grouping in reports.
        display_name: Human label for reports.
        invoke_id: Model/inference-profile ID for InvokeModel, or ``None``.
        invoke_region: Region where ``invoke_id`` was found, or ``None``.
        invoke_tiers: Tiers served on InvokeModel (e.g. ``(DEFAULT, FLEX, PRIORITY)``).
        mantle_id: Model ID for the Mantle OpenAI endpoint, or ``None``.
        mantle_region: Region where ``mantle_id`` was found, or ``None``.
        mantle_tiers: Tiers served on Mantle.
        payload_style: Body/stream schema for the InvokeModel transport.
        notes: Free-form caveats.
    """

    key: str
    family: str
    display_name: str
    invoke_id: str | None
    invoke_region: str | None
    invoke_tiers: tuple[Tier, ...]
    mantle_id: str | None
    mantle_region: str | None
    mantle_tiers: tuple[Tier, ...]
    payload_style: PayloadStyle = PayloadStyle.OPENAI
    notes: str = ""

    # --- transport-keyed accessors ----------------------------------------
    def id_for(self, transport: Transport) -> str | None:
        return self.invoke_id if transport is Transport.INVOKE else self.mantle_id

    def region_for(self, transport: Transport) -> str | None:
        return self.invoke_region if transport is Transport.INVOKE else self.mantle_region

    def tiers_for(self, transport: Transport) -> tuple[Tier, ...]:
        return self.invoke_tiers if transport is Transport.INVOKE else self.mantle_tiers

    def supports(self, transport: Transport) -> bool:
        return self.id_for(transport) is not None and bool(self.tiers_for(transport))

    def resolve_region(self, transport: Transport, preference: tuple[str, ...]) -> str | None:
        """Region to use for ``transport``.

        The generator records exactly one region per (model, transport). If it is
        in the caller's ``preference`` order we return it; otherwise we still fall
        back to the discovered region (the model genuinely lives there), which
        keeps a us-east-1-only model benchmarkable even when the preference lists
        us-west-2 first.
        """
        discovered = self.region_for(transport)
        if discovered is None:
            return None
        for region in preference:
            if region == discovered:
                return discovered
        return discovered


def _spec_from_dict(d: dict) -> ModelSpec:
    def _tiers(values: list[str]) -> tuple[Tier, ...]:
        return tuple(Tier(v) for v in values)

    return ModelSpec(
        key=d["key"],
        family=d["family"],
        display_name=d["display_name"],
        invoke_id=d.get("invoke_id"),
        invoke_region=d.get("invoke_region"),
        invoke_tiers=_tiers(d.get("invoke_tiers", [])),
        mantle_id=d.get("mantle_id"),
        mantle_region=d.get("mantle_region"),
        mantle_tiers=_tiers(d.get("mantle_tiers", [])),
        payload_style=PayloadStyle(d.get("payload_style", "openai")),
        notes=d.get("notes", ""),
    )


def load_registry(path: Path | None = None) -> list[ModelSpec]:
    """Load and validate the generated model registry from JSON.

    Args:
        path: Override for the registry file. Defaults to :data:`MODELS_FILE`.

    Returns:
        The list of :class:`ModelSpec` entries.

    Raises:
        FileNotFoundError: If the registry file does not exist (run
            ``python -m bedrock_bench.discovery`` to generate it).
    """
    path = path or MODELS_FILE
    if not path.exists():
        raise FileNotFoundError(
            f"Model registry {path} not found. Generate it with "
            f"`python -m bedrock_bench.discovery`."
        )
    data = json.loads(path.read_text())
    return [_spec_from_dict(d) for d in data]


@functools.lru_cache(maxsize=1)
def registry() -> tuple[ModelSpec, ...]:
    """Return the loaded registry, reading the JSON file on first use only.

    Loading is lazy and cached so that merely importing this module (e.g. for
    ``--help`` or unit tests of unrelated code) never performs file I/O and
    cannot fail on a missing ``models.json``.
    """
    return tuple(load_registry())


def select(
    families: tuple[str, ...] | None = None,
    keys: tuple[str, ...] | None = None,
) -> list[ModelSpec]:
    """Return registry entries matching the given filters.

    Args:
        families: Keep only these families (case-insensitive substring match on
            the family label). ``None`` = all.
        keys: Keep only these exact logical keys. ``None`` = all.

    Returns:
        The matching :class:`ModelSpec` entries (a fresh list).
    """
    specs = list(registry())
    if families:
        wanted = [f.lower() for f in families]
        specs = [s for s in specs if any(w in s.family.lower() for w in wanted)]
    if keys:
        kset = set(keys)
        specs = [s for s in specs if s.key in kset]
    return specs


def families() -> list[str]:
    """Return distinct family names, in registry order."""
    seen: dict[str, None] = {}
    for s in registry():
        seen.setdefault(s.family, None)
    return list(seen)
