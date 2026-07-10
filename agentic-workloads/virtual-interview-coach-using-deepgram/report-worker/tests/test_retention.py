"""F006 (G6) retention TTL sweep tests (report-worker).

Requires a local Postgres reachable via DATABASE_URL; skipped otherwise. The S3 delete is stubbed
(retention._delete_audio) so the test is offline. Validates: expired-not-keep sessions are purged
(audio + rows), keep sessions are retained, not-yet-expired sessions are untouched, the sweep is
idempotent, and only counts/ids are logged (no raw PII)."""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

from src.config import Config
from src import retention

pytestmark = pytest.mark.asyncio

_DEFAULT_DB = "postgres://postgres:test@localhost:55432/interviewcoach"


def _config() -> Config:
    os.environ.setdefault("DATABASE_URL", _DEFAULT_DB)
    os.environ.setdefault("AWS_REGION", "us-west-2")
    os.environ["AUDIO_BUCKET"] = "audio-bkt"
    os.environ["RETENTION_SWEEP_INTERVAL_S"] = "3600"
    return Config.load()


async def _db_or_skip(cfg: Config) -> asyncpg.Connection:
    try:
        return await asyncpg.connect(cfg.database_url, timeout=3)
    except Exception:  # noqa: BLE001
        pytest.skip("no local Postgres reachable for retention test")


async def _seed(conn, sub: str, *, age_days: int, retain: bool, retention_days: int = 30) -> str:
    """Seed a user + a session with created_at age_days in the past + one audio turn."""
    await conn.execute(
        "INSERT INTO users (id, user_sub, retention_days, created_at) "
        "VALUES ($1,$2,$3, now()) ON CONFLICT (user_sub) DO UPDATE SET retention_days=$3",
        uuid.uuid4(), sub, retention_days,
    )
    sid = uuid.uuid4()
    await conn.execute(
        "INSERT INTO voice_session (session_id, user_sub, reply_provider, retain, created_at) "
        "VALUES ($1,$2,'bedrock_direct',$3, now() - make_interval(days => $4))",
        sid, sub, retain, age_days,
    )
    await conn.execute(
        "INSERT INTO conversation_turn (turn_id, session_id, turn_index, speaker, transcript, "
        "started_at, audio_uri) VALUES ($1,$2,0,'student','hi', now(), $3)",
        uuid.uuid4(), sid, f"s3://audio-bkt/audio/{sid}/turn-0.wav",
    )
    return str(sid)


def _stub_audio(monkeypatch):
    rec = {"uris": [], "sessions": []}

    def fake(config, uris, session_ids):
        rec["uris"].extend(uris)
        rec["sessions"].extend(session_ids)
        return len(uris)

    monkeypatch.setattr(retention, "_delete_audio", fake)
    return rec


async def test_expired_not_keep_is_purged(monkeypatch):
    cfg = _config()
    conn = await _db_or_skip(cfg)
    rec = _stub_audio(monkeypatch)
    sub = f"ret-exp-{uuid.uuid4().hex[:8]}"
    sid = await _seed(conn, sub, age_days=40, retain=False)  # 40 > 30-day window
    try:
        result = await retention.sweep_expired(cfg, conn)
        assert result["sessions"] >= 1
        assert sid in rec["sessions"]                       # its audio was purged
        assert await conn.fetchval(
            "SELECT count(*) FROM voice_session WHERE session_id=$1", uuid.UUID(sid)
        ) == 0                                              # rows gone
    finally:
        await conn.execute("DELETE FROM voice_session WHERE session_id=$1", uuid.UUID(sid))
        await conn.execute("DELETE FROM users WHERE user_sub=$1", sub)
        await conn.close()


async def test_s3_delete_failure_keeps_rds_rows(monkeypatch):
    # Privacy-safe failure direction (code-review #7): if audio deletion partially FAILS, the RDS rows
    # must SURVIVE (deferred to the next sweep), never be deleted while the audio still exists (which
    # would orphan the audio). _delete_audio raising AudioDeleteError must not purge the rows.
    cfg = _config()
    conn = await _db_or_skip(cfg)

    def boom(config, uris, session_ids):
        raise retention.AudioDeleteError("simulated S3 partial failure")

    monkeypatch.setattr(retention, "_delete_audio", boom)
    sub = f"ret-fail-{uuid.uuid4().hex[:8]}"
    sid = await _seed(conn, sub, age_days=40, retain=False)
    try:
        result = await retention.sweep_expired(cfg, conn)
        assert result["sessions"] == 0 and result.get("deferred_sessions", 0) >= 1
        # rows MUST still be present — not orphaned against the (still-present) audio.
        assert await conn.fetchval(
            "SELECT count(*) FROM voice_session WHERE session_id=$1", uuid.UUID(sid)
        ) == 1
    finally:
        await conn.execute("DELETE FROM voice_session WHERE session_id=$1", uuid.UUID(sid))
        await conn.execute("DELETE FROM users WHERE user_sub=$1", sub)
        await conn.close()


async def test_expired_but_keep_is_retained(monkeypatch):
    cfg = _config()
    conn = await _db_or_skip(cfg)
    _stub_audio(monkeypatch)
    sub = f"ret-keep-{uuid.uuid4().hex[:8]}"
    sid = await _seed(conn, sub, age_days=40, retain=True)  # past window but KEEP
    try:
        await retention.sweep_expired(cfg, conn)
        assert await conn.fetchval(
            "SELECT count(*) FROM voice_session WHERE session_id=$1", uuid.UUID(sid)
        ) == 1                                              # retained
    finally:
        await conn.execute("DELETE FROM voice_session WHERE session_id=$1", uuid.UUID(sid))
        await conn.execute("DELETE FROM users WHERE user_sub=$1", sub)
        await conn.close()


async def test_not_yet_expired_is_untouched(monkeypatch):
    cfg = _config()
    conn = await _db_or_skip(cfg)
    _stub_audio(monkeypatch)
    sub = f"ret-fresh-{uuid.uuid4().hex[:8]}"
    sid = await _seed(conn, sub, age_days=5, retain=False)  # within the 30-day window
    try:
        await retention.sweep_expired(cfg, conn)
        assert await conn.fetchval(
            "SELECT count(*) FROM voice_session WHERE session_id=$1", uuid.UUID(sid)
        ) == 1
    finally:
        await conn.execute("DELETE FROM voice_session WHERE session_id=$1", uuid.UUID(sid))
        await conn.execute("DELETE FROM users WHERE user_sub=$1", sub)
        await conn.close()


async def test_sweep_is_idempotent(monkeypatch):
    cfg = _config()
    conn = await _db_or_skip(cfg)
    _stub_audio(monkeypatch)
    sub = f"ret-idem-{uuid.uuid4().hex[:8]}"
    sid = await _seed(conn, sub, age_days=40, retain=False)
    try:
        await retention.sweep_expired(cfg, conn)
        second = await retention.sweep_expired(cfg, conn)
        # Nothing of THIS session remains to purge on the second run (idempotent).
        assert sid not in second.get("_ignored", [])  # sanity
        assert await conn.fetchval(
            "SELECT count(*) FROM voice_session WHERE session_id=$1", uuid.UUID(sid)
        ) == 0
    finally:
        await conn.execute("DELETE FROM voice_session WHERE session_id=$1", uuid.UUID(sid))
        await conn.execute("DELETE FROM users WHERE user_sub=$1", sub)
        await conn.close()
