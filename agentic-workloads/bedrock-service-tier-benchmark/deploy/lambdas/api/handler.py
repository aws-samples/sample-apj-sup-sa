"""Read-only HTTP API over the benchmark database (RDS Data API, ``bench_reader`` role).

Routes (all GET, JWT-authorised by API Gateway before this code runs):

* ``/models``                      registry: models and their offerings
* ``/runs``                        recent runs (newest first)
* ``/filters``                     distinct values of every dimension (for the UI)
* ``/comparisons?<dims>&run_id=``  tier-vs-default deltas
* ``/cells?<dims>&run_id=``        per-cell summaries

Every filter value is passed as a Data API bind parameter and every column name
comes from a fixed allowlist, so no request data is ever concatenated into SQL.
The database role itself can only SELECT.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import boto3

rds = boto3.client("rds-data")

CLUSTER = os.environ["CLUSTER_ARN"]
SECRET = os.environ["READER_SECRET_ARN"]
DB = os.environ["DB_NAME"]

DIMENSIONS = ("model", "endpoint", "api", "scope", "region", "prompt_size", "cache", "tier")
_VALUE = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")
_MAX_ROWS = 2000
_HEADERS = {
    "content-type": "application/json",
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
}


def _query(sql: str, params: dict[str, str] | None = None) -> list[dict[str, Any]]:
    resp = rds.execute_statement(
        resourceArn=CLUSTER,
        secretArn=SECRET,
        database=DB,
        sql=sql,
        parameters=[{"name": k, "value": {"stringValue": v}} for k, v in (params or {}).items()],
        formatRecordsAs="JSON",
    )
    return json.loads(resp.get("formattedRecords") or "[]")


def _filters(qs: dict[str, str]) -> tuple[str, dict[str, str]]:
    """WHERE clause from allowlisted dimensions; values are bind parameters."""
    clauses, params = [], {}
    for dim in (*DIMENSIONS, "run_id"):
        value = qs.get(dim)
        if value is None:
            continue
        if not _VALUE.match(value):
            raise ValueError(f"invalid value for {dim}")
        clauses.append(f"{dim} = :{dim}")
        params[dim] = value
    if "run_id" not in params:
        clauses.append("run_id = (SELECT run_id FROM runs ORDER BY started DESC NULLS LAST LIMIT 1)")
    return " AND ".join(clauses), params


def models(_qs: dict[str, str]) -> Any:
    return _query(
        "SELECT m.key, m.family, m.display_name, m.reasoning, m.verified_at, "
        "COALESCE(json_agg(json_build_object('endpoint', o.endpoint, 'api', o.api, 'scope', o.scope, "
        "'model_id', o.model_id, 'regions', o.regions, 'tiers', o.tiers)) "
        "FILTER (WHERE o.model_key IS NOT NULL), '[]') AS offerings "
        "FROM models m LEFT JOIN offerings o ON o.model_key = m.key "
        "GROUP BY m.key ORDER BY m.family, m.display_name"
    )


def runs(_qs: dict[str, str]) -> Any:
    return _query(
        "SELECT run_id, started, finished, version, meta->>'cells' AS cells "
        "FROM runs ORDER BY started DESC NULLS LAST LIMIT 100"
    )


def filters(qs: dict[str, str]) -> Any:
    where, params = _filters({k: v for k, v in qs.items() if k == "run_id"})
    out: dict[str, Any] = {}
    for dim in DIMENSIONS:
        sql = f"SELECT DISTINCT {dim} AS v FROM cells WHERE {where} ORDER BY 1"  # nosec B608 - column names from the DIMENSIONS allowlist; values are bind parameters
        rows = _query(sql, params)
        out[dim] = [r["v"] for r in rows]
    return out


def comparisons(qs: dict[str, str]) -> Any:
    where, params = _filters(qs)
    cols = "run_id, model, display_name, endpoint, api, scope, region, prompt_size, cache, tier, deltas"
    order = "model, endpoint, api, prompt_size, cache, tier"
    sql = f"SELECT {cols} FROM comparisons WHERE {where} ORDER BY {order} LIMIT {_MAX_ROWS}"  # nosec B608 - allowlisted columns, bind params
    return _query(sql, params)


def cells(qs: dict[str, str]) -> Any:
    where, params = _filters(qs)
    cols = "run_id, label, model, display_name, endpoint, api, scope, region, prompt_size, cache, tier, summary"
    sql = f"SELECT {cols} FROM cells WHERE {where} ORDER BY label LIMIT {_MAX_ROWS}"  # nosec B608 - allowlisted columns, bind params
    return _query(sql, params)


ROUTES = {
    "/models": models,
    "/runs": runs,
    "/filters": filters,
    "/comparisons": comparisons,
    "/cells": cells,
}


def _respond(status: int, body: Any) -> dict[str, Any]:
    return {"statusCode": status, "headers": _HEADERS, "body": json.dumps(body, default=str)}


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    route = ROUTES.get(event.get("rawPath", ""))
    if route is None or event.get("requestContext", {}).get("http", {}).get("method") != "GET":
        return _respond(404, {"error": "not found"})
    try:
        return _respond(200, route(event.get("queryStringParameters") or {}))
    except ValueError as e:
        return _respond(400, {"error": str(e)})
    except Exception:  # noqa: BLE001 - never leak internals to the client
        print("internal error")  # details stay in CloudWatch via the Lambda runtime
        raise
