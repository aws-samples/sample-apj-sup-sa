"""Aurora access for the worker through the RDS Data API (``bench_writer`` role).

All values are bind parameters; JSON columns are passed as text and cast in SQL.
"""

from __future__ import annotations

import json
import os
from typing import Any

import boto3
from bedrock_bench.registry import ModelSpec, spec_from_dict, spec_to_dict

_rds = None


def _client():
    global _rds
    if _rds is None:
        _rds = boto3.client("rds-data")
    return _rds


def _param(name: str, value: Any) -> dict[str, Any]:
    if value is None:
        return {"name": name, "value": {"isNull": True}}
    if isinstance(value, bool):
        return {"name": name, "value": {"booleanValue": value}}
    return {"name": name, "value": {"stringValue": str(value)}}


def execute(sql: str, params: dict[str, Any] | None = None, *, transaction_id: str | None = None) -> list[dict]:
    kw: dict[str, Any] = {
        "resourceArn": os.environ["CLUSTER_ARN"],
        "secretArn": os.environ["WRITER_SECRET_ARN"],
        "database": os.environ.get("DB_NAME", "bench"),
        "sql": sql,
        "parameters": [_param(k, v) for k, v in (params or {}).items()],
        "formatRecordsAs": "JSON",
    }
    if transaction_id:
        kw["transactionId"] = transaction_id
    resp = _client().execute_statement(**kw)
    return json.loads(resp.get("formattedRecords") or "[]")


# ------------------------------------------------------------------ registry
def load_registry() -> list[ModelSpec]:
    rows = execute(
        "SELECT m.key, m.family, m.display_name, m.reasoning, m.body_style, m.cache::text AS cache, "
        "m.sources::text AS sources, m.verified_at::text AS verified_at, m.notes, "
        "COALESCE(json_agg(json_build_object('endpoint', o.endpoint, 'api', o.api, 'scope', o.scope, "
        "'model_id', o.model_id, 'regions', o.regions, 'tiers', o.tiers, 'base_path', o.base_path)) "
        "FILTER (WHERE o.model_key IS NOT NULL), '[]')::text AS offerings "
        "FROM models m LEFT JOIN offerings o ON o.model_key = m.key GROUP BY m.key"
    )
    specs = []
    for r in rows:
        d = {
            **r,
            "cache": json.loads(r["cache"]),
            "sources": json.loads(r["sources"]),
            "offerings": [{k: v for k, v in o.items() if v is not None} for o in json.loads(r["offerings"])],
        }
        specs.append(spec_from_dict(d))
    return specs


def upsert_spec(spec: ModelSpec) -> None:
    """Replace one model and its offerings atomically. Callers persist only probe-verified specs."""
    if not spec.verified_at:
        raise ValueError(f"refusing to persist unverified spec {spec.key}")
    d = spec_to_dict(spec)
    tx = _client().begin_transaction(
        resourceArn=os.environ["CLUSTER_ARN"],
        secretArn=os.environ["WRITER_SECRET_ARN"],
        database=os.environ.get("DB_NAME", "bench"),
    )["transactionId"]
    try:
        execute(
            "INSERT INTO models (key, family, display_name, reasoning, body_style, cache, sources, verified_at, "
            "notes, updated_at) VALUES (:key, :family, :display_name, :reasoning, :body_style, "
            "CAST(:cache AS jsonb), CAST(:sources AS jsonb), CAST(:verified_at AS date), :notes, now()) "
            "ON CONFLICT (key) DO UPDATE SET family = EXCLUDED.family, display_name = EXCLUDED.display_name, "
            "reasoning = EXCLUDED.reasoning, body_style = EXCLUDED.body_style, cache = EXCLUDED.cache, "
            "sources = EXCLUDED.sources, verified_at = EXCLUDED.verified_at, notes = EXCLUDED.notes, "
            "updated_at = now()",
            {
                "key": d["key"],
                "family": d["family"],
                "display_name": d["display_name"],
                "reasoning": d["reasoning"],
                "body_style": d["body_style"],
                "cache": json.dumps(d["cache"]),
                "sources": json.dumps(d["sources"]),
                "verified_at": d["verified_at"],
                "notes": d["notes"],
            },
            transaction_id=tx,
        )
        execute("DELETE FROM offerings WHERE model_key = :key", {"key": d["key"]}, transaction_id=tx)
        for o in d["offerings"]:
            execute(
                "INSERT INTO offerings (model_key, endpoint, api, scope, model_id, regions, tiers, base_path) "
                "VALUES (:key, :endpoint, :api, :scope, :model_id, "
                "ARRAY(SELECT json_array_elements_text(CAST(:regions AS json))), "
                "ARRAY(SELECT json_array_elements_text(CAST(:tiers AS json))), :base_path)",
                {
                    "key": d["key"],
                    "endpoint": o["endpoint"],
                    "api": o["api"],
                    "scope": o["scope"],
                    "model_id": o["model_id"],
                    "regions": json.dumps(o["regions"]),
                    "tiers": json.dumps(o["tiers"]),
                    "base_path": o.get("base_path"),
                },
                transaction_id=tx,
            )
        _client().commit_transaction(
            resourceArn=os.environ["CLUSTER_ARN"], secretArn=os.environ["WRITER_SECRET_ARN"], transactionId=tx
        )
    except Exception:
        _client().rollback_transaction(
            resourceArn=os.environ["CLUSTER_ARN"], secretArn=os.environ["WRITER_SECRET_ARN"], transactionId=tx
        )
        raise


def record_event(kind: str, model_key: str | None, detail: dict[str, Any]) -> None:
    execute(
        "INSERT INTO discovery_events (kind, model_key, detail) VALUES (:kind, :model_key, CAST(:detail AS jsonb))",
        {"kind": kind, "model_key": model_key, "detail": json.dumps(detail, default=str)},
    )


# ------------------------------------------------------------------ results
_CTX = ("model", "endpoint", "api", "scope", "region", "prompt_size", "cache")


def load_summary(summary: dict[str, Any]) -> int:
    """Insert one benchmark run (summary.json, schema 2). Returns the number of cells written."""
    meta = summary["meta"]
    run_id = meta["run_id"]
    execute(
        "INSERT INTO runs (run_id, started, finished, version, config, meta) VALUES (:run_id, "
        "CAST(:started AS timestamptz), CAST(:finished AS timestamptz), :version, CAST(:config AS jsonb), "
        "CAST(:meta AS jsonb)) ON CONFLICT (run_id) DO NOTHING",
        {
            "run_id": run_id,
            "started": meta.get("started"),
            "finished": meta.get("finished"),
            "version": meta.get("version"),
            "config": json.dumps(summary.get("config", {}), default=str),
            "meta": json.dumps({**meta, "cells": len(summary.get("cells", []))}, default=str),
        },
    )
    for c in summary.get("cells", []):
        cell = c["cell"]
        execute(
            "INSERT INTO cells (run_id, label, model, display_name, family, model_id, endpoint, api, scope, "
            "region, prompt_size, cache, tier, summary) VALUES (:run_id, :label, :model, :display_name, "
            ":family, :model_id, :endpoint, :api, :scope, :region, :prompt_size, :cache, :tier, "
            "CAST(:summary AS jsonb)) ON CONFLICT (run_id, label) DO NOTHING",
            {
                "run_id": run_id,
                "label": cell["label"],
                **{k: cell.get(k) for k in ("display_name", "family", "model_id", "tier", *_CTX)},
                "summary": json.dumps(c, default=str),
            },
        )
    for cmp in summary.get("comparisons", []):
        ctx = cmp["context"]
        execute(
            "INSERT INTO comparisons (run_id, model, display_name, endpoint, api, scope, region, prompt_size, "
            "cache, tier, deltas) VALUES (:run_id, :model, :display_name, :endpoint, :api, :scope, :region, "
            ":prompt_size, :cache, :tier, CAST(:deltas AS jsonb)) ON CONFLICT DO NOTHING",
            {
                "run_id": run_id,
                "display_name": cmp.get("display_name"),
                "tier": cmp["tier"],
                **{k: ctx.get(k) for k in _CTX},
                "deltas": json.dumps(cmp["deltas"], default=str),
            },
        )
    return len(summary.get("cells", []))
