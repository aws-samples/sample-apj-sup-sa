"""payments: charges, refunds, and payment status. It also hosts the chaos switch.

POST /chaos {"latency_ms": 2500, "error_rate": 0.3}   inject a fault
DELETE /chaos                                          clear it
"""

import asyncio
import logging
import random
import uuid

from fastapi import FastAPI, HTTPException
from opentelemetry import metrics, trace
from pydantic import BaseModel

log = logging.getLogger("payments")
tracer = trace.get_tracer("payments")
charges = metrics.get_meter("payments").create_counter("payments.charges", unit="{charge}")

# opentelemetry-instrument already exports traces, metrics, and logs. FastAPI >= 0.140's
# automatic telemetry would add a second OTLP exporter from the same OTEL_* variables
# and send every span, metric, and log twice.
app = FastAPI(title="payments", telemetry={"auto_configure": False})

PAYMENTS = {
    "ORD-1001": {"state": "captured", "amount": 59.90},
    "ORD-1002": {"state": "authorized", "amount": 120.00},
    "ORD-1003": {"state": "captured", "amount": 18.25},
}
CHAOS = {"latency_ms": 0, "error_rate": 0.0}


class Charge(BaseModel):
    order_id: str
    amount: float


class Chaos(BaseModel):
    latency_ms: int = 0
    error_rate: float = 0.0


async def apply_chaos(operation: str):
    span = trace.get_current_span()
    if CHAOS["latency_ms"]:
        span.set_attribute("chaos.latency_ms", CHAOS["latency_ms"])
        # Simulate a slow card processor: a child span makes the time visible on the trace.
        with tracer.start_as_current_span("card_processor.authorize"):
            await asyncio.sleep(CHAOS["latency_ms"] / 1000 * random.uniform(0.8, 1.2))  # nosec B311 - chaos jitter
    if random.random() < CHAOS["error_rate"]:  # nosec B311 - intentional fault simulation
        span.set_attribute("chaos.injected_error", True)
        log.error("card processor unavailable during %s", operation)
        raise HTTPException(status_code=503, detail="card processor unavailable")


@app.post("/charge")
async def charge(body: Charge):
    try:
        await apply_chaos("charge")
    except HTTPException:
        charges.add(1, {"outcome": "failed"})
        raise
    PAYMENTS[body.order_id] = {"state": "captured", "amount": body.amount}
    charges.add(1, {"outcome": "captured"})
    trace.get_current_span().set_attribute("payment.amount", body.amount)
    return {"order_id": body.order_id, "payment_id": f"pay_{uuid.uuid4().hex[:10]}", "state": "captured"}


@app.get("/payments/{order_id}")
async def payment_status(order_id: str):
    await apply_chaos("status")
    if order_id not in PAYMENTS:
        raise HTTPException(status_code=404, detail="no payment for order")
    return {"order_id": order_id, **PAYMENTS[order_id]}


@app.post("/refunds/{order_id}")
async def refund(order_id: str):
    await apply_chaos("refund")
    payment = PAYMENTS.get(order_id)
    if payment and payment["state"] == "refund_pending":
        log.info("refund already pending for %s", order_id)
        return {"order_id": order_id, "state": payment["state"]}
    if not payment or payment["state"] != "captured":
        raise HTTPException(status_code=409, detail="nothing to refund")
    payment["state"] = "refund_pending"
    log.info("refund started for %s", order_id)
    return {"order_id": order_id, "state": "refund_pending"}


@app.post("/chaos")
async def set_chaos(body: Chaos):
    CHAOS.update(body.model_dump())
    log.warning("chaos enabled: %s", CHAOS)
    return CHAOS


@app.delete("/chaos")
async def clear_chaos():
    CHAOS.update(latency_ms=0, error_rate=0.0)
    log.warning("chaos cleared")
    return CHAOS


@app.get("/healthz")
async def healthz():
    return {"ok": True, "chaos": CHAOS}
