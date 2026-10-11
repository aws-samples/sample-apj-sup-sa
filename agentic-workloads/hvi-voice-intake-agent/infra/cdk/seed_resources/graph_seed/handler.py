"""Graph seed custom-resource handler (Knowledge stack, Neptune path only).

Materialises only when ``hvi:enableNeptune=true``. It does **not** reimplement
any seed logic: it invokes the already-deployed ``hvi-graph`` Lambda — which
owns the seed/stats actions verbatim (``lambdas/graph/handler.py``) — with
``{"action":"seed"}`` and then ``{"action":"stats"}``, and fails the resource
(rolling the stack back) if the graph came up empty. This mirrors
``scripts/31-graph-seed.sh``'s post-seed verification.

Invoked by the CDK ``Provider`` framework; the event carries ``RequestType``
and ``ResourceProperties`` (``GraphFunctionName`` and ``SeedHash`` for change
detection). ``GraphFunctionName`` is also provided as the ``GRAPH_FN`` env var.
"""

from __future__ import annotations

import json
import os

import boto3

_lambda = boto3.client("lambda")


def _physical_id(event: dict) -> str:
    return event.get("PhysicalResourceId") or "hvi-graph-seed"


def _invoke(fn_name: str, payload: dict) -> dict:
    resp = _lambda.invoke(
        FunctionName=fn_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode("utf-8"),
    )
    body = resp["Payload"].read()
    parsed = json.loads(body) if body else {}
    if resp.get("FunctionError"):
        raise RuntimeError(
            f"hvi-graph {payload.get('action')} errored: {parsed}"
        )
    if isinstance(parsed, dict) and parsed.get("ok") is False:
        raise RuntimeError(
            f"hvi-graph {payload.get('action')} failed: {parsed.get('error')}"
        )
    return parsed


def _total_vertices(stats: dict) -> int:
    """Sum the label counts the graph Lambda's ``stats`` action returns."""
    result = stats.get("result", stats) if isinstance(stats, dict) else {}
    total = 0
    for value in (result or {}).values():
        if isinstance(value, (int, float)):
            total += int(value)
        elif isinstance(value, dict):
            for inner in value.values():
                if isinstance(inner, (int, float)):
                    total += int(inner)
    return total


def handler(event: dict, _context) -> dict:  # noqa: ANN001
    request_type = event.get("RequestType")
    props = event.get("ResourceProperties", {})

    if request_type == "Delete":
        # The graph lives in Neptune (gated, flag-off tears it down separately);
        # nothing to undo here.
        return {"PhysicalResourceId": _physical_id(event)}

    fn_name = props.get("GraphFunctionName") or os.environ["GRAPH_FN"]

    _invoke(fn_name, {"action": "seed"})
    stats = _invoke(fn_name, {"action": "stats"})

    if _total_vertices(stats) == 0:
        raise RuntimeError(
            "graph seed produced an empty graph (stats returned zero vertices)"
        )

    return {
        "PhysicalResourceId": _physical_id(event),
        "Data": {"Vertices": str(_total_vertices(stats))},
    }
