"""Discover benchmarkable models and regenerate the registry (``bedrock-bench-discover``).

No Bedrock API reports which service tiers, APIs or inference scopes a model
supports. Discovery therefore combines:

1. **Documentation** — the endpoint-availability page and each model card
   (parsed by :mod:`bedrock_bench.catalog`): APIs per endpoint, tiers, model and
   inference-profile ids, regions per scope, prompt-caching support.
2. **Live probes** (``--probe``, recommended) — one tiny request per
   (model, endpoint, scope, tier) on a representative API, in one source
   region. A tier is kept only if Bedrock accepts it and reports serving it (or
   reports nothing, for the default tier). Probes also detect reasoning models.

Only text-output models documented with more than one on-demand tier are kept.
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from . import catalog
from .apis.base import Request
from .auth import AuthBroker
from .cells import Cell
from .config import Api, CacheMode, Endpoint, PromptSize, Scope, Tier, validate_region
from .registry import (
    MODELS_FILE,
    CacheSupport,
    ModelSpec,
    Offering,
    load_registry,
    save_registry,
)
from .runner import build_adapter, served_matches

logger = logging.getLogger("bedrock_bench.discovery")

#: Card "APIs supported" labels -> streaming APIs benchmarked by default.
_CARD_APIS: dict[str, tuple[Api, ...]] = {
    "Converse": (Api.CONVERSE_STREAM, Api.CONVERSE),
    "Invoke": (Api.INVOKE_STREAM, Api.INVOKE),
    "Chat Completions": (Api.CHAT_COMPLETIONS,),
    "Responses": (Api.RESPONSES,),
    "Messages": (Api.MESSAGES,),
}
_CARD_TIERS = {"Standard": Tier.DEFAULT, "Flex": Tier.FLEX, "Priority": Tier.PRIORITY}
_ENDPOINTS = {"bedrock-runtime": Endpoint.RUNTIME, "bedrock-mantle": Endpoint.MANTLE}
_PROBE_API = {Endpoint.RUNTIME: Api.CONVERSE_STREAM, Endpoint.MANTLE: Api.CHAT_COMPLETIONS}


#: Geo inference-profile prefix -> source-region prefixes it serves. A model card lists
#: one "Geo" column for all of its geo profiles, so each profile is narrowed to its
#: own geography (sending ``apac.*`` from us-east-1 fails with "model identifier is invalid").
_GEO_REGION_PREFIXES: dict[str, tuple[str, ...]] = {
    "us": ("us-",),
    "eu": ("eu-",),
    "apac": ("ap-",),
    "jp": ("ap-northeast-1", "ap-northeast-3"),
    "au": ("ap-southeast-2", "ap-southeast-4"),
    "ca": ("ca-",),
    "in": ("ap-south-1", "ap-south-2"),
}


def geo_regions(profile_id: str, regions: tuple[str, ...]) -> tuple[str, ...]:
    """The subset of ``regions`` a geo profile id (``us.``, ``eu.``, ...) can be called from."""
    prefixes = _GEO_REGION_PREFIXES.get(profile_id.split(".", 1)[0])
    if prefixes is None:
        return regions
    return tuple(r for r in regions if r.startswith(prefixes))


def _body_style(model_id: str) -> str:
    base = (
        model_id.split(".", 1)[-1]
        if model_id.split(".", 1)[0] in ("us", "eu", "apac", "jp", "au", "ca", "in", "global")
        else model_id
    )
    if base.startswith("anthropic."):
        return "anthropic"
    if base.startswith("amazon.nova"):
        return "nova"
    return "openai"


def offerings_from_card(card: catalog.ModelCard) -> tuple[str, list[Offering]]:
    """Derive (model key, offerings) from a parsed model card (documentation only)."""
    tiers = tuple(t for label, t in _CARD_TIERS.items() if card.tiers.get(label))
    if Tier.DEFAULT not in tiers:
        tiers = (Tier.DEFAULT, *tiers)
    apis = [a for label in card.apis for a in _CARD_APIS.get(label, ())]
    endpoints = [_ENDPOINTS[e] for e in card.endpoints if e in _ENDPOINTS]
    key = ""
    offs: list[Offering] = []
    for row in card.access:
        ep = _ENDPOINTS.get(str(row.get("Endpoint", "")).strip())
        model_id = str(row.get("Model ID", "")).strip()
        if not ep or not model_id or ep not in endpoints:
            continue
        key = key or model_id
        scope_ids: list[tuple[Scope, str, str]] = []
        in_regions = [r for r, v in card.regions.items() if v.get("In-Region")]
        if in_regions:
            scope_ids.append((Scope.IN_REGION, model_id, "In-Region"))
        for gid in row.get("geo_ids") or []:  # type: ignore[union-attr]
            scope_ids.append((Scope.GEO, str(gid), "Geo"))
        gl = str(row.get("Global inference ID", "")).strip()
        if gl.startswith("global."):
            scope_ids.append((Scope.GLOBAL, gl, "Global"))
        for scope, mid, col in scope_ids:
            regions = tuple(sorted(r for r, v in card.regions.items() if v.get(col)))
            if scope is Scope.GEO:
                regions = geo_regions(mid, regions)
            if not regions:
                continue
            for api in apis:
                if api not in (
                    {Api.CHAT_COMPLETIONS, Api.RESPONSES, Api.MESSAGES}
                    if ep is Endpoint.MANTLE
                    else set(Api)
                ):
                    continue
                offs.append(Offering(ep, api, scope, mid, regions, tiers))
    return key, offs


def discover_from_docs(only: set[str] | None = None) -> list[ModelSpec]:
    """Build specs from the documentation (no AWS calls)."""
    rows = catalog.parse_endpoint_availability(catalog.fetch(catalog.AVAILABILITY_URL))
    specs: list[ModelSpec] = []
    for row in rows:
        if not row.card_url or (only and row.model_name not in only):
            continue
        try:
            card = catalog.parse_model_card(catalog.fetch(row.card_url), row.card_url)
        except Exception as e:  # noqa: BLE001 - one bad page must not stop discovery
            logger.warning("skipping %s: %s", row.model_name, e)
            continue
        if "Text" not in card.output_modalities or not card.features.get("Response streaming"):
            continue
        if sum(card.tiers.get(t, False) for t in ("Standard", "Flex", "Priority")) < 2:
            continue
        key, offs = offerings_from_card(card)
        if not offs:
            continue
        specs.append(
            ModelSpec(
                key=key,
                family=row.section,
                display_name=card.title or row.model_name,
                offerings=tuple(offs),
                body_style=_body_style(key),
                cache=CacheSupport(
                    implicit=card.implicit_cache,
                    explicit=bool(
                        card.explicit_cache and card.explicit_cache.get("supported") == "Yes"
                    ),
                    min_tokens=card.cache_min_tokens,
                ),
                sources=(row.card_url,),
                notes="documentation only (not probed)",
            )
        )
    return specs


def probe(specs: list[ModelSpec], broker: AuthBroker, regions: tuple[str, ...]) -> list[ModelSpec]:
    """Verify tiers live: one request per (model, endpoint, scope, tier) on a probe API."""
    out: list[ModelSpec] = []
    today = datetime.now(timezone.utc).date().isoformat()
    for spec in specs:
        verified: dict[tuple[Endpoint, Scope, str], tuple[Tier, ...]] = {}
        reasoning = False
        for off in spec.offerings:
            group = (off.endpoint, off.scope, off.model_id)
            if group in verified or off.api is not _PROBE_API[off.endpoint]:
                continue
            region = next((r for r in regions if r in off.regions), None)
            if region is None:
                continue
            ok: list[Tier] = []
            for tier in off.tiers:
                cell = Cell(spec, off, region, PromptSize.SMALL, CacheMode.COLD, tier)
                m = build_adapter(cell, broker).send(
                    Request(
                        off.model_id,
                        region,
                        "Probe.",
                        "Reply with the single word: ok",
                        32,
                        None if tier.is_default else tier.value,
                        temperature=None,
                        timeout=300,
                    )
                )
                served = served_matches(tier, m.served_tier)
                # A non-default tier counts only when Bedrock reports serving it.
                if m.error is None and (served is True or (tier.is_default and served is None)):
                    ok.append(tier)
                streamed_reasoning = m.ttft is not None and m.ttft != m.ttfat
                reasoning = reasoning or bool(m.usage.reasoning_tokens) or streamed_reasoning
                logger.info(
                    "probe %s %s %s %s -> %s",
                    spec.key,
                    off.endpoint.value,
                    off.scope.value,
                    tier.value,
                    "ok" if m.error is None else m.error_kind,
                )
            verified[group] = tuple(ok)
        offs = tuple(
            Offering(
                o.endpoint,
                o.api,
                o.scope,
                o.model_id,
                o.regions,
                verified.get((o.endpoint, o.scope, o.model_id), ()),
                o.base_path,
            )
            for o in spec.offerings
            if verified.get((o.endpoint, o.scope, o.model_id))
        )
        if not any(len(o.tiers) > 1 for o in offs):
            logger.info("dropping %s: fewer than two tiers verified", spec.key)
            continue
        out.append(
            ModelSpec(
                spec.key,
                spec.family,
                spec.display_name,
                offs,
                reasoning,
                spec.body_style,
                spec.cache,
                spec.sources,
                today,
                "probed live",
            )
        )
    return out


def overwrite_refusal(new: list[ModelSpec], existing: list[ModelSpec]) -> str | None:
    """Why writing ``new`` over ``existing`` would lose verified data (``None`` = safe).

    A throttled probe can drop models, and a docs-only run replaces live-verified
    tiers with documented ones; both need ``--force``.
    """
    if not new:
        return "no models discovered"
    if len(new) < len(existing):
        return f"would shrink the registry from {len(existing)} to {len(new)} models"
    if any(s.verified_at for s in existing) and not any(s.verified_at for s in new):
        return "would replace live-probed entries with documentation-only ones"
    return None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="bedrock-bench-discover", description=__doc__.splitlines()[0])
    p.add_argument(
        "--aws-profile",
        "--profile",
        dest="aws_profile",
        default=os.environ.get("BEDROCK_BENCH_PROFILE"),
    )
    p.add_argument("--regions", default="us-east-1,us-west-2", help="Probe source regions.")
    p.add_argument(
        "--models", help="Comma-separated model names as shown in the docs (e.g. 'GLM 5.3')."
    )
    p.add_argument(
        "--probe",
        action="store_true",
        help="Verify tiers with live requests (incurs a small cost).",
    )
    p.add_argument("--output", type=Path, default=MODELS_FILE)
    p.add_argument(
        "--force",
        action="store_true",
        help="Write even if the result is empty or smaller than the existing registry.",
    )
    p.add_argument("--dry-run", action="store_true", help="Print a summary; do not write.")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    regions = tuple(validate_region(r.strip()) for r in a.regions.split(",") if r.strip())
    only = {m.strip() for m in a.models.split(",")} if a.models else None
    specs = discover_from_docs(only)
    logger.info("%d multi-tier text models documented", len(specs))
    if a.probe:
        specs = probe(specs, AuthBroker(profile=a.aws_profile), regions)
    for s in specs:
        tiers = sorted({t.value for o in s.offerings for t in o.tiers})
        print(
            f"{s.key:40s} {s.display_name:28s} offerings={len(s.offerings):3d} "
            f"tiers={','.join(tiers)}"
        )
    if not a.dry_run:
        try:
            existing = load_registry(a.output) if a.output.exists() else []
        except ValueError:
            existing = []  # old schema: always replaceable
        reason = overwrite_refusal(specs, existing)
        if reason and not a.force:
            logger.error("%s; refusing to overwrite %s (use --force)", reason, a.output)
            return 1
        save_registry(specs, a.output, datetime.now(timezone.utc).isoformat(timespec="seconds"))
        logger.info("wrote %d models to %s", len(specs), a.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
