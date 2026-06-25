"""get_grocery_list — AgentCore Gateway tool (UC1).

Returns a shopper's persistent grocery list (active items) for a given user_id.
The list is durable across sessions (unlike the session cart). A never-used
user_id returns an empty list shape rather than an error.

Event: bare args {user_id} or {"arguments": {...}}.
Returns {"data": {"list": GroceryList}}.
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


def load_list(user_id: str) -> dict:
    """Active grocery items for a user, shaped as GroceryList. Shared by
    get_grocery_list and update_grocery_list (which returns the refreshed list)."""
    resp = _query(
        """
        SELECT item_id, raw_text, product_id, name, qty, unit, status
        FROM grocery_items
        WHERE user_id = :user_id AND status <> 'removed'
        ORDER BY added_at
        """,
        [{"name": "user_id", "value": {"stringValue": user_id}}],
    )
    rows = json.loads(resp.get("formattedRecords") or "[]")
    items = [
        {
            "item_id": r["item_id"],
            "raw_text": r["raw_text"],
            "product_id": r.get("product_id"),
            "name": r.get("name"),
            "qty": r.get("qty"),
            "unit": r.get("unit"),
            "status": r["status"],
        }
        for r in rows
    ]
    return {"user_id": user_id, "items": items}


def handler(event, context):
    event = event or {}
    args = event.get("arguments") if isinstance(event.get("arguments"), dict) else event
    user_id = (args.get("user_id") or "").strip()
    if not user_id:
        return {"error": {"code": "invalid_argument", "message": "user_id is required"}}
    try:
        return {"data": {"list": load_list(user_id)}}
    except Exception as e:  # noqa: BLE001
        return {"error": {"code": "query_failed", "message": str(e)}}
