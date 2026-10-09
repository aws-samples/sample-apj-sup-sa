"""frontend: the public API. It fans checkout and order lookups out to orders."""

import logging
import os

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

ORDERS_URL = os.environ.get("ORDERS_URL", "http://orders:8000")
log = logging.getLogger("frontend")

# opentelemetry-instrument already exports traces, metrics, and logs. FastAPI >= 0.140's
# automatic telemetry would add a second OTLP exporter from the same OTEL_* variables
# and send every span, metric, and log twice.
app = FastAPI(title="frontend", telemetry={"auto_configure": False})
http = httpx.AsyncClient(base_url=ORDERS_URL, timeout=10)


class Cart(BaseModel):
    items: list[str]


async def forward(method: str, path: str, **kwargs):
    try:
        response = await http.request(method, path, **kwargs)
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="orders timed out")
    if response.status_code >= 400:
        if response.status_code >= 500:
            log.error("orders returned %s for %s %s", response.status_code, method, path)
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = response.text
        raise HTTPException(status_code=response.status_code, detail=detail)
    return response.json()


@app.post("/checkout")
async def checkout(cart: Cart):
    if not cart.items:
        raise HTTPException(status_code=400, detail="cart is empty")
    return await forward("POST", "/orders", json={"items": cart.items})


@app.get("/orders/{order_id}")
async def order_status(order_id: str):
    return await forward("GET", f"/orders/{order_id}")


@app.get("/healthz")
async def healthz():
    return {"ok": True}
