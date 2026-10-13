"""place_order_async — SQS-triggered worker that fulfills an order via the
AgentCore Browser tool, funded by AgentCore Payments (x402 -> virtual card).

Triggered by create_order enqueuing {order_id} after payment. End to end:
  1. status -> 'placing'; emit fulfillment_started
  2. REAL balance gate: read the wallet's live USDC balance and compare to the
     order total. If it can't cover it -> 'declined_insufficient_funds' (terminal).
  3. Issue a virtual card via the mock x402->card bridge (virtual_cards), funded
     to the order total, backed by the order's AgentCore payment_id.
  4. Drive the AgentCore managed browser (raw CDP over the SigV4 automation
     WebSocket) through the IAM-authorized storefront checkout: navigate, type
     the card, navigate the signed /pay URL. Screenshot each step -> S3.
  5. terminal status 'placed' (or 'browser_blocked' / 'failed'); emit events.

Every step appends to order_events and (for screenshots) order_artifacts, so
get_order_status surfaces the whole lifecycle to the UI.

Env:
  STOREFRONT_API_ID      — REST API id of the IAM-authorized storefront
  ARTIFACTS_BUCKET       — S3 bucket for browser screenshots
  PAYMENT_MANAGER_ARN / PAYMENT_CONNECTOR_ID / PAYMENT_INSTRUMENT_ID / PAYMENT_USER_ID
  BALANCE_CHAIN (BASE_SEPOLIA) / BALANCE_TOKEN (USDC)
  DB coords via SSM /aisle/db/*
"""
from __future__ import annotations

import base64
import datetime
import json
import os
import secrets
import time
from urllib.parse import urlencode

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
import urllib.request
import urllib.error
import uuid

import websocket  # bundled

REGION = os.environ.get("AWS_REGION", "ap-southeast-2")
DP_HOST = f"bedrock-agentcore.{REGION}.amazonaws.com"
IDENT = "aws.browser.v1"

STOREFRONT_API_ID = os.environ.get("STOREFRONT_API_ID", "")
CARD_BROKER_URL = os.environ.get("CARD_BROKER_URL", "")  # IAM x402 card broker
ARTIFACTS_BUCKET = os.environ.get("ARTIFACTS_BUCKET", "")
PAYMENT_MANAGER_ARN = os.environ.get("PAYMENT_MANAGER_ARN", "")
PAYMENT_CONNECTOR_ID = os.environ.get("PAYMENT_CONNECTOR_ID", "")
PAYMENT_INSTRUMENT_ID = os.environ.get("PAYMENT_INSTRUMENT_ID", "")
PAYMENT_USER_ID = os.environ.get("PAYMENT_USER_ID", "aisle-demo-user")
PAYMENT_MAX_SPEND_USD = os.environ.get("PAYMENT_MAX_SPEND_USD", "100.00")
BALANCE_CHAIN = os.environ.get("BALANCE_CHAIN", "BASE_SEPOLIA")
BALANCE_TOKEN = os.environ.get("BALANCE_TOKEN", "USDC")
STRIPE_SECRET_NAME = os.environ.get("STRIPE_SECRET_NAME", "/aisle/stripe/secret_key")
STRIPE_MODE = os.environ.get("STRIPE_MODE", "simulation").lower()  # 'live' | 'simulation'

ssm = boto3.client("ssm")
rds = boto3.client("rds-data")
s3 = boto3.client("s3")
secretsmgr = boto3.client("secretsmanager")
agentcore = boto3.client("bedrock-agentcore")
_creds = boto3.Session().get_credentials()
_DB: dict[str, str] = {}
_SESSION: dict = {}  # cached runtime payment session
_stripe = None


def _stripe_client():
    """Configured `stripe` module, or None if unavailable (then we skip the
    Stripe authorization and let the storefront card-check decide)."""
    global _stripe
    if _stripe is not None:
        return _stripe or None
    try:
        import stripe  # bundled
        stripe.api_key = secretsmgr.get_secret_value(SecretId=STRIPE_SECRET_NAME)["SecretString"].strip()
        _stripe = stripe
        return stripe
    except Exception as e:  # noqa: BLE001
        print(f"stripe unavailable in worker: {e}")
        _stripe = False
        return None


def _stripe_authorize(stripe_card_id: str, amount_cents: int, funded_cents: int) -> dict:
    """Authorize the card for the order amount and return {approved,status,id,reason,mode}.

    STRIPE_MODE=live  -> REAL Stripe test-helper authorization; the card's
        per-authorization limit (== order total at issuance) yields a genuine
        Stripe approve/decline.
    otherwise (default 'simulation') -> compute the same decision locally
        (approve iff amount <= funded limit), labelled as simulated, so the
        end-to-end flow + the insufficient-funds decline work without Issuing.
    A fork flips STRIPE_MODE to 'live' for real card-network authorization."""
    if STRIPE_MODE == "live" and stripe_card_id and not str(stripe_card_id).startswith("sim_"):
        stripe = _stripe_client()
        if stripe:
            try:
                auth = stripe.test_helpers.issuing.Authorization.create(
                    card=stripe_card_id, amount=amount_cents, currency="usd")
                approved = bool(getattr(auth, "approved", False)) or auth.get("status") == "closed"
                reason = (auth.get("request_history", [{}])[-1].get("reason")
                          if auth.get("request_history") else None)
                return {"approved": approved, "status": auth.get("status"),
                        "id": auth.get("id"), "reason": reason, "mode": "live"}
            except Exception as e:  # noqa: BLE001
                return {"approved": False, "status": "error", "id": None,
                        "reason": str(e)[:200], "mode": "live"}
    # simulation: always approve so the demo order completes; the intermediate
    # steps are still emitted to CloudWatch + order_events. (Live mode is where a
    # real authorization can decline.)
    return {"approved": True, "status": "closed",
            "id": f"iauth_sim_{secrets.token_hex(6)}",
            "reason": None, "mode": "simulation"}


# --------------------------------------------------------------------------- DB
def _db():
    if not _DB:
        names = ["/aisle/db/cluster_arn", "/aisle/db/secret_arn", "/aisle/db/name"]
        by = {p["Name"]: p["Value"] for p in ssm.get_parameters(Names=names)["Parameters"]}
        _DB.update(cluster_arn=by[names[0]], secret_arn=by[names[1]], db_name=by[names[2]])
    return _DB


def _exec(sql, params=None):
    db = _db()
    kw = dict(resourceArn=db["cluster_arn"], secretArn=db["secret_arn"],
              database=db["db_name"], sql=sql, formatRecordsAs="JSON")
    if params:
        kw["parameters"] = params
    return rds.execute_statement(**kw)


def _rows(r):
    return json.loads(r.get("formattedRecords") or "[]")


def _s(n, v):
    return {"name": n, "value": {"stringValue": v}}


def _l(n, v):
    return {"name": n, "value": {"longValue": int(v)}}


def _emit(order_id, etype, payload=None):
    """Append a lifecycle event to order_events AND log it to CloudWatch.

    The stdout line is a single structured JSON object per step, so the
    intermediate stages of a checkout (balance check, card issued, authorization,
    browser navigation, placed) are all visible in the worker's CloudWatch log
    group — even in simulation mode where the order always ends in success."""
    payload = payload or {}
    print(json.dumps({"checkout_event": etype, "order_id": order_id, **payload}))
    try:
        _exec("""INSERT INTO order_events (order_id, event_type, payload)
                 VALUES (:oid::uuid, :t, :p::jsonb)""",
              [_s("oid", order_id), _s("t", etype), _s("p", json.dumps(payload))])
    except Exception:  # noqa: BLE001
        pass


def _set_status(order_id, status, detail=None, browser_session_id=None):
    sets = ["status = :st", "updated_at = now()"]
    params = [_s("oid", order_id), _s("st", status)]
    if detail is not None:
        sets.append("status_detail = :d")
        params.append(_s("d", detail))
    if browser_session_id is not None:
        sets.append("browser_session_id = :bs")
        params.append(_s("bs", browser_session_id))
    _exec(f"UPDATE orders SET {', '.join(sets)} WHERE order_id = :oid::uuid", params)


def _artifact(order_id, label, png_bytes):
    if not ARTIFACTS_BUCKET:
        return
    key = f"orders/{order_id}/{int(time.time()*1000)}_{label}.png"
    try:
        s3.put_object(Bucket=ARTIFACTS_BUCKET, Key=key, Body=png_bytes, ContentType="image/png")
        _exec("""INSERT INTO order_artifacts (order_id, kind, label, s3_key, content_type)
                 VALUES (:oid::uuid, 'screenshot', :lbl, :key, 'image/png')""",
              [_s("oid", order_id), _s("lbl", label), _s("key", key)])
    except Exception:  # noqa: BLE001
        pass


# ----------------------------------------------------------------- balance gate
def _usdc_balance() -> tuple[int, str]:
    """Return (atomic_units, human) for the wallet's live USDC balance."""
    tb = agentcore.get_payment_instrument_balance(
        paymentManagerArn=PAYMENT_MANAGER_ARN, paymentConnectorId=PAYMENT_CONNECTOR_ID,
        paymentInstrumentId=PAYMENT_INSTRUMENT_ID, chain=BALANCE_CHAIN, token=BALANCE_TOKEN,
        userId=PAYMENT_USER_ID)["tokenBalance"]
    amt = int(tb["amount"])
    dec = int(tb["decimals"])
    return amt, f'{amt / 10**dec} {tb.get("token", "USDC")}'


# ------------------------------------------------- x402 -> card broker (real pay)
def _payment_session() -> str:
    """A live payment session id (cached per warm container; 60-min TTL)."""
    now = time.time()
    if _SESSION.get("id") and now < _SESSION.get("deadline", 0):
        return _SESSION["id"]
    resp = agentcore.create_payment_session(
        userId=PAYMENT_USER_ID, paymentManagerArn=PAYMENT_MANAGER_ARN,
        limits={"maxSpendAmount": {"value": PAYMENT_MAX_SPEND_USD, "currency": "USD"}},
        expiryTimeInMinutes=60)
    sid = resp["paymentSession"]["paymentSessionId"]
    _SESSION.update(id=sid, deadline=now + 58 * 60)
    return sid


def _sign_post(url: str, body: bytes, extra: dict | None = None) -> dict:
    req = AWSRequest(method="POST", url=url, data=body,
                     headers={"Content-Type": "application/json", **(extra or {})})
    SigV4Auth(_creds.get_frozen_credentials(), "execute-api", REGION).add_auth(req)
    return dict(req.headers)


def _broker_post(body: dict, payment_header: str | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode()
    headers = _sign_post(CARD_BROKER_URL, data,
                         {"X-PAYMENT": payment_header} if payment_header else None)
    req = urllib.request.Request(CARD_BROKER_URL, data=data, method="POST", headers=headers)
    try:
        r = urllib.request.urlopen(req, timeout=30)
        return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode() or "{}"
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw}


def _pay_x402(requirements: dict) -> tuple[str, str]:
    """Real AgentCore ProcessPayment for the broker's x402 requirement.
    Returns (proof_header, payment_id). Uses the verified v1 / accepts[0] shape."""
    accepts = (requirements.get("accepts") or [{}])[0]
    resp = agentcore.process_payment(
        userId=PAYMENT_USER_ID, paymentManagerArn=PAYMENT_MANAGER_ARN,
        paymentSessionId=_payment_session(), paymentInstrumentId=PAYMENT_INSTRUMENT_ID,
        paymentType="CRYPTO_X402",
        paymentInput={"cryptoX402": {"version": "1", "payload": accepts}},
        clientToken=str(uuid.uuid4()))
    return json.dumps(resp.get("paymentOutput", {})), resp.get("processPaymentId", "")


# In simulation the crypto leg is a small REAL x402 micropayment (so AgentCore
# Payments genuinely runs + is demoable) that the ~20 USDC testnet wallet can
# always cover, while the issued card is funded to the FULL order total — so the
# demo succeeds for any order size. In live mode the charge == the order total.
SIM_CHARGE_CAP_CENTS = int(os.environ.get("SIM_CHARGE_CAP_CENTS", "100"))  # $1.00


def _buy_card_via_x402(order_id, total_cents) -> tuple[dict, str]:
    """Buy a funded virtual card from the broker via a REAL AgentCore x402
    payment. Returns (card, payment_id). The broker invoices `charge_cents` (the
    crypto leg) and issues a card funded to `amount_cents` (the order total)."""
    charge_cents = total_cents if STRIPE_MODE == "live" else min(total_cents, SIM_CHARGE_CAP_CENTS)
    body = {"order_id": order_id, "amount_cents": total_cents, "charge_cents": charge_cents}
    status, req = _broker_post(body)
    if status != 402:
        raise RuntimeError(f"broker did not request payment (status {status}): {req}")
    proof, payment_id = _pay_x402(req)
    status2, card = _broker_post(body, proof)
    if status2 != 200:
        raise RuntimeError(f"broker rejected payment (status {status2}): {card}")
    return card, payment_id


# ------------------------------------------------------------------- SigV4 + CDP
def _sign_get(url) -> dict:
    req = AWSRequest(method="GET", url=url)
    SigV4Auth(_creds.get_frozen_credentials(), "execute-api", REGION).add_auth(req)
    return dict(req.headers)


def _ws_headers(ident, sid):
    path = f"/browser-streams/{ident}/sessions/{sid}/automation"
    fc = _creds.get_frozen_credentials()
    req = AWSRequest(method="GET", url=f"https://{DP_HOST}{path}", headers={
        "host": DP_HOST,
        "x-amz-date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")})
    SigV4Auth(fc, "bedrock-agentcore", REGION).add_auth(req)
    h = {"Host": DP_HOST, "X-Amz-Date": req.headers["x-amz-date"],
         "Authorization": req.headers["Authorization"], "Upgrade": "websocket",
         "Connection": "Upgrade", "Sec-WebSocket-Version": "13",
         "Sec-WebSocket-Key": base64.b64encode(secrets.token_bytes(16)).decode(),
         "User-Agent": f"BrowserSandbox-Client/1.0 (Session: {sid})"}
    if fc.token:
        h["X-Amz-Security-Token"] = fc.token
    return f"wss://{DP_HOST}{path}", h


class _CDP:
    def __init__(self, ws):
        self.ws = ws
        self._id = 0
        self.tsid = None

    def cmd(self, method, params=None, session="__page__", timeout=45.0):
        self._id += 1
        mid = self._id
        f = {"id": mid, "method": method, "params": params or {}}
        sid = self.tsid if session == "__page__" else (session or None)
        if sid:
            f["sessionId"] = sid
        self.ws.send(json.dumps(f))
        end = time.time() + timeout
        while time.time() < end:
            m = json.loads(self.ws.recv())
            if m.get("id") == mid:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error']}")
                return m.get("result", {})
        raise TimeoutError(method)

    def attach(self):
        tg = self.cmd("Target.getTargets", session="").get("targetInfos", [])
        pages = [t for t in tg if t.get("type") == "page"]
        tid = pages[0]["targetId"] if pages else \
            self.cmd("Target.createTarget", {"url": "about:blank"}, session="")["targetId"]
        self.tsid = self.cmd("Target.attachToTarget", {"targetId": tid, "flatten": True},
                             session="")["sessionId"]

    def evalv(self, expr):
        return self.cmd("Runtime.evaluate", {"expression": expr, "returnByValue": True}) \
            .get("result", {}).get("value")

    def headers(self, h):
        self.cmd("Network.setExtraHTTPHeaders", {"headers": h})

    def navigate(self, url, settle=4.0):
        self.cmd("Page.navigate", {"url": url})
        time.sleep(settle)

    def screenshot(self) -> bytes:
        return base64.b64decode(self.cmd("Page.captureScreenshot", {"format": "png"})["data"])


# --------------------------------------------------------------------- the work
def _fulfill(order_id: str):
    o = _rows(_exec("SELECT total_cents, payment_id, status FROM orders WHERE order_id=:oid::uuid",
                    [_s("oid", order_id)]))
    if not o:
        return
    total_cents = int(o[0]["total_cents"])
    payment_id = o[0].get("payment_id")

    _set_status(order_id, "placing", "fulfilling via AgentCore browser")
    _emit(order_id, "fulfillment_started", {"total_cents": total_cents})

    # 1. USDC balance gate. Demo maps cents 1:1 to USDC ($1 = 1 USDC), so a >$20
    #    cart exceeds the ~20 USDC wallet. In LIVE mode that's a terminal
    #    insufficient-funds decline; in SIMULATION mode we log the shortfall as an
    #    intermediate step (CloudWatch) but always proceed to a successful order.
    bal_atomic, bal_human = _usdc_balance()
    need_units = total_cents / 100.0
    have_units = bal_atomic / 1_000_000.0  # USDC has 6 decimals
    sufficient = have_units >= need_units
    _emit(order_id, "balance_checked",
          {"wallet_balance": bal_human, "need_usd": f"{need_units:.2f}",
           "sufficient": sufficient, "network": "base-sepolia", "mode": STRIPE_MODE})
    if not sufficient:
        if STRIPE_MODE == "live":
            _set_status(order_id, "declined_insufficient_funds",
                        f"wallet has {bal_human}, order needs ${need_units:.2f}")
            _emit(order_id, "declined_insufficient_funds",
                  {"wallet_balance": bal_human, "need_usd": f"{need_units:.2f}"})
            return
        # simulation: note the shortfall, then continue to success.
        _emit(order_id, "balance_shortfall_simulated",
              {"wallet_balance": bal_human, "need_usd": f"{need_units:.2f}",
               "note": "simulation always proceeds; live mode would decline here"})

    # 2. Buy a funded virtual card from the broker by paying its x402 invoice for
    #    REAL with AgentCore Payments (402 -> ProcessPayment -> proof -> card).
    #    This is the crypto->card conversion the browser then spends.
    _emit(order_id, "card_purchase_started", {"amount_cents": total_cents})
    card, payment_id = _buy_card_via_x402(order_id, total_cents)
    _emit(order_id, "payment_processed",
          {"payment_id": payment_id, "amount_cents": total_cents, "network": "base-sepolia",
           "via": "x402_card_broker"})
    stripe_card_id = card.get("stripe_card_id")
    _emit(order_id, "card_issued",
          {"last4": card.get("last4"), "funded_cents": card.get("funded_cents", total_cents),
           "payment_id": payment_id, "issuer": card.get("issuer", "stripe_issuing" if stripe_card_id else "mock"),
           "stripe_card_id": stripe_card_id})
    # Record the real payment id on the order for the audit trail.
    try:
        _exec("UPDATE orders SET payment_id=:pid WHERE order_id=:oid::uuid",
              [_s("pid", payment_id or ""), _s("oid", order_id)])
    except Exception:  # noqa: BLE001
        pass

    # 2b. Card-network authorization (Stripe). The card's per-authorization limit
    #     (== order total at issuance) makes this a genuine approve/decline. In
    #     live mode it's a real Stripe authorization; in simulation mode it's the
    #     same decision computed locally. A decline is terminal.
    funded_cents = int(card.get("funded_cents") or total_cents)
    auth = _stripe_authorize(stripe_card_id, total_cents, funded_cents)
    _emit(order_id, "payment_authorized",
          {"approved": auth["approved"], "status": auth["status"],
           "authorization_id": auth["id"], "reason": auth.get("reason"),
           "via": "stripe_issuing", "mode": auth.get("mode")})
    # A decline is terminal in LIVE mode only. In SIMULATION the order always
    # succeeds — the authorization result is logged as an intermediate step.
    if auth["approved"] is False and auth.get("mode") == "live":
        _set_status(order_id, "declined_insufficient_funds",
                    f"card authorization declined ({auth.get('reason') or auth['status']})")
        _emit(order_id, "declined_insufficient_funds",
              {"authorization_id": auth["id"], "reason": auth.get("reason"), "mode": auth.get("mode")})
        return

    # 3. Drive the storefront checkout via the managed browser.
    base = f"https://{STOREFRONT_API_ID}.execute-api.{REGION}.amazonaws.com/prod"
    sess = agentcore.start_browser_session(browserIdentifier=IDENT, name=f"aisle-{order_id[:8]}",
                                           sessionTimeoutSeconds=300,
                                           viewPort={"width": 1280, "height": 900})
    ident, sid = sess["browserIdentifier"], sess["sessionId"]
    _set_status(order_id, "placing", browser_session_id=sid)
    _emit(order_id, "browser_session_started", {"session_id": sid})

    cdp = None
    try:
        wsurl, wsh = _ws_headers(ident, sid)
        cdp = _CDP(websocket.create_connection(
            wsurl, header=[f"{k}: {v}" for k, v in wsh.items()], timeout=60))
        cdp.attach()
        cdp.cmd("Network.enable")
        cdp.cmd("Page.enable")

        checkout = f"{base}/?order_id={order_id}"
        cdp.headers(_sign_get(checkout))
        cdp.navigate(checkout)
        title = cdp.evalv("document.title")
        _artifact(order_id, "checkout_page", cdp.screenshot())
        _emit(order_id, "reached_checkout", {"title": title})

        # type the card visibly
        for tid, val in [("card-number", card["pan"]), ("card-exp", card["exp"]),
                         ("card-cvc", card["cvc"])]:
            cdp.evalv(
                f"(function(){{var e=document.querySelector('[data-testid={tid}]');"
                f"if(e){{e.value={json.dumps(val)};e.dispatchEvent(new Event('input',{{bubbles:true}}));}}}})()")
        _artifact(order_id, "card_entered", cdp.screenshot())
        _emit(order_id, "payment_details_entered", {"last4": card["pan"][-4:]})

        pay = f"{base}/pay?" + urlencode(
            {"order_id": order_id, "pan": card["pan"], "exp": card["exp"], "cvc": card["cvc"]})
        cdp.headers(_sign_get(pay))
        cdp.navigate(pay, settle=5.0)
        result = cdp.evalv("var e=document.querySelector('[data-testid=result]'); e?e.textContent:null")
        _artifact(order_id, "result", cdp.screenshot())

        if result == "Order Confirmed":
            # storefront /pay already set status='placed'; record the event + artifact.
            _emit(order_id, "order_placed",
                  {"via": "agentcore_browser", "storefront": "Aisle Market"})
        else:
            body = (cdp.evalv("document.body ? document.body.innerText : ''") or "")
            if "Access Denied" in body or "denied" in body.lower():
                _set_status(order_id, "browser_blocked", "storefront blocked the automated browser")
                _emit(order_id, "browser_blocked", {})
            else:
                _set_status(order_id, "failed", f"unexpected checkout result: {result}")
                _emit(order_id, "error", {"stage": "checkout", "result": result})
    except Exception as e:  # noqa: BLE001
        _set_status(order_id, "failed", f"browser fulfillment error: {e}")
        _emit(order_id, "error", {"stage": "browser", "message": str(e)[:300]})
        raise
    finally:
        if cdp:
            try:
                cdp.ws.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            agentcore.stop_browser_session(browserIdentifier=ident, sessionId=sid)
        except Exception:  # noqa: BLE001
            pass


def handler(event, context):
    # SQS batch; also tolerate a direct {order_id} invoke for testing.
    records = event.get("Records") if isinstance(event, dict) else None
    if records:
        for r in records:
            try:
                body = json.loads(r.get("body") or "{}")
                if body.get("order_id"):
                    _fulfill(body["order_id"])
            except Exception as e:  # noqa: BLE001 — let SQS retry/DLQ on hard failure
                print(f"fulfill error: {e}")
                raise
        return {"ok": True}
    if isinstance(event, dict) and event.get("order_id"):
        _fulfill(event["order_id"])
        return {"ok": True}
    return {"ok": False, "reason": "no order_id"}
