"""SC-007 privacy gate evidence (T042) — quickstart "Also verify" / FR-219 / Constitution III.

Two machine-checkable guarantees behind privacy-by-architecture:

  no-residual: after DELETE /me (db.delete_user_cascade) every RDS row keyed to the account is
               gone — the users profile (the sole durable home of resume facts/uri), all the
               account's voice_sessions, and their conversation_turns + interview_blueprints (via
               ON DELETE CASCADE). A second delete is idempotent (zero counts).

  derived-store holds no raw PII: conversation_turn — the only per-turn record F002 writes — carries
               structural facts only (archetype_id, is_followup, targeted_star_element). The raw
               interview content (transcript) is the candidate's own answer text in RDS, which the
               delete cascade removes; no separate derived-signal store mirrors raw resume/JD text.

The S3 resume-object delete is exercised by the API layer (resume_store.delete_resume) and is out of
scope for the DB test; it is covered in the audit doc (docs/g2-privacy-audit.md).

Offline; local pgvector container only; never touches deployed RDS.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.asyncio

_SUB = f"privacy-audit-{uuid.uuid5(uuid.UUID('6f4c0d2e-0000-4002-a010-0000000420ff'), 'sub')}"


@pytest.fixture
async def seeded_account(pool):
    """A full account footprint: user profile (with resume PII), a session, turns, a blueprint."""
    from src import db

    user = await db.get_or_create_user(_SUB, email="audit@example.test")
    user_id = user["id"]
    # Give the profile raw resume PII so we can prove it is gone after delete.
    await pool.execute(
        """
        UPDATE users SET consent_recording = TRUE, resume_uri = $2,
               resume_parsed_facts = $3::jsonb, resume_confirmed_at = now()
         WHERE user_sub = $1
        """,
        _SUB, "s3://local-unconfigured/resumes/test/resume.pdf",
        '{"name":"Test Candidate","summary":"raw pii here"}',
    )
    session_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO voice_session (session_id, user_sub, user_id, created_at, reply_provider,
                                   job_title, job_description, difficulty)
        VALUES ($1,$2,$3,now(),'bedrock_direct','Engineer','raw JD text','moderate')
        """,
        session_id, _SUB, uuid.UUID(user_id),
    )
    await pool.execute(
        """
        INSERT INTO conversation_turn
            (turn_id, session_id, turn_index, speaker, transcript, started_at,
             archetype_id, is_followup, targeted_star_element)
        VALUES ($1,$2,0,'student','my raw answer',now(), NULL, FALSE, 'action')
        """,
        uuid.uuid4(), session_id,
    )
    await pool.execute(
        """
        INSERT INTO interview_blueprint (id, session_id, target_competencies, ordered_archetype_ids,
                                         opening_archetype_id, created_at)
        VALUES ($1,$2,$3,$4,NULL,now())
        """,
        uuid.uuid4(), session_id, ["teamwork"], [],
    )
    yield {"user_sub": _SUB, "session_id": session_id}
    # Safety-net cleanup if a test left anything behind.
    await pool.execute("DELETE FROM voice_session WHERE user_sub = $1", _SUB)
    await pool.execute("DELETE FROM users WHERE user_sub = $1", _SUB)


async def test_delete_leaves_no_residual(seeded_account, pool):
    """DELETE /me removes every RDS row keyed to the account — no residual PII (FR-219 / SC-007)."""
    from src import db

    sub = seeded_account["user_sub"]
    sid = seeded_account["session_id"]

    deleted = await db.delete_user_cascade(sub)
    assert deleted["users"] == 1
    assert deleted["sessions"] >= 1

    # Nothing keyed to the account survives anywhere.
    assert await pool.fetchval("SELECT count(*) FROM users WHERE user_sub=$1", sub) == 0
    assert await pool.fetchval("SELECT count(*) FROM voice_session WHERE user_sub=$1", sub) == 0
    assert await pool.fetchval(
        "SELECT count(*) FROM conversation_turn WHERE session_id=$1", sid
    ) == 0
    assert await pool.fetchval(
        "SELECT count(*) FROM interview_blueprint WHERE session_id=$1", sid
    ) == 0
    # F008: the derived cross-session coaching guidance is inside the blast radius too (SC-006).
    assert await pool.fetchval(
        "SELECT count(*) FROM coaching_guidance WHERE user_sub=$1", sub
    ) == 0


async def test_delete_is_idempotent(seeded_account, pool):
    """A repeated delete yields zero counts (no error, no residual) — bounded blast radius."""
    from src import db

    sub = seeded_account["user_sub"]
    await db.delete_user_cascade(sub)
    again = await db.delete_user_cascade(sub)
    # F006: the fan-out now also reports audio_objects purged (0 on a repeat — nothing left).
    assert again == {"sessions": 0, "turns": 0, "users": 0, "audio_objects": 0}


async def test_consent_revoke_purges_resume_pii(seeded_account, pool):
    """Revoking consent NULLs the stored resume facts/uri so RDS holds no materials without consent."""
    from src import db

    sub = seeded_account["user_sub"]
    await db.set_consent(sub, consent_store_materials=False)
    row = await pool.fetchrow(
        "SELECT resume_uri, resume_parsed_facts, resume_confirmed_at FROM users WHERE user_sub=$1",
        sub,
    )
    assert row["resume_uri"] is None
    assert row["resume_parsed_facts"] is None
    assert row["resume_confirmed_at"] is None
