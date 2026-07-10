"""card_broker — the x402 -> virtual-card broker.

This is the real "crypto to card" conversion: an x402 merchant whose product is a
funded virtual card. The async worker calls it to turn a real AgentCore x402
payment into a card the AgentCore browser can spend at the (card-only) storefront.

Flow (behind an IAM-authorized API Gateway; the worker SigV4-signs):
  POST /  {order_id, amount_cents}  no X-PAYMENT
        -> 402 + x402 requirements (amount_cents mapped to USDC atomic units)
  POST /  {order_id, amount_cents}  WITH X-PAYMENT: <proof>
        -> validate a proof is present, mint a virtual_cards row funded to
           amount_cents (backed by the proof's payment_id), return the card:
           {pan, exp, cvc, last4, funded_cents}

Demo simplification (same as merchant_api): a real broker would verify/settle the
x402 proof on-chain via a facilitator and hold real card-rail funds. Here the
presence of a signed AgentCore proof is treated as settled — enough to prove the
agent obtained a real payment proof before a spendable card is issued.

Money mapping: the demo treats $1 = 1 USDC, so amount_cents -> USDC atomic units
(6 decimals) is amount_cents * 10000 (e.g. $6.90 = 690c -> 6_900_000 atomic).

Env:
  PAYTO_ADDRESS — testnet wallet that receives the micropayment (funded wallet)
  ASSET_ADDRESS — USDC token contract (Base Sepolia default)
  NETWORK       — x402 network id (base-sepolia)
  DB coords via SSM /aisle/db/*
"""
from __future__ import annotations

import json
import os
import secrets

import boto3

ssm = boto3.client("ssm")
rds = boto3.client("rds-data")
secretsmgr = boto3.client("secretsmanager")
_DB: dict[str, str] = {}

PAYTO = os.environ.get("PAYTO_ADDRESS", "0x0000000000000000000000000000000000000000")
ASSET = os.environ.get("ASSET_ADDRESS", "0x036CbD53842c5426634e7929541eC2318f3dCF7e")
NETWORK = os.environ.get("NETWORK", "base-sepolia")
USDC_PER_DOLLAR_ATOMIC = 1_000_000  # 1 USDC = 1e6 atomic (6 decimals); demo $1 = 1 USDC

# Stripe Issuing. Secret lives in Secrets Manager, not env.
#
# STRIPE_MODE controls the card rail (see docs/STRIPE_ISSUING.md):
#   "simulation" (default) — DO NOT call Stripe; mint a Stripe-shaped simulated
#       card locally. Use when Stripe Issuing isn't available on the account
#       (it's geo/approval-gated). A fork enables real issuing by setting
#       STRIPE_MODE=live once their account has Issuing activated.
#   "live"       — call the REAL Stripe Issuing API to issue a virtual card.
# In BOTH modes the spending limit == the order total, so the downstream
# authorization decisions are identical; only the issuer differs.
STRIPE_SECRET_NAME = os.environ.get("STRIPE_SECRET_NAME", "/aisle/stripe/secret_key")
STRIPE_CURRENCY = os.environ.get("STRIPE_CURRENCY", "usd")
STRIPE_MODE = os.environ.get("STRIPE_MODE", "simulation").lower()
_stripe = None  # lazily imported + keyed Stripe client


def _stripe_client():
    """Return a configured `stripe` module, or None if unavailable. Only used
    when STRIPE_MODE == 'live'."""
    global _stripe
    if _stripe is not None:
        return _stripe or None
    try:
        import stripe  # bundled
        key = secretsmgr.get_secret_value(SecretId=STRIPE_SECRET_NAME)["SecretString"].strip()
        stripe.api_key = key
        _stripe = stripe
        return stripe
    except Exception as e:  # noqa: BLE001
        print(f"stripe client unavailable: {e}")
        _stripe = False
        return None


def _issue_stripe_card(order_id: str, amount_cents: int) -> dict | None:
    """Issue a REAL virtual card via Stripe Issuing, per-auth limit == order
    total. Returns card dict, or None to signal the caller to use simulation."""
    if STRIPE_MODE != "live":
        return None  # simulation mode: caller mints a simulated card
    stripe = _stripe_client()
    if not stripe:
        return None
    try:
        cardholder = stripe.issuing.Cardholder.create(
            name="Aisle Agent",
            email="agent@aisle.demo",
            type="individual",
            billing={"address": {"line1": "1 Market St", "city": "Sydney",
                                 "state": "NSW", "postal_code": "2000", "country": "AU"}},
        )
        card = stripe.issuing.Card.create(
            cardholder=cardholder.id, currency=STRIPE_CURRENCY, type="virtual",
            status="active",
            spending_controls={"spending_limits": [
                {"amount": amount_cents, "interval": "per_authorization"}]},
        )
        # PAN/CVC are sensitive — must be expanded explicitly.
        full = stripe.issuing.Card.retrieve(card.id, expand=["number", "cvc"])
        exp = f"{int(full.exp_month):02d}/{str(full.exp_year)[-2:]}"
        return {"pan": full.number, "exp": exp, "cvc": full.cvc,
                "last4": full.last4, "stripe_card_id": card.id}
    except Exception as e:  # noqa: BLE001 — Issuing not activated etc. -> simulate
        print(f"stripe issuing failed ({e}); falling back to simulation")
        return None


def _db() -> dict[str, str]:
    if not _DB:
        names = ["/aisle/db/cluster_arn", "/aisle/db/secret_arn", "/aisle/db/name"]
        by = {p["Name"]: p["Value"] for p in ssm.get_parameters(Names=names)["Parameters"]}
        _DB.update(cluster_arn=by[names[0]], secret_arn=by[names[1]], db_name=by[names[2]])
    return _DB


def _exec(sql, params):
    db = _db()
    return rds.execute_statement(resourceArn=db["cluster_arn"], secretArn=db["secret_arn"],
                                 database=db["db_name"], sql=sql, parameters=params,
                                 formatRecordsAs="JSON")


def _s(n, v):
    return {"name": n, "value": {"stringValue": v}}


def _l(n, v):
    return {"name": n, "value": {"longValue": int(v)}}


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
    rc = (event.get("requestContext") or {})
    path = rc.get("path") or (rc.get("http", {}) or {}).get("path", "/")
    return f"https://{h.get('host', h.get('Host', 'broker.local'))}{path}"


def _atomic(amount_cents: int) -> str:
    # cents -> dollars -> USDC atomic units (6 decimals). $1 = 1 USDC (demo).
    return str(int(round(amount_cents / 100.0 * USDC_PER_DOLLAR_ATOMIC)))


def _proof_payment_id(proof: str) -> str | None:
    # The worker passes the AgentCore paymentOutput JSON as X-PAYMENT; we don't
    # need to parse it (presence == settled, demo), but record any id we find.
    try:
        obj = json.loads(proof)
        return obj.get("processPaymentId") or obj.get("payment_id")
    except (ValueError, TypeError):
        return None


def _json(status: int, body: dict, extra_headers: dict | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    return {"statusCode": status, "headers": headers, "body": json.dumps(body)}


def handler(event, context):
    headers = _headers(event)
    proof = headers.get("x-payment", "").strip()
    body = _body(event)
    order_id = (body.get("order_id") or "").strip()
    try:
        amount_cents = int(body.get("amount_cents") or 0)
    except (TypeError, ValueError):
        amount_cents = 0
    # charge_cents is the crypto leg the worker actually pays (may be capped in
    # simulation so the testnet wallet can cover any order); the card is still
    # FUNDED to amount_cents (the order total). Defaults to amount_cents.
    try:
        charge_cents = int(body.get("charge_cents") or amount_cents)
    except (TypeError, ValueError):
        charge_cents = amount_cents

    if not order_id or amount_cents <= 0:
        return _json(400, {"error": "order_id and positive amount_cents required"})

    if not proof:
        # x402: tell the worker how to pay for the card (invoice = charge_cents).
        return _json(402, {
            "x402Version": 1,
            "accepts": [{
                "scheme": "exact",
                "network": NETWORK,
                "maxAmountRequired": _atomic(charge_cents),
                "resource": _resource_url(event),
                "description": f"Aisle virtual card funded to ${amount_cents/100:.2f}",
                "mimeType": "application/json",
                "payTo": PAYTO,
                "maxTimeoutSeconds": 300,
                "asset": ASSET,
                "extra": {"name": "USDC", "version": "2"},
            }],
        })

    # Paid: mint a virtual card funded to the order amount, backed by the proof.
    # STRIPE_MODE=live issues a REAL Stripe card; otherwise (default) we mint a
    # Stripe-shaped SIMULATED card. A fork flips STRIPE_MODE to "live" once their
    # account has Issuing activated. See docs/STRIPE_ISSUING.md.
    payment_id = _proof_payment_id(proof) or "broker"
    card = _issue_stripe_card(order_id, amount_cents)
    issuer = "stripe_issuing"
    if not card:
        issuer = "stripe_simulation"
        # Stripe's standard test PAN so it looks/behaves like a Stripe card.
        card = {"pan": "4242424242424242", "exp": "12/30",
                "cvc": "%03d" % secrets.randbelow(1000),
                "last4": "4242", "stripe_card_id": f"sim_{secrets.token_hex(8)}"}
    _exec("""INSERT INTO virtual_cards (order_id, pan, exp, cvc, funded_cents, payment_id, status, stripe_card_id)
             VALUES (:oid::uuid, :pan, :exp, :cvc, :funded, :pid, 'active', :scid)""",
          [_s("oid", order_id), _s("pan", card["pan"]), _s("exp", card["exp"]), _s("cvc", card["cvc"]),
           _l("funded", amount_cents), _s("pid", payment_id),
           (_s("scid", card["stripe_card_id"]) if card.get("stripe_card_id")
            else {"name": "scid", "value": {"isNull": True}})])
    return _json(200, {
        "status": "issued", "issuer": issuer,
        "pan": card["pan"], "exp": card["exp"], "cvc": card["cvc"],
        "last4": card["last4"], "funded_cents": amount_cents,
        "stripe_card_id": card.get("stripe_card_id"),
        "payment_id": payment_id, "network": NETWORK,
    }, {"X-Payment-Response": "settled"})
