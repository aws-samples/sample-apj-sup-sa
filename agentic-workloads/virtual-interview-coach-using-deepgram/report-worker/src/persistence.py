"""Persistence + report-job lifecycle for the report worker (FR-309/313 / R6).

Loads a completed session's structural facts, runs the scorer + feedback + voice metrics, and writes
the report + question_feedback rows idempotently (one per session). The report_job is claimed with a
guarded status transition so duplicate SQS deliveries / two workers can't double-process, and a partial
session is scored over its answered questions rather than failing.

No raw transcript/resume text is logged (Principle III) — only ids, counts, and statuses.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

import asyncpg

from .config import Config
from .feedback import build_question_feedback
from .scorer import score_session
from .voice_metrics import compute_metrics

log = logging.getLogger("report_worker")


_password_provider = None  # built once from config.db_secret_arn; reused (its own TTL cache)


async def connect(config: Config) -> asyncpg.Connection:
    if not config.database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    # Rotation-proof: when a secret ARN is configured, asyncpg fetches the live password per connect
    # via the provider callable (the DSN is passwordless). A rotation is then transparent — every
    # fresh sweep/scoring connection picks up the new password without a restart.
    if config.db_secret_arn:
        global _password_provider
        if _password_provider is None:
            from .db_secret import make_password_provider

            _password_provider = make_password_provider(config.db_secret_arn, config.aws_region)
        return await asyncpg.connect(config.database_url, password=_password_provider)
    return await asyncpg.connect(config.database_url)


async def claim_job(conn: asyncpg.Connection, session_id: str) -> bool:
    """Guarded claim: move report_job queued|failed -> processing. Returns True if THIS worker won the
    claim (so it should process), False if already processing/scored (skip — idempotent). Creates the
    job row if absent (e.g. a direct/harness invocation without the enqueue step)."""
    async with conn.transaction():
        row = await conn.fetchrow(
            "SELECT status FROM report_job WHERE session_id = $1 FOR UPDATE", uuid.UUID(session_id)
        )
        if row is None:
            await conn.execute(
                "INSERT INTO report_job (id, session_id, status, attempts, started_at) "
                "VALUES ($1, $2, 'processing', 1, now())",
                uuid.uuid4(), uuid.UUID(session_id),
            )
            return True
        if row["status"] in ("queued", "failed"):
            await conn.execute(
                "UPDATE report_job SET status='processing', attempts = attempts + 1, started_at = now() "
                "WHERE session_id = $1",
                uuid.UUID(session_id),
            )
            return True
        return False  # already processing or scored -> another worker has it


async def finish_job(conn: asyncpg.Connection, session_id: str, status: str, error: str | None = None) -> None:
    await conn.execute(
        "UPDATE report_job SET status = $2, last_error = $3, finished_at = now() WHERE session_id = $1",
        uuid.UUID(session_id), status, error,
    )


async def load_session(conn: asyncpg.Connection, session_id: str) -> dict | None:
    """Load the session's structural facts needed to score it: difficulty + rubric_version, the ordered
    turns, the planned competencies, and the student's confirmed resume facts."""
    sess = await conn.fetchrow(
        "SELECT session_id, user_id, difficulty, rubric_version, archetype_ids, ended_at "
        "FROM voice_session WHERE session_id = $1",
        uuid.UUID(session_id),
    )
    if sess is None:
        return None
    turns = await conn.fetch(
        "SELECT turn_index, speaker, transcript, started_at, ended_at, archetype_id, is_followup, "
        "targeted_star_element FROM conversation_turn WHERE session_id = $1 ORDER BY turn_index",
        uuid.UUID(session_id),
    )
    # Competencies planned for this session (from the archetypes), for the scorecard.
    competencies: list[str] = []
    if sess["archetype_ids"]:
        crows = await conn.fetch(
            "SELECT DISTINCT competency FROM question_archetype WHERE id = ANY($1::uuid[])",
            list(sess["archetype_ids"]),
        )
        competencies = [r["competency"] for r in crows]
    # Confirmed resume facts for resume-grounded strong answers (read-only, under existing consent).
    resume_facts = None
    if sess["user_id"]:
        rf = await conn.fetchval("SELECT resume_parsed_facts FROM users WHERE id = $1", sess["user_id"])
        if isinstance(rf, str):
            rf = json.loads(rf)
        resume_facts = rf
    return {
        "session_id": session_id,
        "difficulty": sess["difficulty"],
        "rubric_version": sess["rubric_version"],
        "turns": [dict(t) for t in turns],
        "competencies": competencies,
        "resume_facts": resume_facts,
    }


def _qa_pairs(turns: list[dict]) -> list[dict]:
    """Pair each student answer with the immediately preceding coach question (its archetype/competency
    travel with the question)."""
    pairs: list[dict] = []
    ordered = sorted(turns, key=lambda t: t.get("turn_index", 0))
    last_q: dict | None = None
    for t in ordered:
        if t.get("speaker") == "coach":
            last_q = t
        elif t.get("speaker") == "student" and t.get("transcript", "").strip():
            pairs.append({
                "question_text": (last_q or {}).get("transcript", ""),
                "student_transcript": t.get("transcript", ""),
                "turn_index": t.get("turn_index"),
                "archetype_id": str(last_q["archetype_id"]) if last_q and last_q.get("archetype_id") else None,
                "competency": None,  # competency is looked up at scorecard time via archetype
            })
    return pairs


async def generate_report(conn: asyncpg.Connection, session_id: str, config: Config, *, score_fn=None, feedback_fn=None) -> str:
    """Full report generation for one session, called after the job is claimed. Writes report +
    question_feedback idempotently. Returns the final status ('scored'). Raises on unrecoverable error
    (the caller marks the job failed)."""
    data = await load_session(conn, session_id)
    if data is None:
        raise RuntimeError(f"session {session_id} not found")

    turns = data["turns"]
    student_turns = [t for t in turns if t.get("speaker") == "student" and t.get("transcript", "").strip()]
    if not student_turns:
        # Honest "insufficient material" report rather than fabricated scores (edge case / Principle II).
        await _write_report(conn, session_id, data, score=None, metrics={}, scorecard=[])
        log.info("session %s scored as insufficient-material (no student answers)", session_id)
        return "scored"

    score = score_session(turns, data["competencies"], config, score_fn=score_fn)
    metrics = compute_metrics(turns, long_pause_ms=config.long_pause_ms)
    report_id = await _write_report(conn, session_id, data, score=score, metrics=metrics,
                                    scorecard=[_sc_to_dict(c) for c in score.competencies])

    # Per-question feedback is a best-effort enrichment: the report (scores + evidence-anchored
    # scorecard) is already written and is the gate-critical content. A failure building or writing
    # feedback must NOT fail the whole report (which would mark the job failed and hide a good report).
    try:
        qa = _qa_pairs(turns)
        feedbacks = build_question_feedback(qa, data["resume_facts"], config, feedback_fn=feedback_fn)
        await _write_question_feedback(conn, report_id, session_id, feedbacks)
        n_fb = len(feedbacks)
    except Exception as exc:  # noqa: BLE001 - keep the scored report; feedback is non-critical
        log.warning("session %s question-feedback step failed (%s); report kept without per-Q feedback",
                    session_id, type(exc).__name__)
        n_fb = 0
    log.info("session %s scored: overall=%s, %d competenc(ies), %d question feedbacks",
             session_id, score.overall, len(score.competencies), n_fb)
    return "scored"


def _sc_to_dict(c) -> dict:
    return {
        "competency": c.competency,
        "score_1_5": c.score_1_5,
        "evidence_quote": c.evidence_quote,
        "star_element": c.star_element,
        "turn_index": c.turn_index,
        "assessed": c.assessed,
    }


async def _write_report(conn, session_id, data, *, score, metrics, scorecard) -> str:
    """Upsert the report row (one per session — idempotent). Returns the report id."""
    report_id = uuid.uuid4()
    row = await conn.fetchrow(
        """
        INSERT INTO report (id, session_id, status, overall, score_content, score_structure,
            score_communication, score_confidence, difficulty, rubric_version, summary_strengths,
            summary_improvements, metrics, competency_scorecard, scoring_model, generated_at)
        VALUES ($1,$2,'scored',$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,now())
        ON CONFLICT (session_id) DO UPDATE SET
            status='scored', overall=EXCLUDED.overall, score_content=EXCLUDED.score_content,
            score_structure=EXCLUDED.score_structure, score_communication=EXCLUDED.score_communication,
            score_confidence=EXCLUDED.score_confidence, difficulty=EXCLUDED.difficulty,
            rubric_version=EXCLUDED.rubric_version, summary_strengths=EXCLUDED.summary_strengths,
            summary_improvements=EXCLUDED.summary_improvements, metrics=EXCLUDED.metrics,
            competency_scorecard=EXCLUDED.competency_scorecard, scoring_model=EXCLUDED.scoring_model,
            generated_at=now()
        RETURNING id
        """,
        report_id, uuid.UUID(session_id),
        score.overall if score else None,
        score.content if score else None,
        score.structure if score else None,
        score.communication if score else None,
        score.confidence if score else None,
        data["difficulty"], data["rubric_version"] or config_rubric_version(),
        json.dumps(score.strengths if score else []),
        json.dumps(score.improvements if score else []),
        json.dumps(metrics),
        json.dumps(scorecard),
        score_model_id(),
    )
    rid = row["id"]
    # Replace any prior question_feedback for an idempotent re-score.
    await conn.execute("DELETE FROM question_feedback WHERE session_id = $1", uuid.UUID(session_id))
    return str(rid)


# Small indirections so _write_report stays pure-ish; set by generate flow via module globals.
_ACTIVE_CONFIG: Config | None = None


def set_active_config(cfg: Config) -> None:
    global _ACTIVE_CONFIG
    _ACTIVE_CONFIG = cfg


def config_rubric_version() -> str:
    return _ACTIVE_CONFIG.rubric_version if _ACTIVE_CONFIG else "g3-2026.1"


def score_model_id() -> str:
    return _ACTIVE_CONFIG.bedrock_model_id if _ACTIVE_CONFIG else "unknown"


async def _write_question_feedback(conn, report_id, session_id, feedbacks) -> None:
    for f in feedbacks:
        await conn.execute(
            """
            INSERT INTO question_feedback (id, report_id, session_id, turn_index, archetype_id,
                competency, question_text, student_transcript, what_worked, what_to_improve,
                strong_answer_example, q_score, star_coverage, evidence_quote)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
            """,
            uuid.uuid4(), uuid.UUID(report_id), uuid.UUID(session_id), f.turn_index,
            uuid.UUID(f.archetype_id) if f.archetype_id else None, f.competency,
            f.question_text, f.student_transcript, f.what_worked, f.what_to_improve,
            f.strong_answer_example, None, json.dumps(f.star_coverage), f.evidence_quote,
        )


# --- F008 (US4): cross-session coaching guidance -------------------------------------------------


async def load_scored_material_for_user(conn: asyncpg.Connection, user_sub: str) -> list[dict]:
    """The user's scored reports, oldest -> newest, as the guidance prompt's grounding material.

    REPORT-derived only — per-rubric scores, competency scorecards (with their evidence quotes),
    summary strengths/improvements, difficulty + rubric_version + when. NO raw transcripts: the
    reports already distill the evidence (smaller context, traceable claims, no extra PII spread —
    research R3 / SC-005)."""
    rows = await conn.fetch(
        """
        SELECT r.overall, r.score_content, r.score_structure, r.score_communication,
               r.score_confidence, r.difficulty, r.rubric_version,
               r.summary_strengths, r.summary_improvements, r.competency_scorecard,
               r.created_at, vs.job_title
          FROM report r
          JOIN voice_session vs ON vs.session_id = r.session_id
         WHERE vs.user_sub = $1 AND r.status = 'scored'
         ORDER BY r.created_at ASC
        """,
        user_sub,
    )
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        for k in ("summary_strengths", "summary_improvements", "competency_scorecard"):
            if isinstance(d.get(k), str):
                d[k] = json.loads(d[k])
        for k in ("overall", "score_content", "score_structure", "score_communication",
                  "score_confidence"):
            d[k] = float(d[k]) if d[k] is not None else None
        d["created_at"] = d["created_at"].isoformat() if d["created_at"] else None
        out.append(d)
    return out


async def upsert_guidance(conn: asyncpg.Connection, user_sub: str, payload: dict) -> None:
    """Whole-row replace of the user's ONE current guidance (data-model.md). The payload is the
    validated generation output plus bookkeeping (sessions_analyzed, rubric_versions, model_id)."""
    await conn.execute(
        """
        INSERT INTO coaching_guidance
            (user_sub, generated_at, sessions_analyzed, rubric_versions,
             strengths, improvement_areas, trend_note, next_actions, model_id)
        VALUES ($1, now(), $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (user_sub) DO UPDATE SET
            generated_at = EXCLUDED.generated_at,
            sessions_analyzed = EXCLUDED.sessions_analyzed,
            rubric_versions = EXCLUDED.rubric_versions,
            strengths = EXCLUDED.strengths,
            improvement_areas = EXCLUDED.improvement_areas,
            trend_note = EXCLUDED.trend_note,
            next_actions = EXCLUDED.next_actions,
            model_id = EXCLUDED.model_id
        """,
        user_sub,
        payload["sessions_analyzed"],
        payload["rubric_versions"],
        json.dumps(payload["strengths"]),
        json.dumps(payload["improvement_areas"]),
        payload["trend_note"],
        json.dumps(payload["next_actions"]),
        payload["model_id"],
    )


async def user_sub_for_session(conn: asyncpg.Connection, session_id: str) -> str | None:
    """The owner of a session — the guidance refresh hook needs it after scoring."""
    return await conn.fetchval(
        "SELECT user_sub FROM voice_session WHERE session_id = $1", uuid.UUID(session_id)
    )
