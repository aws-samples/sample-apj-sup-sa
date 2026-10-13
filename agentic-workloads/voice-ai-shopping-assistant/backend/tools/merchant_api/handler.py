"""merchant_api — fake x402 "purchase" merchant (behind a Lambda Function URL).

Stands in for a real store checkout endpoint. Speaks the x402 protocol so the
create_order tool can exercise AgentCore Payments end-to-end:

  POST /  (no X-PAYMENT header)   -> 402 + x402 payment-required body
  POST /  (with X-PAYMENT header) -> 200 + order confirmation (pickup_code)

DEMO SIMPLIFICATION: a real x402 merchant would verify/settle the payment proof
on-chain via a facilitator. Here, presence of a non-empty X-PAYMENT header is
treated as paid — enough to prove the agent obtained a signed proof from
AgentCore ProcessPayment. The charge is a small fixed token amount (see
AMOUNT_ATOMIC), not the grocery total, so a testnet wallet can cover it.

Env:
  PAYTO_ADDRESS  — testnet wallet address that receives the micropayment
  ASSET_ADDRESS  — token contract (default USDC on Base Sepolia)
  NETWORK        — x402 network id (default base-sepolia)
  AMOUNT_ATOMIC  — required amount in atomic units (default 10000 = 0.01 USDC, 6dp)
"""
from __future__ import annotations

import json
import os
import uuid

PAYTO = os.environ.get("PAYTO_ADDRESS", "0x0000000000000000000000000000000000000000")
ASSET = os.environ.get("ASSET_ADDRESS", "0x036CbD53842c5426634e7929541eC2318f3dCF7e")
NETWORK = os.environ.get("NETWORK", "base-sepolia")
AMOUNT_ATOMIC = os.environ.get("AMOUNT_ATOMIC", "10000")  # 0.01 USDC (6 decimals)


def _headers(event) -> dict:
    # Function URL (v2) lowercases header keys into event["headers"].
    return {k.lower(): v for k, v in (event.get("headers") or {}).items()}


def _body(event) -> dict:
    raw = event.get("body") or "{}"
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return {}


def _resource_url(event) -> str:
    rc = (event.get("requestContext") or {}).get("http", {})
    domain = (event.get("headers") or {}).get("host", "merchant.local")
    return f"https://{domain}{rc.get('path', '/')}"


def handler(event, context):
    headers = _headers(event)
    payment_proof = headers.get("x-payment", "").strip()

    if not payment_proof:
        # x402: tell the caller how to pay.
        requirements = {
            "x402Version": 1,
            "accepts": [{
                "scheme": "exact",
                "network": NETWORK,
                "maxAmountRequired": AMOUNT_ATOMIC,
                "resource": _resource_url(event),
                "description": "Aisle grocery order placement",
                "mimeType": "application/json",
                "payTo": PAYTO,
                "maxTimeoutSeconds": 300,
                "asset": ASSET,
                "extra": {"name": "USDC", "version": "2"},
            }],
        }
        return {
            "statusCode": 402,
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(requirements),
        }

    # Paid: confirm the order. Echo back a pickup code + the order reference the
    # caller passed (so create_order can correlate).
    body = _body(event)
    confirmation = {
        "status": "confirmed",
        "pickup_code": uuid.uuid4().hex[:6].upper(),
        "merchant_reference": body.get("order_ref") or uuid.uuid4().hex,
        "amount_charged_atomic": AMOUNT_ATOMIC,
        "network": NETWORK,
    }
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json", "X-Payment-Response": "settled"},
        "body": json.dumps(confirmation),
    }
