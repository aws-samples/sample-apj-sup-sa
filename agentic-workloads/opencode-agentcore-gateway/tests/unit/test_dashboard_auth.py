# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Unit tests for the dashboard's ALB OIDC header check (lambda/dashboard.py).

Signs tokens the way the ALB does (ES256, header with kid/signer/exp, "="-padded base64url) with a throwaway P-256 key,
then checks that only a valid, unexpired token from the right ALB with an admin or viewer group gets the page.

  uv run --no-project --with-requirements tests/requirements.txt python -m pytest tests/unit/test_dashboard_auth.py -q
"""
import base64
import hashlib
import json
import os
import secrets
import sys
import time

import pytest
from pathlib import Path

os.environ.setdefault("TABLE", "test")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lambda"))
import dashboard as d  # noqa: E402

ALB = "arn:aws:elasticloadbalancing:us-east-1:111122223333:loadbalancer/app/ocgw-dashboard/abc"
PRIV = secrets.randbelow(d._N - 1) + 1
PUB = d._mul(PRIV, d._G)


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode()  # keeps "=" padding, like the ALB


def sign(msg: bytes, priv=PRIV) -> bytes:
    e = int.from_bytes(hashlib.sha256(msg).digest(), "big")
    while True:
        k = secrets.randbelow(d._N - 1) + 1
        r = d._mul(k, d._G)[0] % d._N
        s = pow(k, -1, d._N) * (e + r * priv) % d._N
        if r and s:
            return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def token(claims, signer=ALB, exp=None, priv=PRIV, alg="ES256", kid="k1"):
    head = {"alg": alg, "kid": kid, "signer": signer, "iss": "https://oauth.id.jumpcloud.com/", "client": "c",
            "exp": exp if exp is not None else int(time.time()) + 60}
    h, p = b64(json.dumps(head).encode()), b64(json.dumps(claims).encode())
    return f"{h}.{p}.{b64(sign(f'{h}.{p}'.encode(), priv))}"


def keys(kid):
    return PUB


ADMIN = {"sub": "u1", "email": "admin@example.com", "groups": ["ocgw-admin"]}


def test_valid_token():
    assert d.alb_claims(token(ADMIN), ALB, keys)["sub"] == "u1"


def test_wrong_signer_alb():
    assert d.alb_claims(token(ADMIN, signer=ALB + "x"), ALB, keys) is None


def test_expired():
    assert d.alb_claims(token(ADMIN, exp=int(time.time()) - 5), ALB, keys) is None


def test_forged_signature():
    other = secrets.randbelow(d._N - 1) + 1
    assert d.alb_claims(token(ADMIN, priv=other), ALB, keys) is None


def test_tampered_payload():
    h, _, s = token({"sub": "u2", "groups": ["ocgw-regular"]}).split(".")
    p = b64(json.dumps(ADMIN).encode())
    assert d.alb_claims(f"{h}.{p}.{s}", ALB, keys) is None


def test_alg_none_and_garbage():
    assert d.alb_claims(token(ADMIN, alg="none"), ALB, keys) is None
    assert d.alb_claims("", ALB, keys) is None
    assert d.alb_claims("a.b", ALB, keys) is None
    assert d.alb_claims("e30.e30.AAAA", ALB, keys) is None


def test_bad_kid_rejected_before_fetch():
    for kid in ("../x", "a/b", "http://evil", "", "x" * 200):
        try:
            d.alb_key(kid)
            raise AssertionError(f"kid {kid!r} accepted")
        except ValueError:
            pass


def test_groups_claim_shapes():
    assert d.groups_of({"groups": ["a", " b "]}) == {"a", "b"}
    assert d.groups_of({"groups": "a,b"}) == {"a", "b"}
    assert d.groups_of({}) == set()


def test_handler_gate(monkeypatch=None):
    d.ALB_ARN, d.ADMIN_GROUPS = ALB, {"ocgw-admin"}
    real = d.alb_key
    d.alb_key = keys
    try:
        call = lambda tok: d.lambda_handler({"path": "/", "headers": {"x-amzn-oidc-data": tok} if tok else {}}, None)
        assert call(None)["statusCode"] == 401
        assert call(token(ADMIN, signer="arn:other"))["statusCode"] == 401
        assert call(token({"sub": "u2", "groups": ["ocgw-regular"]}))["statusCode"] == 403
        assert call(token(ADMIN))["statusCode"] == 200
    finally:
        d.alb_key, d.ALB_ARN, d.ADMIN_GROUPS = real, "", set()


def test_logout_and_multivalue():
    d.ALB_ARN, d.ADMIN_GROUPS = ALB, {"ocgw-admin"}
    real = d.alb_key
    d.alb_key = keys
    try:
        tok = token(ADMIN)
        r = d.lambda_handler({"path": "/logout", "multiValueHeaders": {"x-amzn-oidc-data": [tok]}}, None)
        assert r["statusCode"] == 302 and r["multiValueHeaders"]["location"] == ["/signed-out"]
        assert len(r["multiValueHeaders"]["set-cookie"]) == 4 and all("Expires=Thu, 01 Jan 1970" in c for c in r["multiValueHeaders"]["set-cookie"])
        assert "headers" not in r
        r = d.lambda_handler({"path": "/", "multiValueHeaders": {"x-amzn-oidc-data": [tok]}}, None)
        assert r["statusCode"] == 200 and r["multiValueHeaders"]["content-type"][0].startswith("text/html")
        assert d.lambda_handler({"path": "/logout", "multiValueHeaders": {}}, None)["statusCode"] == 401
    finally:
        d.alb_key, d.ALB_ARN, d.ADMIN_GROUPS = real, "", set()


@pytest.mark.parametrize("tok", ["W10.e30.AA", "e30.W10.AA", "bnVsbA.e30.AA", "MQ.e30.AA", "a.b", ""])
def test_non_object_jose_parts_are_refused_not_crashed(tok):
    assert d.alb_claims(tok, ALB, key_for=lambda kid: None) is None


@pytest.mark.parametrize("role", ["viewer", "admin"])
def test_function_url_actor_is_the_iam_principal(monkeypatch, role):
    monkeypatch.setattr(d, "ALB_ARN", "")
    monkeypatch.setattr(d, "PRIVATE_ROLE", role)
    seen = {}
    import dashboard_api  # noqa: PLC0415
    monkeypatch.setattr(dashboard_api, "handle", lambda method, path, q, body, ident: (seen.update(ident), (200, {}))[1])
    arn = "arn:aws:sts::111122223333:assumed-role/Admin/alice"
    ev = {"rawPath": "/api/v2/me", "requestContext": {"http": {"method": "GET"}, "authorizer": {"iam": {"userArn": arn, "userId": "AROA:alice"}}},
          "headers": {}}
    d.route(ev)
    assert seen.get("sub") == arn and seen.get("role") == role


@pytest.mark.parametrize("body", ["!!!not-base64!!!", "//79"])  # bad base64; valid base64 of non-UTF-8 bytes
def test_bad_write_body_is_400(monkeypatch, body):
    monkeypatch.setattr(d, "ALB_ARN", "")
    ev = {"rawPath": "/api/v2/policy/org", "requestContext": {"http": {"method": "PUT"}, "authorizer": {"iam": {"userArn": "arn:x"}}},
          "headers": {"x-ocgw-csrf": "1", "origin": "https://h", "host": "h", "content-type": "application/json"},
          "body": body, "isBase64Encoded": True}
    assert d.route(ev)["statusCode"] == 400


@pytest.mark.parametrize("env,role", [(None, "viewer"), ("viewer", "viewer"), ("admin", "admin"), ("Admin", "viewer"), ("root", "viewer"), ("", "viewer")])
def test_private_role_defaults_to_viewer(monkeypatch, env, role):
    import importlib  # noqa: PLC0415
    if env is None:
        monkeypatch.delenv("PRIVATE_ROLE", raising=False)
    else:
        monkeypatch.setenv("PRIVATE_ROLE", env)
    assert importlib.reload(d).PRIVATE_ROLE == role
    monkeypatch.delenv("PRIVATE_ROLE", raising=False)
    importlib.reload(d)


@pytest.mark.parametrize("private,header,role", [("admin", "read-only-proxy", "viewer"), ("viewer", "read-only-proxy", "viewer"),
                                                 ("admin", None, "admin"), ("admin", "something-else", "admin"),
                                                 ("viewer", None, "viewer")])
def test_read_only_proxy_is_shown_as_viewer(monkeypatch, private, header, role):
    monkeypatch.setattr(d, "ALB_ARN", "")
    monkeypatch.setattr(d, "PRIVATE_ROLE", private)
    seen = {}
    import dashboard_api  # noqa: PLC0415
    monkeypatch.setattr(dashboard_api, "handle", lambda method, path, q, body, ident: (seen.update(ident), (200, {}))[1])
    for path in ("/api/v2/me", "/api/v2/me/", "/api/v2//me"):
        ev = {"rawPath": path, "requestContext": {"http": {"method": "GET"}, "authorizer": {"iam": {"userArn": "arn:x"}}},
              "headers": {"X-Ocgw-Client": header} if header else {}}
        d.route(ev)
        assert seen["role"] == role


@pytest.mark.parametrize("size,accept,want_status,gz,alb", [
    (100, "gzip", 200, False, True), (40_000, "gzip, br", 200, True, True), (40_000, "", 200, False, True),
    (2_000_000, "", 413, False, True), (2_000_000, "", 200, False, False), (7_000_000, "", 413, False, False),
])
def test_large_api_answers_are_gzipped_and_oversized_ones_are_a_clear_413(monkeypatch, size, accept, want_status, gz, alb):
    monkeypatch.setattr(d, "MAX_BODY", (1_000_000 if alb else 6_000_000) - 4096)  # the ALB's 1 MB, a Function URL's 6 MB
    import gzip as gz_mod
    body = {"rows": ["x" * 50] * (size // 54)}
    r = d.api_resp(200, body, {"accept-encoding": accept}, "rid")
    assert r["statusCode"] == want_status
    assert (r["headers"].get("content-encoding") == "gzip") is gz
    if gz:
        assert r["isBase64Encoded"] and json.loads(gz_mod.decompress(base64.b64decode(r["body"]))) == body


@pytest.mark.parametrize("bad", [{"x": {1}}, {"x": float("nan")}])
def test_an_answer_json_cannot_hold_is_the_json_500(bad):
    # r184 /code-review: resp() raises for a value JSON cannot hold, and api_resp runs outside api_v2's try.
    r = d.api_resp(200, bad, {}, "rid", write=True)
    assert r["statusCode"] == 500 and json.loads(r["body"])["request_id"] == "rid"
    # r185 /code-review: a 2xx write committed; the 500 must say so, so nobody retries a done change
    assert "saved" in json.loads(r["body"])["error"]
    # r186 /code-review: a read fails the same way on every reload, so it is the plain internal error
    assert json.loads(d.api_resp(200, bad, {}, "rid")["body"])["error"] == "internal error"
    assert json.loads(d.api_resp(400, bad, {}, "rid")["body"])["error"] == "internal error"


@pytest.mark.parametrize("alb,cap", [("arn:aws:elasticloadbalancing:us-east-1:1:loadbalancer/app/x/y", 1_000_000), ("", 6_000_000)])
def test_the_size_cap_follows_the_deployment_mode(monkeypatch, alb, cap):
    import importlib
    monkeypatch.setenv("ALB_ARN", alb)
    try:
        m = importlib.reload(d)
        assert m.MAX_BODY == cap - 4096
        r = m.api_resp(200, {"rows": ['"q"' * 100] * (cap // 300 + 10)}, {}, "rid")  # quotes grow when Lambda escapes them
        assert r["statusCode"] == 413
    finally:
        monkeypatch.delenv("ALB_ARN", raising=False)
        importlib.reload(d)


@pytest.mark.parametrize("accept,gz", [("gzip, deflate, br", True), ("", False), ("identity", False)])
def test_static_files_follow_accept_encoding(monkeypatch, tmp_path, accept, gz):
    import gzip as gz_mod
    (tmp_path / "index.html").write_text("<html>hi</html>")
    monkeypatch.setattr(d, "UI_DIR", str(tmp_path))
    monkeypatch.setattr(d, "_STATIC", {})
    r = d.static("/", accept)
    raw = base64.b64decode(r["body"])
    assert (r["headers"].get("content-encoding") == "gzip") is gz and r["headers"]["vary"] == "accept-encoding"
    assert (gz_mod.decompress(raw) if gz else raw) == b"<html>hi</html>"


def test_dashboard_groups_come_from_the_configured_claim(monkeypatch):
    monkeypatch.setattr(d, "GROUP_CLAIM", "memberOf")
    assert d.groups_of({"memberOf": ["ocgw-admin"]}) == {"ocgw-admin"}
    # r48: only the configured claim counts, as in the interceptor (no fallback to `groups`), so roles and model
    # access always come from the same claim.
    assert d.groups_of({"groups": "a, b"}) == set()
    assert d.groups_of({"memberOf": "a, b"}) == {"a", "b"}



def test_static_is_cached_and_labelled_by_the_file_it_resolves_to(tmp_path, monkeypatch):
    # r59: dot-segment spellings each added a cache entry, and /assets/../index.html got the year-long immutable header.
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>hi</html>")
    (tmp_path / "assets" / "a.js").write_text("x=1")
    monkeypatch.setattr(d, "UI_DIR", str(tmp_path))
    monkeypatch.setattr(d, "_STATIC", {})
    r = d.static("/assets/../index.html")
    assert r["headers"]["cache-control"] == "no-store"
    for p in ("/assets/a.js", "/assets/./a.js", "/assets/x/../a.js", "/assets//a.js"):
        assert d.static(p)["headers"]["cache-control"].startswith("public")
    assert set(d._STATIC) == {"index.html", "assets/a.js"}


def pem_of(point) -> str:
    """A P-256 SubjectPublicKeyInfo PEM for an uncompressed point, the shape the ALB key host serves."""
    from cryptography.hazmat.primitives import serialization  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric import ec  # noqa: PLC0415
    key = ec.EllipticCurvePublicNumbers(point[0], point[1], ec.SECP256R1()).public_key()
    return key.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()


@pytest.mark.parametrize("n", [0, 1, 63, 65, 128])
def test_a_signature_that_is_not_64_bytes_is_refused(n):
    assert d.es256_verify(PUB, b"message", b"\x01" * n) is False


@pytest.mark.parametrize("r, s", [(0, 1), (1, 0), (0, 0), (d._N, 1), (1, d._N), (d._N - 1, d._N)])
def test_r_or_s_outside_one_to_n_minus_one_is_refused(r, s):
    # The point at infinity and a signature reduced to zero are the classic ECDSA forgeries; both must fail the
    # bounds check before any curve arithmetic runs.
    sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    assert d.es256_verify(PUB, b"message", sig) is False


def test_a_public_key_that_is_not_on_the_curve_is_refused():
    assert d.es256_verify((1, 1), b"message", b"\x01" * 64) is False
    assert d.es256_verify((d._G[0], d._G[1] + 1), b"message", b"\x01" * 64) is False


def test_the_alb_key_pem_is_read_as_its_point():
    assert d.pem_point(pem_of(d._G)) == d._G
    assert d.pem_point(pem_of(PUB)) == PUB


def _spki_header():
    """The DER header every P-256 public key starts with (SEQUENCE, the id-ecPublicKey and prime256v1 OIDs, and the
    BIT STRING tag, length and unused-bits byte), taken from a real key so no key material is spelled out here."""
    der = base64.b64decode("".join(pem_of(PUB).splitlines()[1:-1]))
    return der[:-65]  # what is left is the 65-byte point: 0x04, then x and y


@pytest.mark.parametrize("point", [
    b"\x02" + b"\x11" * 32,  # compressed: too short
    b"\x03" + b"\x11" * 64,  # the right length, but not 0x04 (uncompressed)
])
def test_a_key_that_is_not_an_uncompressed_point_is_refused(point):
    b = base64.b64encode(_spki_header() + point).decode()
    pem = "-----BEGIN PUBLIC KEY-----\n" + b + "\n-----END PUBLIC KEY-----\n"
    with pytest.raises(ValueError, match="uncompressed"):
        d.pem_point(pem)


def test_a_fetched_alb_key_is_parsed_once_and_cached(monkeypatch):
    calls = []

    class Reply:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return pem_of(PUB).encode()

    def urlopen(url, timeout=None):
        calls.append(url)
        return Reply()

    monkeypatch.setattr(d.urllib.request, "urlopen", urlopen)
    monkeypatch.setitem(d.__dict__, "_KEYS", {})
    kid = "01234567-89ab-cdef-0123-456789abcdef"
    assert d.alb_key(kid) == PUB
    assert d.alb_key(kid) == PUB  # the second call is served from _KEYS
    assert calls == [f"https://public-keys.auth.elb.{d.REGION}.amazonaws.com/{kid}"]



def test_a_function_url_reply_sends_every_cookie(monkeypatch):
    # r186 /code-review: without multi-value headers the four logout cookies collapsed to the last one.
    monkeypatch.setattr(d, "route", lambda event: d.signed_out())
    r = d.lambda_handler({"version": "2.0", "headers": {}}, None)
    assert len(r["cookies"]) == 4 and r["headers"]["location"] == "/signed-out" and "set-cookie" not in r["headers"]


def test_alb_mode_answers_carry_hsts(monkeypatch, tmp_path):
    """r196 threat model T-043: without Strict-Transport-Security, a first visit typed without https:// starts in
    plain HTTP that an on-path attacker can answer. Every ALB answer carries it; Function URL answers do not need it."""
    (tmp_path / "index.html").write_text("<html>hi</html>")
    monkeypatch.setattr(d, "UI_DIR", str(tmp_path))
    monkeypatch.setattr(d, "_STATIC", {})
    monkeypatch.setattr(d, "alb_key", keys)
    monkeypatch.setattr(d, "ADMIN_GROUPS", {"ocgw-admin"})
    monkeypatch.setattr(d, "api_v2", lambda event, method, path, q, hdrs, ident: d.resp(200, {"ok": True}))
    other = token({"sub": "u2", "groups": ["ocgw-regular"]})
    cases = [("/api/v2/me", token(ADMIN), 200, "application/json"), ("/", token(ADMIN), 200, "text/html; charset=utf-8"),
             ("/assets/../index.html", token(ADMIN), 200, "text/html; charset=utf-8"),
             ("/", other, 403, "text/html; charset=utf-8"), ("/logout", token(ADMIN), 302, None), ("/", "", 401, "application/json")]
    for alb in (ALB, ""):
        monkeypatch.setattr(d, "ALB_ARN", alb)
        for path, tok, status, ctype in cases:
            event = {"path": path, "headers": {"x-amzn-oidc-data": tok} if alb else {}}
            r = d.lambda_handler(event, None)
            if alb:
                assert r["statusCode"] == status and r["headers"]["strict-transport-security"] == "max-age=31536000", (path, r)
                assert ctype is None or r["headers"]["content-type"] == ctype
                mv = d.lambda_handler({"path": path, "multiValueHeaders": {"x-amzn-oidc-data": [tok]}}, None)
                assert mv["multiValueHeaders"]["strict-transport-security"] == ["max-age=31536000"]
            else:
                assert "strict-transport-security" not in r.get("headers", {}), (path, r)


def test_the_alb_listener_sets_hsts_on_its_own_answers():
    """r197: the ALB's authenticate-oidc redirects and WAF blocks never reach the Lambda, so the listener sets HSTS."""
    import re
    from pathlib import Path
    tf = (Path(__file__).resolve().parents[2] / "terraform" / "dashboard_alb.tf").read_text()
    block = tf[tf.index('resource "aws_lb_listener" "dashboard"'):]
    block = block[:block.index("\n}\n")]
    assert re.search(r'routing_http_response_strict_transport_security_header_value\s*=\s*"max-age=31536000"', block)
