"""F006 (G6) delete fan-out tests: zero-residual across S3 audio + RDS rows (SC-002).

Runs under the voice-worker venv against the local pgvector container (DATABASE_URL). Skipped if no DB.
The S3 side (audio_store.delete_objects / delete_session_prefix) is stubbed to record what it was asked
to delete — so these prove the fan-out CALLS S3 (by uri + by session prefix) and removes all RDS rows,
that consent-off sessions still delete cleanly, and that consent-revoke purges audio + NULLs uris.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio


def _spy_audio_store(monkeypatch):
    """Patch audio_store's S3 calls (imported lazily inside db) to record calls; returns the record."""
    from src import audio_store

    rec = {"objects": [], "prefixes": []}
    monkeypatch.setattr(audio_store, "delete_objects",
                        lambda uris: (rec["objects"].extend(uris), len(uris))[1])
    monkeypatch.setattr(audio_store, "delete_session_prefix",
                        lambda sid: (rec["prefixes"].append(sid), 1)[1])
    return rec


async def _seed_session_with_audio(pool, session_id: str, owner: str, n_audio: int) -> None:
    sid = uuid.UUID(session_id)
    await pool.execute(
        "INSERT INTO voice_session (session_id, user_sub, reply_provider, consent_store_materials) "
        "VALUES ($1,$2,'bedrock_direct',TRUE)",
        sid, owner,
    )
    for i in range(n_audio):
        await pool.execute(
            "INSERT INTO conversation_turn (turn_id, session_id, turn_index, speaker, transcript, "
            "started_at, audio_uri) VALUES ($1,$2,$3,'student','hi',now(),$4)",
            uuid.uuid4(), sid, i, f"s3://audio-bkt/audio/{session_id}/turn-{i}.wav",
        )


async def test_session_delete_purges_audio_and_rows_zero_residual(pool, monkeypatch):
    from src import db

    rec = _spy_audio_store(monkeypatch)
    sid = str(uuid.uuid4())
    await _seed_session_with_audio(pool, sid, "owner-a", n_audio=3)

    counts = await db.delete_session_cascade(sid, "owner-a")
    assert counts is not None
    assert counts["audio_objects"] >= 3  # 3 by uri + 1 prefix sweep
    # S3 was asked to delete the 3 recorded uris AND the session prefix (race-safe, FR-016).
    assert len(rec["objects"]) == 3 and sid in rec["prefixes"]
    # Zero RDS residual.
    residual = await pool.fetchval(
        "SELECT count(*) FROM conversation_turn WHERE session_id=$1", uuid.UUID(sid)
    )
    assert residual == 0
    assert await pool.fetchval("SELECT count(*) FROM voice_session WHERE session_id=$1", uuid.UUID(sid)) == 0


async def test_account_delete_purges_all_sessions_audio(pool, monkeypatch):
    from src import db

    rec = _spy_audio_store(monkeypatch)
    s1, s2 = str(uuid.uuid4()), str(uuid.uuid4())
    await _seed_session_with_audio(pool, s1, "owner-b", n_audio=2)
    await _seed_session_with_audio(pool, s2, "owner-b", n_audio=1)
    try:
        counts = await db.delete_user_cascade("owner-b")
        assert counts["sessions"] == 2
        assert len(rec["objects"]) == 3  # 2 + 1 audio uris across both sessions
        assert s1 in rec["prefixes"] and s2 in rec["prefixes"]
        for sid in (s1, s2):
            assert await pool.fetchval(
                "SELECT count(*) FROM voice_session WHERE session_id=$1", uuid.UUID(sid)
            ) == 0
    finally:
        for sid in (s1, s2):
            await pool.execute("DELETE FROM voice_session WHERE session_id=$1", uuid.UUID(sid))


async def test_consent_off_session_deletes_cleanly_no_audio(pool, monkeypatch):
    from src import db

    rec = _spy_audio_store(monkeypatch)
    sid = str(uuid.uuid4())
    await pool.execute(
        "INSERT INTO voice_session (session_id, user_sub, reply_provider, consent_store_materials) "
        "VALUES ($1,'owner-c','bedrock_direct',FALSE)",
        uuid.UUID(sid),
    )
    await pool.execute(
        "INSERT INTO conversation_turn (turn_id, session_id, turn_index, speaker, transcript, "
        "started_at, audio_uri) VALUES ($1,$2,0,'student','hi',now(),NULL)",
        uuid.uuid4(), uuid.UUID(sid),
    )
    counts = await db.delete_session_cascade(sid, "owner-c")
    assert counts is not None
    assert len(rec["objects"]) == 0          # no recorded audio uris
    assert sid in rec["prefixes"]            # prefix sweep still runs (harmless, race-safe)
    assert counts["audio_objects"] == 1      # the (no-op-in-real) prefix delete counts 1 in the spy


async def test_consent_revoke_purges_audio_and_nulls_uris(pool, monkeypatch):
    from src import db

    rec = _spy_audio_store(monkeypatch)
    sid = str(uuid.uuid4())
    await _seed_session_with_audio(pool, sid, "owner-d", n_audio=2)
    try:
        await db.set_consent("owner-d", consent_store_materials=False, retention_days=30)
        assert len(rec["objects"]) == 2 and sid in rec["prefixes"]
        # audio_uri NULLed for all the user's turns (store never holds audio without consent).
        remaining = await pool.fetchval(
            "SELECT count(*) FROM conversation_turn ct JOIN voice_session vs ON vs.session_id=ct.session_id "
            "WHERE vs.user_sub='owner-d' AND ct.audio_uri IS NOT NULL"
        )
        assert remaining == 0
    finally:
        await pool.execute("DELETE FROM voice_session WHERE session_id=$1", uuid.UUID(sid))
        await pool.execute("DELETE FROM users WHERE user_sub='owner-d'")


async def test_non_owner_session_delete_returns_none(pool):
    from src import db

    sid = str(uuid.uuid4())
    await _seed_session_with_audio(pool, sid, "owner-e", n_audio=1)
    try:
        assert await db.delete_session_cascade(sid, "someone-else") is None  # 404, no delete
        assert await pool.fetchval(
            "SELECT count(*) FROM voice_session WHERE session_id=$1", uuid.UUID(sid)
        ) == 1  # untouched
    finally:
        await pool.execute("DELETE FROM voice_session WHERE session_id=$1", uuid.UUID(sid))
