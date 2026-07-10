"""check_relevant_changes — AgentCore Gateway tool (UC5).

Surfaces changes that matter to THIS user right now, by joining their active
grocery list against current specials: "3 things on your list are on special."
Intended to run on connect so the agent can proactively flag savings.

Scope (honest to the data we have): currently this reports list items that are
on special now (with how much is saved). It does NOT do historical/seasonal
price comparison — we have no price history.

Event: bare args {user_id} or {"arguments": {...}}.
Returns {"data": {"changes": [RelevantChange]}}.
"""
from __future__ import annotations

import json
import boto3

ssm = boto3.client("ssm")
rds = boto3.client("rds-data")
_DB: dict[str, str] = {}


def _db() -> dict[str, str]:
    if not _DB:
        names = ["/aisle/db/cluster_arn", "/aisle/db/secret_arn", "/aisle/db/name"]
        by_name = {p["Name"]: p["Value"] for p in ssm.get_parameters(Names=names)["Parameters"]}
        _DB.update(
            cluster_arn=by_name["/aisle/db/cluster_arn"],
            secret_arn=by_name["/aisle/db/secret_arn"],
            db_name=by_name["/aisle/db/name"],
        )
    return _DB


def _query(sql: str, params: list[dict]):
    db = _db()
    return rds.execute_statement(
        resourceArn=db["cluster_arn"], secretArn=db["secret_arn"], database=db["db_name"],
        sql=sql, parameters=params, formatRecordsAs="JSON",
    )


# A list item is "relevant" right now if its resolved product is on special.
# (out_of_stock items are also surfaced as a change so the agent can suggest
# alternatives — both are things the shopper would want to hear on connect.)
_SQL = """
SELECT g.item_id, g.name AS item_name, g.product_id, g.status,
       p.name AS product_name, p.in_stock,
       s.special_price_cents, s.was_price_cents, s.savings_cents, s.special_type
FROM grocery_items g
JOIN products p ON p.product_id = g.product_id
LEFT JOIN specials s ON s.product_id = g.product_id
WHERE g.user_id = :user_id
  AND g.status IN ('active', 'out_of_stock')
  AND (s.special_id IS NOT NULL OR p.in_stock = false)
ORDER BY COALESCE(s.savings_cents, 0) DESC
"""


def handler(event, context):
    event = event or {}
    args = event.get("arguments") if isinstance(event.get("arguments"), dict) else event
    user_id = (args.get("user_id") or "").strip()
    if not user_id:
        return {"error": {"code": "invalid_argument", "message": "user_id is required"}}

    try:
        rows = json.loads(
            _query(_SQL, [{"name": "user_id", "value": {"stringValue": user_id}}])
            .get("formattedRecords") or "[]"
        )
        changes = []
        for r in rows:
            name = r.get("product_name") or r.get("item_name")
            if r.get("special_price_cents") is not None:
                changes.append({
                    "kind": "on_special",
                    "item_id": r["item_id"],
                    "product_id": r["product_id"],
                    "name": name,
                    "special_price_cents": r["special_price_cents"],
                    "was_price_cents": r["was_price_cents"],
                    "savings_cents": r.get("savings_cents") or 0,
                    "special_type": r.get("special_type"),
                })
            elif r.get("in_stock") is False:
                changes.append({
                    "kind": "out_of_stock",
                    "item_id": r["item_id"],
                    "product_id": r["product_id"],
                    "name": name,
                })
        return {"data": {"changes": changes}}
    except Exception as e:  # noqa: BLE001
        return {"error": {"code": "query_failed", "message": str(e)}}
