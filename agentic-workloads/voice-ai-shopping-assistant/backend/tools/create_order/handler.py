"""create_order — AgentCore Gateway tool, payment-gated.

Turns the shopper's cart into a submitted order. Flow:
  1. read cart from Aurora, compute total
  2. POST the merchant API (fake x402 store checkout)
  3. if 402 and PAYMENTS_ENABLED: run AgentCore ProcessPayment (x402 on testnet),
     retry the merchant call with the X-PAYMENT proof header
  4. persist the order (+ order_items snapshot), clear the cart, return Order

Payment is flag-gated: with PAYMENTS_ENABLED=false (or no payment resources
configured), the order completes directly without a payment — so the flow is
demoable before the Privy wallet is funded. Set PAYMENTS_ENABLED=true once the
payment instrument/session exist.

Event = bare args {session_id, pickup_time?}; returns {"data": {"order": Order}}.

Env:
  MERCHANT_URL          — merchant_api Function URL
  PAYMENTS_ENABLED      — "true" to run the real x402 payment leg
  PAYMENT_MANAGER_ARN   — AgentCore payment manager
  PAYMENT_INSTRUMENT_ID — funded+delegated testnet wallet instrument
  PAYMENT_USER_ID       — end-user id bound to the instrument
  PAYMENT_MAX_SPEND_USD — per-session spend cap (default "1.00")

Payment sessions have a 60-minute TTL, so we create one at runtime (cached per
warm container, recreated when it expires) rather than baking a session id in at
deploy time.
"""
from __future__ import annotations

import json
import os
import uuid
import urllib.request
import urllib.error

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

ssm = boto3.client("ssm")
rds = boto3.client("rds-data")
sqs = boto3.client("sqs")
_DB: dict[str, str] = {}

# When set, create_order enqueues the placed order for async fulfillment by the
# AgentCore browser worker (place_order_async). Optional — orders still complete
# synchronously without it.
FULFILLMENT_QUEUE_URL = os.environ.get("FULFILLMENT_QUEUE_URL", "")
# In TEST_MODE, create_order always reports the order as successfully PLACED to
# the agent immediately, even on the async browser pathway (which is still
# enqueued and runs for real — its true progress is recorded in order_events and
# CloudWatch). Turn off (TEST_MODE=false) to report the real interim status
# (e.g. 'submitted' until the worker finishes).
TEST_MODE = os.environ.get("TEST_MODE", "false").lower() == "true"
MERCHANT_URL = os.environ.get("MERCHANT_URL", "")
PAYMENTS_ENABLED = os.environ.get("PAYMENTS_ENABLED", "false").lower() == "true"
# The merchant is fronted by an IAM-authorized API Gateway REST API, so every
# request is SigV4-signed by this function's execution role (the only principal
# granted execute-api:Invoke). API Gateway authenticates as the "execute-api"
# service.
_REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION", "ap-southeast-2")
_SIGV4 = SigV4Auth(boto3.Session().get_credentials(), "execute-api", _REGION)

PAYMENT_MANAGER_ARN = os.environ.get("PAYMENT_MANAGER_ARN", "")
PAYMENT_INSTRUMENT_ID = os.environ.get("PAYMENT_INSTRUMENT_ID", "")
PAYMENT_USER_ID = os.environ.get("PAYMENT_USER_ID", "")
PAYMENT_MAX_SPEND_USD = os.environ.get("PAYMENT_MAX_SPEND_USD", "1.00")
SESSION_EXPIRY_MINUTES = 60

# AgentCore Payments client + a cached runtime session. Sessions live 60 min, so
# we lazily (re)create one and reuse it across warm invokes until it nears expiry.
_agentcore = None
_SESSION: dict[str, float] = {}  # {"id": ..., "deadline": epoch_seconds}


def _payments():
    global _agentcore
    if _agentcore is None:
        _agentcore = boto3.client("bedrock-agentcore")
    return _agentcore


def _payment_session() -> str:
    """Return a live payment session id, creating one if missing/near expiry."""
    import time
    now = time.time()
    if _SESSION.get("id") and now < _SESSION.get("deadline", 0):
        return _SESSION["id"]
    resp = _payments().create_payment_session(
        userId=PAYMENT_USER_ID,
        paymentManagerArn=PAYMENT_MANAGER_ARN,
        limits={"maxSpendAmount": {"value": PAYMENT_MAX_SPEND_USD, "currency": "USD"}},
        expiryTimeInMinutes=SESSION_EXPIRY_MINUTES,
    )
    sid = resp["paymentSession"]["paymentSessionId"]
    # Refresh a couple of minutes before the real TTL to avoid mid-call expiry.
    _SESSION.update(id=sid, deadline=now + (SESSION_EXPIRY_MINUTES - 2) * 60)
    return sid


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


def _l(name, val):
    return {"name": name, "value": {"longValue": int(val)}}


def _emit(order_id: str, event_type: str, payload: dict | None = None) -> None:
    """Append a row to order_events (the observability timeline). Best-effort:
    never let logging failures break order placement."""
    try:
        _exec(
            """INSERT INTO order_events (order_id, event_type, payload)
               VALUES (:oid::uuid, :etype, :payload::jsonb)""",
            [_s("oid", order_id), _s("etype", event_type),
             _s("payload", json.dumps(payload or {}))],
        )
    except Exception:  # noqa: BLE001
        pass


def _load_merchant(merchant_id: str) -> dict | None:
    """Look up the merchant the order routes to (the router's source of truth)."""
    resp = _exec(
        "SELECT merchant_id, name, supports_x402, endpoint FROM merchants WHERE merchant_id = :mid",
        [_s("mid", merchant_id)], json_records=True,
    )
    rows = json.loads(resp.get("formattedRecords") or "[]")
    return rows[0] if rows else None


def _load_cart(session_id: str) -> dict | None:
    resp = _exec(
        """
        SELECT c.cart_id, ci.product_id, ci.name, ci.qty, ci.price_cents
        FROM carts c
        LEFT JOIN cart_items ci ON ci.cart_id = c.cart_id
        WHERE c.session_id = :session_id
        """,
        [_s("session_id", session_id)], json_records=True,
    )
    rows = json.loads(resp.get("formattedRecords") or "[]")
    if not rows:
        return None
    items = [
        {"product_id": r["product_id"], "name": r["name"], "qty": r["qty"], "price_cents": r["price_cents"]}
        for r in rows if r.get("product_id")
    ]
    return {"cart_id": rows[0]["cart_id"], "items": items,
            "total_cents": sum(i["qty"] * i["price_cents"] for i in items)}


def _http_post(url: str, body: dict, headers: dict) -> tuple[int, dict, dict]:
    data = json.dumps(body).encode()
    # The merchant API uses IAM authorization, so every request must be
    # SigV4-signed by this function's execution role (the only principal granted
    # execute-api:Invoke on it). We sign the body+headers with botocore, then
    # copy the generated auth headers onto the urllib request. The x402
    # X-PAYMENT header (when present) is part of the signed header set.
    sign_headers = {"Content-Type": "application/json", **headers}
    aws_req = AWSRequest(method="POST", url=url, data=data, headers=sign_headers)
    _SIGV4.add_auth(aws_req)
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers=dict(aws_req.headers))
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        return resp.status, dict(resp.headers), json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode() or "{}"
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {"raw": raw}
        return e.code, dict(e.headers), payload


def _pay_x402(payment_required: dict) -> tuple[str, str, dict]:
    """Run AgentCore ProcessPayment for an x402 requirement.

    Returns (proof_header, payment_id, audit) where `audit` is payment-trail
    detail for the observability timeline (status, charged amount, network,
    remaining session budget).

    Uses the x402 *version "1"* shape with the merchant's full `accepts[0]`
    requirement forwarded verbatim (keeping `maxAmountRequired`). This is the
    shape AgentCore ProcessPayment accepts (it returns PROOF_GENERATED); the
    older version "2" + `amount` shape is rejected.
    """
    accepts = (payment_required.get("accepts") or [{}])[0]
    session_id = _payment_session()
    resp = _payments().process_payment(
        userId=PAYMENT_USER_ID,
        paymentManagerArn=PAYMENT_MANAGER_ARN,
        paymentSessionId=session_id,
        paymentInstrumentId=PAYMENT_INSTRUMENT_ID,
        paymentType="CRYPTO_X402",
        paymentInput={"cryptoX402": {"version": "1", "payload": accepts}},
        clientToken=str(uuid.uuid4()),
    )
    # The signed proof goes back to the merchant in the X-PAYMENT header.
    proof = json.dumps(resp.get("paymentOutput", {}))
    payment_id = resp.get("processPaymentId", "")
    # atomic-unit charge -> cents is not 1:1 (USDC token micropayment), so we
    # record the raw atomic amount the merchant asked for as a string.
    try:
        amount_atomic = int(accepts.get("maxAmountRequired", "0"))
    except (TypeError, ValueError):
        amount_atomic = None
    audit = {
        "status": resp.get("status"),
        "payment_id": payment_id,
        "network": accepts.get("network"),
        "amount_atomic": amount_atomic,
        "payment_session_id": session_id,
    }
    return proof, payment_id, audit


def handler(event, context):
    event = event or {}
    args = event.get("arguments") if isinstance(event.get("arguments"), dict) else event
    session_id = (args.get("session_id") or "").strip()
    pickup_time = (args.get("pickup_time") or "").strip() or None
    # Which merchant to order from. Defaults to the grocery storefront (browser
    # path) so existing callers are unchanged.
    merchant_id = (args.get("merchant_id") or "aisle-grocery").strip()
    if not session_id:
        return {"error": {"code": "invalid_argument", "message": "session_id is required"}}

    try:
        cart = _load_cart(session_id)
        if not cart or not cart["items"]:
            return {"error": {"code": "empty_cart", "message": "cart is empty or does not exist"}}

        merchant = _load_merchant(merchant_id)
        if not merchant:
            return {"error": {"code": "unknown_merchant", "message": f"no merchant '{merchant_id}'"}}
        supports_x402 = bool(merchant.get("supports_x402"))

        order_ref = uuid.uuid4().hex
        payment_id = None
        pay_audit: dict | None = None
        order_status = "submitted"  # browser path stays 'submitted' until the worker finishes

        # ROUTER: one pathway per merchant capability.
        if supports_x402 and PAYMENTS_ENABLED:
            # x402 pathway — pay the merchant's x402 endpoint DIRECTLY via
            # AgentCore Payments. No card, no browser, no queue. The merchant is
            # the hero of this path (a paid API/MCP/x402 store).
            endpoint = merchant["endpoint"]
            status, _, payload = _http_post(endpoint, {"order_ref": order_ref, "total_cents": cart["total_cents"]}, {})
            if status == 402:
                proof, payment_id, pay_audit = _pay_x402(payload)
                status2, _, conf = _http_post(
                    endpoint, {"order_ref": order_ref, "total_cents": cart["total_cents"]},
                    {"X-PAYMENT": proof},
                )
                if status2 != 200:
                    return {"error": {"code": "payment_failed", "message": f"merchant rejected payment ({status2})"}}
                pickup_code = conf.get("confirmation_code") or conf.get("pickup_code") or uuid.uuid4().hex[:6].upper()
                order_status = "placed"  # paid + confirmed synchronously
            elif status == 200:
                pickup_code = payload.get("confirmation_code") or payload.get("pickup_code") or uuid.uuid4().hex[:6].upper()
                order_status = "placed"
            else:
                return {"error": {"code": "merchant_error", "message": f"merchant returned {status}"}}
        elif FULFILLMENT_QUEUE_URL:
            # browser pathway — record + enqueue; the place_order_async worker
            # issues a Stripe card and drives the merchant's web checkout. No
            # payment here. The AgentCore Browser is the hero of this path.
            pickup_code = uuid.uuid4().hex[:6].upper()
        else:
            # demo fallback (no queue / payments disabled): treat as placed.
            pickup_code = uuid.uuid4().hex[:6].upper()

        # Persist the order + line-item snapshot, then clear the cart.
        order_id = str(uuid.uuid4())
        params = [
            _s("order_id", order_id), _s("session_id", session_id),
            _s("pickup_code", pickup_code), _l("total", cart["total_cents"]),
        ]
        pt_clause = "NULL"
        if pickup_time:
            pt_clause = ":pickup_time::timestamptz"
            params.append(_s("pickup_time", pickup_time))
        pid_clause = "NULL"
        if payment_id:
            pid_clause = ":payment_id"
            params.append(_s("payment_id", payment_id))
        params.append(_s("status", order_status))
        _exec(
            f"""INSERT INTO orders (order_id, session_id, status, pickup_code, pickup_time, total_cents, payment_id)
                VALUES (:order_id::uuid, :session_id, :status, :pickup_code, {pt_clause}, :total, {pid_clause})""",
            params,
        )
        for it in cart["items"]:
            _exec(
                """INSERT INTO order_items (order_id, product_id, name, qty, price_cents)
                   VALUES (:order_id::uuid, :pid::uuid, :name, :qty, :price)""",
                [_s("order_id", order_id), _s("pid", it["product_id"]), _s("name", it["name"]),
                 _l("qty", it["qty"]), _l("price", it["price_cents"])],
            )
        _exec("DELETE FROM cart_items WHERE cart_id = :cid::uuid", [_s("cid", cart["cart_id"])])

        # Observability timeline (Phase 2): record creation + payment so
        # get_order_status can surface the cart/buying lifecycle to the UI.
        _emit(order_id, "order_created", {
            "total_cents": cart["total_cents"],
            "item_count": len(cart["items"]),
            "merchant_id": merchant_id,
            "pathway": "x402" if supports_x402 else "browser",
            "items": [{"name": i["name"], "qty": i["qty"], "price_cents": i["price_cents"]}
                      for i in cart["items"]],
        })
        if pay_audit:
            _emit(order_id, "payment_processed", pay_audit)
        if order_status == "placed" and supports_x402:
            _emit(order_id, "order_placed", {"via": "x402_direct", "merchant_id": merchant_id})

        # browser pathway only: hand off to the async AgentCore browser worker.
        # x402 orders are already paid + placed synchronously above.
        if not supports_x402 and FULFILLMENT_QUEUE_URL:
            try:
                sqs.send_message(QueueUrl=FULFILLMENT_QUEUE_URL,
                                 MessageBody=json.dumps({"order_id": order_id, "merchant_id": merchant_id}))
                _emit(order_id, "enqueued_for_fulfillment", {"merchant_id": merchant_id})
            except Exception:  # noqa: BLE001
                pass

        # Build the Order response (created_at from DB for ISO-8601 consistency).
        created = _exec("SELECT created_at FROM orders WHERE order_id = :oid::uuid",
                        [_s("oid", order_id)], json_records=True)
        created_at = json.loads(created.get("formattedRecords") or "[{}]")[0].get("created_at", "")
        # In TEST_MODE always tell the agent the order was placed successfully.
        # The DB row keeps its true status; the async worker still runs and logs
        # the real interim steps to order_events / CloudWatch.
        reported_status = "placed" if TEST_MODE else order_status
        order = {
            "order_id": order_id, "session_id": session_id, "status": reported_status,
            "pickup_code": pickup_code, "pickup_time": pickup_time,
            "total_cents": cart["total_cents"], "created_at": created_at,
        }
        if payment_id:
            order["payment_id"] = payment_id
        return {"data": {"order": order}}
    except Exception as e:  # noqa: BLE001
        return {"error": {"code": "order_failed", "message": str(e)}}
