"""S3 audio object helpers (F006 / G6) — delete-by-uri + delete-by-session-prefix.

Per-turn interview audio is PII; it lives ONLY in the audio bucket (SSE-KMS, single region) with its
uri in `conversation_turn.audio_uri` (Constitution III). These helpers are the S3 side of the bounded
hard-delete fan-out (delete_session_cascade / delete_user_cascade), the consent-revoke purge, and the
retention TTL sweep. They run OFF the live path.

Delete-by-prefix exists so a session delete catches audio whose upload was still in flight (its uri not
yet written to RDS) — see research R6 / FR-016. Both functions are no-ops (return 0) when AUDIO_BUCKET
is unconfigured (local/dev), mirroring resume_store. Counts only — never log object keys or bytes.
"""

from __future__ import annotations

import logging

from .config import settings

log = logging.getLogger("backend")

_PREFIX = "audio/"


def audio_key(session_id: str, turn_id: str, ext: str = "wav") -> str:
    """Canonical per-turn audio key: audio/{session_id}/{turn_id}.{ext} (session prefix enables the
    race-safe prefix delete)."""
    return f"{_PREFIX}{session_id}/{turn_id}.{ext}"


def _bucket_key(uri: str) -> tuple[str, str] | None:
    """Split an s3://bucket/key uri into (bucket, key); None if not a usable uri for OUR bucket."""
    if not uri or not uri.startswith("s3://"):
        return None
    without = uri[len("s3://") :]
    bucket, _, key = without.partition("/")
    if not settings.audio_bucket or bucket != settings.audio_bucket or not key:
        return None
    return bucket, key


def delete_objects(uris: list[str]) -> int:
    """Delete the given audio objects by s3:// uri. Returns the count deleted. No-op (0) when the
    bucket is unconfigured. Never raises object keys/bytes into logs."""
    if not settings.audio_bucket:
        return 0
    keys = []
    for uri in uris or []:
        bk = _bucket_key(uri)
        if bk is not None:
            keys.append(bk[1])
    if not keys:
        return 0

    import boto3

    client = boto3.client("s3", region_name=settings.aws_region)
    deleted = 0
    # S3 delete_objects caps at 1000 keys per call; batch to stay safe at any scale.
    for i in range(0, len(keys), 1000):
        batch = keys[i : i + 1000]
        resp = client.delete_objects(
            Bucket=settings.audio_bucket,
            Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
        )
        deleted += len(batch) - len(resp.get("Errors") or [])
    return deleted


def delete_session_prefix(session_id: str) -> int:
    """Delete EVERY object under audio/{session_id}/ — catches in-flight uploads whose uri is not yet
    in RDS (FR-016). Returns the count deleted. No-op (0) when the bucket is unconfigured."""
    if not settings.audio_bucket or not session_id:
        return 0

    import boto3

    client = boto3.client("s3", region_name=settings.aws_region)
    prefix = f"{_PREFIX}{session_id}/"
    deleted = 0
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=settings.audio_bucket, Prefix=prefix):
        objs = [{"Key": o["Key"]} for o in page.get("Contents") or []]
        if not objs:
            continue
        resp = client.delete_objects(
            Bucket=settings.audio_bucket, Delete={"Objects": objs, "Quiet": True}
        )
        deleted += len(objs) - len(resp.get("Errors") or [])
    return deleted
