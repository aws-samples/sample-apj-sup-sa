"""delivery_api — an x402-NATIVE merchant (priority delivery slot).

This is the example merchant for create_order's x402 pathway: a paid service the
agent pays for DIRECTLY via AgentCore Payments, with no card and no browser. It
demonstrates the agentic-commerce case x402 is actually built for — an agent
paying a machine for a service on the fly (HTTP 402 -> pay -> retry) — as opposed
to the card-only browser pathway used for normal web stores.

Routes (behind an IAM-authorized API Gateway; create_order SigV4-signs):
  POST /  {order_id, amount_cents?}  no X-PAYMENT
        -> 402 + x402 requirements (amount in USDC atomic units)
  POST /  {order_id}                 WITH X-PAYMENT: <proof>
        -> 200 + booked priority delivery slot (confirmation + window)

Demo simplification (same as merchant_api): presence of a signed AgentCore proof
is treated as settled; a real merchant would verify/settle via a facilitator.

Env:
  PAYTO_ADDRESS — testnet wallet that receives the micropayment
  ASSET_ADDRESS — USDC token contract (Base Sepolia default)
  NETWORK       — x402 network id (base-sepolia)
  SLOT_FEE_CENTS — fixed fee for a priority slot (default 199 = $1.99)
"""
from __future__ import annotations

import json
import os
import uuid

PAYTO = os.environ.get("PAYTO_ADDRESS", "0x0000000000000000000000000000000000000000")
ASSET = os.environ.get("ASSET_ADDRESS", "0x036CbD53842c5426634e7929541eC2318f3dCF7e")
NETWORK = os.environ.get("NETWORK", "base-sepolia")
SLOT_FEE_CENTS = int(os.environ.get("SLOT_FEE_CENTS", "199"))  # $1.99 priority slot
USDC_PER_DOLLAR_ATOMIC = 1_000_000  # 6 decimals; demo $1 = 1 USDC


def _headers(event) -> dict:
    return {k.lower(): v for k, v in (event.get("headers") or {}).items()}


def _body(event) -> dict:
    raw = event.get("body") or "{}"
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return {}


def _resource_url(event) -> str:
    h = event.get("headers") or {}
    rc = event.get("requestContext") or {}
    path = rc.get("path") or (rc.get("http", {}) or {}).get("path", "/")
    return f"https://{h.get('host', h.get('Host', 'delivery.local'))}{path}"


def _atomic(amount_cents: int) -> str:
    return str(int(round(amount_cents / 100.0 * USDC_PER_DOLLAR_ATOMIC)))


def _json(status: int, body: dict, extra: dict | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if extra:
        headers.update(extra)
    return {"statusCode": status, "headers": headers, "body": json.dumps(body)}


def handler(event, context):
    headers = _headers(event)
    proof = headers.get("x-payment", "").strip()
    body = _body(event)
    order_id = (body.get("order_id") or "").strip()
    # The slot fee is fixed; amount_cents in the body is advisory.
    amount_cents = SLOT_FEE_CENTS

    if not proof:
        # x402: tell the agent how to pay for the priority slot.
        return _json(402, {
            "x402Version": 1,
            "accepts": [{
                "scheme": "exact",
                "network": NETWORK,
                "maxAmountRequired": _atomic(amount_cents),
                "resource": _resource_url(event),
                "description": "Aisle priority delivery slot (next-hour)",
                "mimeType": "application/json",
                "payTo": PAYTO,
                "maxTimeoutSeconds": 300,
                "asset": ASSET,
                "extra": {"name": "USDC", "version": "2"},
            }],
        })

    # Paid: book the slot.
    return _json(200, {
        "status": "booked",
        "service": "priority_delivery",
        "order_ref": order_id or uuid.uuid4().hex,
        "slot_window": "next 60 minutes",
        "confirmation_code": uuid.uuid4().hex[:6].upper(),
        "amount_charged_cents": amount_cents,
        "network": NETWORK,
    }, {"X-Payment-Response": "settled"})
