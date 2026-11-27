# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Usage dashboard for the AgentCore gateway: tokens, requests and $ per person, per IdP group, per model and per
tag. Viewers read; admins also change limits, boosts and group and organization policies (dashboard_api.py), each
change with an audit row. The gateway's own rate limits are set in Terraform and shown read-only.

Served either from a Lambda Function URL with AuthType AWS_IAM (no anonymous access; people open it through
dashboard/proxy.py, which listens on 127.0.0.1 and signs each request with their AWS credentials), or, with ALB_ARN set,
behind an ALB that signs people in with the IdP (terraform/dashboard_alb.tf). Then every request must carry an
x-amzn-oidc-data header signed by that ALB (ES256, key from the ALB public-key endpoint), not expired, whose groups
claim contains one of ADMIN_GROUPS or VIEWER_GROUPS; anything else gets 401 or 403. The ALB only authenticates, so
this is the authorization check.

  GET /, /assets/*           the built UI (dashboard/ui-src)
  /api/v2/*                  its API (dashboard_api.py, contract in dashboard/ui-src/API.md)

Rows come from the rollups usage_sync writes from Mantle metrics (USER#<u>, GROUP#<g>, MODEL#<m>, TAG#<dim>=<v> with sk
MONTH#... or DAY#...), plus request counts and web search charges the interceptor adds.
"""
import base64
import gzip
import hashlib
import mimetypes
import json
import os
import re
import time
import urllib.parse
import urllib.request

import boto3

import policy

TABLE = os.environ["TABLE"]
ALB_ARN = os.environ.get("ALB_ARN", "")
# Function URL mode (no ALB): the role of every IAM caller, decided here so the proxy and any other client agree.
PRIVATE_ROLE = "admin" if os.environ.get("PRIVATE_ROLE") == "admin" else "viewer"
ADMIN_GROUPS = {g.strip() for g in os.environ.get("ADMIN_GROUPS", "").split(",") if g.strip()}
VIEWER_GROUPS = {g.strip() for g in os.environ.get("VIEWER_GROUPS", "").split(",") if g.strip()}
UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard_ui")  # built Cloudscape app (source: dashboard/ui-src)
_STATIC: dict = {}
REGION = os.environ.get("AWS_REGION", "us-east-1")
ddb = boto3.client("dynamodb")

# ---- ALB OIDC header check: ES256 (ECDSA P-256 + SHA-256) in pure Python, since the Lambda runtime has no crypto
# library. Verification only, on public data, so constant-time arithmetic is not needed.
_P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
_B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
_G = (0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296,
      0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5)
_KEYS: dict = {}


def _on_curve(pt):
    x, y = pt
    return 0 <= x < _P and 0 <= y < _P and (y * y - (x * x * x - 3 * x + _B)) % _P == 0


def _add(p1, p2):
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    (x1, y1), (x2, y2) = p1, p2
    if x1 == x2 and (y1 + y2) % _P == 0:
        return None
    if p1 == p2:
        lam = (3 * x1 * x1 - 3) * pow(2 * y1, -1, _P) % _P
    else:
        lam = (y2 - y1) * pow(x2 - x1, -1, _P) % _P
    x3 = (lam * lam - x1 - x2) % _P
    return x3, (lam * (x1 - x3) - y1) % _P


def _mul(k, pt):
    out = None
    while k:
        if k & 1:
            out = _add(out, pt)
        pt = _add(pt, pt)
        k >>= 1
    return out


def es256_verify(pub, msg: bytes, sig: bytes) -> bool:
    if len(sig) != 64 or not _on_curve(pub):
        return False
    r, s = int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")
    if not (0 < r < _N and 0 < s < _N):
        return False
    e = int.from_bytes(hashlib.sha256(msg).digest(), "big")
    w = pow(s, -1, _N)
    pt = _add(_mul(e * w % _N, _G), _mul(r * w % _N, pub))
    return pt is not None and pt[0] % _N == r


def _b64(seg: str) -> bytes:
    # The ALB pads its base64url segments with "=", unlike standard JWTs; accept both.
    seg = seg.rstrip("=")
    return base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))


def pem_point(pem: str):
    der = base64.b64decode("".join(l for l in pem.strip().splitlines() if not l.startswith("-----")))
    if len(der) < 65 or der[-65] != 4:
        raise ValueError("not an uncompressed P-256 public key")
    return int.from_bytes(der[-64:-32], "big"), int.from_bytes(der[-32:], "big")


def alb_key(kid: str):
    # kid only selects a file on the fixed regional ALB key host; refuse anything that is not a plain id.
    if not re.fullmatch(r"[A-Za-z0-9-]{1,128}", kid):
        raise ValueError("bad kid")
    if kid not in _KEYS:
        url = f"https://public-keys.auth.elb.{REGION}.amazonaws.com/{kid}"
        with urllib.request.urlopen(url, timeout=5) as r:  # nosec B310 - fixed https host, kid checked above  # nosemgrep: dynamic-urllib-use-detected -- URL is an https endpoint from config/Terraform (scheme checked where user-supplied)
            _KEYS[kid] = pem_point(r.read().decode())
    return _KEYS[kid]


def alb_claims(token: str, alb_arn: str, key_for=None, now=None):
    """Claims of a valid ALB x-amzn-oidc-data token, or None."""
    try:
        h64, p64, s64 = token.split(".")
        head = json.loads(_b64(h64))
        if not isinstance(head, dict):
            return None
        if head.get("alg") != "ES256" or head.get("signer") != alb_arn:
            return None
        if not es256_verify((key_for or alb_key)(head.get("kid", "")), f"{h64}.{p64}".encode(), _b64(s64)):
            return None
        claims = json.loads(_b64(p64))
        if not isinstance(claims, dict):
            return None
        exp = head.get("exp", claims.get("exp"))
        if exp is None or float(exp) < (now if now is not None else time.time()):
            return None
        return claims
    except (ValueError, KeyError, TypeError, AttributeError, OSError, json.JSONDecodeError):
        return None


GROUP_CLAIM = os.environ.get("GROUP_CLAIM") or "groups"


def groups_of(claims) -> set:
    """The signed-in user's groups from the ALB's claims, parsed exactly as the gateway's interceptor does
    (policy.groups_of, the configured group_claim only)."""
    return set(policy.groups_of(claims, GROUP_CLAIM))


def resp(status, body, ctype="application/json"):
    return {"statusCode": status, "headers": {"content-type": ctype, "cache-control": "no-store",
                                              "x-content-type-options": "nosniff",
                                              "content-security-policy": "default-src 'none'; frame-ancestors 'none'"},
            "body": body if isinstance(body, str) else json.dumps(body, allow_nan=False, default=policy.shown)}


LOGOUT_COOKIES = [f"AWSELBAuthSessionCookie-{i}" for i in range(4)]  # the ALB shards its session cookie up to 4 ways


HSTS = "max-age=31536000"  # one year; the ALB listens on 443 only, so a host typed without https:// is never answered


def lambda_handler(event, context):
    out = route(event)
    if ALB_ARN:  # every answer behind the ALB (API, static files, 401/403 pages, the /logout 302) tells the browser to
        # use https:// from now on; a Function URL is always reached by its https:// URL (r196, threat model T-043)
        out["headers"] = {**out.get("headers", {}), "strict-transport-security": HSTS}
    if "multiValueHeaders" in event:  # ALB target group with multi-value headers on
        mv = {k: [v] for k, v in out.pop("headers", {}).items()}
        mv.update(out.pop("multiValueHeaders", {}))
        out["multiValueHeaders"] = mv
    elif "multiValueHeaders" in out:
        mv = out.pop("multiValueHeaders")
        if event.get("version") == "2.0" and "set-cookie" in mv:  # a Function URL sends each cookie of the list
            out["cookies"] = list(mv.pop("set-cookie"))
        # An ALB target group without multi-value headers can carry one value a name (the target group Terraform
        # creates turns them on, so this is only a hand-made one).
        out.setdefault("headers", {}).update({k: v[-1] for k, v in mv.items()})
    return out


def signed_out():
    """Expire the ALB session cookies and land on /signed-out, which the ALB serves without sign-in."""
    return {"statusCode": 302, "headers": {"location": "/signed-out", "cache-control": "no-store"},
            "multiValueHeaders": {"set-cookie": [f"{c}=; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT; Secure; HttpOnly"
                                                 for c in LOGOUT_COOKIES]}, "body": ""}


def route(event):
    if "multiValueHeaders" in event:
        event = {**event, "headers": {k: v[-1] for k, v in (event.get("multiValueHeaders") or {}).items() if v},
                 "queryStringParameters": {k: v[-1] for k, v in (event.get("multiValueQueryStringParameters") or {}).items() if v}}
    hdrs = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    # Function URL: AWS_IAM is the gate and PRIVATE_ROLE (viewer unless Terraform says admin) the role. The signed
    # caller's IAM principal names the actor in audit rows.
    iam = ((event.get("requestContext") or {}).get("authorizer") or {}).get("iam") or {}
    # A client that says it cannot write (dashboard/proxy.py signs GETs only, and signs this header with them) is
    # shown as viewer whatever PRIVATE_ROLE is, so its UI never offers edits that would fail. It can only lower a role.
    role = "viewer" if hdrs.get("x-ocgw-client") == "read-only-proxy" else PRIVATE_ROLE
    ident = {"email": None, "sub": iam.get("userArn") or iam.get("userId") or None, "groups": [], "role": role}
    # ALB events carry "path"; Function URL events carry "rawPath".
    path = event.get("rawPath") or event.get("path") or "/"
    if ALB_ARN:
        claims = alb_claims(hdrs.get("x-amzn-oidc-data", ""), ALB_ARN)
        if claims is None:
            print(json.dumps({"auth": "denied", "reason": "no valid ALB OIDC header"}))
            return resp(401, {"error": "sign-in required"})
        groups = groups_of(claims)
        role = "admin" if groups & ADMIN_GROUPS else "viewer" if groups & VIEWER_GROUPS else None
        if not role:
            print(json.dumps({"auth": "denied", "reason": "not in an admin or viewer group", "sub": claims.get("sub")}))
            if path == "/logout":  # signing out is how this user picks another account, so it comes before the 403
                return signed_out()
            return resp(403, '<p>Your account is in neither an admin nor a viewer group for this dashboard.</p>'
                             '<p><a href="/logout">Sign out</a></p>', "text/html; charset=utf-8")
        ident = {"email": claims.get("email"), "sub": claims.get("sub"), "groups": sorted(groups), "role": role}
        print(json.dumps({"auth": "ok", "sub": claims.get("sub"), "role": role}))  # no email in logs: the subject is enough
    if ALB_ARN and path == "/logout":
        return signed_out()
    q = event.get("queryStringParameters") or {}
    if "elb" in (event.get("requestContext") or {}):  # the ALB passes query values URL-encoded
        q = {urllib.parse.unquote_plus(k): urllib.parse.unquote_plus(v) for k, v in q.items()}
    method = (event.get("httpMethod") or (event.get("requestContext") or {}).get("http", {}).get("method") or "GET").upper()
    if path.startswith("/api/v2/"):
        return api_v2(event, method, path, q, hdrs, ident)
    if method not in ("GET", "HEAD"):  # the API answers its own methods above; nothing else here is writable
        return resp(405, {"error": "method not allowed"})
    if os.path.isfile(os.path.join(UI_DIR, "index.html")):
        return static(path, hdrs.get("accept-encoding") or "")  # HEAD: same status and headers (the body is dropped)
    if path == "/":
        return resp(200, NO_UI, "text/html; charset=utf-8")
    return resp(404, {"error": "not found"})


def api_v2(event, method, path, q, hdrs, ident):
    import dashboard_api  # noqa: PLC0415 - loaded only for API calls
    rid = (event.get("requestContext") or {}).get("requestId") or hdrs.get("x-amzn-trace-id", "")[-24:] or os.urandom(6).hex()
    body = {}
    if method == "HEAD":  # not a write, and no API route answers it: 405, not a CSRF refusal logged as a write
        return resp(405, {"error": "method not allowed", "request_id": rid})
    if method != "GET":
        # Cross-site request forgery: a form or a no-CORS fetch from another site cannot set a custom header, and the
        # browser's Origin must be this host.
        host = hdrs.get("host", "")
        if hdrs.get("x-ocgw-csrf") != "1" or (hdrs.get("origin") or "") != f"https://{host}":
            return resp(403, {"error": "missing CSRF header or wrong origin", "request_id": rid})
        if not (hdrs.get("content-type") or "").startswith("application/json"):
            return resp(415, {"error": "send application/json", "request_id": rid})
        raw = event.get("body") or ""
        try:
            if event.get("isBase64Encoded"):
                raw = base64.b64decode(raw, validate=True).decode()  # binascii.Error and UnicodeDecodeError are ValueErrors
            body = json.loads(raw) if raw else {}
        except ValueError:
            return resp(400, {"error": "body is not JSON", "request_id": rid})
        if not isinstance(body, dict):
            return resp(400, {"error": "body must be a JSON object", "request_id": rid})
    try:
        status, out = dashboard_api.handle(method, path, q, body, ident)
    except dashboard_api.ApiError as e:
        status, out = e.status, e.body
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"error": repr(e)[:500], "path": path, "request_id": rid}))
        status, out = 500, {"error": "internal error"}
    if status >= 400:
        out = {**out, "request_id": rid}
    if method != "GET":
        print(json.dumps({"write": path, "method": method, "status": status, "actor_sub": ident.get("sub"), "request_id": rid}))
    return api_resp(status, out, hdrs, rid, write=method != "GET")


# The ALB refuses a Lambda response over 1 MB (502), and a Function URL allows 6 MB. Large answers (a long usage range
# by user, a full audit page) are gzipped for a client that accepts it, as the static files are; one still too big is
# a clear 413 asking for a narrower query instead of an opaque 502.
GZIP_FROM = 16 * 1024
# The ALB's limit is 1 MB; a Function URL allows 6 MB. Headroom for the status line and headers.
MAX_BODY = (1_000_000 if ALB_ARN else 6_000_000) - 4096


def api_resp(status, out, hdrs, rid, write=False):
    try:
        r = resp(status, out)
    except (TypeError, ValueError):  # a value JSON cannot hold (a bug, or NaN): a JSON 500, never an escaped exception
        print(json.dumps({"error": "response not serializable", "status": status, "request_id": rid}))
        # A 2xx write here already committed: say so, so nobody retries a done change. A read would fail the same
        # way on every reload, so it is the plain internal error.
        msg = ("the change was saved, but its answer could not be sent; reload to see the result" if write and status < 300
               else "internal error")
        r = resp(500, {"error": msg, "request_id": rid})
    raw = r["body"].encode()
    if len(raw) >= GZIP_FROM and "gzip" in (hdrs.get("accept-encoding") or "").lower():
        r["body"], r["isBase64Encoded"] = base64.b64encode(gzip.compress(raw, 6)).decode(), True
        r["headers"] = {**r["headers"], "content-encoding": "gzip", "vary": "accept-encoding"}
    # Measured as Lambda sends it: the envelope is JSON too, so a plain body's quotes are escaped (base64 has none to
    # escape); resp() wrote the body with json.dumps' default ensure_ascii, so it is ASCII and nothing grows to \uXXXX
    # here. The headers fit in MAX_BODY's headroom.
    size = len(r["body"]) + 2 if r.get("isBase64Encoded") else len(json.dumps(r["body"]))
    if size > MAX_BODY:
        print(json.dumps({"error": "response too large", "bytes": len(r["body"]), "request_id": rid}))
        return resp(413, {"error": "this answer is too large to send; narrow the date range or add a filter", "request_id": rid})
    return r


def static(path, accept="gzip"):
    """The built app: hashed files under /assets/ (cached a year), index.html for every other path (client routing).
    Sent gzipped (every browser accepts it); to a client that does not accept gzip, the plain file if it fits the
    response limit (MAX_BODY), else the gzipped one anyway, which is all the ALB's 1 MB allows for the largest bundle."""
    rel = path.lstrip("/")
    root = os.path.realpath(UI_DIR)
    full = os.path.realpath(os.path.join(UI_DIR, rel)) if rel else ""
    # Keyed and labelled by the file it resolves to, not the path as asked: /assets/../index.html is index.html (no
    # year-long cache), and dot-segment spellings of one asset share one cache entry instead of adding copies.
    rel = os.path.relpath(full, root).replace(os.sep, "/") if full.startswith(root + os.sep) else ""
    if not (rel.startswith("assets/") and os.path.isfile(full)):
        full, rel = os.path.join(UI_DIR, "index.html"), "index.html"
    if rel not in _STATIC:
        with open(full, "rb") as f:
            raw = f.read()
        plain = base64.b64encode(raw).decode()
        _STATIC[rel] = (base64.b64encode(gzip.compress(raw, 9)).decode(), plain if len(plain) <= MAX_BODY else None)
    zipped, plain = _STATIC[rel]
    use_plain = plain is not None and "gzip" not in accept.lower()
    ctype = mimetypes.guess_type(rel)[0] or "application/octet-stream"
    if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
        ctype += "; charset=utf-8"
    return {"statusCode": 200, "isBase64Encoded": True, "body": plain if use_plain else zipped,
            "headers": {"content-type": ctype, **({} if use_plain else {"content-encoding": "gzip"}), "vary": "accept-encoding",
                        "x-content-type-options": "nosniff",
                        "cache-control": "public, max-age=31536000, immutable" if rel.startswith("assets/") else "no-store",
                        "content-security-policy": "default-src 'self'; img-src 'self' data:; font-src 'self' data:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'",
                        "referrer-policy": "same-origin", "x-frame-options": "DENY"}}


# Served at / only when the UI has not been built (make -C dashboard/ui-src build).
NO_UI = ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>opencode gateway dashboard</title></head>"
         "<body><p>The dashboard UI is not built. Run <code>make -C dashboard/ui-src build</code>, then <code>terraform apply</code>.</p></body></html>")
