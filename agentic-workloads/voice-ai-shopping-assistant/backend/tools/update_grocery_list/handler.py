"""update_grocery_list — AgentCore Gateway tool (UC1).

Mutates a shopper's persistent grocery list, then returns the refreshed list.
Three independent operations, any combination in one call:
  add:    [{ raw_text, qty?, product_id?, name? }] -> INSERT new active items.
          Pass product_id + name (found via search_products) to link the item to
          a real catalogue product; omit them to add unresolved raw text.
  remove: [item_id]                       -> soft-delete (status='removed')
  update: [{ item_id, qty?, status?, product_id?, name? }] -> set qty, status,
          and/or the resolved product_id + name (status in
          active|have|out_of_stock|removed)

Event: bare args or {"arguments": {...}}.
Returns {"data": {"list": GroceryList}} (active items, removed excluded).
"""
from __future__ import annotations

import json
import boto3

ssm = boto3.client("ssm")
rds = boto3.client("rds-data")
_DB: dict[str, str] = {}

_STATUSES = {"active", "have", "out_of_stock", "removed"}


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


def _s(name: str, val: str) -> dict:
    return {"name": name, "value": {"stringValue": val}}


def load_list(user_id: str) -> dict:
    resp = _exec(
        """
        SELECT item_id, raw_text, product_id, name, qty, unit, status
        FROM grocery_items
        WHERE user_id = :user_id AND status <> 'removed'
        ORDER BY added_at
        """,
        [_s("user_id", user_id)], json_records=True,
    )
    rows = json.loads(resp.get("formattedRecords") or "[]")
    items = [
        {
            "item_id": r["item_id"], "raw_text": r["raw_text"],
            "product_id": r.get("product_id"), "name": r.get("name"),
            "qty": r.get("qty"), "unit": r.get("unit"), "status": r["status"],
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

    add = args.get("add") or []
    remove = args.get("remove") or []
    update = args.get("update") or []

    try:
        # add: new active items. raw_text required; product_id + name optional
        # (pass them when the agent has already resolved the phrase via
        # search_products, so the item is linked to a real catalogue product).
        for it in add:
            if not isinstance(it, dict):
                continue
            raw = (it.get("raw_text") or "").strip()
            if not raw:
                continue
            try:
                qty = float(it.get("qty", 1) or 1)
            except (TypeError, ValueError):
                qty = 1.0
            cols = ["user_id", "raw_text", "qty"]
            vals = [":user_id", ":raw_text", ":qty"]
            params = [_s("user_id", user_id), _s("raw_text", raw),
                      {"name": "qty", "value": {"doubleValue": qty}}]
            product_id = (it.get("product_id") or "").strip()
            if product_id:
                cols.append("product_id"); vals.append(":product_id::uuid")
                params.append(_s("product_id", product_id))
                name = (it.get("name") or "").strip()
                if name:
                    cols.append("name"); vals.append(":name")
                    params.append(_s("name", name))
            _exec(
                f"INSERT INTO grocery_items ({', '.join(cols)}) VALUES ({', '.join(vals)})",
                params,
            )

        # remove: soft-delete by item_id (scoped to this user)
        for item_id in remove:
            item_id = str(item_id).strip()
            if not item_id:
                continue
            _exec(
                "UPDATE grocery_items SET status='removed' "
                "WHERE item_id = :item_id::uuid AND user_id = :user_id",
                [_s("item_id", item_id), _s("user_id", user_id)],
            )

        # update: set qty, status, and/or the resolved product_id + name by item_id
        for upd in update:
            if not isinstance(upd, dict):
                continue
            item_id = str(upd.get("item_id") or "").strip()
            if not item_id:
                continue
            sets, params = [], [_s("item_id", item_id), _s("user_id", user_id)]
            if upd.get("qty") is not None:
                try:
                    sets.append("qty = :qty")
                    params.append({"name": "qty", "value": {"doubleValue": float(upd["qty"])}})
                except (TypeError, ValueError):
                    pass
            status = (upd.get("status") or "").strip().lower()
            if status in _STATUSES:
                sets.append("status = :status")
                params.append(_s("status", status))
            product_id = (upd.get("product_id") or "").strip()
            if product_id:
                sets.append("product_id = :product_id::uuid")
                params.append(_s("product_id", product_id))
            name = (upd.get("name") or "").strip()
            if name:
                sets.append("name = :name")
                params.append(_s("name", name))
            if sets:
                _exec(
                    f"UPDATE grocery_items SET {', '.join(sets)} "
                    "WHERE item_id = :item_id::uuid AND user_id = :user_id",
                    params,
                )

        return {"data": {"list": load_list(user_id)}}
    except Exception as e:  # noqa: BLE001
        return {"error": {"code": "query_failed", "message": str(e)}}
