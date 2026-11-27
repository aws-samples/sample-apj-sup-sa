# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Local viewer for the usage dashboard. Listens on 127.0.0.1 only and forwards GET requests to the
dashboard's Lambda Function URL (AuthType AWS_IAM), signing each with SigV4 from your AWS profile.

  python dashboard/proxy.py [--profile default] [--port 8090] [--url <dashboard_url>]
  then open the http://127.0.0.1:8090/?k=<key> link it prints

Every request needs a key made fresh for each run: the printed link sets it as an HttpOnly cookie. Without it,
another OS user on the same machine could read the dashboard through this proxy with your AWS credentials.
Browsers send the key cookie to every port on 127.0.0.1, so a server another OS user runs on this machine could
receive it if your browser visits it; on a shared machine use the team (ALB) mode, and stop the proxy when you are
done. On Windows the port is bound with SO_EXCLUSIVEADDRUSE, so no other account's socket can share it while the
proxy listens.

The URL defaults to `terraform output -raw dashboard_url` in terraform/. Needs boto3 and requests.
"""
import argparse
import hmac
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import boto3
import requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "terraform" / "helpers"))
import agentcore_wait  # noqa: E402 - the one copy of the empty-profile rule (stdlib only)

agentcore_wait.drop_empty_profile()  # before `terraform output` inherits an empty profile
ap = argparse.ArgumentParser()
ap.add_argument("--profile", default=os.environ.get("AWS_PROFILE") or None)
ap.add_argument("--region", help="the deployment's region (default: read from the Function URL host)")
ap.add_argument("--port", type=int, default=8090)
ap.add_argument("--url")
a = ap.parse_args()
URL = (a.url or subprocess.check_output(["terraform", "output", "-raw", "dashboard_url"], text=True,
                                         cwd=Path(__file__).resolve().parent.parent / "terraform",
                                         env={**os.environ, **({"AWS_PROFILE": a.profile} if a.profile else {}),
                                              **({"AWS_REGION": a.region} if a.region else {})})).rstrip("/")
# This proxy signs requests for a Lambda Function URL. Behind the ALB (dashboard_hostname set) the dashboard has no
# Function URL and the ALB redirects to the IdP: refuse rather than send signed headers to another host.
_host = urllib.parse.urlsplit(URL).hostname or ""
_m = re.fullmatch(r"[a-z0-9]+\.lambda-url\.([a-z0-9-]+)\.on\.aws", _host)
a.region = a.region or (_m.group(1) if _m else None)  # the URL names its region; the shell's AWS_REGION may not match
if not _m:
    sys.exit(f"dashboard/proxy.py: {URL} is not a Lambda Function URL; with the ALB, open the dashboard in a browser instead")
if _m.group(1) != a.region:
    sys.exit(f"dashboard/proxy.py: --region {a.region} does not match the Function URL's region ({_m.group(1)}); drop --region")
session = boto3.Session(profile_name=a.profile or None, region_name=a.region)
KEY = secrets.token_urlsafe(24)
COOKIE = f"ocgw_dash_{a.port}"  # cookies are not scoped by port: one name per proxy port, so two proxies do not clash


class _Exclusive:
    """Bind so no other socket can share the port. http.server sets SO_REUSEADDR, which on Windows lets another
    account's socket bind the same 127.0.0.1 port while this one listens (r196; client/ocgw.py has the same mixin)."""
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class _ProxyServer(_Exclusive, ThreadingHTTPServer):
    pass


def has_key(handler):
    for part in (handler.headers.get("Cookie") or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE and hmac.compare_digest(value.encode(), KEY.encode()):
            return True
    return False


class Handler(BaseHTTPRequestHandler):
    def _host_ok(self):
        # DNS rebinding: a page on another name that resolves to 127.0.0.1 would otherwise read the signed API.
        if self.headers.get("Host", "") not in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"):
            self.send_error(403, "wrong Host")
            return False
        return True

    def do_GET(self):
        if not self._host_ok():
            return
        # Only origin-form paths: "/x?y". Anything else ("@evil", "//evil", absolute URLs) could change the host the
        # signed request goes to.
        if not self.path.startswith("/") or self.path.startswith("//") or "@" in self.path.split("?", 1)[0]:
            self.send_error(400, "bad path")
            return
        parts = urllib.parse.urlsplit(self.path)
        given = urllib.parse.parse_qs(parts.query).get("k", [""])[0]
        if given and hmac.compare_digest(given.encode(), KEY.encode()):
            # The printed link: store the key as a cookie and drop it from the address bar.
            self.send_response(302)
            self.send_header("Set-Cookie", f"{COOKIE}={KEY}; HttpOnly; SameSite=Strict; Path=/")
            rest = urllib.parse.urlencode([(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
                                           if k != "k"], quote_via=urllib.parse.quote)
            self.send_header("Location", (parts.path or "/") + (f"?{rest}" if rest else ""))  # filters in the link stay
            self.send_header("content-length", "0")
            self.end_headers()
            return
        if not has_key(self):
            self.send_error(401, "open the link this proxy printed at start-up")
            return
        # Re-encode the query the SigV4 way (%20, not the "+" URLSearchParams uses for a space): botocore signs the
        # query as given, and the service reads "+" as a literal plus, so the signature would not match.
        # "/" too is encoded (%2F), exactly as SigV4 canonicalises a query value.
        query = urllib.parse.urlencode([(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
                                        if k != "k"],  # a stale key from an old link is not sent on
                                       quote_via=lambda s, *_a: urllib.parse.quote(s, safe=""))
        target = URL + parts.path + (f"?{query}" if query else "")
        if urllib.parse.urlsplit(target).netloc != urllib.parse.urlsplit(URL).netloc:
            self.send_error(400, "bad path")
            return
        try:
            creds = session.get_credentials().get_frozen_credentials()
        except Exception as e:  # noqa: BLE001 - no credentials, or an expired SSO session that cannot refresh
            self.send_error(502, f"no usable AWS credentials ({type(e).__name__}); run `aws sso login` (or set AWS_PROFILE) and reload")
            return
        # Signed with the request, so the Lambda knows this client cannot write and reports the role as viewer.
        req = AWSRequest(method="GET", url=target, headers={"x-ocgw-client": "read-only-proxy"})
        SigV4Auth(creds, "lambda", a.region).add_auth(req)
        try:
            # No redirects: a redirect would carry the signed headers (session token included) to another host.
            r = requests.get(target, headers=dict(req.headers), timeout=30, allow_redirects=False)
        except requests.RequestException as e:  # DNS, reset, timeout: answer 502 instead of dropping the connection
            self.send_error(502, f"dashboard Function URL unreachable: {type(e).__name__}")
            return
        if 300 <= r.status_code < 400:  # not followed (signed headers); a bare 3xx without Location would show a blank page
            self.send_error(502, f"the dashboard Function URL answered a redirect ({r.status_code}); this proxy does not follow it")
            return
        # requests has already undone any gzip, so the body is sent as is (content-encoding is not copied below).
        content = r.content
        self.send_response(r.status_code)
        for k in ("content-type", "cache-control", "content-security-policy", "x-content-type-options"):
            if k in r.headers:
                self.send_header(k, r.headers[k])
        self.send_header("content-length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _read_only(self, body=True):
        # The dashboard's writes need the ALB sign-in (CSRF header and a same-origin admin session); this proxy signs
        # GET requests only. Answer in JSON so the UI shows a clear error rather than stdlib's HTML 501.
        if not self._host_ok():
            return
        payload = json.dumps({"error": "this local proxy is read-only: make changes through the ALB dashboard"}).encode()
        self.close_connection = True
        self.send_response(405)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        if body:  # RFC 9110: a HEAD reply carries the headers its GET would and no body, or the next read desyncs
            self.wfile.write(payload)

    def do_HEAD(self):
        self._read_only(body=False)

    do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _read_only

    def log_message(self, fmt, *args):
        # The first request carries the per-run key as ?k=; log request lines without their query string.
        print("proxy:", re.sub(r"\?[^ \"]*", "", fmt % args), flush=True)


print(f"Dashboard: http://127.0.0.1:{a.port}/?k={KEY}  ->  {URL}", flush=True)
_ProxyServer(("127.0.0.1", a.port), Handler).serve_forever()
