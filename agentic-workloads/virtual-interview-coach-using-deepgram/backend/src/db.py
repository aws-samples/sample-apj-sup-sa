"""Backend DB access (T009) — connection pool + read queries for the G1 slice.

Writes during session create are done here; the voice worker owns the live-loop writes
(turns, latency, terminal state). Reads back sessions, transcripts, and the latency verdict
for the harness and SPA (contracts/session-api.md).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import asyncpg

from .config import settings

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL is not configured")
        kwargs = {"min_size": 1, "max_size": 5}
        if settings.db_secret_arn:
            # Rotation-proof: the pool fetches the live password per new connection from Secrets
            # Manager, so an RDS password rotation is picked up without restarting the task.
            from .db_secret import make_password_provider

            kwargs["password"] = make_password_provider(
                settings.db_secret_arn, settings.aws_region
            )
        _pool = await asyncpg.create_pool(settings.database_url, **kwargs)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --- users (F002 / G2) -------------------------------------------------------------------
# The product user profile. Provisioned lazily from the Cognito sub already carried on
# voice_session.user_sub, so a returning G1 identity simply gains a profile row. This is the
# sole durable home (with S3) for raw personal data (Constitution III). All of this runs in the
# setup/prep window — never on the response_gap clock.


async def get_or_create_user(user_sub: str, email: str | None = None) -> dict:
    """Return the user's profile row, creating it on first sight of this Cognito sub.

    Idempotent: ON CONFLICT(user_sub) leaves an existing profile untouched (so consent and
    resume facts persist across sessions). Returns the full row as a dict.
    """
    pool = await get_pool()
    user_id = str(uuid.uuid4())
    row = await pool.fetchrow(
        """
        INSERT INTO users (id, user_sub, email, created_at)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (user_sub) DO UPDATE
            SET email = COALESCE(users.email, EXCLUDED.email)
        RETURNING id, user_sub, email, age_attested, consent_recording,
                  consent_recording_at, retention_days, resume_uri, resume_parsed_facts,
                  resume_confirmed_at, role
        """,
        user_id,
        user_sub,
        email,
        _utcnow(),
    )
    result = dict(row)
    result["id"] = str(result["id"])
    return result


async def set_consent(
    user_sub: str, consent_store_materials: bool, retention_days: int = 30
) -> dict:
    """Set the consent flag that gates storing raw materials (FR-220) + retention (FR-217).

    On revoke (consent_store_materials = FALSE) the stored resume materials are purged here so
    the DB never holds materials without consent. F006 (G6): revoking ALSO purges the user's stored
    audio objects and NULLs their conversation_turn.audio_uri (FR-017) — the store never holds audio
    without active consent. Returns the consent view.
    """
    from . import audio_store

    await get_or_create_user(user_sub)
    pool = await get_pool()

    # On revoke, purge stored audio (S3) before NULLing the uris (privacy-safe ordering).
    if not consent_store_materials:
        uri_rows = await pool.fetch(
            "SELECT ct.audio_uri, vs.session_id FROM conversation_turn ct "
            "JOIN voice_session vs ON vs.session_id = ct.session_id "
            "WHERE vs.user_sub = $1 AND ct.audio_uri IS NOT NULL",
            user_sub,
        )
        audio_store.delete_objects([r["audio_uri"] for r in uri_rows])
        for sid in {str(r["session_id"]) for r in uri_rows}:
            audio_store.delete_session_prefix(sid)
        await pool.execute(
            "UPDATE conversation_turn ct SET audio_uri = NULL "
            "FROM voice_session vs "
            "WHERE ct.session_id = vs.session_id AND vs.user_sub = $1 AND ct.audio_uri IS NOT NULL",
            user_sub,
        )

    now = _utcnow()
    row = await pool.fetchrow(
        """
        UPDATE users
           SET consent_recording = $2,
               consent_recording_at = CASE WHEN $2 THEN $3::timestamptz ELSE NULL END,
               retention_days = $4,
               -- Revoking consent purges stored materials (raw S3 deletion is the caller's job).
               resume_uri = CASE WHEN $2 THEN resume_uri ELSE NULL END,
               resume_parsed_facts = CASE WHEN $2 THEN resume_parsed_facts ELSE NULL END,
               resume_confirmed_at = CASE WHEN $2 THEN resume_confirmed_at ELSE NULL END
         WHERE user_sub = $1
        RETURNING consent_recording, retention_days, consent_recording_at, resume_uri
        """,
        user_sub,
        consent_store_materials,
        now,
        retention_days,
    )
    return dict(row)


async def get_user_by_sub(user_sub: str) -> dict | None:
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT id, user_sub, email, age_attested, consent_recording, consent_recording_at,
               retention_days, resume_uri, resume_parsed_facts, resume_confirmed_at, role
          FROM users WHERE user_sub = $1
        """,
        user_sub,
    )
    if row is None:
        return None
    result = dict(row)
    result["id"] = str(result["id"])
    if result.get("resume_parsed_facts") is not None:
        result["resume_parsed_facts"] = json.loads(result["resume_parsed_facts"])
    return result


async def set_resume(user_sub: str, resume_uri: str, parsed_facts: dict) -> None:
    """Record the uploaded resume's S3 path + the (unconfirmed) parsed facts (FR-201).

    Requires prior consent — enforced by the API (409) and by the DB CHECK
    (users_consent_gates_resume_chk: no consent => resume_uri/parsed_facts MUST be NULL).
    resume_confirmed_at is cleared because the new parse is not yet confirmed.
    """
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE users
           SET resume_uri = $2,
               resume_parsed_facts = $3::jsonb,
               resume_confirmed_at = NULL
         WHERE user_sub = $1
        """,
        user_sub,
        resume_uri,
        json.dumps(parsed_facts),
    )


async def confirm_resume(user_sub: str, parsed_facts: dict) -> datetime:
    """Make the confirmed/corrected facts authoritative for grounding (FR-204).

    Sets resume_confirmed_at; the confirmed facts (manual entry or edited parse) overwrite the
    raw parse. Returns the confirmation timestamp.
    """
    pool = await get_pool()
    now = _utcnow()
    await pool.execute(
        """
        UPDATE users
           SET resume_parsed_facts = $3::jsonb,
               resume_confirmed_at = $2
         WHERE user_sub = $1
        """,
        user_sub,
        now,
        json.dumps(parsed_facts),
    )
    return now


# --- audio playback lookup (F006 / G6) ---------------------------------------------------


async def session_owned_by(session_id: str, user_sub: str) -> bool:
    """True iff the session exists AND is owned by user_sub. The authorization primitive for every
    session-scoped read/write endpoint (sessions read/latency/report/end) — prevents IDOR: one
    authenticated user reaching another's transcript/scores/latency or ending their session. A
    malformed session_id (not a UUID) is treated as not-owned rather than raising."""
    pool = await get_pool()
    try:
        owner = await pool.fetchval(
            "SELECT user_sub FROM voice_session WHERE session_id = $1", session_id
        )
    except Exception:  # noqa: BLE001 - a malformed id is simply not-found, not a 500
        return False
    return owner is not None and owner == user_sub


async def get_turn_audio_uri_for_owner(
    session_id: str, turn_id: str, user_sub: str
) -> dict | None:
    """Owner-scoped lookup for per-answer playback (FR-009). Returns None when the session does not
    exist OR is not owned by user_sub (the API maps None -> 404, no existence leak). Returns
    {"audio_uri": <str|None>} when the caller owns the session — audio_uri NULL means no recording
    for that turn (FR-010)."""
    pool = await get_pool()
    owner = await pool.fetchval(
        "SELECT user_sub FROM voice_session WHERE session_id = $1", session_id
    )
    if owner is None or owner != user_sub:
        return None
    uri = await pool.fetchval(
        "SELECT audio_uri FROM conversation_turn WHERE turn_id = $1 AND session_id = $2",
        turn_id,
        session_id,
    )
    return {"audio_uri": uri}


# --- functional delete (FR-219) ----------------------------------------------------------
# Bounded-blast-radius hard delete, off the response_gap clock. A session delete removes the
# session and everything that cascades from it (turns, latency, blueprint via ON DELETE CASCADE).
# An account delete removes ALL of the user's sessions first (voice_session.user_id -> users(id)
# has no cascade), then the users profile row. The raw S3 resume object is deleted by the API
# layer (resume_store.delete_resume); this module owns only the RDS side.


async def delete_session_cascade(session_id: str, user_sub: str) -> dict | None:
    """Hard-delete one session owned by user_sub. Returns deleted-row counts, or None if the
    session does not exist or is not owned by this user (the API maps None -> 404).

    F006 (G6): the fan-out now also purges the session's S3 audio objects, BEFORE the RDS cascade
    removes the rows (S3-before-RDS = the privacy-safe failure direction, research R6). The audio
    objects are deleted both by the uris recorded in conversation_turn AND by the audio/{session}/
    prefix, so an upload still in flight (uri not yet written) is caught too (FR-016)."""
    from . import audio_store

    pool = await get_pool()
    # Ownership check + collect audio uris first (read-only), so we can delete S3 BEFORE the RDS rows.
    owner = await pool.fetchval(
        "SELECT user_sub FROM voice_session WHERE session_id = $1", session_id
    )
    if owner is None or owner != user_sub:
        return None
    uri_rows = await pool.fetch(
        "SELECT audio_uri FROM conversation_turn WHERE session_id = $1 AND audio_uri IS NOT NULL",
        session_id,
    )
    audio_objects = audio_store.delete_objects([r["audio_uri"] for r in uri_rows])
    audio_objects += audio_store.delete_session_prefix(session_id)

    async with pool.acquire() as conn:
        async with conn.transaction():
            turns = await conn.fetchval(
                "SELECT count(*) FROM conversation_turn WHERE session_id = $1", session_id
            )
            blueprints = await conn.fetchval(
                "SELECT count(*) FROM interview_blueprint WHERE session_id = $1", session_id
            )
            # F003: report rows cascade on session delete too; count them for audit (SC-008).
            reports = await conn.fetchval(
                "SELECT count(*) FROM report WHERE session_id = $1", session_id
            )
            feedbacks = await conn.fetchval(
                "SELECT count(*) FROM question_feedback WHERE session_id = $1", session_id
            )
            report_jobs = await conn.fetchval(
                "SELECT count(*) FROM report_job WHERE session_id = $1", session_id
            )
            # ON DELETE CASCADE removes conversation_turn, turn_latency, interview_blueprint, and the
            # F003 report / question_feedback / report_job rows (all FK -> voice_session).
            await conn.execute("DELETE FROM voice_session WHERE session_id = $1", session_id)
    return {
        "sessions": 1, "turns": int(turns or 0), "blueprints": int(blueprints or 0),
        "reports": int(reports or 0), "question_feedback": int(feedbacks or 0),
        "report_jobs": int(report_jobs or 0), "audio_objects": int(audio_objects),
    }


async def delete_user_cascade(user_sub: str) -> dict:
    """Hard-delete the account's PII from RDS: all of the user's sessions (cascading to turns,
    latency, blueprints) and the users profile row. Returns deleted-row counts. Idempotent: a
    missing user yields zero counts. The raw S3 resume object is deleted by the caller.

    F006 (G6): also purges the S3 audio objects of EVERY session the user owns, BEFORE the RDS
    cascade (per-uri + per-session-prefix, race-safe — FR-015/FR-016)."""
    from . import audio_store

    pool = await get_pool()
    # Collect session ids + audio uris first (read-only), delete S3 before the RDS rows.
    session_rows = await pool.fetch(
        "SELECT session_id FROM voice_session WHERE user_sub = $1", user_sub
    )
    session_ids = [str(r["session_id"]) for r in session_rows]
    uri_rows = await pool.fetch(
        "SELECT ct.audio_uri FROM conversation_turn ct "
        "JOIN voice_session vs ON vs.session_id = ct.session_id "
        "WHERE vs.user_sub = $1 AND ct.audio_uri IS NOT NULL",
        user_sub,
    )
    audio_objects = audio_store.delete_objects([r["audio_uri"] for r in uri_rows])
    for sid in session_ids:
        audio_objects += audio_store.delete_session_prefix(sid)

    async with pool.acquire() as conn:
        async with conn.transaction():
            turns = await conn.fetchval(
                "SELECT count(*) FROM conversation_turn ct "
                "JOIN voice_session vs ON vs.session_id = ct.session_id "
                "WHERE vs.user_sub = $1",
                user_sub,
            )
            # Sessions first: voice_session.user_id -> users(id) has no ON DELETE clause.
            await conn.execute("DELETE FROM voice_session WHERE user_sub = $1", user_sub)
            # F008: the derived cross-session coaching guidance is part of the bounded blast
            # radius (Constitution III) — no FK, so it is removed explicitly here.
            await conn.execute("DELETE FROM coaching_guidance WHERE user_sub = $1", user_sub)
            status = await conn.execute("DELETE FROM users WHERE user_sub = $1", user_sub)
    deleted_users = int(status.split()[-1]) if status else 0
    return {
        "sessions": len(session_ids), "turns": int(turns or 0), "users": deleted_users,
        "audio_objects": int(audio_objects),
    }


async def get_guidance(user_sub: str) -> dict | None:
    """The caller's current cross-session coaching guidance (F008 US4); None when none exists.
    JSONB columns come back as strings from asyncpg — decode them here so the API serializes
    real arrays (the same defense get_report applies to its JSONB columns)."""
    pool = await get_pool()
    row = await pool.fetchrow(
        "SELECT generated_at, sessions_analyzed, strengths, improvement_areas, trend_note, "
        "next_actions FROM coaching_guidance WHERE user_sub = $1",
        user_sub,
    )
    if row is None:
        return None
    out = dict(row)
    for k in ("strengths", "improvement_areas", "next_actions"):
        if isinstance(out.get(k), str):
            out[k] = json.loads(out[k])
    return out


# --- session-prep blueprint (FR-208) -----------------------------------------------------
# Written in the prep window, before the turn clock starts. Persists the JD-ranked plan so the
# live loop walks a fixed queue (no DB on the gap clock). No scores (F002).


async def create_blueprint(
    session_id: str,
    target_competencies: list[str],
    ordered_archetype_ids: list[str],
    opening_archetype_id: str | None,
) -> str:
    """Persist the per-session interview_blueprint; returns its id. UUID[] columns take real
    uuid.UUID objects, so archetype-id strings are coerced here."""
    pool = await get_pool()
    blueprint_id = str(uuid.uuid4())
    ordered = [uuid.UUID(a) for a in ordered_archetype_ids]
    opening = uuid.UUID(opening_archetype_id) if opening_archetype_id else None
    await pool.execute(
        """
        INSERT INTO interview_blueprint
            (id, session_id, target_competencies, ordered_archetype_ids, opening_archetype_id, created_at)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        uuid.UUID(blueprint_id),
        uuid.UUID(session_id),
        target_competencies,
        ordered,
        opening,
        _utcnow(),
    )
    return blueprint_id


async def set_session_plan(
    session_id: str,
    archetype_ids: list[str],
    blueprint_id: str,
    domain_coverage_reduced: bool,
) -> None:
    """Link the assembled plan onto voice_session (archetype_ids, blueprint_id) and record the
    honest niche-role fallback flag (FR-222)."""
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE voice_session
           SET archetype_ids = $2,
               blueprint_id = $3,
               domain_coverage_reduced = $4
         WHERE session_id = $1
        """,
        uuid.UUID(session_id),
        [uuid.UUID(a) for a in archetype_ids],
        uuid.UUID(blueprint_id),
        domain_coverage_reduced,
    )


async def create_session(
    user_sub: str,
    reply_provider: str,
    *,
    user_id: str | None = None,
    job_title: str | None = None,
    job_description: str | None = None,
    difficulty: str | None = None,
    consent_store_materials: bool = False,
    rubric_version: str | None = None,
    duration_minutes: int | None = None,
) -> str:
    """Create a voice_session. G1 callers pass just (user_sub, reply_provider); F002 personalized
    sessions also carry the user_id link + job scope + difficulty + the consent flag + the pinned
    rubric_version (additive, all nullable/defaulted so the G1 path is unchanged). The difficulty
    tier and rubric_version are recorded as their own columns — never blended into a composite
    (FR-215 / Principle II). `duration_minutes` is the student's chosen length; the worker bounds the
    live session to it so a slow funnel can't overrun (duration gating). The blueprint is attached
    afterwards by set_session_plan once prep assembles it."""
    session_id = str(uuid.uuid4())
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO voice_session
            (session_id, user_sub, created_at, reply_provider,
             user_id, job_title, job_description, difficulty, consent_store_materials, rubric_version,
             duration_minutes)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
        """,
        uuid.UUID(session_id),
        user_sub,
        _utcnow(),
        reply_provider,
        uuid.UUID(user_id) if user_id else None,
        job_title,
        job_description,
        difficulty,
        consent_store_materials,
        rubric_version,
        duration_minutes,
    )
    return session_id


async def end_session(session_id: str, end_reason: str = "student_ended") -> dict | None:
    pool = await get_pool()
    await pool.execute(
        """
        UPDATE voice_session
           SET ended_at = COALESCE(ended_at, $2),
               end_reason = COALESCE(end_reason, $3)
         WHERE session_id = $1
        """,
        session_id,
        _utcnow(),
        end_reason,
    )
    return await get_session(session_id)


async def list_sessions_for_owner(user_sub: str) -> list[dict]:
    """The owner's practice sessions, newest first, for the F008 session picker (US1).

    One query: voice_session owner-filtered (the F008 owner/created index pins this path) LEFT
    JOINed to report_job so the SPA can label each entry without N+1 report polls. Sessions that
    never reached the interview (zero turns AND no report job) are omitted — abandoned setup
    attempts, per the spec's assumptions. Owner scoping is the WHERE clause itself: there is no
    id parameter to probe (SC-006).
    """
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT vs.session_id, vs.created_at, vs.ended_at, vs.end_reason,
               vs.job_title, vs.difficulty, vs.duration_minutes,
               COALESCE(rj.status, 'none') AS report_status
          FROM voice_session vs
          LEFT JOIN report_job rj ON rj.session_id = vs.session_id
         WHERE vs.user_sub = $1
           AND (vs.turn_count > 0 OR rj.id IS NOT NULL)
         ORDER BY vs.created_at DESC
        """,
        user_sub,
    )
    return [{**dict(r), "session_id": str(r["session_id"])} for r in rows]


async def get_session(session_id: str) -> dict | None:
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT session_id, end_reason, network_path, reply_provider,
               started_at, ended_at, turn_count, difficulty, rubric_version
          FROM voice_session WHERE session_id = $1
        """,
        session_id,
    )
    if row is None:
        return None
    turns = await pool.fetch(
        """
        SELECT turn_id, turn_index, speaker, transcript, interrupted,
               (audio_uri IS NOT NULL) AS has_audio
          FROM conversation_turn WHERE session_id = $1 ORDER BY turn_index
        """,
        session_id,
    )
    result = dict(row)
    result["session_id"] = str(result["session_id"])
    # F006: expose turn_id + has_audio so the SPA can render a per-answer play control (the actual
    # signed URL is minted separately, owner-scoped, by GET .../turns/{turn_id}/audio-url). audio_uri
    # itself is NOT exposed here — only whether a recording exists.
    result["turns"] = [
        {**dict(t), "turn_id": str(t["turn_id"])} for t in turns
    ]
    return result


async def enqueue_report_job(session_id: str) -> None:
    """Insert a queued report_job for the session (idempotent — one job per session). Paired with the
    SQS send in queue.enqueue_report; ON CONFLICT DO NOTHING makes a duplicate /end harmless (F003)."""
    import uuid as _uuid

    pool = await get_pool()
    await pool.execute(
        "INSERT INTO report_job (id, session_id, status) VALUES ($1, $2, 'queued') "
        "ON CONFLICT (session_id) DO NOTHING",
        _uuid.uuid4(),
        session_id,
    )


async def get_report(session_id: str) -> dict | None:
    """Return the report + its question_feedback for retrieval. None if no report job exists yet;
    a job row with no scored report yet surfaces as a processing/failed status (F003 / FR-309)."""
    pool = await get_pool()
    job = await pool.fetchrow(
        "SELECT status FROM report_job WHERE session_id = $1", session_id
    )
    rep = await pool.fetchrow(
        """
        SELECT id, status, overall, score_content, score_structure, score_communication,
               score_confidence, difficulty, rubric_version, summary_strengths, summary_improvements,
               metrics, competency_scorecard, scoring_model, generated_at
          FROM report WHERE session_id = $1
        """,
        session_id,
    )
    if job is None and rep is None:
        return None
    if rep is None:
        # job exists but not scored yet -> honest processing/failed status, no half-built report
        return {"status": job["status"] if job else "processing", "report": None}
    report = dict(rep)
    report["id"] = str(report["id"])
    # asyncpg returns JSONB columns as text; decode them so the client gets real arrays/objects.
    import json as _json

    for k in ("summary_strengths", "summary_improvements", "metrics", "competency_scorecard"):
        v = report.get(k)
        if isinstance(v, str):
            report[k] = _json.loads(v)
    # NUMERIC score columns come back as Decimal, which FastAPI serializes as a JSON STRING ("7.00").
    # The SPA expects numbers (it calls .toFixed on them), so coerce to float (None stays None).
    from decimal import Decimal as _Decimal

    for k in ("overall", "score_content", "score_structure", "score_communication", "score_confidence"):
        v = report.get(k)
        if isinstance(v, _Decimal):
            report[k] = float(v)
    feedbacks = await pool.fetch(
        """
        SELECT turn_index, archetype_id, competency, question_text, student_transcript,
               what_worked, what_to_improve, strong_answer_example, q_score, star_coverage, evidence_quote
          FROM question_feedback WHERE session_id = $1 ORDER BY turn_index
        """,
        session_id,
    )
    fb_list = []
    for f in feedbacks:
        d = dict(f)
        if isinstance(d.get("star_coverage"), str):
            d["star_coverage"] = _json.loads(d["star_coverage"])
        if d.get("archetype_id") is not None:
            d["archetype_id"] = str(d["archetype_id"])
        fb_list.append(d)
    report["question_feedback"] = fb_list
    return {"status": rep["status"], "report": report}


async def get_latency(session_id: str) -> dict | None:
    pool = await get_pool()
    sess = await pool.fetchrow(
        "SELECT reply_provider, network_path FROM voice_session WHERE session_id = $1",
        session_id,
    )
    if sess is None:
        return None
    rows = await pool.fetch(
        """
        SELECT ct.turn_index, tl.response_gap_ms, tl.stt_finalization_ms,
               tl.reply_ttft_ms, tl.tts_first_audio_ms, tl.orchestration_ms
          FROM turn_latency tl
          JOIN conversation_turn ct ON ct.turn_id = tl.turn_id
         WHERE tl.session_id = $1
         ORDER BY ct.turn_index
        """,
        session_id,
    )
    return {
        "session_id": session_id,
        "reply_provider": sess["reply_provider"],
        "network_path": sess["network_path"],
        "turns": [dict(r) for r in rows],
    }
