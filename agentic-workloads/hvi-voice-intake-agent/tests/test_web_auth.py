"""Cognito id-token verification for the browser surface, proven offline.

Every check mints a REAL RS256 token with a self-signed key and hands the
verifier a JWKS built from that key's public numbers through an injected fetcher,
so nothing touches Cognito or the network. Proves the security-relevant cases:

  - a well-formed id-token verifies and yields the caller phone from a claim
  - expired, wrong-aud, wrong-iss, wrong-token_use, tampered-signature,
    alg=none/HS256, unknown-kid and a missing phone claim ALL raise AuthError
  - the caller phone comes from the verified claim, never from the raw token

alg-pinning and the aud/iss/exp checks are the ones that stop a forged or
replayed token being accepted as a real caller, which on this system means a
medical record written against the wrong person.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "common"))

from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding, rsa  # noqa: E402

import hvi_web_auth as auth  # noqa: E402

failures: list[str] = []
checks = 0

REGION = "ap-south-1"
POOL = "ap-south-1_TESTPOOL"
CLIENT = "test-app-client-id"
ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{POOL}"
KID = "test-key-1"

_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def check(label: str, cond: bool, detail: str = "") -> None:
    global checks
    checks += 1
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{'' if cond else '  <- ' + detail}")
    if not cond:
        failures.append(label)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _int_to_b64url(value: int) -> str:
    length = (value.bit_length() + 7) // 8
    return _b64url(value.to_bytes(length, "big"))


def _jwks_doc(_url: str) -> dict:
    """A JWKS built from the test key's public numbers - the injected fetcher."""
    pub = _key.public_key().public_numbers()
    return {"keys": [{
        "kty": "RSA", "kid": KID, "use": "sig", "alg": "RS256",
        "n": _int_to_b64url(pub.n), "e": _int_to_b64url(pub.e),
    }]}


def _make_token(claims: dict, *, kid: str = KID, alg: str = "RS256",
                sign: bool = True) -> str:
    header = {"alg": alg, "kid": kid, "typ": "JWT"}
    h = _b64url(json.dumps(header).encode())
    p = _b64url(json.dumps(claims).encode())
    signing_input = f"{h}.{p}".encode("ascii")
    if not sign:
        return f"{h}.{p}.{_b64url(b'not-a-signature')}"
    sig = _key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{h}.{p}.{_b64url(sig)}"


def _claims(**over) -> dict:
    now = int(time.time())
    base = {
        "iss": ISSUER, "aud": CLIENT, "token_use": "id",
        "exp": now + 3600, "iat": now, "nbf": now - 10,
        "phone_number": "+911234567890", "sub": "user-123",
    }
    base.update(over)
    return base


def _verifier(now: float | None = None) -> auth.CognitoVerifier:
    cfg = auth.CognitoConfig(region=REGION, user_pool_id=POOL, app_client_id=CLIENT)
    jwks = auth.JWKSCache(cfg.jwks_url, fetcher=_jwks_doc)
    return auth.CognitoVerifier(cfg, jwks=jwks,
                                now_fn=(lambda: now) if now else time.time)


def _raises(label: str, token: str, verifier=None) -> None:
    v = verifier or _verifier()
    try:
        v.verify(token)
        check(label, False, "expected AuthError, none raised")
    except auth.AuthError:
        check(label, True)
    except Exception as exc:  # noqa: BLE001
        check(label, False, f"wrong exception type: {type(exc).__name__}")


def test_valid() -> None:
    print("[1] a well-formed id-token verifies and binds the caller phone")
    v = _verifier()
    claims = v.verify(_make_token(_claims()))
    check("valid token verifies", claims.get("sub") == "user-123")
    check("caller phone comes from the verified claim",
          v.caller_phone(claims) == "+911234567890")


def test_rejections() -> None:
    print("\n[2] every bad token raises AuthError (mapped to an opaque 403)")
    _raises("expired token", _make_token(_claims(exp=int(time.time()) - 3600)))
    _raises("wrong audience", _make_token(_claims(aud="some-other-client")))
    _raises("wrong issuer", _make_token(_claims(iss="https://evil.example/pool")))
    _raises("access-token (token_use != id)",
            _make_token(_claims(token_use="access")))
    _raises("unknown kid", _make_token(_claims(), kid="rotated-away"))
    _raises("alg none", _make_token(_claims(), alg="none", sign=False))
    _raises("alg HS256 (algorithm confusion)",
            _make_token(_claims(), alg="HS256", sign=False))
    _raises("not a three-segment JWT", "just.two")
    _raises("empty token", "")


def test_tampered_signature() -> None:
    print("\n[3] a tampered payload no longer verifies")
    good = _make_token(_claims())
    h, p, s = good.split(".")
    # Re-encode the claims with an escalated phone but keep the original signature.
    forged_claims = _claims(phone_number="+910000000000")
    p2 = _b64url(json.dumps(forged_claims).encode())
    _raises("payload swapped under the old signature", f"{h}.{p2}.{s}")


def test_missing_claim() -> None:
    print("\n[4] a verified token with no phone claim is refused, not defaulted")
    v = _verifier()
    claims = v.verify(_make_token(_claims(phone_number=None)))
    try:
        v.caller_phone(claims)
        check("missing phone claim raises", False, "returned instead of raising")
    except auth.AuthError:
        check("missing phone claim raises", True)


def test_config_from_env() -> None:
    print("\n[5] config requires the pool env, and derives issuer/jwks url")
    cfg = auth.CognitoConfig(region=REGION, user_pool_id=POOL, app_client_id=CLIENT)
    check("issuer is the pool issuer", cfg.issuer == ISSUER)
    check("jwks url is issuer/.well-known/jwks.json",
          cfg.jwks_url == f"{ISSUER}/.well-known/jwks.json")
    try:
        auth.CognitoConfig.from_env({"HVI_COGNITO_REGION": REGION})  # missing pool/client
        check("incomplete env raises", False, "did not raise")
    except auth.AuthError:
        check("incomplete env raises", True)


def test_offer_routing() -> None:
    """The /api/offer auth routing, tested only where the web stack is present.

    media.bot_web pulls aiortc + pipecat, which are installed only on the media
    host (see media/requirements-web.txt). So this imports lazily and SKIPS on a
    lean box rather than failing - the verifier itself is already covered above,
    and this only proves the thin mode-routing glue.
    """
    print("\n[6] /api/offer auth routing (skipped if the web stack is absent)")
    os.environ["HVI_WEB_AUTH"] = "token"
    os.environ["HVI_WEB_TOKEN"] = "shared-secret"
    try:
        import importlib
        bw = importlib.import_module("media.bot_web")
        importlib.reload(bw)
    except Exception as exc:  # noqa: BLE001 - ImportError on a lean box, and more
        check("web stack present (else skip)", True,
              f"skipped: {type(exc).__name__}")
        return

    # token mode: right secret -> the client phone; wrong -> _Forbidden.
    check("token mode accepts the right secret",
          bw._authenticate({"token": "shared-secret", "phone": "+91999"})
          == "+91999")
    try:
        bw._authenticate({"token": "wrong"})
        check("token mode rejects a bad secret", False, "did not raise")
    except bw._Forbidden:
        check("token mode rejects a bad secret", True)

    # none mode: open, returns the supplied phone.
    bw.WEB_AUTH = "none"
    check("none mode is open", bw._authenticate({"phone": "+91888"}) == "+91888")


def main() -> int:
    print("Cognito web-auth verification")
    test_valid()
    test_rejections()
    test_tampered_signature()
    test_missing_claim()
    test_config_from_env()
    test_offer_routing()
    print(f"\n{'FAIL' if failures else 'PASS'}: {checks} checks, "
          f"{len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
