"""Retention TTL cleanup (F006 / G6) — purge audio + rows for sessions past retention, not marked keep.

Runs in the always-on report-worker, OFF the live path (Constitution I). Selects sessions older than
the owner's retention window (users.retention_days, default 30) that are NOT marked retain=TRUE, deletes
their S3 audio objects (per-uri + per-session-prefix, S3-before-RDS = the privacy-safe failure
direction, R6), then deletes the session rows (RDS cascade removes turns/latency/blueprint/report/...).

Idempotent (safe every interval). Counts-only logs — never raw PII (FR-013 / SC-006). When AUDIO_BUCKET
is unconfigured (local/dev), the S3 deletes are no-ops; an S3 bucket lifecycle rule is a separate
defense-in-depth backstop, but this sweep is the authoritative purge (only it also removes RDS rows).
"""

from __future__ import annotations

import logging

import asyncpg

from .config import Config

log = logging.getLogger("report_worker")


class AudioDeleteError(RuntimeError):
    """Raised when one or more audio objects could not be deleted, so the caller does NOT proceed to
    delete the RDS rows (which would orphan the audio — Principle III). The sweep skips this batch and
    retries next interval."""


def _delete_audio(config: Config, uris: list[str], session_ids: list[str]) -> int:
    """Delete the given audio objects (by uri) + every audio/{session}/ prefix. No-op (0) when the
    bucket is unconfigured. Never logs object keys/bytes. RAISES AudioDeleteError if any object fails
    to delete — a partial PII-deletion failure must be surfaced, never silently counted as success
    (code-review findings #6/#7)."""
    if not config.audio_bucket:
        return 0
    import boto3

    client = boto3.client("s3", region_name=config.aws_region)
    deleted = 0
    failed = 0
    keys = []
    for uri in uris:
        if uri and uri.startswith(f"s3://{config.audio_bucket}/"):
            keys.append(uri[len(f"s3://{config.audio_bucket}/") :])
    for i in range(0, len(keys), 1000):
        batch = keys[i : i + 1000]
        if batch:
            resp = client.delete_objects(
                Bucket=config.audio_bucket,
                Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
            )
            errs = resp.get("Errors") or []
            deleted += len(batch) - len(errs)
            failed += len(errs)
    paginator = client.get_paginator("list_objects_v2")
    for sid in session_ids:
        for page in paginator.paginate(Bucket=config.audio_bucket, Prefix=f"audio/{sid}/"):
            objs = [{"Key": o["Key"]} for o in page.get("Contents") or []]
            if objs:
                resp = client.delete_objects(
                    Bucket=config.audio_bucket, Delete={"Objects": objs, "Quiet": True}
                )
                errs = resp.get("Errors") or []
                deleted += len(objs) - len(errs)
                failed += len(errs)
    if failed:
        # Surface the failure (codes only, never keys/bytes) and stop — the caller must not delete the
        # RDS rows for these sessions, or the still-present audio becomes an unreferenced orphan.
        log.warning("retention: %d audio object(s) failed to delete (%d ok) — skipping RDS purge this run",
                    failed, deleted)
        raise AudioDeleteError(f"{failed} audio object(s) failed to delete")
    return deleted


async def _expired_sessions(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    """Sessions past the owner's retention window, not marked keep. retention_days defaults to 30 when
    the user row is missing/null (the safe default — eligible to age out)."""
    return await conn.fetch(
        """
        SELECT vs.session_id::text AS session_id
          FROM voice_session vs
          LEFT JOIN users u ON u.user_sub = vs.user_sub
         WHERE COALESCE(vs.retain, FALSE) = FALSE
           AND vs.created_at < now() - make_interval(days => COALESCE(u.retention_days, 30))
        """
    )


async def _audio_uris_for(conn: asyncpg.Connection, session_ids: list[str]) -> list[str]:
    if not session_ids:
        return []
    rows = await conn.fetch(
        "SELECT audio_uri FROM conversation_turn "
        "WHERE session_id = ANY($1::uuid[]) AND audio_uri IS NOT NULL",
        session_ids,
    )
    return [r["audio_uri"] for r in rows]


async def sweep_expired(config: Config, conn: asyncpg.Connection) -> dict:
    """Purge audio + rows for expired, not-keep sessions. Idempotent; counts-only logs.
    Returns {"sessions": n, "audio_objects": m}."""
    expired = await _expired_sessions(conn)
    session_ids = [r["session_id"] for r in expired]
    if not session_ids:
        return {"sessions": 0, "audio_objects": 0}

    uris = await _audio_uris_for(conn, session_ids)
    # S3 BEFORE RDS (privacy-safe failure direction): a crash/partial-failure leaves at most orphan
    # rows, never orphan audio without a row pointer. Run in a thread so the boto3 calls don't block
    # the loop. If S3 deletion partially FAILS (AudioDeleteError), we do NOT delete the RDS rows — the
    # sessions stay eligible and the next sweep retries, rather than orphaning the still-present audio.
    import asyncio

    try:
        audio_objects = await asyncio.get_running_loop().run_in_executor(
            None, _delete_audio, config, uris, session_ids
        )
    except AudioDeleteError:
        return {"sessions": 0, "audio_objects": 0, "deferred_sessions": len(session_ids)}
    async with conn.transaction():
        await conn.execute(
            "DELETE FROM voice_session WHERE session_id = ANY($1::uuid[])", session_ids
        )
    log.info("retention sweep purged %d session(s), %d audio object(s)", len(session_ids), audio_objects)
    return {"sessions": len(session_ids), "audio_objects": int(audio_objects)}
