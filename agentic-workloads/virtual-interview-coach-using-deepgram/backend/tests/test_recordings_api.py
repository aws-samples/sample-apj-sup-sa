"""F006 (G6) playback tests: owner-scoped short-lived signed URL; no cross-user/public access.

Runs under the voice-worker venv against the local pgvector container (DATABASE_URL). Skipped if no DB.
Asserts the BACKEND side: an owner gets a presigned URL for a recorded turn; a non-owner is refused
(404, no leak); a turn with no audio yields {"available": false}; and the URL is never logged (SC-006).
The S3 presign + bucket config are stubbed so the test is offline.
"""

from __future__ import annotations

import dataclasses
import uuid

import pytest

pytestmark = pytest.mark.asyncio


def _with_settings(monkeypatch, **overrides):
    """Settings is a frozen dataclass — replace audio_playback.settings with a modified copy."""
    from src import audio_playback

    patched = dataclasses.replace(audio_playback.settings, **overrides)
    monkeypatch.setattr(audio_playback, "settings", patched)
    return patched


async def _seed_turn(pool, session_id: str, owner: str, audio_uri: str | None) -> str:
    sid = uuid.UUID(session_id)
    await pool.execute(
        "INSERT INTO voice_session (session_id, user_sub, reply_provider) VALUES ($1,$2,'bedrock_direct')",
        sid, owner,
    )
    tid = uuid.uuid4()
    await pool.execute(
        "INSERT INTO conversation_turn (turn_id, session_id, turn_index, speaker, transcript, "
        "started_at, audio_uri) VALUES ($1,$2,0,'student','hi', now(), $3)",
        tid, sid, audio_uri,
    )
    return str(tid)


async def test_owner_gets_presigned_url_for_recorded_turn(pool, monkeypatch):
    from src import audio_playback, db

    _with_settings(monkeypatch, audio_bucket="audio-bkt", audio_url_ttl_s=300)
    monkeypatch.setattr(audio_playback, "presign_get", lambda uri: "https://s3.example/signed?token=xyz")

    sid = str(uuid.uuid4())
    tid = await _seed_turn(pool, sid, "owner-a", f"s3://audio-bkt/audio/{sid}/{uuid.uuid4()}.wav")
    try:
        owned = await db.get_turn_audio_uri_for_owner(sid, tid, "owner-a")
        assert owned is not None and owned["audio_uri"].startswith("s3://audio-bkt/")
        body = audio_playback.build_playback(owned["audio_uri"])
        assert body["available"] is True
        assert body["expires_in"] == 300
        assert body["url"].startswith("https://")
    finally:
        await pool.execute("DELETE FROM voice_session WHERE session_id=$1", uuid.UUID(sid))


async def test_non_owner_is_refused_no_leak(pool):
    from src import db

    sid = str(uuid.uuid4())
    tid = await _seed_turn(pool, sid, "owner-a", f"s3://audio-bkt/audio/{sid}/x.wav")
    try:
        # A different user gets None (the endpoint maps this to 404 — no existence leak, FR-009).
        assert await db.get_turn_audio_uri_for_owner(sid, tid, "owner-b") is None
    finally:
        await pool.execute("DELETE FROM voice_session WHERE session_id=$1", uuid.UUID(sid))


async def test_turn_without_audio_is_unavailable(pool, monkeypatch):
    from src import audio_playback, db

    _with_settings(monkeypatch, audio_bucket="audio-bkt")

    sid = str(uuid.uuid4())
    tid = await _seed_turn(pool, sid, "owner-a", None)  # consent off / not yet uploaded -> NULL
    try:
        owned = await db.get_turn_audio_uri_for_owner(sid, tid, "owner-a")
        assert owned == {"audio_uri": None}
        assert audio_playback.build_playback(owned["audio_uri"]) == {"available": False}
    finally:
        await pool.execute("DELETE FROM voice_session WHERE session_id=$1", uuid.UUID(sid))


async def test_build_playback_unconfigured_bucket_is_unavailable(monkeypatch):
    from src import audio_playback

    _with_settings(monkeypatch, audio_bucket=None)  # local/dev
    assert audio_playback.build_playback("s3://anything/audio/s/t.wav") == {"available": False}


async def test_presign_url_not_logged(pool, monkeypatch, caplog):
    from src import audio_playback

    _with_settings(monkeypatch, audio_bucket="audio-bkt")
    secret_url = "https://s3.example/signed?X-Amz-Signature=DEADBEEF"
    monkeypatch.setattr(audio_playback, "presign_get", lambda uri: secret_url)
    with caplog.at_level("INFO"):
        body = audio_playback.build_playback("s3://audio-bkt/audio/s/t.wav")
    assert body["url"] == secret_url
    assert "DEADBEEF" not in caplog.text  # SC-006: the signed URL must never reach the logs


async def test_presign_uses_sigv4(monkeypatch):
    """F008 US3 root-cause regression: the audio objects are SSE-KMS, and S3 REJECTS non-SigV4
    presigned GETs for them (400 InvalidArgument — the live silent-playback bug). The minted URL
    must carry the SigV4 marker (X-Amz-Algorithm=AWS4-HMAC-SHA256). Offline: a real botocore
    client signs locally with dummy credentials; no network call is made by presigning."""
    from src import audio_playback

    _with_settings(monkeypatch, audio_bucket="audio-bkt", audio_url_ttl_s=300)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    url = audio_playback.presign_get("s3://audio-bkt/audio/s/t.wav")
    assert url is not None
    assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in url  # SigV4, valid for SSE-KMS objects
