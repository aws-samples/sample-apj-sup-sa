"""add_to_cart — AgentCore Gateway tool.

Adds a product to the shopper's session cart and returns the updated Cart.
Creates the cart on first add. Re-adding the same product increments its qty.
Name + price are snapshotted from products at add time so the cart total is
stable. Event = bare args {session_id, product_id, qty}; returns {"data": {"cart": Cart}}.
"""
from __future__ import annotations

import json
import os
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
    kwargs = dict(resourceArn=db["cluster_arn"], secretArn=db["secret_arn"],
                  database=db["db_name"], sql=sql, parameters=params)
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


def handler(event, context):
    event = event or {}
    args = event.get("arguments") if isinstance(event.get("arguments"), dict) else event
    session_id = (args.get("session_id") or "").strip()
    product_id = (args.get("product_id") or "").strip()
    try:
        qty = int(args.get("qty", 1))
    except (TypeError, ValueError):
        qty = 1
    if not session_id or not product_id:
        return {"error": {"code": "invalid_argument", "message": "session_id and product_id are required"}}
    if qty < 1:
        return {"error": {"code": "invalid_argument", "message": "qty must be >= 1"}}

    try:
        # Ensure a cart exists for this session (idempotent).
        _exec(
            "INSERT INTO carts (session_id) VALUES (:session_id) ON CONFLICT (session_id) DO NOTHING",
            [_s("session_id", session_id)],
        )
        # Verify product exists + snapshot its current name/price.
        presp = _exec(
            "SELECT name, price_cents, in_stock FROM products WHERE product_id = :pid::uuid",
            [_s("pid", product_id)], json_records=True,
        )
        prows = json.loads(presp.get("formattedRecords") or "[]")
        if not prows:
            return {"error": {"code": "not_found", "message": f"product {product_id} not found"}}
        if not prows[0].get("in_stock", True):
            return {"error": {"code": "out_of_stock", "message": f"{prows[0]['name']} is out of stock"}}
        name, price_cents = prows[0]["name"], prows[0]["price_cents"]

        # Upsert the line item; re-adding bumps qty.
        _exec(
            """
            INSERT INTO cart_items (cart_id, product_id, name, qty, price_cents)
            SELECT c.cart_id, :pid::uuid, :name, :qty, :price
            FROM carts c WHERE c.session_id = :session_id
            ON CONFLICT (cart_id, product_id)
            DO UPDATE SET qty = cart_items.qty + EXCLUDED.qty,
                          name = EXCLUDED.name, price_cents = EXCLUDED.price_cents
            """,
            [
                _s("pid", product_id), _s("name", name),
                {"name": "qty", "value": {"longValue": qty}},
                {"name": "price", "value": {"longValue": int(price_cents)}},
                _s("session_id", session_id),
            ],
        )
        return {"data": {"cart": load_cart(session_id)}}
    except Exception as e:  # noqa: BLE001
        return {"error": {"code": "query_failed", "message": str(e)}}
