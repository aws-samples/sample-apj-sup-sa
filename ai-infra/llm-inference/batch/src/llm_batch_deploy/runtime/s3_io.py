"""S3 I/O helpers used by the runtime entrypoint.

All functions here are async (using aioboto3) so they compose with the
asyncio driver in vllm_driver.py.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import aioboto3

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class S3Uri:
    bucket: str
    key: str

    @classmethod
    def parse(cls, uri: str) -> "S3Uri":
        if not uri.startswith("s3://"):
            raise ValueError(f"Not an s3:// URI: {uri!r}")
        parsed = urlparse(uri)
        if not parsed.netloc or not parsed.path:
            raise ValueError(f"Malformed s3:// URI: {uri!r}")
        return cls(bucket=parsed.netloc, key=parsed.path.lstrip("/"))

    def join(self, *parts: str) -> "S3Uri":
        """Produce a new S3Uri with parts appended to the key."""
        suffix = "/".join(p.strip("/") for p in parts if p)
        new_key = f"{self.key.rstrip('/')}/{suffix}" if self.key else suffix
        return S3Uri(self.bucket, new_key)

    def __str__(self) -> str:
        return f"s3://{self.bucket}/{self.key}"


async def read_text(s3, uri: S3Uri) -> str:
    """Fetch an S3 object as UTF-8 text."""
    resp = await s3.get_object(Bucket=uri.bucket, Key=uri.key)
    async with resp["Body"] as stream:
        data = await stream.read()
    return data.decode("utf-8")


async def write_text(s3, uri: S3Uri, body: str, *, content_type: str = "application/json") -> None:
    """Put a UTF-8 text body to S3."""
    await s3.put_object(
        Bucket=uri.bucket,
        Key=uri.key,
        Body=body.encode("utf-8"),
        ContentType=content_type,
    )


async def object_exists(s3, uri: S3Uri) -> bool:
    """HEAD check — True if an object exists at uri."""
    try:
        await s3.head_object(Bucket=uri.bucket, Key=uri.key)
        return True
    except Exception as exc:  # noqa: BLE001
        # aioboto3 wraps the error; cheapest check is str match on 404.
        if "404" in str(exc) or "Not Found" in str(exc):
            return False
        # Re-raise for actual errors (permissions, etc.)
        raise


def iter_input_records(body: str, *, uri: str) -> list[dict[str, Any]]:
    """Parse an S3 object body as either JSONL (one record per line) or a
    single JSON object/array.

    Returns
    -------
    list[dict] — one dict per record in the file.
    """
    stripped = body.lstrip()
    if not stripped:
        return []

    # Heuristic: a JSON array or single object -> parse whole-file.
    # Anything else (one object per line) -> JSONL.
    if stripped[0] == "[":
        data = json.loads(body)
        if not isinstance(data, list):
            raise ValueError(f"{uri}: expected JSON array at top level")
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                raise ValueError(f"{uri}: array element {i} is not an object")
        return data

    if stripped[0] == "{":
        # Could be a single object OR a JSONL file that happens to start with '{'.
        # The distinguishing test: valid JSON when parsed whole?
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            # Fall through to JSONL mode
            pass
        else:
            if isinstance(data, dict):
                return [data]
            raise ValueError(f"{uri}: unexpected top-level JSON type {type(data).__name__}")

    # JSONL — one JSON object per non-empty line.
    records: list[dict[str, Any]] = []
    for lineno, line in enumerate(body.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{uri}:{lineno}: invalid JSON: {exc}") from exc
        if not isinstance(obj, dict):
            raise ValueError(f"{uri}:{lineno}: expected object, got {type(obj).__name__}")
        records.append(obj)
    return records


def s3_session():
    """Return an aioboto3 Session for async S3 use, with explicit credentials.

    aiobotocore (>= 2.25, shipped in newer ``vllm/vllm-openai`` base images)
    has a regression where ``AioContainerProvider`` doesn't reliably resolve
    ECS task credentials from ``AWS_CONTAINER_CREDENTIALS_RELATIVE_URI`` in
    some AWS Batch compute-environment configurations: the resolver falls
    through to an unsigned request, S3 returns
    ``AccessDenied: No AWSAccessKey was presented``, and the entrypoint dies
    after vLLM is up.

    Sync boto3's credential discovery (which has been stable for years)
    finds the ECS container credentials correctly, so we use it once at
    session-creation time to fetch the access key / secret / session token
    and hand them explicitly to ``aioboto3.Session``. The token is
    short-lived but boto3's frozen credentials are good for the duration of
    a single Batch attempt (max 5400s) which is well
    within the ECS-issued token's typical 6-hour lifetime.

    Tests can monkeypatch this function directly.
    """
    import os
    import boto3 as _boto3
    sess = _boto3.Session()
    creds = sess.get_credentials()
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if creds is None:
        # Defer to default async resolution; the error message will still
        # surface, but at least we don't shadow it.
        return aioboto3.Session(region_name=region)
    frozen = creds.get_frozen_credentials()
    return aioboto3.Session(
        aws_access_key_id=frozen.access_key,
        aws_secret_access_key=frozen.secret_key,
        aws_session_token=frozen.token,
        region_name=region,
    )
