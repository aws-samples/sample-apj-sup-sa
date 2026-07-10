"""Discover which Bedrock text models serve which service tiers, and (re)generate
the model registry (:data:`bedrock_bench.registry.MODELS_FILE`).

The Bedrock control plane exposes no "which tiers does this model support" field,
so the only reliable signal is to *probe*: send one tiny streaming request per
(model, transport, tier) and record whether it is accepted and which tier is
actually served. A model is included in the registry only if it serves
**flex and/or priority** on at least one transport (default-only models have
nothing to compare).

Run as a module to refresh ``models.json``::

    python -m bedrock_bench.discovery --profile my-aws-profile --regions us-west-2,us-east-1

This is intentionally separate from the benchmark run: discovery is cheap and
infrequent (catalog changes), while benchmarking is the expensive, paced part.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config

from .auth import AuthBroker
from .config import validate_region
from .registry import MODELS_FILE

logger = logging.getLogger("bedrock_bench.discovery")

# A tiny, cheap probe. We only need to know the request is accepted and what
# tier is served — not to measure anything.
_PROBE_PROMPT = "Reply with the single character: y"
_PROBE_MAX_TOKENS = 8
_PROBE_TIERS: tuple[str | None, ...] = (None, "flex", "priority")  # None == default

# Utility / non-chat families to skip (rerankers, vision-only, audio).
_SKIP_SUBSTRINGS = ("rerank", "pegasus", "palmyra-vision", "voxtral", "embed")

# Provider prefix -> family label for reports.
_PROVIDER_FAMILY = {
    "amazon": "Amazon Nova",
    "anthropic": "Anthropic Claude",
    "deepseek": "DeepSeek",
    "google": "Google Gemma",
    "minimax": "MiniMax",
    "mistral": "Mistral",
    "moonshotai": "Kimi (Moonshot)",
    "moonshot": "Kimi (Moonshot)",
    "nvidia": "NVIDIA Nemotron",
    "openai": "OpenAI GPT-OSS",
    "qwen": "Qwen",
    "zai": "GLM (Z.AI)",
}


def _canonical_key(model_id: str) -> str:
    """Collapse a transport-specific model id to a stable logical key.

    InvokeModel and Mantle use different id strings for the same model
    (``...-v1:0`` suffixes, ``moonshot.`` vs ``moonshotai.``, Mantle's
    ``-instruct``/``-it`` suffixes). This normalises them so the two transports
    of one model share a registry entry.
    """
    s = re.sub(r"^moonshot\.", "moonshotai.", model_id)
    s = re.sub(r"-v\d+(:\d+)?$", "", s)  # -v1, -v1:0
    s = re.sub(r"-\d+:\d+$", "", s)  # -1:0 (e.g. gpt-oss-120b-1:0)
    s = re.sub(r":\d+$", "", s)  # trailing :0
    s = re.sub(r"-1$", "", s)  # leftover -1
    s = re.sub(r"-instruct$", "", s)
    s = re.sub(r"-it$", "", s)
    return s


def _family(model_id: str) -> str:
    provider = model_id.split(".", 1)[0]
    return _PROVIDER_FAMILY.get(provider, provider.title())


def _display_name(key: str) -> str:
    """Human label like 'Qwen3 235B A22B 2507' from a logical key.

    Includes the provider token so names are unambiguous in reports (e.g.
    'Deepseek V3.2', not just 'V3.2').
    """
    provider, _, body = key.partition(".")  # split only the provider prefix
    body_words = body.replace("-", " ").replace("_", " ")
    # Avoid duplication when the model name already carries the brand
    # (e.g. 'qwen.qwen3-...', 'minimax.minimax-m2', 'moonshotai.kimi-...').
    first = body_words.split(" ", 1)[0].lower()
    brand_in_body = first.startswith(provider.lower()[:4]) or (
        provider == "moonshotai" and first.startswith("kimi")
    )
    pretty = (body_words if brand_in_body else f"{provider} {body_words}").title()
    for a, b in (
        ("Glm", "GLM"),
        ("Gpt", "GPT"),
        ("Oss", "OSS"),
        ("Vl", "VL"),
        ("Zai", "Z.AI"),
        ("Deepseek", "DeepSeek"),
        ("Minimax", "MiniMax"),
        ("Nvidia", "NVIDIA"),
        ("Openai", "OpenAI"),
    ):
        pretty = pretty.replace(a, b)
    return pretty


def _is_nova(model_id: str) -> bool:
    return model_id.startswith("amazon.nova")


def _find_profile(profiles: dict[str, dict], model_arn: str) -> str | None:
    """Best inference-profile id for ``model_arn`` (prefer global.* then us.*)."""
    matches = [
        pid
        for pid, prof in profiles.items()
        for m in prof.get("models", [])
        if m.get("modelArn") == model_arn
    ]
    matches.sort(key=lambda x: (not x.startswith("global."), not x.startswith("us."), x))
    return matches[0] if matches else None


@dataclass
class Candidate:
    """A (model, transport, region) we will probe for tier support."""

    model_id: str
    transport: str  # "invoke" | "mantle"
    region: str
    nova: bool = False


@dataclass
class ProbeOutcome:
    """Result of probing one candidate across all tiers."""

    candidate: Candidate
    has_flex: bool = False
    has_priority: bool = False
    tier_detail: dict = field(default_factory=dict)


class Discoverer:
    """Enumerates candidates and probes each for served tiers."""

    def __init__(self, profile: str, regions: tuple[str, ...], max_workers: int = 12):
        self._broker = AuthBroker(profile=profile)
        self._session = boto3.Session(profile_name=profile)
        self._regions = regions
        self._max_workers = max_workers
        self._probe_cfg = Config(retries={"max_attempts": 1}, read_timeout=60, connect_timeout=10)

    # --- enumeration -------------------------------------------------------
    def candidates(self) -> list[Candidate]:
        """All text-in/text-out models worth probing, on each transport."""
        return self._invoke_candidates() + self._mantle_candidates()

    def _invoke_candidates(self) -> list[Candidate]:
        out: dict[str, Candidate] = {}
        for region in self._regions:
            bedrock = self._session.client("bedrock", region_name=region)
            fms = bedrock.list_foundation_models(byOutputModality="TEXT")["modelSummaries"]
            profiles = {
                p["inferenceProfileId"]: p
                for p in bedrock.list_inference_profiles(maxResults=1000).get(
                    "inferenceProfileSummaries", []
                )
            }

            for m in fms:
                mid = m["modelId"]
                if not m.get("responseStreamingSupported"):
                    continue
                if "TEXT" not in m.get("inputModalities", []) or "TEXT" not in m.get(
                    "outputModalities", []
                ):
                    continue
                if any(s in mid for s in _SKIP_SUBSTRINGS):
                    continue
                its = m.get("inferenceTypesSupported", [])
                if "ON_DEMAND" in its:
                    invoke_id: str | None = mid
                elif "INFERENCE_PROFILE" in its:
                    invoke_id = _find_profile(profiles, m["modelArn"])
                else:
                    continue  # PROVISIONED-only / not invokable on-demand
                if not invoke_id or invoke_id in out:
                    continue
                out[invoke_id] = Candidate(
                    model_id=invoke_id, transport="invoke", region=region, nova=_is_nova(invoke_id)
                )
        return list(out.values())

    def _mantle_candidates(self) -> list[Candidate]:
        from openai import OpenAI

        out: dict[str, Candidate] = {}
        for region in self._regions:
            try:
                client = OpenAI(
                    api_key=self._broker.mantle_token(region),
                    base_url=self._broker.mantle_base_url(region),
                )
                for model in client.models.list().data:
                    mid = model.id
                    if any(s in mid for s in _SKIP_SUBSTRINGS) or mid in out:
                        continue
                    out[mid] = Candidate(model_id=mid, transport="mantle", region=region)
            except Exception as e:  # pragma: no cover - network/permission dependent
                logger.warning("Mantle catalog unavailable in %s: %s", region, e)
        return list(out.values())

    # --- probing -----------------------------------------------------------
    def _probe_invoke(self, c: Candidate, tier: str | None) -> dict:
        client = self._session.client(
            "bedrock-runtime", region_name=c.region, config=self._probe_cfg
        )
        body: dict[str, Any]
        if c.nova:
            body = {
                "messages": [{"role": "user", "content": [{"text": _PROBE_PROMPT}]}],
                "inferenceConfig": {"maxTokens": _PROBE_MAX_TOKENS},
            }
        else:
            body = {
                "messages": [{"role": "user", "content": _PROBE_PROMPT}],
                "max_tokens": _PROBE_MAX_TOKENS,
            }
        kwargs = dict(
            modelId=c.model_id,
            body=json.dumps(body),
            accept="application/json",
            contentType="application/json",
        )
        if tier:
            kwargs["serviceTier"] = tier
        try:
            resp = client.invoke_model_with_response_stream(**kwargs)
            served = (
                resp.get("ResponseMetadata", {})
                .get("HTTPHeaders", {})
                .get("x-amzn-bedrock-service-tier")
            )
            for _event in resp["body"]:  # touch the stream so errors surface
                break
            return {"ok": True, "served": served}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:140]}"}

    def _probe_mantle(self, c: Candidate, tier: str | None) -> dict:
        from openai import OpenAI

        try:
            client = OpenAI(
                api_key=self._broker.mantle_token(c.region),
                base_url=self._broker.mantle_base_url(c.region),
                max_retries=0,
                timeout=60,
            )
            kwargs: dict[str, Any] = dict(
                model=c.model_id,
                messages=[{"role": "user", "content": _PROBE_PROMPT}],
                max_tokens=_PROBE_MAX_TOKENS,
                stream=True,
            )
            if tier:
                kwargs["extra_body"] = {"service_tier": tier}
            served = None
            for chunk in client.chat.completions.create(**kwargs):
                served = getattr(chunk, "service_tier", served) or served
                break
            return {"ok": True, "served": served}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:140]}"}

    def _probe_candidate(self, c: Candidate) -> ProbeOutcome:
        outcome = ProbeOutcome(candidate=c)
        for tier in _PROBE_TIERS:
            label = tier or "default"
            if c.transport == "invoke":
                res = self._probe_invoke(c, tier)
            else:
                res = self._probe_mantle(c, tier)
            outcome.tier_detail[label] = res
            time.sleep(0.05)  # be gentle even on probes

        # A tier "counts" only if accepted AND actually served as requested.
        def served_as(name: str) -> bool:
            r = outcome.tier_detail.get(name, {})
            return bool(r.get("ok")) and (r.get("served") or "").lower() == name

        outcome.has_flex = served_as("flex")
        outcome.has_priority = served_as("priority")
        return outcome

    def probe_all(self, candidates: list[Candidate]) -> list[ProbeOutcome]:
        results: list[ProbeOutcome] = []
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            futures = {pool.submit(self._probe_candidate, c): c for c in candidates}
            for done, future in enumerate(as_completed(futures), 1):
                outcome = future.result()
                results.append(outcome)
                logger.info(
                    "[%d/%d] %s %s flex=%s priority=%s",
                    done,
                    len(candidates),
                    outcome.candidate.transport,
                    outcome.candidate.model_id,
                    outcome.has_flex,
                    outcome.has_priority,
                )
        return results

    # --- registry assembly -------------------------------------------------
    @staticmethod
    def build_registry(outcomes: list[ProbeOutcome]) -> list[dict]:
        """Fold probe outcomes into logical models with per-transport tiers.

        Only models serving flex and/or priority on some transport are kept.
        """
        models: dict[str, dict] = {}
        for o in outcomes:
            if not (o.has_flex or o.has_priority):
                continue
            c = o.candidate
            key = _canonical_key(c.model_id)
            model = models.setdefault(
                key,
                {
                    "key": key,
                    "family": _family(c.model_id),
                    "display_name": _display_name(key),
                    "invoke_id": None,
                    "invoke_region": None,
                    "invoke_tiers": [],
                    "mantle_id": None,
                    "mantle_region": None,
                    "mantle_tiers": [],
                    "payload_style": "nova" if c.nova else "openai",
                    "notes": "",
                },
            )
            tiers = ["default"]
            if o.has_flex:
                tiers.append("flex")
            if o.has_priority:
                tiers.append("priority")
            if c.transport == "invoke":
                model["invoke_id"] = c.model_id
                model["invoke_region"] = c.region
                model["invoke_tiers"] = tiers
            else:
                model["mantle_id"] = c.model_id
                model["mantle_region"] = c.region
                model["mantle_tiers"] = tiers
        return sorted(models.values(), key=lambda m: (m["family"], m["key"]))

    def run(self) -> list[dict]:
        cands = self.candidates()
        logger.info("Probing %d (model, transport) candidates ...", len(cands))
        outcomes = self.probe_all(cands)
        registry = self.build_registry(outcomes)
        logger.info("%d models qualify (serve flex and/or priority).", len(registry))
        return registry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bedrock_bench.discovery",
        description=(
            "Probe Bedrock text models for flex/priority support and regenerate models.json."
        ),
    )
    parser.add_argument(
        "--profile",
        default=os.environ.get("BEDROCK_BENCH_PROFILE"),
        help="AWS named profile. Defaults to $BEDROCK_BENCH_PROFILE, else the "
        "standard boto3 credential chain.",
    )
    parser.add_argument("--regions", default="us-west-2,us-east-1", help="Comma-separated regions.")
    parser.add_argument("--max-workers", type=int, default=12, help="Concurrent probes.")
    parser.add_argument(
        "--output", default=str(MODELS_FILE), help="Where to write the generated registry JSON."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the registry to stdout, do not write."
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    regions = tuple(validate_region(r.strip()) for r in args.regions.split(",") if r.strip())
    discoverer = Discoverer(profile=args.profile, regions=regions, max_workers=args.max_workers)
    registry = discoverer.run()

    payload = json.dumps(registry, indent=2)
    if args.dry_run:
        print(payload)
    else:
        Path(args.output).write_text(payload)
        logger.info("Wrote %d models to %s", len(registry), args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
