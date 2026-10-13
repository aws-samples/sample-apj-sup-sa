"""JWT-gated Pipecat Cloud start proxy.

The browser must NOT hold the Pipecat Cloud API key (anyone could read it from
the SPA and burn credits). This Lambda sits behind a public Function URL and:

  1. Validates the caller's Cognito access token (signature + expiry + issuer +
     client_id) against the User Pool's JWKS.
  2. Only then calls Pipecat Cloud's start API server-side with the secret key.
  3. Returns the Daily room URL + token to the browser, which joins the room.

Env (set by CloudFormation):
  PCC_AGENT_NAME          - deployed Pipecat Cloud agent name
  PCC_API_KEY_SECRET_ARN  - Secrets Manager secret holding the PCC key (filled
                            post-deploy by scripts/deploy_voice_pcc.sh). Read
                            per-invocation so it works the moment it's filled.
  COGNITO_USER_POOL_ID
  COGNITO_REGION
  COGNITO_APP_CLIENT_ID  - expected token client_id (audience-equivalent)
  ALLOWED_ORIGIN      - CORS allow-origin (the Amplify site), or "*"
"""

import json
import os
import time
import urllib.request

import boto3
# jose is vendored via a Lambda layer / bundled deps (python-jose).
from jose import jwt
from jose.utils import base64url_decode  # noqa: F401  (ensures jose is present)


def _https_urlopen(req_or_url, timeout):
    """urlopen that REFUSES any scheme other than https.

    Closes the file:// / custom-scheme risk that Bandit B310 flags: an attacker
    who could influence a URL must not be able to make us read a local file or a
    non-TLS endpoint. Every URL here is a hardcoded https Cognito / Pipecat Cloud
    endpoint, but we enforce it explicitly so the guarantee survives refactors.
    """
    full_url = req_or_url.full_url if isinstance(req_or_url, urllib.request.Request) else req_or_url
    if not full_url.lower().startswith("https://"):
        raise ValueError(f"refusing non-https URL: {full_url[:60]}")
    return urllib.request.urlopen(req_or_url, timeout=timeout)  # nosec B310 - scheme pinned to https above

_JWKS_CACHE = {"keys": None, "fetched_at": 0}
_JWKS_TTL = 3600

# Cache the PCC key briefly so we don't hit Secrets Manager on every /start, but
# short enough that filling the placeholder takes effect within ~1 min.
_KEY_CACHE = {"value": None, "fetched_at": 0}
_KEY_TTL = 60
_sm = boto3.client("secretsmanager")


def _pcc_key():
    now = time.time()
    if _KEY_CACHE["value"] and now - _KEY_CACHE["fetched_at"] < _KEY_TTL:
        return _KEY_CACHE["value"]
    val = _sm.get_secret_value(SecretId=os.environ["PCC_API_KEY_SECRET_ARN"])["SecretString"].strip()
    _KEY_CACHE.update(value=val, fetched_at=now)
    return val


def _jwks(pool_id: str, region: str):
    now = time.time()
    if _JWKS_CACHE["keys"] and now - _JWKS_CACHE["fetched_at"] < _JWKS_TTL:
        return _JWKS_CACHE["keys"]
    url = f"https://cognito-idp.{region}.amazonaws.com/{pool_id}/.well-known/jwks.json"
    with _https_urlopen(url, timeout=5) as r:
        keys = json.loads(r.read())["keys"]
    _JWKS_CACHE.update(keys=keys, fetched_at=now)
    return keys


def _cors(origin: str):
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Headers": "authorization,content-type",
        "Access-Control-Allow-Methods": "POST,OPTIONS",
    }


def _resp(status: int, body: dict, origin: str):
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json", **_cors(origin)},
        "body": json.dumps(body),
    }


def _verify_token(token: str) -> dict:
    """Verify a Cognito access token; raises on any failure."""
    pool_id = os.environ["COGNITO_USER_POOL_ID"]
    region = os.environ["COGNITO_REGION"]
    client_id = os.environ.get("COGNITO_APP_CLIENT_ID")

    headers = jwt.get_unverified_header(token)
    kid = headers["kid"]
    key = next((k for k in _jwks(pool_id, region) if k["kid"] == kid), None)
    if key is None:
        raise ValueError("signing key not found in JWKS")

    issuer = f"https://cognito-idp.{region}.amazonaws.com/{pool_id}"
    # Cognito ACCESS tokens have no 'aud'; the audience is carried as 'client_id'.
    claims = jwt.decode(token, key, algorithms=["RS256"], issuer=issuer,
                        options={"verify_aud": False})
    if claims.get("token_use") != "access":
        raise ValueError("not an access token")
    if client_id and claims.get("client_id") != client_id:
        raise ValueError("client_id mismatch")
    return claims


def handler(event, _context):
    origin = os.environ.get("ALLOWED_ORIGIN", "*")
    method = (event.get("requestContext", {})
              .get("http", {}).get("method", "POST")).upper()
    if method == "OPTIONS":
        return _resp(200, {"ok": True}, origin)

    # 1. Extract + verify the Cognito JWT.
    auth = (event.get("headers", {}) or {}).get("authorization") \
        or (event.get("headers", {}) or {}).get("Authorization") or ""
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not token:
        return _resp(401, {"error": "missing bearer token"}, origin)
    try:
        _verify_token(token)
    except Exception as e:  # noqa: BLE001
        return _resp(401, {"error": f"invalid token: {e}"}, origin)

    # 1b. Read the shared runtimeSessionId from the browser's request body (if any),
    # so voice + text chat share one AgentCore Memory thread. The browser sends it
    # in the POST body; we forward it into the PCC session body alongside the token.
    runtime_session_id = ""
    try:
        raw = event.get("body") or ""
        if event.get("isBase64Encoded"):
            import base64 as _b64
            raw = _b64.b64decode(raw).decode("utf-8", "ignore")
        if raw:
            req_body = json.loads(raw)
            if isinstance(req_body, dict):
                runtime_session_id = req_body.get("runtimeSessionId") or ""
    except Exception:  # noqa: BLE001
        runtime_session_id = ""

    # 2. Call Pipecat Cloud start API server-side with the secret key. Forward the
    # (now-validated) user access token in the session `body` so the bot uses it
    # as the AgentCore gateway_token — i.e. RBAC/RLS apply to the REAL speaking
    # user, not a shared demo identity.
    agent = os.environ["PCC_AGENT_NAME"]
    try:
        pcc_key = _pcc_key()
    except Exception as e:  # noqa: BLE001
        return _resp(503, {"error": f"PCC key not available yet: {e}"}, origin)
    if not pcc_key or pcc_key == "REPLACE_ME":
        return _resp(503, {"error": "PCC key placeholder not filled — run deploy_voice_pcc.sh"}, origin)
    url = f"https://api.pipecat.daily.co/v1/public/{agent}/start"
    session_body = {"gateway_token": token}
    if runtime_session_id:
        session_body["runtimeSessionId"] = runtime_session_id
    payload = json.dumps({
        "createDailyRoom": True,
        "body": session_body,
    }).encode()
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Authorization": f"Bearer {pcc_key}",
                 "Content-Type": "application/json"},
    )
    try:
        with _https_urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:  # noqa: BLE001
        return _resp(502, {"error": "pcc start failed",
                           "detail": e.read().decode("utf-8", "ignore")}, origin)
    except Exception as e:  # noqa: BLE001
        return _resp(502, {"error": f"pcc start error: {e}"}, origin)

    # 3. Return the Daily room + token to the browser.
    return _resp(200, {
        "dailyRoom": data.get("dailyRoom"),
        "dailyToken": data.get("dailyToken"),
        "sessionId": data.get("sessionId"),
    }, origin)
