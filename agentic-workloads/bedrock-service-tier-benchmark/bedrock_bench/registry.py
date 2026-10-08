"""The model registry (schema v2): what can be benchmarked, and how.

Each model lists **offerings**: one per (endpoint, API, scope) it is reachable
on, with the model or inference-profile id, the source regions, and the tiers
verified for it. Example::

    {
      "key": "zai.glm-5.3",
      "family": "GLM (Z.AI)",
      "display_name": "GLM 5.3",
      "reasoning": true,
      "body_style": "openai",
      "cache": {"implicit": true, "explicit": true, "min_tokens": 1024},
      "offerings": [
        {"endpoint": "runtime", "api": "converse_stream", "scope": "geo",
         "model_id": "us.zai.glm-5.3", "regions": ["us-east-1", "us-west-2"],
         "tiers": ["default", "flex", "priority"]}
      ],
      "sources": ["https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-zai-glm-5-3.html"],
      "verified_at": "2026-10-08"
    }

The file is produced by ``bedrock-bench-discover`` (live probes on top of the
model cards) and, in the web deployment, kept in Aurora by the discovery agent.
Only models with more than one tier on some offering are benchmarked.
"""

from __future__ import annotations

import functools
import json
from dataclasses import dataclass, field
from pathlib import Path

from .config import Api, Endpoint, Scope, Tier

MODELS_FILE = Path(__file__).with_name("models.json")
SCHEMA_VERSION = 2


@dataclass(frozen=True)
class Offering:
    endpoint: Endpoint
    api: Api
    scope: Scope
    model_id: str
    regions: tuple[str, ...]
    tiers: tuple[Tier, ...]
    #: Mantle path prefix for OpenAI-compatible APIs ("/v1" or "/openai/v1").
    base_path: str | None = None


@dataclass(frozen=True)
class CacheSupport:
    implicit: bool = False
    explicit: bool = False
    min_tokens: int | None = None


@dataclass(frozen=True)
class ModelSpec:
    key: str
    family: str
    display_name: str
    offerings: tuple[Offering, ...]
    reasoning: bool = False
    body_style: str = "openai"
    cache: CacheSupport = field(default_factory=CacheSupport)
    sources: tuple[str, ...] = ()
    verified_at: str | None = None
    notes: str = ""

    @property
    def multi_tier(self) -> bool:
        """True when some offering serves more than one tier (worth benchmarking)."""
        return any(len(o.tiers) > 1 for o in self.offerings)


def _offering(d: dict) -> Offering:
    return Offering(
        endpoint=Endpoint(d["endpoint"]),
        api=Api(d["api"]),
        scope=Scope(d["scope"]),
        model_id=d["model_id"],
        regions=tuple(d.get("regions", [])),
        tiers=tuple(Tier(t) for t in d.get("tiers", [])),
        base_path=d.get("base_path"),
    )


def spec_from_dict(d: dict) -> ModelSpec:
    c = d.get("cache") or {}
    return ModelSpec(
        key=d["key"],
        family=d.get("family", d["key"].split(".", 1)[0]),
        display_name=d.get("display_name", d["key"]),
        offerings=tuple(_offering(o) for o in d.get("offerings", [])),
        reasoning=bool(d.get("reasoning", False)),
        body_style=d.get("body_style", "openai"),
        cache=CacheSupport(
            implicit=bool(c.get("implicit", False)),
            explicit=bool(c.get("explicit", False)),
            min_tokens=c.get("min_tokens"),
        ),
        sources=tuple(d.get("sources", [])),
        verified_at=d.get("verified_at"),
        notes=d.get("notes", ""),
    )


def spec_to_dict(s: ModelSpec) -> dict:
    return {
        "key": s.key,
        "family": s.family,
        "display_name": s.display_name,
        "reasoning": s.reasoning,
        "body_style": s.body_style,
        "cache": {
            "implicit": s.cache.implicit,
            "explicit": s.cache.explicit,
            "min_tokens": s.cache.min_tokens,
        },
        "offerings": [
            {
                "endpoint": o.endpoint.value,
                "api": o.api.value,
                "scope": o.scope.value,
                "model_id": o.model_id,
                "regions": list(o.regions),
                "tiers": [t.value for t in o.tiers],
                **({"base_path": o.base_path} if o.base_path else {}),
            }
            for o in s.offerings
        ],
        "sources": list(s.sources),
        "verified_at": s.verified_at,
        "notes": s.notes,
    }


def load_registry(path: Path | None = None) -> list[ModelSpec]:
    """Load and validate a registry file.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the schema version is unsupported.
    """
    path = path or MODELS_FILE
    if not path.exists():
        raise FileNotFoundError(
            f"Model registry {path} not found. Generate it with `bedrock-bench-discover`."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"{path}: expected schema_version {SCHEMA_VERSION}; regenerate with "
            "`bedrock-bench-discover`."
        )
    return [spec_from_dict(d) for d in data.get("models", [])]


def save_registry(specs: list[ModelSpec], path: Path, generated_at: str) -> None:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "models": [spec_to_dict(s) for s in sorted(specs, key=lambda s: (s.family, s.key))],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


@functools.lru_cache(maxsize=4)
def _cached(path: str) -> tuple[ModelSpec, ...]:
    return tuple(load_registry(Path(path)))


def select(
    families: tuple[str, ...] | None = None,
    keys: tuple[str, ...] | None = None,
    path: Path | None = None,
) -> list[ModelSpec]:
    """Registry entries matching the filters (case-insensitive family substring, exact key)."""
    specs = list(_cached(str(path or MODELS_FILE)))
    if families:
        wanted = [f.lower() for f in families]
        specs = [s for s in specs if any(w in s.family.lower() for w in wanted)]
    if keys:
        kset = set(keys)
        specs = [s for s in specs if s.key in kset]
    return specs
