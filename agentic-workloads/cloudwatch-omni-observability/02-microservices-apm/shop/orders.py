"""orders: order records. It calls payments to charge, refund, and read payment state.

These are the same three orders the support agent knows (ORD-1001..1003), so in
sample 03 the agent's tools work against this service unchanged.
"""

import logging
import os
import random
import uuid

import httpx
from fastapi import FastAPI, HTTPException
from opentelemetry import metrics, trace
from pydantic import BaseModel

PAYMENTS_URL = os.environ.get("PAYMENTS_URL", "http://payments:8000")
log = logging.getLogger("orders")
orders_placed = metrics.get_meter("orders").create_counter("orders.placed", unit="{order}")

# opentelemetry-instrument already exports traces, metrics, and logs. FastAPI >= 0.140's
# automatic telemetry would add a second OTLP exporter from the same OTEL_* variables
# and send every span, metric, and log twice.
app = FastAPI(title="orders", telemetry={"auto_configure": False})
http = httpx.AsyncClient(base_url=PAYMENTS_URL, timeout=10)

ORDERS = {
    "ORD-1001": {"status": "shipped", "carrier": "UPS", "eta": "2026-10-03", "total": 59.90},
    "ORD-1002": {"status": "processing", "carrier": None, "eta": "2026-10-06", "total": 120.00},
    "ORD-1003": {"status": "delivered", "carrier": "USPS", "eta": None, "total": 18.25},
}


class NewOrder(BaseModel):
    items: list[str]


class ReturnRequest(BaseModel):
    reason: str


async def payments(method: str, path: str, *, propagate_conflict: bool = False,
                   allow_not_found: bool = False, **kwargs):
    """Call payments and turn unexpected responses into an orders API error."""
    try:
        response = await http.request(method, path, **kwargs)
    except httpx.TimeoutException:
        log.error("payments timed out on %s %s", method, path)
        raise HTTPException(status_code=504, detail="payments timed out")
    if response.is_success or (response.status_code == 404 and allow_not_found):
        return response
    if response.status_code == 409 and propagate_conflict:
        log.info("payments rejected %s %s with a conflict", method, path)
        raise HTTPException(status_code=409, detail=response.json().get("detail", "refund could not be started"))
    if response.status_code >= 500:
        log.error("payments failed on %s %s: %s", method, path, response.status_code)
        raise HTTPException(status_code=502, detail="payments unavailable")
    log.error("payments rejected %s %s: %s", method, path, response.status_code)
    raise HTTPException(status_code=502, detail="payments returned an unexpected response")


@app.post("/orders", status_code=201)
async def create_order(body: NewOrder):
    order_id = f"ORD-{uuid.uuid4().hex[:6].upper()}"
    total = round(sum(random.uniform(5, 80) for _ in body.items), 2)  # nosec B311 - synthetic prices
    span = trace.get_current_span()
    span.set_attribute("order.id", order_id)
    span.set_attribute("order.items", len(body.items))
    await payments("POST", "/charge", json={"order_id": order_id, "amount": total})
    ORDERS[order_id] = {"status": "processing", "carrier": None, "eta": None, "total": total}
    orders_placed.add(1)
    log.info("order %s placed (%d items, %.2f)", order_id, len(body.items), total)
    return {"order_id": order_id, "total": total}


@app.get("/orders/{order_id}")
async def get_order(order_id: str):
    trace.get_current_span().set_attribute("order.id", order_id)
    order = ORDERS.get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
    payment = await payments("GET", f"/payments/{order_id}", allow_not_found=True)
    payment_state = payment.json().get("state") if payment.status_code == 200 else "unknown"
    return {"order_id": order_id, **order, "payment": payment_state}


@app.post("/orders/{order_id}/returns")
async def start_return(order_id: str, body: ReturnRequest):
    order = ORDERS.get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
    if order["status"] != "delivered":
        return {"order_id": order_id, "accepted": False, "detail": "Only delivered orders can be returned"}
    await payments("POST", f"/refunds/{order_id}", propagate_conflict=True)
    log.info("return accepted for %s: %s", order_id, body.reason)
    return {"order_id": order_id, "accepted": True, "return_label": f"RMA-{order_id[-4:]}"}


@app.get("/healthz")
async def healthz():
    return {"ok": True}
