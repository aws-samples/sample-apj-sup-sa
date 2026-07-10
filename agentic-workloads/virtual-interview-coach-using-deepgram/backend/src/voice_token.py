"""Short-lived per-session media join token + ICE server payload (supports T015, T037).

The voice_token authenticates the WebRTC media join on the media plane (NFR-5); it is
short-TTL and per-session so no long-lived secret reaches the SPA. ICE servers include a
STUN server and (for US3) a managed TURN relay with ephemeral credentials.
"""

from __future__ import annotations

import time

from jose import jwt

from .config import settings


_DEV_FALLBACK_SECRET = "dev-insecure-secret"  # local/dev only — never used when Cognito is configured


def _voice_token_secret() -> str:
    """Resolve the HS256 secret. Fails fast on a DEPLOYED instance (Cognito configured) whose
    VOICE_TOKEN_SECRET is missing — otherwise tokens would be signed with a public, guessable secret,
    making WebRTC media-join forgeable for any session_id/user_sub (code-review finding #3). The
    dev fallback survives ONLY for local/dev (no Cognito pool), so tests + local runs are unaffected."""
    if settings.voice_token_secret:
        return settings.voice_token_secret
    if settings.cognito_user_pool_id:  # a real deployment must have a real secret
        raise RuntimeError("VOICE_TOKEN_SECRET must be set when Cognito is configured (deployed env)")
    return _DEV_FALLBACK_SECRET


def mint_voice_token(session_id: str, user_sub: str) -> str:
    """Sign a short-lived join token bound to the session and user."""
    secret = _voice_token_secret()
    now = int(time.time())
    claims = {
        "sub": user_sub,
        "sid": session_id,
        "iat": now,
        "exp": now + settings.voice_token_ttl_s,
        "scope": "media-join",
    }
    return jwt.encode(claims, secret, algorithm="HS256")


def build_ice_servers() -> list[dict]:
    """Return the ICE server list for RTCPeerConnection.

    Always includes STUN. Includes a managed TURN relay with ephemeral credentials when the
    provider is configured (US3 / FR-009). For G1's first gate run the direct path is used;
    TURN is provisioned but only relays for UDP-blocked minorities.
    """
    servers: list[dict] = [{"urls": [settings.stun_url]}]
    if settings.turn_api_key and settings.turn_api_secret:
        # NOTE: managed providers (Twilio/Cloudflare) issue ephemeral credentials via their
        # own API. This is the integration point — wire the provider SDK here to fetch a
        # short-TTL username/credential per session. Placeholder structure shown.
        servers.append(
            {
                "urls": ["turn:turn.example.com:3478?transport=udp"],
                "username": "ephemeral-per-session",
                "credential": "ephemeral-secret",
            }
        )
    return servers
