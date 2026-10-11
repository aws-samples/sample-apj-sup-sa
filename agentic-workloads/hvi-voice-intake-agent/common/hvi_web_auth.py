"""Cognito id-token verification for the browser (WebRTC) surface.

WHY THIS MODULE EXISTS. The demo surface gates /api/offer on a shared secret and
trusts a client-supplied phone, which means anyone with the URL can start an
intake session as any customer in the CRM. For a real deploy the caller must be
authenticated AND the session must bind to *their own* record. This verifies a
Cognito id-token and returns the caller identity from a VERIFIED claim, never from
client request_data.

WHY NO NEW DEPENDENCY. Signature verification uses `cryptography`, which is
already pinned in media/requirements-web.txt (aiortc needs it). A Cognito JWKS
entry is just an RSA public key (n, e) in base64url; we rebuild the key and verify
RS256 over `header.payload` ourselves rather than pull in PyJWT/jose. Less surface,
one fewer pinned package on a 2GB media host.

WHAT IS CHECKED, and why each matters for a medical record:
  - signature (RS256) against the pool's published JWKS  -> the token is genuine
  - `iss` == the pool issuer                              -> minted by OUR pool
  - `aud` (or `client_id`) == our app client             -> minted for OUR app
  - `token_use` == "id"                                   -> an id-token, not an
                                                             access-token
  - `exp` / `nbf` with a small leeway                     -> not expired/early

The caller phone is read from a configured claim (default `phone_number`), so the
CRM binding is decided by Cognito, not by the browser.

This module does no network I/O of its own beyond fetching the JWKS document, and
that fetch is injectable so the tests never touch the network.
"""
from __future__ import annotations

import base64
import json
import time
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed  # noqa: F401


class AuthError(Exception):
    """Any verification failure. The caller maps every one of these to an opaque
    403 - the reason is logged, never returned, so a probe cannot learn which
    check it tripped."""


@dataclass(frozen=True)
class CognitoConfig:
    """Everything needed to verify a token, all from the environment.

    region + user_pool_id give the issuer and the JWKS URL; app_client_id is the
    audience; phone_claim names the claim that carries the CRM phone.
    """

    region: str
    user_pool_id: str
    app_client_id: str
    phone_claim: str = "phone_number"
    leeway_secs: int = 60

    @property
    def issuer(self) -> str:
        return f"https://cognito-idp.{self.region}.amazonaws.com/{self.user_pool_id}"

    @property
    def jwks_url(self) -> str:
        return f"{self.issuer}/.well-known/jwks.json"

    @classmethod
    def from_env(cls, env: dict) -> "CognitoConfig":
        region = env.get("HVI_COGNITO_REGION") or env.get("DATA_REGION") or ""
        pool = env.get("HVI_COGNITO_USER_POOL_ID") or ""
        client = env.get("HVI_COGNITO_APP_CLIENT_ID") or ""
        if not (region and pool and client):
            raise AuthError(
                "cognito auth is enabled but HVI_COGNITO_REGION / "
                "HVI_COGNITO_USER_POOL_ID / HVI_COGNITO_APP_CLIENT_ID are not all set")
        return cls(
            region=region,
            user_pool_id=pool,
            app_client_id=client,
            phone_claim=env.get("HVI_COGNITO_PHONE_CLAIM", "phone_number"),
            leeway_secs=int(env.get("HVI_COGNITO_LEEWAY_SECS", "60")),
        )


def _b64url_to_int(value: str) -> int:
    """A JWKS n/e is base64url without padding; decode to the big integer the RSA
    public numbers need."""
    padding_len = (-len(value)) % 4
    raw = base64.urlsafe_b64decode(value + "=" * padding_len)
    return int.from_bytes(raw, "big")


def _b64url_decode(segment: str) -> bytes:
    padding_len = (-len(segment)) % 4
    return base64.urlsafe_b64decode(segment + "=" * padding_len)


class JWKSCache:
    """Fetch and cache a pool's JWKS, keyed by `kid`.

    Cognito rotates signing keys rarely and publishes both the current and the
    next, so a long TTL is fine; on an unknown kid we refetch once before failing,
    which is what lets a freshly rotated key verify without a restart.
    """

    def __init__(self, jwks_url: str,
                 fetcher: Optional[Callable[[str], dict]] = None,
                 ttl_secs: int = 3600) -> None:
        self._url = jwks_url
        self._fetch = fetcher or _http_get_json
        self._ttl = ttl_secs
        self._keys: dict[str, rsa.RSAPublicKey] = {}
        self._fetched_at = 0.0

    def _load(self) -> None:
        doc = self._fetch(self._url)
        keys: dict[str, rsa.RSAPublicKey] = {}
        for jwk in doc.get("keys", []):
            if jwk.get("kty") != "RSA" or "kid" not in jwk:
                continue
            n = _b64url_to_int(jwk["n"])
            e = _b64url_to_int(jwk["e"])
            keys[jwk["kid"]] = rsa.RSAPublicNumbers(e, n).public_key()
        if not keys:
            raise AuthError("JWKS document had no usable RSA keys")
        self._keys = keys
        self._fetched_at = time.time()

    def get(self, kid: str) -> rsa.RSAPublicKey:
        fresh = (time.time() - self._fetched_at) < self._ttl
        if not self._keys or not fresh:
            self._load()
        if kid not in self._keys:
            # Unknown kid on a warm cache: the key may have just rotated. Refetch
            # once before giving up.
            self._load()
        key = self._keys.get(kid)
        if key is None:
            raise AuthError(f"no JWKS key for kid {kid!r}")
        return key


def _http_get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 (https only)
        return json.loads(resp.read().decode("utf-8"))


class CognitoVerifier:
    """Verify a Cognito id-token and extract the caller phone.

    The JWKS cache is injectable so tests supply keys directly and never hit the
    network. `now_fn` is injectable so expiry can be tested deterministically.
    """

    def __init__(self, config: CognitoConfig,
                 jwks: Optional[JWKSCache] = None,
                 now_fn: Callable[[], float] = time.time) -> None:
        self.config = config
        self.jwks = jwks or JWKSCache(config.jwks_url)
        self._now = now_fn

    def verify(self, token: str) -> dict:
        """Return the verified claims, or raise AuthError. Never returns partial
        trust: a token that fails any check raises."""
        if not token or token.count(".") != 2:
            raise AuthError("token is not a three-segment JWT")
        header_b64, payload_b64, sig_b64 = token.split(".")

        try:
            header = json.loads(_b64url_decode(header_b64))
            claims = json.loads(_b64url_decode(payload_b64))
            signature = _b64url_decode(sig_b64)
        except (ValueError, json.JSONDecodeError) as exc:
            raise AuthError(f"token is not decodable: {exc}") from exc

        if header.get("alg") != "RS256":
            # Pin the algorithm. Accepting alg from the header is the classic JWT
            # bug: "none" or an HMAC alg would let an attacker forge a token.
            raise AuthError(f"unexpected alg {header.get('alg')!r}, want RS256")
        kid = header.get("kid")
        if not kid:
            raise AuthError("token header has no kid")

        key = self.jwks.get(kid)
        signed = f"{header_b64}.{payload_b64}".encode("ascii")
        try:
            key.verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
        except InvalidSignature as exc:
            raise AuthError("signature does not verify") from exc

        self._check_claims(claims)
        return claims

    def _check_claims(self, claims: dict) -> None:
        cfg = self.config
        if claims.get("iss") != cfg.issuer:
            raise AuthError("iss does not match the pool issuer")

        # id-tokens carry the app client in `aud`; access-tokens use `client_id`.
        # We require an id-token, so check aud.
        if claims.get("token_use") != "id":
            raise AuthError("token_use is not 'id'")
        aud = claims.get("aud")
        if aud != cfg.app_client_id:
            raise AuthError("aud is not our app client id")

        now = self._now()
        exp = claims.get("exp")
        if not isinstance(exp, (int, float)) or now > exp + cfg.leeway_secs:
            raise AuthError("token is expired")
        nbf = claims.get("nbf")
        if isinstance(nbf, (int, float)) and now < nbf - cfg.leeway_secs:
            raise AuthError("token is not yet valid")

    def caller_phone(self, claims: dict) -> str:
        """The CRM phone, from the configured verified claim. This is the whole
        point: identity comes from the token, not from client request_data."""
        phone = claims.get(self.config.phone_claim)
        if not phone:
            raise AuthError(
                f"verified token has no {self.config.phone_claim!r} claim")
        return str(phone).strip()
