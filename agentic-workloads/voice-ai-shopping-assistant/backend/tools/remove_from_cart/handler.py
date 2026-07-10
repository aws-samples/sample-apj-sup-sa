"""remove_from_cart — AgentCore Gateway tool.

Removes a product from the shopper's session cart and returns the updated Cart.
If `qty` is given, decrements by that amount (deleting the line when it reaches
zero); otherwise removes the line entirely. Event = bare args
{session_id, product_id, qty?}; returns {"data": {"cart": Cart}}.

Mirrors add_to_cart's DB access (Aurora Data API, SSM-config, carts / cart_items
schema).
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


def _exec(sql: str, params: list[dict], json_records: bool = False):
    db = _db()
    kwargs = dict(
        resourceArn=db["cluster_arn"], secretArn=db["secret_arn"],
        database=db["db_name"], sql=sql, parameters=params,
    )
    if json_records:
        kwargs["formatRecordsAs"] = "JSON"
    return rds.execute_statement(**kwargs)


def _s(name, val):
    return {"name": name, "value": {"stringValue": val}}


def load_cart(session_id: str) -> dict:
    resp = _exec(
        """
        SELECT c.cart_id, c.session_id, ci.product_id, ci.name, ci.qty, ci.price_cents
        FROM carts c
        LEFT JOIN cart_items ci ON ci.cart_id = c.cart_id
        WHERE c.session_id = :session_id
        ORDER BY ci.name
        """,
        [_s("session_id", session_id)],
        json_records=True,
    )
    rows = json.loads(resp.get("formattedRecords") or "[]")
    if not rows:
        return {"cart_id": "", "session_id": session_id, "items": [], "subtotal_cents": 0}
    items = [
        {"product_id": r["product_id"], "name": r["name"], "qty": r["qty"], "price_cents": r["price_cents"]}
        for r in rows if r.get("product_id")
    ]
    subtotal = sum(i["qty"] * i["price_cents"] for i in items)
    return {"cart_id": rows[0]["cart_id"], "session_id": session_id, "items": items, "subtotal_cents": subtotal}


def _current_qty(session_id: str, product_id: str):
    resp = _exec(
        """
        SELECT ci.qty FROM cart_items ci
        JOIN carts c ON ci.cart_id = c.cart_id
        WHERE c.session_id = :session_id AND ci.product_id = :pid::uuid
        """,
        [_s("session_id", session_id), _s("pid", product_id)],
        json_records=True,
    )
    rows = json.loads(resp.get("formattedRecords") or "[]")
    return rows[0]["qty"] if rows else None


def handler(event, context):
    event = event or {}
    args = event.get("arguments") if isinstance(event.get("arguments"), dict) else event
    session_id = (args.get("session_id") or "").strip()
    product_id = (args.get("product_id") or "").strip()
    raw_qty = args.get("qty", None)

    if not session_id or not product_id:
        return {"error": {"code": "invalid_argument", "message": "session_id and product_id are required"}}

    try:
        current = _current_qty(session_id, product_id)
        if current is None:
            # Nothing to remove; return the cart as-is.
            return {"data": {"cart": load_cart(session_id)}}

        remove_qty = None
        if raw_qty is not None:
            try:
                remove_qty = int(raw_qty)
            except (TypeError, ValueError):
                remove_qty = None

        if remove_qty is None or remove_qty >= current or remove_qty <= 0:
            # Remove the whole line.
            _exec(
                """
                DELETE FROM cart_items ci USING carts c
                WHERE ci.cart_id = c.cart_id
                  AND c.session_id = :session_id AND ci.product_id = :pid::uuid
                """,
                [_s("session_id", session_id), _s("pid", product_id)],
            )
        else:
            # Decrement.
            _exec(
                """
                UPDATE cart_items ci SET qty = ci.qty - :rq
                FROM carts c
                WHERE ci.cart_id = c.cart_id
                  AND c.session_id = :session_id AND ci.product_id = :pid::uuid
                """,
                [
                    _s("session_id", session_id), _s("pid", product_id),
                    {"name": "rq", "value": {"longValue": remove_qty}},
                ],
            )

        return {"data": {"cart": load_cart(session_id)}}
    except Exception as e:  # noqa: BLE001
        return {"error": {"code": "query_failed", "message": str(e)}}
