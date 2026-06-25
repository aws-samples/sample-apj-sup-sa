#!/usr/bin/env python3
"""Phase-2b stealth probe: try to get past Woolworths' Akamai block.

The default AgentCore browser UA contains 'Amazon-Bedrock-AgentCore-Browser/1.0'
and 'X11; Linux x86_64' — instant bot tells. This probe applies the strongest
IN-PLATFORM evasion (no residential proxy) BEFORE navigating:
  - Emulation.setUserAgentOverride with a real macOS Chrome UA + full
    userAgentMetadata (so Sec-CH-UA client hints are consistent),
  - Network.setExtraHTTPHeaders (Accept-Language, sec-ch-ua, referer),
  - a warm-up navigation + retry so Akamai can set its _abck/bm_sz cookies,
then loads woolworths.com.au and reports whether we got the real site or the
edge "Access Denied".

Usage: /tmp/browservenv/bin/python backend/tools/browser/probe_stealth.py [url]
Optional proxy via env: PROXY_SERVER, PROXY_PORT, PROXY_USER, PROXY_PASS
"""
from __future__ import annotations

import base64
import datetime
import json
import os
import secrets
import sys
import time

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
import websocket

REGION = "ap-southeast-2"
IDENT = "aws.browser.v1"
HOST = f"bedrock-agentcore.{REGION}.amazonaws.com"
TARGET = sys.argv[1] if len(sys.argv) > 1 else "https://www.woolworths.com.au/"

# A real, current desktop Chrome on macOS — common, low-suspicion.
REAL_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36")
UA_METADATA = {
    "brands": [
        {"brand": "Chromium", "version": "146"},
        {"brand": "Google Chrome", "version": "146"},
        {"brand": "Not?A_Brand", "version": "24"},
    ],
    "fullVersion": "146.0.0.0",
    "platform": "macOS",
    "platformVersion": "15.0.0",
    "architecture": "arm",
    "model": "",
    "mobile": False,
}
EXTRA_HEADERS = {
    "Accept-Language": "en-AU,en;q=0.9",
    "sec-ch-ua": '"Chromium";v="146", "Google Chrome";v="146", "Not?A_Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "Upgrade-Insecure-Requests": "1",
}


def ws_connect(ident: str, sid: str):
    path = f"/browser-streams/{ident}/sessions/{sid}/automation"
    creds = boto3.Session().get_credentials().get_frozen_credentials()
    req = AWSRequest(method="GET", url=f"https://{HOST}{path}", headers={
        "host": HOST,
        "x-amz-date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
    })
    SigV4Auth(creds, "bedrock-agentcore", REGION).add_auth(req)
    hdrs = {
        "Host": HOST, "X-Amz-Date": req.headers["x-amz-date"],
        "Authorization": req.headers["Authorization"],
        "Upgrade": "websocket", "Connection": "Upgrade",
        "Sec-WebSocket-Version": "13",
        "Sec-WebSocket-Key": base64.b64encode(secrets.token_bytes(16)).decode(),
        "User-Agent": f"BrowserSandbox-Client/1.0 (Session: {sid})",
    }
    if creds.token:
        hdrs["X-Amz-Security-Token"] = creds.token
    return websocket.create_connection(
        f"wss://{HOST}{path}", header=[f"{k}: {v}" for k, v in hdrs.items()], timeout=60)


class CDP:
    def __init__(self, ws):
        self.ws = ws
        self._id = 0
        self.tsid = None

    def send(self, method, params=None, session="__page__", timeout=45.0):
        self._id += 1
        mid = self._id
        f = {"id": mid, "method": method, "params": params or {}}
        sid = self.tsid if session == "__page__" else (session or None)
        if sid:
            f["sessionId"] = sid
        self.ws.send(json.dumps(f))
        deadline = time.time() + timeout
        while time.time() < deadline:
            m = json.loads(self.ws.recv())
            if m.get("id") == mid:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error']}")
                return m.get("result", {})
        raise TimeoutError(method)

    def attach(self):
        tg = self.send("Target.getTargets", session="").get("targetInfos", [])
        pages = [t for t in tg if t.get("type") == "page"]
        tid = pages[0]["targetId"] if pages else \
            self.send("Target.createTarget", {"url": "about:blank"}, session="")["targetId"]
        self.tsid = self.send("Target.attachToTarget", {"targetId": tid, "flatten": True},
                              session="")["sessionId"]

    def evalv(self, expr):
        return self.send("Runtime.evaluate", {"expression": expr, "returnByValue": True}) \
            .get("result", {}).get("value")


def main():
    proxy = None
    if os.environ.get("PROXY_SERVER"):
        ep = {"server": os.environ["PROXY_SERVER"], "port": int(os.environ.get("PROXY_PORT", "0"))}
        if os.environ.get("PROXY_USER"):
            ep["credentials"] = {"basicAuth": {
                "username": os.environ["PROXY_USER"], "password": os.environ.get("PROXY_PASS", "")}}
        proxy = {"proxies": [{"externalProxy": ep}]}
        print(f"Using residential proxy: {ep['server']}:{ep['port']}")

    data = boto3.client("bedrock-agentcore", region_name=REGION)
    print("=" * 72)
    print(f"Stealth probe -> {TARGET}")
    print("=" * 72)

    start_kwargs = dict(browserIdentifier=IDENT, name="aisle-stealth",
                        sessionTimeoutSeconds=300, viewPort={"width": 1280, "height": 800})
    if proxy:
        start_kwargs["proxyConfiguration"] = proxy
    s = data.start_browser_session(**start_kwargs)
    ident, sid = s["browserIdentifier"], s["sessionId"]
    print(f"session {sid}")

    cdp = None
    try:
        cdp = CDP(ws_connect(ident, sid))
        cdp.attach()
        cdp.send("Network.enable")
        cdp.send("Page.enable")

        # Stealth BEFORE navigating: real UA + client-hint metadata + headers.
        cdp.send("Emulation.setUserAgentOverride",
                 {"userAgent": REAL_UA, "userAgentMetadata": UA_METADATA,
                  "acceptLanguage": "en-AU,en;q=0.9", "platform": "MacIntel"})
        cdp.send("Network.setExtraHTTPHeaders", {"headers": EXTRA_HEADERS})
        # Mask webdriver defensively (already False, but harmless).
        cdp.send("Page.addScriptToEvaluateOnNewDocument",
                 {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"})

        print(f"UA now: {cdp.evalv('navigator.userAgent')!r}")

        def go(url, label):
            cdp.send("Page.navigate", {"url": url})
            time.sleep(5)
            title = cdp.evalv("document.title")
            href = cdp.evalv("location.href")
            blen = cdp.evalv("document.body ? document.body.innerText.length : 0")
            denied = bool(title and "Access Denied" in title) or \
                (cdp.evalv("document.body ? document.body.innerText : ''") or "").find("Access Denied") >= 0
            print(f"  [{label}] title={title!r} url={href!r} body_len={blen} denied={denied}")
            return denied

        print("navigate (attempt 1)...")
        denied1 = go(TARGET, "try1")
        if denied1:
            print("retry after cookie warm-up (attempt 2)...")
            time.sleep(3)
            denied1 = go(TARGET, "try2")

        shot = cdp.send("Page.captureScreenshot", {"format": "png"})
        out = "/tmp/stealth_probe.png"
        open(out, "wb").write(base64.b64decode(shot["data"]))
        print(f"screenshot -> {out}")

        print("\nRESULT:", "STILL BLOCKED (Access Denied)" if denied1 else "GOT THROUGH ✅")
        if denied1 and not proxy:
            print("  Block fired with a real UA too → it's IP-reputation (AWS datacenter ASN).")
            print("  Next lever: residential proxy via PROXY_SERVER/PORT/USER/PASS env.")
    finally:
        if cdp:
            try:
                cdp.ws.close()
            except Exception:  # noqa: BLE001
                pass
        data.stop_browser_session(browserIdentifier=ident, sessionId=sid)


if __name__ == "__main__":
    main()
