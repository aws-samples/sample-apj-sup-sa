"""get_cart — AgentCore Gateway tool.

Returns the shopper's current session cart. Event = bare args {session_id};
returns {"data": {"cart": Cart}}. Empty (never-used) session returns an empty
cart shape rather than an error.
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


def handler(event, context):
    event = event or {}
    args = event.get("arguments") if isinstance(event.get("arguments"), dict) else event
    session_id = (args.get("session_id") or "").strip()
    if not session_id:
        return {"error": {"code": "invalid_argument", "message": "session_id is required"}}

    db = _db()
    try:
        resp = rds.execute_statement(
            resourceArn=db["cluster_arn"], secretArn=db["secret_arn"], database=db["db_name"],
            sql="""
                SELECT c.cart_id, ci.product_id, ci.name, ci.qty, ci.price_cents
                FROM carts c
                LEFT JOIN cart_items ci ON ci.cart_id = c.cart_id
                WHERE c.session_id = :session_id
                ORDER BY ci.name
            """,
            parameters=[{"name": "session_id", "value": {"stringValue": session_id}}],
            formatRecordsAs="JSON",
        )
        rows = json.loads(resp.get("formattedRecords") or "[]")
        if not rows:
            cart = {"cart_id": "", "session_id": session_id, "items": [], "subtotal_cents": 0}
        else:
            items = [
                {"product_id": r["product_id"], "name": r["name"], "qty": r["qty"], "price_cents": r["price_cents"]}
                for r in rows if r.get("product_id")
            ]
            cart = {
                "cart_id": rows[0]["cart_id"],
                "session_id": session_id,
                "items": items,
                "subtotal_cents": sum(i["qty"] * i["price_cents"] for i in items),
            }
        return {"data": {"cart": cart}}
    except Exception as e:  # noqa: BLE001
        return {"error": {"code": "query_failed", "message": str(e)}}
