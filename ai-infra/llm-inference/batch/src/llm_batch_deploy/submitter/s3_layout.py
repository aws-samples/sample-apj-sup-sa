"""Client-side S3 layout conventions + manifest helpers.

These run on the user's machine (notebook, CLI). Sync boto3, not async.

Layout
------
Everything for a given stack lives under ``s3://{staging_bucket}/``:

    staging/<submission_id>/manifests/shard-<N>.jsonl    # one per Batch job
    staging/<submission_id>/inputs/<original_filename>   # uploaded local files
    outputs/<submission_id>/shard-<N>/<original_filename>  # shard output files
    outputs/<submission_id>/shard-<N>/_summary.json      # per-job summary

``submission_id`` is ``<ISO-timestamp>-<short-uuid>`` so listings sort
chronologically and two submissions never collide.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

LOG = logging.getLogger(__name__)


def make_submission_id(prefix: str = "") -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    short = uuid.uuid4().hex[:8]
    if prefix:
        return f"{prefix}-{ts}-{short}"
    return f"{ts}-{short}"


@dataclass(frozen=True)
class S3Layout:
    """Conventions for where things live in the staging bucket."""

    bucket: str
    submission_id: str

    @property
    def manifest_prefix(self) -> str:
        return f"staging/{self.submission_id}/manifests/"

    @property
    def inputs_prefix(self) -> str:
        return f"staging/{self.submission_id}/inputs/"

    @property
    def outputs_prefix(self) -> str:
        return f"outputs/{self.submission_id}/"

    def manifest_uri(self, shard_index: int) -> str:
        return f"s3://{self.bucket}/{self.manifest_prefix}shard-{shard_index:04d}.jsonl"

    def output_prefix_uri(self, shard_index: int) -> str:
        return f"s3://{self.bucket}/{self.outputs_prefix}shard-{shard_index:04d}/"

    def upload_uri(self, filename: str) -> str:
        return f"s3://{self.bucket}/{self.inputs_prefix}{filename}"


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """Parse ``s3://bucket/key`` → ``(bucket, key)``."""
    if not uri.startswith("s3://"):
        raise ValueError(f"Not an s3:// URI: {uri!r}")
    parsed = urlparse(uri)
    if not parsed.netloc:
        raise ValueError(f"Missing bucket: {uri!r}")
    return parsed.netloc, parsed.path.lstrip("/")


def is_s3_uri(value: str | Path) -> bool:
    return isinstance(value, str) and value.startswith("s3://")


# -----------------------------------------------------------------------------
# Manifest: a JSONL file listing S3 URIs, one per line.
# -----------------------------------------------------------------------------
def write_manifest(s3_client, bucket: str, key: str, input_uris: Iterable[str]) -> str:
    """Write a JSONL manifest to S3. Each line is one S3 URI (no JSON wrap).

    Returns the full ``s3://bucket/key`` URI.
    """
    body = "\n".join(uri.strip() for uri in input_uris if uri.strip()) + "\n"
    s3_client.put_object(
        Bucket=bucket, Key=key,
        Body=body.encode("utf-8"),
        ContentType="text/plain",
    )
    return f"s3://{bucket}/{key}"


def read_manifest(s3_client, bucket: str, key: str) -> list[str]:
    resp = s3_client.get_object(Bucket=bucket, Key=key)
    body = resp["Body"].read().decode("utf-8")
    return [line.strip() for line in body.splitlines() if line.strip()]


# -----------------------------------------------------------------------------
# Chunking — split a flat URI list into shards.
# -----------------------------------------------------------------------------
def chunk_uris(uris: list[str], *, max_per_shard: int) -> list[list[str]]:
    """Greedy chunking of a URI list into shards of at most ``max_per_shard``."""
    if max_per_shard < 1:
        raise ValueError("max_per_shard must be >= 1")
    return [uris[i : i + max_per_shard] for i in range(0, len(uris), max_per_shard)]


# -----------------------------------------------------------------------------
# Local file upload — normalize local paths to S3 URIs.
# -----------------------------------------------------------------------------
def upload_local_file(s3_client, local_path: Path, bucket: str, key: str) -> str:
    """Upload one local file to ``s3://bucket/key``. Returns the URI."""
    if not local_path.is_file():
        raise FileNotFoundError(local_path)
    content_type = "application/x-ndjson" if local_path.suffix == ".jsonl" else "application/json"
    s3_client.upload_file(
        Filename=str(local_path), Bucket=bucket, Key=key,
        ExtraArgs={"ContentType": content_type},
    )
    return f"s3://{bucket}/{key}"


def normalize_input_sources(
    s3_client,
    sources: list[str | Path],
    *,
    layout: S3Layout,
) -> list[str]:
    """Resolve each source to an S3 URI. Local paths get uploaded.

    Accepted shapes in ``sources``:
    * ``"s3://bucket/key"`` — passed through.
    * ``"s3://bucket/prefix/"`` (trailing slash) — *not* expanded here; the
      caller should pre-expand to concrete keys (we don't want surprise
      recursive fetches at this layer).
    * local file path (str or Path) — uploaded to the staging bucket under
      ``inputs_prefix/<basename>``.
    * local directory path — all ``*.json`` and ``*.jsonl`` files inside,
      non-recursive, each uploaded.

    Returns
    -------
    A flat list of S3 URIs in the order they appeared.
    """
    resolved: list[str] = []
    for src in sources:
        if is_s3_uri(src):
            if src.endswith("/"):
                raise ValueError(
                    f"{src!r}: S3 prefix inputs aren't supported; pass concrete keys."
                )
            resolved.append(str(src))
            continue

        p = Path(src)
        if p.is_dir():
            children = sorted([
                c for c in p.iterdir()
                if c.is_file() and c.suffix in (".json", ".jsonl")
            ])
            if not children:
                raise ValueError(f"{p}: directory has no .json/.jsonl files.")
            for c in children:
                uri = upload_local_file(
                    s3_client, c, layout.bucket,
                    f"{layout.inputs_prefix}{c.name}",
                )
                LOG.info("Uploaded %s → %s", c.name, uri)
                resolved.append(uri)
        elif p.is_file():
            if p.suffix not in (".json", ".jsonl"):
                raise ValueError(f"{p}: unsupported extension; use .json or .jsonl.")
            uri = upload_local_file(
                s3_client, p, layout.bucket,
                f"{layout.inputs_prefix}{p.name}",
            )
            LOG.info("Uploaded %s → %s", p.name, uri)
            resolved.append(uri)
        else:
            raise FileNotFoundError(f"Source not found: {src!r}")
    return resolved
