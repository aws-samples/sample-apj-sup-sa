"""storefront — a minimal x402-funded grocery checkout the AgentCore browser drives.

Stands in for a real storefront, seeded with the SAME synthetic Aisle catalogue
products (via the order's order_items in Aurora). The async browser
worker navigates here, types the bridge-issued virtual card, and clicks Pay —
exercising a real browser checkout that completes a real AgentCore-funded payment
(through the mock x402->card bridge in virtual_cards).

Routes (behind an IAM-authorized API Gateway REST API; the browser worker
SigV4-signs each navigation, so the storefront is NOT public):
  GET  /?order_id=<uuid>                  -> HTML checkout page (cart + card form)
  GET  /pay?order_id&pan&exp&cvc          -> validate card vs virtual_cards, mark
        the card charged + order placed; returns an "Order Confirmed" page (200)
        or a decline page (402). GET (not POST) so every request is a payload-less
        SigV4 GET the worker can sign + inject via CDP before navigating.

Browser-automation friendly: stable data-testid selectors, no captcha, no JS
framework — predictable DOM the worker can drive deterministically.

INTEGRATING A REAL STOREFRONT: this stands in for a merchant's checkout. To
drive a real site instead, register the merchant in the `merchants` table
(`endpoint` = its base URL) and update the selectors/navigation in
backend/tools/place_order_async/handler.py to match that site's DOM and auth.
Only automate sites you operate or are authorized to automate, and respect their
terms of use — do NOT add bot-detection / CAPTCHA circumvention to drive a third
party's site.

Env: DB coords from SSM /aisle/db/* (same pattern as the tool Lambdas).
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
        _DB.update(cluster_arn=by_name["/aisle/db/cluster_arn"],
                   secret_arn=by_name["/aisle/db/secret_arn"], db_name=by_name["/aisle/db/name"])
    return _DB


def _exec(sql, params):
    db = _db()
    return rds.execute_statement(resourceArn=db["cluster_arn"], secretArn=db["secret_arn"],
                                 database=db["db_name"], sql=sql, parameters=params,
                                 formatRecordsAs="JSON")


def _s(n, v):
    return {"name": n, "value": {"stringValue": v}}


def _rows(resp):
    return json.loads(resp.get("formattedRecords") or "[]")


def _order(order_id: str) -> dict | None:
    rows = _rows(_exec(
        "SELECT order_id, status, total_cents, pickup_code FROM orders WHERE order_id = :oid::uuid",
        [_s("oid", order_id)]))
    return rows[0] if rows else None


def _items(order_id: str) -> list[dict]:
    return _rows(_exec(
        "SELECT name, qty, price_cents FROM order_items WHERE order_id = :oid::uuid ORDER BY name",
        [_s("oid", order_id)]))


def _money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def _page(order: dict, items: list[dict], msg: str = "") -> str:
    rows = "".join(
        f'<tr><td data-testid="item-name">{i["name"]}</td>'
        f'<td class="qty">{i["qty"]}</td>'
        f'<td class="price">{_money(i["price_cents"] * i["qty"])}</td></tr>'
        for i in items)
    banner = f'<div class="banner" data-testid="message">{msg}</div>' if msg else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Aisle Market — Checkout</title>
<style>
  :root {{ --green:#178a3f; --ink:#0b1f17; --bg:#f4f7f2; }}
  * {{ box-sizing:border-box; }}
  body {{ font-family:-apple-system,Segoe UI,Roboto,sans-serif; margin:0; background:var(--bg); color:var(--ink); }}
  header {{ background:var(--green); color:#fff; padding:16px 24px; font-size:20px; font-weight:700; }}
  .wrap {{ max-width:720px; margin:24px auto; padding:0 16px; }}
  .card {{ background:#fff; border-radius:14px; box-shadow:0 2px 10px rgba(0,0,0,.06); padding:22px; margin-bottom:18px; }}
  h2 {{ margin:0 0 14px; font-size:18px; }}
  table {{ width:100%; border-collapse:collapse; }}
  td {{ padding:9px 6px; border-bottom:1px solid #eef1ee; }}
  .qty,.price {{ text-align:right; color:#3a4a42; white-space:nowrap; }}
  .total {{ display:flex; justify-content:space-between; font-size:20px; font-weight:700; margin-top:14px; }}
  label {{ display:block; font-size:13px; color:#4a5a52; margin:12px 0 5px; }}
  input {{ width:100%; padding:12px; border:1px solid #cfd8d2; border-radius:9px; font-size:15px; }}
  .row {{ display:flex; gap:12px; }} .row > div {{ flex:1; }}
  button {{ width:100%; margin-top:18px; padding:14px; background:var(--green); color:#fff; border:0;
           border-radius:10px; font-size:16px; font-weight:700; cursor:pointer; }}
  .banner {{ padding:12px 14px; border-radius:9px; margin-bottom:16px; font-weight:600; }}
  .ok {{ background:#e7f6ec; color:#0f6a30; }} .err {{ background:#fdecec; color:#a01919; }}
  .muted {{ color:#7a8a82; font-size:13px; }}
</style></head><body>
<header>🛒 Aisle Market</header>
<div class="wrap">
  {banner}
  <div class="card">
    <h2>Your order</h2>
    <table><tbody>{rows}</tbody></table>
    <div class="total"><span>Total</span><span data-testid="order-total">{_money(order["total_cents"])}</span></div>
    <p class="muted">Order {order["order_id"]}</p>
  </div>
  <div class="card">
    <h2>Payment</h2>
    <form method="GET" action="pay" data-testid="checkout-form">
      <input type="hidden" name="order_id" value="{order['order_id']}">
      <label for="pan">Card number</label>
      <input id="pan" name="pan" data-testid="card-number" autocomplete="cc-number" placeholder="4242 4242 4242 4242">
      <div class="row">
        <div><label for="exp">Expiry</label>
          <input id="exp" name="exp" data-testid="card-exp" autocomplete="cc-exp" placeholder="MM/YY"></div>
        <div><label for="cvc">CVC</label>
          <input id="cvc" name="cvc" data-testid="card-cvc" autocomplete="cc-csc" placeholder="123"></div>
      </div>
      <button type="submit" data-testid="pay-button">Pay {_money(order['total_cents'])}</button>
    </form>
  </div>
</div></body></html>"""


def _confirm_page(order: dict, ok: bool, msg: str) -> str:
    cls = "ok" if ok else "err"
    title = "Order Confirmed" if ok else "Payment Declined"
    extra = (f'<p>Pickup code <b data-testid="pickup-code">{order["pickup_code"]}</b></p>'
             if ok else "")
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>
<style>body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#f4f7f2;margin:0}}
header{{background:#178a3f;color:#fff;padding:16px 24px;font-weight:700;font-size:20px}}
.wrap{{max-width:560px;margin:40px auto;padding:0 16px}}
.card{{background:#fff;border-radius:14px;box-shadow:0 2px 10px rgba(0,0,0,.06);padding:28px;text-align:center}}
.badge{{font-size:22px;font-weight:800;margin:8px 0}} .{cls}{{color:{'#0f6a30' if ok else '#a01919'}}}</style>
</head><body><header>🛒 Aisle Market</header><div class="wrap"><div class="card">
<div class="badge {cls}" data-testid="result">{title}</div>
<p data-testid="result-message">{msg}</p>{extra}
<p style="color:#7a8a82;font-size:13px">Order {order['order_id']}</p>
</div></div></body></html>"""


def _html(status: int, body: str) -> dict:
    return {"statusCode": status, "headers": {"Content-Type": "text/html; charset=utf-8"}, "body": body}


def _parse_form(raw: str) -> dict:
    from urllib.parse import parse_qs
    out = {}
    # accept both urlencoded form posts and JSON
    raw = raw or ""
    if raw.strip().startswith("{"):
        try:
            return json.loads(raw)
        except ValueError:
            return {}
    for k, v in parse_qs(raw).items():
        out[k] = v[0] if v else ""
    return out


def handler(event, context):
    # Support both event shapes: API Gateway REST (v1: top-level httpMethod/path,
    # path includes the {proxy+} value but NOT the stage) and HTTP API / Function
    # URL (v2: requestContext.http.{method,path}).
    rc_http = (event.get("requestContext") or {}).get("http", {})
    method = event.get("httpMethod") or rc_http.get("method") or "GET"
    # Prefer the proxy path param when present (REST {proxy+}); else the raw path.
    pp = (event.get("pathParameters") or {}).get("proxy")
    path = pp if pp is not None else (event.get("path") or rc_http.get("path") or "/")
    qs = event.get("queryStringParameters") or {}

    is_pay = (path or "").rstrip("/").endswith("pay")
    try:
        if not is_pay:
            order_id = (qs.get("order_id") or "").strip()
            if not order_id:
                return _html(400, "<h1>Missing order_id</h1>")
            order = _order(order_id)
            if not order:
                return _html(404, "<h1>Order not found</h1>")
            return _html(200, _page(order, _items(order_id)))

        if is_pay:
            # Params come from the query string (GET — payload-less so the worker
            # can SigV4-sign + navigate) or the body (POST form fallback).
            form = dict(qs) if method == "GET" else _parse_form(event.get("body"))
            order_id = (form.get("order_id") or "").strip()
            pan = (form.get("pan") or "").replace(" ", "").strip()
            order = _order(order_id) if order_id else None
            if not order:
                return _html(404, "<h1>Order not found</h1>")
            # Validate the card against the issuer bridge (virtual_cards).
            cards = _rows(_exec(
                """SELECT card_id, funded_cents, status FROM virtual_cards
                   WHERE order_id = :oid::uuid AND pan = :pan""",
                [_s("oid", order_id), _s("pan", pan)]))
            if not cards:
                return _html(402, _confirm_page(order, False, "Card declined — unknown card."))
            card = cards[0]
            if card["status"] != "active":
                return _html(402, _confirm_page(order, False, "Card declined — already used."))
            if int(card["funded_cents"]) < int(order["total_cents"]):
                return _html(402, _confirm_page(
                    order, False, "Card declined — insufficient funds."))
            # Authorize: mark card charged + order placed.
            _exec("UPDATE virtual_cards SET status='charged' WHERE card_id = :cid::uuid",
                  [_s("cid", card["card_id"])])
            _exec("""UPDATE orders SET status='placed', status_detail='paid via Aisle Market checkout',
                     updated_at=now() WHERE order_id = :oid::uuid""", [_s("oid", order_id)])
            order = _order(order_id)
            return _html(200, _confirm_page(order, True, "Payment received. Thanks for shopping!"))

        return _html(405, "<h1>Method not allowed</h1>")
    except Exception as e:  # noqa: BLE001
        return _html(500, f"<h1>Error</h1><pre>{e}</pre>")
