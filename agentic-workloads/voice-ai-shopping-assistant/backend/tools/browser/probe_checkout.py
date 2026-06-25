#!/usr/bin/env python3
"""Phase-2b-3 probe: AgentCore browser drives the x402 storefront to a paid order.

Proves the full browser-checkout integration before the async worker is wired:
  1. issue a virtual card for an existing order (the mock x402->card bridge:
     insert into virtual_cards, funded >= total),
  2. start an AgentCore browser session,
  3. SigV4-sign + navigate the IAM-authorized storefront checkout page (screenshot),
  4. visibly type the card into the form fields via CDP (screenshot),
  5. SigV4-sign the GET /pay?... URL and navigate to it (a browser form submit
     can't be SigV4-signed, so the worker signs the exact pay URL and injects the
     auth headers via Network.setExtraHTTPHeaders), landing on the confirmation,
  6. assert "Order Confirmed" and the order row flips to status='placed'.

Usage: /tmp/browservenv/bin/python backend/tools/browser/probe_checkout.py <order_id>
       (uses the most recent order if none given)
"""
from __future__ import annotations

import base64
import datetime
import json
import secrets
import sys
import time
from urllib.parse import urlencode, quote

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
import websocket

REGION = "ap-southeast-2"
IDENT = "aws.browser.v1"
DP_HOST = f"bedrock-agentcore.{REGION}.amazonaws.com"

ssm = boto3.client("ssm", region_name=REGION)
rds = boto3.client("rds-data", region_name=REGION)
data = boto3.client("bedrock-agentcore", region_name=REGION)
_creds = boto3.Session().get_credentials()

_DB = {p["Name"]: p["Value"] for p in ssm.get_parameters(
    Names=["/aisle/db/cluster_arn", "/aisle/db/secret_arn", "/aisle/db/name"])["Parameters"]}


def db(sql, params=None):
    kw = dict(resourceArn=_DB["/aisle/db/cluster_arn"], secretArn=_DB["/aisle/db/secret_arn"],
              database=_DB["/aisle/db/name"], sql=sql, formatRecordsAs="JSON")
    if params:
        kw["parameters"] = params
    for _ in range(8):
        try:
            return rds.execute_statement(**kw)
        except rds.exceptions.DatabaseResumingException:
            time.sleep(8)
    raise RuntimeError("db resume")


def rows(r):
    return json.loads(r.get("formattedRecords") or "[]")


def s(n, v):
    return {"name": n, "value": {"stringValue": v}}


# ---- the x402->card bridge: issue a deterministic test card for an order ----
def issue_card(order_id: str, funded_cents: int, payment_id: str = "probe") -> dict:
    pan = "4242" + "".join(secrets.choice("0123456789") for _ in range(12))
    db("""INSERT INTO virtual_cards (order_id, pan, exp, cvc, funded_cents, payment_id, status)
          VALUES (:oid::uuid, :pan, '12/30', :cvc, :funded, :pid, 'active')""",
       [s("oid", order_id), s("pan", pan), s("cvc", "%03d" % secrets.randbelow(1000)),
        {"name": "funded", "value": {"longValue": funded_cents}}, s("pid", payment_id)])
    return {"pan": pan, "exp": "12/30", "cvc": "123"}


# ---- SigV4 helpers ----
def sign_headers_for(url: str, service="execute-api") -> dict:
    req = AWSRequest(method="GET", url=url)
    SigV4Auth(_creds.get_frozen_credentials(), service, REGION).add_auth(req)
    return dict(req.headers)


def ws_headers(ident, sid):
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


class CDP:
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

    def set_headers(self, headers: dict):
        self.cmd("Network.setExtraHTTPHeaders", {"headers": headers})

    def navigate(self, url, settle=4.0):
        self.cmd("Page.navigate", {"url": url})
        time.sleep(settle)

    def shot(self, name):
        png = base64.b64decode(self.cmd("Page.captureScreenshot", {"format": "png"})["data"])
        path = f"/tmp/checkout_{name}.png"
        open(path, "wb").write(png)
        print(f"   screenshot -> {path} ({len(png)} bytes)")
        return path


def main():
    order_id = sys.argv[1] if len(sys.argv) > 1 else \
        rows(db("SELECT order_id FROM orders ORDER BY created_at DESC LIMIT 1"))[0]["order_id"]
    o = rows(db("SELECT order_id,total_cents,status FROM orders WHERE order_id=:oid::uuid",
                [s("oid", order_id)]))[0]
    total = int(o["total_cents"])
    api_id = open("/tmp/storefront_api_id.txt").read().strip()
    base = f"https://{api_id}.execute-api.{REGION}.amazonaws.com/prod"
    print("=" * 72)
    print(f"Checkout probe — order {order_id} total ${total/100:.2f} (status {o['status']})")
    print("=" * 72)

    print("1. Issue virtual card (x402->card bridge)...")
    card = issue_card(order_id, funded_cents=total, payment_id="probe-checkout")
    print(f"   card ****{card['pan'][-4:]} funded ${total/100:.2f}")

    print("2. Start browser session...")
    sess = data.start_browser_session(browserIdentifier=IDENT, name="aisle-checkout",
                                      sessionTimeoutSeconds=300,
                                      viewPort={"width": 1280, "height": 900})
    ident, sid = sess["browserIdentifier"], sess["sessionId"]
    cdp = None
    try:
        wsurl, wsh = ws_headers(ident, sid)
        cdp = CDP(websocket.create_connection(
            wsurl, header=[f"{k}: {v}" for k, v in wsh.items()], timeout=60))
        cdp.attach()
        cdp.cmd("Network.enable")
        cdp.cmd("Page.enable")

        print("3. Navigate checkout page (SigV4-signed)...")
        checkout_url = f"{base}/?order_id={order_id}"
        cdp.set_headers(sign_headers_for(checkout_url))
        cdp.navigate(checkout_url)
        total_shown = cdp.evalv("var e=document.querySelector('[data-testid=order-total]'); e?e.textContent:null")
        print(f"   title={cdp.evalv('document.title')!r} total_shown={total_shown!r}")
        cdp.shot("1_checkout")

        print("4. Type card into the form (visible)...")
        for tid, val in [("card-number", card["pan"]), ("card-exp", card["exp"]), ("card-cvc", card["cvc"])]:
            cdp.evalv(f"(function(){{var e=document.querySelector('[data-testid={tid}]');e.value={json.dumps(val)};e.dispatchEvent(new Event('input',{{bubbles:true}}));return e.value}})()")
        cdp.shot("2_filled")

        print("5. Submit payment — navigate SigV4-signed GET /pay...")
        pay_qs = urlencode({"order_id": order_id, "pan": card["pan"], "exp": card["exp"], "cvc": card["cvc"]})
        pay_url = f"{base}/pay?{pay_qs}"
        cdp.set_headers(sign_headers_for(pay_url))
        cdp.navigate(pay_url, settle=5.0)
        result = cdp.evalv("(document.querySelector('[data-testid=result]')||{}).textContent")
        rmsg = cdp.evalv("(document.querySelector('[data-testid=result-message]')||{}).textContent")
        print(f"   result={result!r} msg={rmsg!r}")
        cdp.shot("3_result")

        db_status = rows(db("SELECT status FROM orders WHERE order_id=:oid::uuid", [s("oid", order_id)]))[0]["status"]
        ok = (result == "Order Confirmed") and (db_status == "placed")
        print(f"\nRESULT: {'SUCCESS ✅ browser placed the order, paid via issued card' if ok else 'INCOMPLETE'}")
        print(f"  order status in DB: {db_status}")
    finally:
        if cdp:
            try:
                cdp.ws.close()
            except Exception:  # noqa: BLE001
                pass
        data.stop_browser_session(browserIdentifier=ident, sessionId=sid)


if __name__ == "__main__":
    main()
