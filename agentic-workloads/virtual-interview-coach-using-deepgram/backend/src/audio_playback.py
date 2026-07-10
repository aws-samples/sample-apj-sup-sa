"""Per-answer playback (F006 / G6) — mint a short-lived, owner-scoped S3 pre-signed GET URL.

Audio (PII) is never public; the browser fetches it directly from S3 using a time-limited URL minted
here on demand for the authenticated owner only (Constitution III / FR-008/FR-009). The audio bytes
never transit the backend. The signed URL is returned to the owner but NEVER logged (SC-006).

Off the live path (the interview is over). No-op safe: when AUDIO_BUCKET is unset (local/dev), no URL
is minted and the caller surfaces "no recording available".
"""

from __future__ import annotations

import logging

from .config import settings

log = logging.getLogger("backend")


def _bucket_key(audio_uri: str) -> tuple[str, str] | None:
    if not audio_uri or not audio_uri.startswith("s3://"):
        return None
    without = audio_uri[len("s3://") :]
    bucket, _, key = without.partition("/")
    if not key:
        return None
    return bucket, key


def presign_get(audio_uri: str) -> str | None:
    """Mint a pre-signed GET URL for the audio object, expiring in AUDIO_URL_TTL_S seconds. Returns
    None when the bucket is unconfigured or the uri is unusable. Never logs the URL.

    The client MUST be pinned to Signature Version 4: the audio objects are SSE-KMS (Constitution
    III), and S3 REJECTS non-SigV4 presigned GETs for KMS-encrypted objects with
    400 InvalidArgument ("...require AWS Signature Version 4") — the root cause of the silent
    'Play my answer' failure found live in F008 (research R1). boto3's default presign signing
    fell back to a non-V4 signature in the deployed environment, so this is explicit, not left
    to defaults."""
    if not settings.audio_bucket:
        return None
    bk = _bucket_key(audio_uri)
    if bk is None or bk[0] != settings.audio_bucket:
        return None

    import boto3
    from botocore.config import Config as BotoConfig

    client = boto3.client(
        "s3",
        region_name=settings.aws_region,
        config=BotoConfig(signature_version="s3v4"),
    )
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bk[0], "Key": bk[1]},
        ExpiresIn=settings.audio_url_ttl_s,
    )


def build_playback(audio_uri: str | None) -> dict:
    """Turn an owner's turn audio_uri into the playback response body. {"available": false} when there
    is no recording (NULL uri, unconfigured bucket, or unusable uri) — FR-010; else a short-lived URL."""
    if not audio_uri:
        return {"available": False}
    url = presign_get(audio_uri)
    if not url:
        return {"available": False}
    return {"available": True, "url": url, "expires_in": settings.audio_url_ttl_s}
