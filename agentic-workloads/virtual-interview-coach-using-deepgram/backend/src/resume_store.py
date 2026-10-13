"""Raw resume storage in S3 + SSE-KMS (T013) — the sole durable home for the raw file (R6/III).

The raw resume file is PII. It lives ONLY in this bucket (SSE-KMS) and as parsed facts in RDS;
nothing raw goes to AgentCore (Constitution III). One active resume per user (the key is fixed
per user, so re-upload overwrites). All calls run off the response_gap clock (setup window).

S3 is optional locally: when no RESUME_BUCKET is configured, `put_resume` returns a synthetic
uri and skips the upload so the rest of the setup flow is still exercisable without AWS.
"""

from __future__ import annotations

import logging

from .config import settings

log = logging.getLogger("backend")


def _key(user_id: str, filename: str) -> str:
    ext = "pdf"
    lower = filename.lower()
    for cand in ("pdf", "docx", "txt"):
        if lower.endswith("." + cand):
            ext = cand
            break
    # Fixed per-user key => one active resume; re-upload overwrites the prior object.
    return f"resumes/{user_id}/resume.{ext}"


def put_resume(user_id: str, file_bytes: bytes, filename: str, content_type: str) -> str:
    """Upload the raw resume under SSE-KMS; returns the s3:// uri (or a synthetic uri locally)."""
    if not settings.resume_bucket:
        log.info("RESUME_BUCKET not configured; skipping S3 upload (local/dev path)")
        return f"s3://local-unconfigured/{_key(user_id, filename)}"

    import boto3

    client = boto3.client("s3", region_name=settings.aws_region)
    key = _key(user_id, filename)
    extra = {"ServerSideEncryption": "aws:kms", "ContentType": content_type or "application/octet-stream"}
    if settings.resume_kms_key_id:
        extra["SSEKMSKeyId"] = settings.resume_kms_key_id
    client.put_object(Bucket=settings.resume_bucket, Key=key, Body=file_bytes, **extra)
    return f"s3://{settings.resume_bucket}/{key}"


def delete_resume(resume_uri: str | None) -> int:
    """Delete a stored resume object (FR-219 bounded blast radius). Returns objects deleted (0/1)."""
    if not resume_uri or not resume_uri.startswith("s3://"):
        return 0
    without = resume_uri[len("s3://") :]
    bucket, _, key = without.partition("/")
    if not settings.resume_bucket or bucket != settings.resume_bucket or not key:
        return 0

    import boto3

    client = boto3.client("s3", region_name=settings.aws_region)
    client.delete_object(Bucket=settings.resume_bucket, Key=key)
    return 1
