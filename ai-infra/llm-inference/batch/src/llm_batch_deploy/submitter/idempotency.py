"""Idempotency helper — skip inputs whose output already exists.

The container does this too (HEAD per URI), but doing it client-side lets
the invoker skip entire shards when all their inputs are already done.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from .s3_layout import S3Layout, parse_s3_uri

LOG = logging.getLogger(__name__)


def predict_output_key(layout: S3Layout, shard_index: int, input_uri: str) -> tuple[str, str]:
    """Derive the bucket+key where an output for this input is expected.

    Mirrors the runtime's output layout exactly (filename under
    ``outputs/<submission>/shard-<N>/``).
    """
    _bucket, key = parse_s3_uri(input_uri)
    filename = key.rsplit("/", 1)[-1]
    return (
        layout.bucket,
        f"{layout.outputs_prefix}shard-{shard_index:04d}/{filename}",
    )


def _exists(s3_client, bucket: str, key: str) -> bool:
    try:
        s3_client.head_object(Bucket=bucket, Key=key)
        return True
    except Exception as exc:  # noqa: BLE001
        if "404" in str(exc) or "Not Found" in str(exc) or "NoSuchKey" in str(exc):
            return False
        raise


def filter_done(
    s3_client,
    layout: S3Layout,
    uris_per_shard: list[list[str]],
    *,
    max_workers: int = 16,
) -> tuple[list[list[str]], dict[int, int]]:
    """For each shard, drop URIs whose output already exists.

    Returns
    -------
    (filtered_shards, skipped_counts_by_shard_index)
    """
    filtered: list[list[str]] = []
    skipped: dict[int, int] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for idx, shard in enumerate(uris_per_shard):
            futmap = {
                pool.submit(_exists, s3_client, *predict_output_key(layout, idx, u)): u
                for u in shard
            }
            done_count = 0
            still_todo: list[str] = []
            for fut in as_completed(futmap):
                uri = futmap[fut]
                if fut.result():
                    done_count += 1
                else:
                    still_todo.append(uri)
            if done_count:
                skipped[idx] = done_count
            # Preserve original order.
            orig_order = {u: i for i, u in enumerate(shard)}
            still_todo.sort(key=lambda u: orig_order[u])
            filtered.append(still_todo)
    return filtered, skipped
