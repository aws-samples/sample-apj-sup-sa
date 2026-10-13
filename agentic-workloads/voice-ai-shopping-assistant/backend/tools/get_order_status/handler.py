"""get_order_status — AgentCore Gateway tool, order observability.

Returns the full cart/buying lifecycle for one order so the frontend (and the
voice agent) can report progress: the Order, its event timeline, the payment
audit trail, and any browser artifacts (screenshots / live-view) captured during
async fulfillment.

Reads the observability tables written by create_order and the async browser
worker (see backend/db/schema.sql: orders, order_events, order_artifacts) and
presigns S3 artifact keys so the frontend can display them directly.

Event = bare args {order_id} OR {session_id} (latest order for the session);
returns {"data": {"order_status": OrderStatusDetail}} or {"error": ...}.

Env:
  ARTIFACTS_BUCKET — S3 bucket holding screenshot/dom/log artifacts (optional)
"""
from __future__ import annotations

import json
import os

import boto3

ssm = boto3.client("ssm")
rds = boto3.client("rds-data")
s3 = boto3.client("s3")
_DB: dict[str, str] = {}

ARTIFACTS_BUCKET = os.environ.get("ARTIFACTS_BUCKET", "")
_PRESIGN_TTL = 3600  # 1h — long enough for a demo viewing session


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


def _exec(sql: str, params: list[dict]):
    db = _db()
    return rds.execute_statement(
        resourceArn=db["cluster_arn"], secretArn=db["secret_arn"], database=db["db_name"],
        sql=sql, parameters=params, formatRecordsAs="JSON",
    )


def _s(name, val):
    return {"name": name, "value": {"stringValue": val}}


def _rows(resp) -> list[dict]:
    return json.loads(resp.get("formattedRecords") or "[]")


def _presign(s3_key: str) -> str | None:
    if not (ARTIFACTS_BUCKET and s3_key):
        return None
    try:
        return s3.generate_presigned_url(
            "get_object", Params={"Bucket": ARTIFACTS_BUCKET, "Key": s3_key}, ExpiresIn=_PRESIGN_TTL,
        )
    except Exception:  # noqa: BLE001 — a bad key shouldn't fail the whole status call
        return None


def _load_order(order_id: str | None, session_id: str | None) -> dict | None:
    if order_id:
        resp = _exec(
            """SELECT order_id, session_id, status, pickup_code, pickup_time,
                      total_cents, payment_id, browser_session_id, status_detail,
                      updated_at, created_at
               FROM orders WHERE order_id = :oid::uuid""",
            [_s("oid", order_id)],
        )
    else:
        # latest order for the session
        resp = _exec(
            """SELECT order_id, session_id, status, pickup_code, pickup_time,
                      total_cents, payment_id, browser_session_id, status_detail,
                      updated_at, created_at
               FROM orders WHERE session_id = :sid
               ORDER BY created_at DESC LIMIT 1""",
            [_s("sid", session_id or "")],
        )
    rows = _rows(resp)
    return rows[0] if rows else None


def _timeline(order_id: str) -> list[dict]:
    rows = _rows(_exec(
        """SELECT event_id, event_type, payload, created_at
           FROM order_events WHERE order_id = :oid::uuid
           ORDER BY created_at ASC""",
        [_s("oid", order_id)],
    ))
    out = []
    for r in rows:
        payload = r.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                payload = {"raw": payload}
        out.append({
            "event_id": r["event_id"], "event_type": r["event_type"],
            "created_at": r["created_at"], "payload": payload or {},
        })
    return out


def _artifacts(order_id: str) -> list[dict]:
    rows = _rows(_exec(
        """SELECT artifact_id, kind, label, s3_key, url, created_at
           FROM order_artifacts WHERE order_id = :oid::uuid
           ORDER BY created_at ASC""",
        [_s("oid", order_id)],
    ))
    out = []
    for r in rows:
        url = r.get("url") or _presign(r.get("s3_key"))
        out.append({
            "artifact_id": r["artifact_id"], "kind": r["kind"],
            "label": r["label"], "created_at": r["created_at"], "url": url,
        })
    return out


def _payment_audit(order: dict, timeline: list[dict]) -> dict | None:
    """Assemble the PaymentAudit from the order row + payment/balance events."""
    if not order.get("payment_id") and order.get("status") not in (
        "declined_insufficient_funds",
    ):
        return None
    audit = {"payment_id": order.get("payment_id")}
    # Enrich from events the worker/create_order logged (payment_processed,
    # balance_checked) without a second AgentCore round-trip.
    for ev in timeline:
        p = ev.get("payload") or {}
        if ev["event_type"] == "payment_processed":
            audit.setdefault("status", p.get("status"))
            audit.setdefault("amount_cents", p.get("amount_cents"))
            audit.setdefault("session_budget_remaining", p.get("session_budget_remaining"))
            audit.setdefault("network", p.get("network"))
        if ev["event_type"] == "balance_checked":
            audit.setdefault("wallet_balance", p.get("wallet_balance"))
            audit.setdefault("network", p.get("network"))
    return audit


def handler(event, context):
    event = event or {}
    args = event.get("arguments") if isinstance(event.get("arguments"), dict) else event
    order_id = (args.get("order_id") or "").strip() or None
    session_id = (args.get("session_id") or "").strip() or None
    if not order_id and not session_id:
        return {"error": {"code": "invalid_argument",
                          "message": "order_id or session_id is required"}}

    try:
        order = _load_order(order_id, session_id)
        if not order:
            return {"error": {"code": "not_found", "message": "no order found"}}

        oid = order["order_id"]
        timeline = _timeline(oid)
        artifacts = _artifacts(oid)
        payment = _payment_audit(order, timeline)

        order_out = {
            "order_id": oid,
            "session_id": order["session_id"],
            "status": order["status"],
            "pickup_code": order["pickup_code"],
            "pickup_time": order.get("pickup_time"),
            "total_cents": order["total_cents"],
            "created_at": order["created_at"],
            "payment_id": order.get("payment_id"),
            "browser_session_id": order.get("browser_session_id"),
            "status_detail": order.get("status_detail"),
            "updated_at": order.get("updated_at"),
        }
        return {"data": {"order_status": {
            "order": order_out,
            "timeline": timeline,
            "payment": payment,
            "artifacts": artifacts,
        }}}
    except Exception as e:  # noqa: BLE001
        return {"error": {"code": "order_status_failed", "message": str(e)}}
