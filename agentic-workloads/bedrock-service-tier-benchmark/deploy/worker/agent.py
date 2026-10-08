"""Discovery agent: keeps the Aurora model registry in step with what Bedrock actually serves.

The agent (Strands, on Bedrock) decides *what* to look at; deterministic tools do
everything that must be right:

* reading the registry from Aurora,
* parsing the Bedrock documentation (``bedrock_bench.catalog``, host-pinned),
* reading the AWS What's New and Machine Learning blog feeds (host-pinned),
* probing a model live (``bedrock_bench.discovery.probe``) and persisting it.

The persist step is inside the probe tool and only writes what the probe verified,
so no model output can put unverified data in the database.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import httpx
from bedrock_bench.auth import AuthBroker
from bedrock_bench.discovery import discover_from_docs, probe
from defusedxml import ElementTree

from . import db

logger = logging.getLogger("worker.agent")

FEEDS = {
    "whats_new": "https://aws.amazon.com/about-aws/whats-new/recent/feeds/",
    "ml_blog": "https://aws.amazon.com/blogs/machine-learning/feed/",
}
_FEED_HOST = "aws.amazon.com"
_MAX_BYTES = 4 * 1024 * 1024
_KEYWORDS = ("bedrock", "service tier", "flex", "priority", "inference profile", "mantle")
PROBE_REGIONS = tuple(r.strip() for r in os.environ.get("PROBE_REGIONS", "us-east-1,us-west-2").split(","))

SYSTEM_PROMPT = """You maintain the model registry of an Amazon Bedrock service-tier benchmark.
The registry lists text models that Bedrock serves on more than one service tier (Standard plus
Flex and/or Priority), with the endpoints, APIs, inference scopes and regions where each tier works.

Work in this order and stop when done:
1. Call registry_summary to see what is stored.
2. Call documented_models to see what the Bedrock documentation lists today.
3. Call recent_announcements to find Bedrock launches from the last 14 days (new models, new tiers,
   new regions, new APIs or endpoints).
4. Decide which models need a live probe:
   - if the registry is empty: every documented model;
   - otherwise: models that are documented but missing from the registry, models whose documented
     tiers, APIs, endpoints or regions differ from the registry, models named in a recent
     announcement, and models whose verified_at is older than 30 days.
5. Call probe_and_store with the model names exactly as documented_models prints them (batches of at
   most 10). This tool probes live and stores only what it verified.
6. Reply with a short report: what you probed, what changed, what was dropped and why.

Rules:
- Never invent model names, IDs, regions or tiers. Use only names returned by documented_models.
- Announcements are hints, not facts: a model counts only after probe_and_store verified it.
- Do not try to store anything any other way. There is no other write tool.
- If a tool fails, report the failure; do not retry more than once.
"""


# ------------------------------------------------------------------ deterministic helpers
def _fetch_feed(url: str) -> bytes:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != _FEED_HOST:
        raise ValueError(f"refusing to fetch {url}")
    with httpx.Client(timeout=30, follow_redirects=False) as c, c.stream("GET", url) as r:
        r.raise_for_status()
        data = bytearray()
        for chunk in r.iter_bytes():
            data += chunk
            if len(data) > _MAX_BYTES:
                raise ValueError("feed too large")
    return bytes(data)


def parse_feed(xml: bytes, days: int, now: datetime | None = None) -> list[dict[str, str]]:
    """Bedrock-related RSS items from the last ``days`` days (defusedxml: no entity expansion)."""
    now = now or datetime.now(timezone.utc)
    root = ElementTree.fromstring(xml)
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        desc = (item.findtext("description") or "").strip()
        try:
            when = parsedate_to_datetime(item.findtext("pubDate") or "")
        except (TypeError, ValueError):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        text = f"{title} {desc}".lower()
        if when >= now - timedelta(days=days) and any(k in text for k in _KEYWORDS):
            out.append({"title": title, "date": when.date().isoformat(), "link": item.findtext("link") or ""})
    return out


def registry_summary_text() -> str:
    specs = db.load_registry()
    if not specs:
        return "The registry is EMPTY."
    lines = [f"{len(specs)} models:"]
    for s in specs:
        tiers = sorted({t.value for o in s.offerings for t in o.tiers})
        apis = sorted({f"{o.endpoint.value}:{o.api.value}" for o in s.offerings})
        lines.append(f"- {s.display_name} ({s.key}) verified_at={s.verified_at} tiers={tiers} apis={apis}")
    return "\n".join(lines)


def documented_models_text() -> tuple[str, dict[str, Any]]:
    specs = discover_from_docs()
    by_name = {s.display_name: s for s in specs}
    lines = [f"{len(specs)} documented multi-tier text models (name | key | tiers | apis | regions):"]
    for s in specs:
        tiers = sorted({t.value for o in s.offerings for t in o.tiers})
        apis = sorted({f"{o.endpoint.value}:{o.api.value}" for o in s.offerings})
        regions = sorted({r for o in s.offerings for r in o.regions})
        lines.append(f"- {s.display_name} | {s.key} | {tiers} | {apis} | {len(regions)} regions")
    return "\n".join(lines), by_name


def probe_and_store_names(names: list[str], documented: dict[str, Any], broker: AuthBroker) -> str:
    unknown = [n for n in names if n not in documented]
    wanted = [documented[n] for n in names if n in documented][:10]
    verified = probe(wanted, broker, PROBE_REGIONS)
    stored = []
    for spec in verified:
        db.upsert_spec(spec)
        db.record_event("verified", spec.key, {"offerings": len(spec.offerings)})
        stored.append(spec.key)
    dropped = sorted({s.key for s in wanted} - set(stored))
    for key in dropped:
        db.record_event("not_verified", key, {"reason": "fewer than two tiers verified"})
    return json.dumps({"stored": stored, "not_verified": dropped, "unknown_names": unknown})


# ------------------------------------------------------------------ Strands agent
def build_agent(broker: AuthBroker, model_id: str):
    from strands import Agent, tool
    from strands.models import BedrockModel

    state: dict[str, Any] = {"documented": {}}

    @tool
    def registry_summary() -> str:
        """Models currently stored in the benchmark registry (Aurora), with tiers, APIs and verified_at."""
        return registry_summary_text()

    @tool
    def documented_models() -> str:
        """Multi-tier text models listed in the Amazon Bedrock documentation today (parsed model cards)."""
        text, state["documented"] = documented_models_text()
        return text

    @tool
    def recent_announcements(days: int = 14) -> str:
        """Bedrock-related items from AWS What's New and the AWS Machine Learning blog in the last N days."""
        items = []
        for name, url in FEEDS.items():
            try:
                items += [{**i, "source": name} for i in parse_feed(_fetch_feed(url), min(days, 60))]
            except Exception as e:  # noqa: BLE001 - a feed outage must not stop discovery
                items.append({"source": name, "error": type(e).__name__})
        return json.dumps(items[:50])

    @tool
    def probe_and_store(model_names: list[str]) -> str:
        """Probe up to 10 documented models live (one tiny request per tier) and store what is verified.

        Args:
            model_names: model names exactly as printed by documented_models.
        """
        if not state["documented"]:
            state["documented"] = documented_models_text()[1]
        return probe_and_store_names(model_names, state["documented"], broker)

    return Agent(
        model=BedrockModel(model_id=model_id, temperature=0.0, max_tokens=4096),
        system_prompt=SYSTEM_PROMPT,
        tools=[registry_summary, documented_models, recent_announcements, probe_and_store],
        callback_handler=None,
    )


def run_discovery(broker: AuthBroker, model_id: str) -> str:
    agent = build_agent(broker, model_id)
    result = agent("Update the registry now.")
    report = str(result)
    db.record_event("agent_report", None, {"report": report[:4000]})
    return report
