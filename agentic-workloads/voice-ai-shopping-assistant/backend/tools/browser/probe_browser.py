#!/usr/bin/env python3
"""Phase-2b probe: prove we can drive the AgentCore managed browser via raw CDP.

De-risks the browser worker OUTSIDE Lambda before building the SQS/worker
machinery — same approach as the payment probe. It:
  1. StartBrowserSession on the default browser (aws.browser.v1),
  2. SigV4-signs the automation-stream WebSocket (reimplements the SDK's
     generate_ws_headers — pure botocore, no bedrock-agentcore SDK / Playwright),
  3. speaks Chrome DevTools Protocol over that socket: enable Page, navigate to a
     target URL, then Page.captureScreenshot,
  4. saves the screenshot locally so we can eyeball that the browser really
     loaded the page, and
  5. StopBrowserSession.

This validates the exact mechanism the worker will use to drive woolworths.com.au
(and to capture decline evidence). Run with the venv that has websocket-client:
    /tmp/browservenv/bin/python backend/tools/browser/probe_browser.py [url]
"""
from __future__ import annotations

import base64
import datetime
import json
import secrets
import sys
import time

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
import websocket  # websocket-client

REGION = "ap-southeast-2"
DEFAULT_IDENTIFIER = "aws.browser.v1"
TARGET_URL = sys.argv[1] if len(sys.argv) > 1 else "https://www.woolworths.com.au/"


def _data_plane_host() -> str:
    return f"bedrock-agentcore.{REGION}.amazonaws.com"


def ws_headers(identifier: str, session_id: str) -> tuple[str, dict]:
    """Reimplements BrowserClient.generate_ws_headers (pure botocore SigV4)."""
    host = _data_plane_host()
    path = f"/browser-streams/{identifier}/sessions/{session_id}/automation"
    ws_url = f"wss://{host}{path}"
    creds = boto3.Session().get_credentials().get_frozen_credentials()
    req = AWSRequest(method="GET", url=f"https://{host}{path}", headers={
        "host": host,
        "x-amz-date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
    })
    SigV4Auth(creds, "bedrock-agentcore", REGION).add_auth(req)
    headers = {
        "Host": host,
        "X-Amz-Date": req.headers["x-amz-date"],
        "Authorization": req.headers["Authorization"],
        "Upgrade": "websocket",
        "Connection": "Upgrade",
        "Sec-WebSocket-Version": "13",
        "Sec-WebSocket-Key": base64.b64encode(secrets.token_bytes(16)).decode(),
        "User-Agent": f"BrowserSandbox-Client/1.0 (Session: {session_id})",
    }
    if creds.token:
        headers["X-Amz-Security-Token"] = creds.token
    return ws_url, headers


class CDP:
    """Minimal Chrome DevTools Protocol client over the automation WebSocket.

    The automation stream connects at the BROWSER level, where Page/Runtime
    domains aren't available. We attach to a page target in "flatten" mode and
    route subsequent commands to that target via the per-message `sessionId`.
    """

    def __init__(self, ws_url: str, headers: dict):
        hdr = [f"{k}: {v}" for k, v in headers.items()]
        self.ws = websocket.create_connection(ws_url, header=hdr, timeout=60)
        self._id = 0
        self.target_session: str | None = None

    def send(self, method: str, params: dict | None = None, timeout: float = 45.0,
             session_id: str | None = None) -> dict:
        self._id += 1
        mid = self._id
        frame = {"id": mid, "method": method, "params": params or {}}
        # Route to the attached page target unless explicitly overridden. Browser-
        # level commands (Target.*) pass session_id="" to stay at browser scope.
        sid = self.target_session if session_id is None else (session_id or None)
        if sid:
            frame["sessionId"] = sid
        self.ws.send(json.dumps(frame))
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"CDP {method} error: {msg['error']}")
                return msg.get("result", {})
            # else: an event — keep reading
        raise TimeoutError(f"CDP {method} timed out")

    def attach_to_page(self, timeout: float = 30.0) -> str:
        """Find (or create) a page target and attach in flatten mode."""
        targets = self.send("Target.getTargets", session_id="").get("targetInfos", [])
        pages = [t for t in targets if t.get("type") == "page"]
        if pages:
            target_id = pages[0]["targetId"]
        else:
            target_id = self.send("Target.createTarget", {"url": "about:blank"},
                                   session_id="")["targetId"]
        res = self.send("Target.attachToTarget", {"targetId": target_id, "flatten": True},
                        session_id="")
        self.target_session = res["sessionId"]
        return self.target_session

    def wait_event(self, event: str, timeout: float = 30.0) -> dict | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = json.loads(self.ws.recv())
            except Exception:  # noqa: BLE001
                return None
            if msg.get("method") == event:
                return msg.get("params", {})
        return None

    def close(self):
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass


def main() -> None:
    data = boto3.client("bedrock-agentcore", region_name=REGION)
    print("=" * 72)
    print("Phase-2b browser probe")
    print(f"target: {TARGET_URL}")
    print("=" * 72)

    print("1. StartBrowserSession...")
    sess = data.start_browser_session(
        browserIdentifier=DEFAULT_IDENTIFIER,
        name="aisle-probe",
        sessionTimeoutSeconds=300,
        viewPort={"width": 1280, "height": 800},
    )
    identifier = sess["browserIdentifier"]
    session_id = sess["sessionId"]
    print(f"   session {session_id}")
    print(f"   automation stream: {sess.get('streams', {}).get('automationStream', {}).get('streamStatus')}")

    cdp = None
    try:
        print("2. Connecting CDP WebSocket (SigV4-signed)...")
        ws_url, headers = ws_headers(identifier, session_id)
        cdp = CDP(ws_url, headers)
        print("   connected")

        print("3. Attach to page target (flatten)...")
        tsid = cdp.attach_to_page()
        print(f"   target session: {tsid}")
        cdp.send("Page.enable")
        nav = cdp.send("Page.navigate", {"url": TARGET_URL})
        print(f"   navigate result: frameId={nav.get('frameId')} errorText={nav.get('errorText')}")
        cdp.wait_event("Page.loadEventFired", timeout=30)
        time.sleep(3)  # let JS settle

        # What did we land on?
        title = cdp.send("Runtime.evaluate", {"expression": "document.title", "returnByValue": True})
        url_now = cdp.send("Runtime.evaluate", {"expression": "location.href", "returnByValue": True})
        body_len = cdp.send("Runtime.evaluate",
                            {"expression": "document.body ? document.body.innerText.length : 0",
                             "returnByValue": True})
        print(f"   title:  {title.get('result', {}).get('value')!r}")
        print(f"   url:    {url_now.get('result', {}).get('value')!r}")
        print(f"   body text length: {body_len.get('result', {}).get('value')}")

        print("4. Page.captureScreenshot...")
        shot = cdp.send("Page.captureScreenshot", {"format": "png"})
        png = base64.b64decode(shot["data"])
        out = "/tmp/browser_probe.png"
        with open(out, "wb") as f:
            f.write(png)
        print(f"   saved {len(png)} bytes -> {out}")

        print("\nRESULT: SUCCESS — drove the managed browser via raw CDP and captured a screenshot.")
        print("  >> Worker can use this exact mechanism (no Playwright/Docker needed).")
    except Exception as e:  # noqa: BLE001
        print(f"\nRESULT: FAILED — {type(e).__name__}: {e}")
        raise
    finally:
        if cdp:
            cdp.close()
        print("5. StopBrowserSession...")
        try:
            data.stop_browser_session(browserIdentifier=identifier, sessionId=session_id)
            print("   stopped")
        except Exception as e:  # noqa: BLE001
            print(f"   stop error (non-fatal): {e}")


if __name__ == "__main__":
    main()
