"""Idempotent schema migration.

G1 slice (T007): voice_session, conversation_turn, turn_latency (see specs/001 data-model.md).
No PII, no durable audio, no scoring/progress tables.

F002 / Gate G2 (T004/T005): BACKWARD-COMPATIBLE additive extension toward the product model
(specs/002 data-model.md). New columns via `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` (all
nullable/defaulted) and new tables via `CREATE TABLE IF NOT EXISTS` — no G1 column is dropped,
renamed, or retyped, so the G1 slice stays a readable subset and the deployed gate evidence
remains valid. Requires the pgvector extension (enabled as the preamble below). The whole
script is idempotent and safe to re-run.

Run:  python -m src.db_migrate
"""

from __future__ import annotations

import asyncio

import asyncpg

from .config import Config
from .logging_setup import setup_logging

# pgvector must exist before any `vector`-typed column is created (F002). Idempotent.
EXTENSION_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;
"""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS voice_session (
    session_id      UUID PRIMARY KEY,
    user_sub        TEXT        NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    ended_at        TIMESTAMPTZ,
    end_reason      TEXT,        -- student_ended | inactivity_timeout | dropped | error | completed
    network_path    TEXT,        -- direct | relayed
    reply_provider  TEXT        NOT NULL,  -- agentcore | bedrock_direct
    turn_count      INT         NOT NULL DEFAULT 0,
    CONSTRAINT voice_session_end_reason_chk
        CHECK (end_reason IS NULL OR end_reason IN
               ('student_ended','inactivity_timeout','dropped','error','completed')),
    CONSTRAINT voice_session_network_path_chk
        CHECK (network_path IS NULL OR network_path IN ('direct','relayed')),
    CONSTRAINT voice_session_reply_provider_chk
        CHECK (reply_provider IN ('agentcore','bedrock_direct')),
    -- end_reason set <=> ended_at set
    CONSTRAINT voice_session_end_consistency_chk
        CHECK ((end_reason IS NULL) = (ended_at IS NULL))
);

CREATE TABLE IF NOT EXISTS conversation_turn (
    turn_id      UUID PRIMARY KEY,
    session_id   UUID        NOT NULL REFERENCES voice_session(session_id) ON DELETE CASCADE,
    turn_index   INT         NOT NULL,
    speaker      TEXT        NOT NULL,  -- student | coach
    transcript   TEXT        NOT NULL,
    started_at   TIMESTAMPTZ NOT NULL,
    ended_at     TIMESTAMPTZ,
    interrupted  BOOLEAN     NOT NULL DEFAULT FALSE,
    CONSTRAINT conversation_turn_speaker_chk CHECK (speaker IN ('student','coach')),
    CONSTRAINT conversation_turn_unique_index UNIQUE (session_id, turn_index)
);
CREATE INDEX IF NOT EXISTS conversation_turn_session_idx
    ON conversation_turn (session_id, turn_index);

CREATE TABLE IF NOT EXISTS turn_latency (
    latency_id          UUID PRIMARY KEY,
    turn_id             UUID        NOT NULL UNIQUE
                                    REFERENCES conversation_turn(turn_id) ON DELETE CASCADE,
    session_id          UUID        NOT NULL REFERENCES voice_session(session_id) ON DELETE CASCADE,
    response_gap_ms     INT         NOT NULL,
    stt_finalization_ms INT         NOT NULL,
    reply_ttft_ms       INT         NOT NULL,
    tts_first_audio_ms  INT         NOT NULL,
    orchestration_ms    INT,
    reply_provider      TEXT        NOT NULL,
    measured_at         TIMESTAMPTZ NOT NULL,
    CONSTRAINT turn_latency_nonneg_chk CHECK (
        response_gap_ms >= 0 AND stt_finalization_ms >= 0 AND
        reply_ttft_ms >= 0 AND tts_first_audio_ms >= 0
    )
);
CREATE INDEX IF NOT EXISTS turn_latency_session_idx ON turn_latency (session_id);
"""

# --- F002 / Gate G2 additive extension (specs/002 data-model.md) -------------------------
# Every statement is `IF NOT EXISTS`-guarded so re-running is a no-op and a G1-only database
# upgrades cleanly. New columns are nullable or defaulted so existing G1 rows stay valid.
# IMPORTANT: this NEVER drops/renames/retypes a G1 column (R6). NO score columns (FR-212a).
F002_SQL = """
-- New product entities -------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS users (
    id                   UUID PRIMARY KEY,
    user_sub             TEXT        UNIQUE NOT NULL,   -- Cognito subject (links voice_session.user_sub)
    email                TEXT,
    age_attested         BOOLEAN     NOT NULL DEFAULT FALSE,
    consent_recording    BOOLEAN     NOT NULL DEFAULT FALSE,
    consent_recording_at TIMESTAMPTZ,
    retention_days       INT         NOT NULL DEFAULT 30,
    resume_uri           TEXT,                          -- S3 path; raw resume lives ONLY here (+ RDS facts)
    resume_parsed_facts  JSONB,                         -- CONFIRMED structured facts (authoritative for grounding)
    resume_confirmed_at  TIMESTAMPTZ,
    role                 TEXT        NOT NULL DEFAULT 'student',
    org_id               UUID,                          -- V2 hook (unused)
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT users_role_chk CHECK (role IN ('student','teacher','org_admin')),
    -- Consent gates persistence: no consent => no stored raw materials (FR-220).
    CONSTRAINT users_consent_gates_resume_chk
        CHECK (consent_recording = TRUE
               OR (resume_uri IS NULL AND resume_parsed_facts IS NULL))
);

CREATE TABLE IF NOT EXISTS question_archetype (
    id               UUID PRIMARY KEY,
    category         TEXT        NOT NULL,              -- general | domain
    competency       TEXT        NOT NULL,              -- closed enum (R5)
    question_type    TEXT        NOT NULL,              -- warmup | behavioral | technical | situational
    industry         TEXT,                              -- domain axis (NULL for general)
    role_family      TEXT,
    seniority        TEXT,
    difficulty       TEXT        NOT NULL,              -- easy | moderate | difficult
    prompt_template  TEXT        NOT NULL,
    follow_up_prompts JSONB      NOT NULL DEFAULT '[]', -- seed funnel probes that travel with the question
    scoring_guidance JSONB       NOT NULL DEFAULT '{}', -- G3 rubric anchors (stored, NOT applied in F002)
    embedding        vector(1024),                      -- Titan Text Embeddings v2 (1024-dim); set on approval (R3)
    embedding_model  TEXT,                              -- model id/version that produced `embedding`
    source           TEXT        NOT NULL,              -- generated | curated
    status           TEXT        NOT NULL DEFAULT 'draft', -- draft | approved | retired (review gate FR-206)
    version          INT         NOT NULL DEFAULT 1,
    active           BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT question_archetype_category_chk CHECK (category IN ('general','domain')),
    CONSTRAINT question_archetype_competency_chk CHECK (competency IN
        ('teamwork','problem_solving','role_specific','motivation_fit',
         'communication','leadership','adaptability')),
    CONSTRAINT question_archetype_qtype_chk CHECK (question_type IN
        ('warmup','behavioral','technical','situational')),
    CONSTRAINT question_archetype_difficulty_chk CHECK (difficulty IN ('easy','moderate','difficult')),
    CONSTRAINT question_archetype_source_chk CHECK (source IN ('generated','curated')),
    CONSTRAINT question_archetype_status_chk CHECK (status IN ('draft','approved','retired'))
);
-- Fast filter indexes for the selection path (status/category/difficulty + competency).
CREATE INDEX IF NOT EXISTS question_archetype_filter_idx
    ON question_archetype (status, category, difficulty);
CREATE INDEX IF NOT EXISTS question_archetype_competency_idx
    ON question_archetype (competency, difficulty, active);

CREATE TABLE IF NOT EXISTS difficulty_profile (
    level             TEXT PRIMARY KEY,                 -- easy | moderate | difficult
    probing_intensity INT     NOT NULL,
    curveball_rate    NUMERIC NOT NULL,
    warmth            INT     NOT NULL,
    hint_policy       TEXT    NOT NULL,                 -- offer | minimal | none
    domain_depth      INT     NOT NULL,
    scoring_strictness INT    NOT NULL,                 -- stored for G3; not applied in F002
    CONSTRAINT difficulty_profile_level_chk CHECK (level IN ('easy','moderate','difficult'))
);

CREATE TABLE IF NOT EXISTS interview_blueprint (
    id                   UUID PRIMARY KEY,
    session_id           UUID        NOT NULL REFERENCES voice_session(session_id) ON DELETE CASCADE,
    target_competencies  TEXT[]      NOT NULL,
    ordered_archetype_ids UUID[]     NOT NULL,
    opening_archetype_id UUID,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS interview_blueprint_session_idx ON interview_blueprint (session_id);

-- Additive columns on the G1 tables (all nullable/defaulted) -----------------------------

ALTER TABLE voice_session ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id);
ALTER TABLE voice_session ADD COLUMN IF NOT EXISTS job_title TEXT;
ALTER TABLE voice_session ADD COLUMN IF NOT EXISTS job_description TEXT;
ALTER TABLE voice_session ADD COLUMN IF NOT EXISTS difficulty TEXT;
ALTER TABLE voice_session ADD COLUMN IF NOT EXISTS archetype_ids UUID[];
ALTER TABLE voice_session ADD COLUMN IF NOT EXISTS blueprint_id UUID REFERENCES interview_blueprint(id);
ALTER TABLE voice_session ADD COLUMN IF NOT EXISTS rubric_version TEXT;
ALTER TABLE voice_session ADD COLUMN IF NOT EXISTS domain_coverage_reduced BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE voice_session ADD COLUMN IF NOT EXISTS consent_store_materials BOOLEAN NOT NULL DEFAULT FALSE;
-- F006 (G6): the per-session "keep" override (FR-011). retain=TRUE exempts a session from the
-- retention TTL sweep; default FALSE = eligible to age out (the safe default).
ALTER TABLE voice_session ADD COLUMN IF NOT EXISTS retain BOOLEAN NOT NULL DEFAULT FALSE;
-- Chosen interview length (minutes) — the AUTHORITATIVE duration the worker bounds the live session
-- to, so a slow funnel can never overrun the time the student picked. NULL for legacy/generic
-- sessions (worker falls back to deriving the budget from the planned question count).
ALTER TABLE voice_session ADD COLUMN IF NOT EXISTS duration_minutes INTEGER;
DO $$ BEGIN
    ALTER TABLE voice_session ADD CONSTRAINT voice_session_difficulty_chk
        CHECK (difficulty IS NULL OR difficulty IN ('easy','moderate','difficult'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

ALTER TABLE conversation_turn ADD COLUMN IF NOT EXISTS archetype_id UUID REFERENCES question_archetype(id);
ALTER TABLE conversation_turn ADD COLUMN IF NOT EXISTS is_followup BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE conversation_turn ADD COLUMN IF NOT EXISTS targeted_star_element TEXT;
ALTER TABLE conversation_turn ADD COLUMN IF NOT EXISTS audio_uri TEXT;
DO $$ BEGIN
    ALTER TABLE conversation_turn ADD CONSTRAINT conversation_turn_star_chk
        CHECK (targeted_star_element IS NULL
               OR targeted_star_element IN ('situation','task','action','result'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- IVFFlat ANN index on the bank embedding (cosine). Built here so the object exists; bank/embed.py
-- rebuilds it with `lists` ~ sqrt(rows) after a bulk load (R3). A partial index over approved+embedded
-- rows keeps it aligned with the selection filter. Guarded so re-runs are a no-op.
DO $$ BEGIN
    CREATE INDEX question_archetype_embedding_ivf
        ON question_archetype USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)
        WHERE status = 'approved' AND embedding IS NOT NULL;
EXCEPTION WHEN duplicate_table THEN NULL; END $$;

-- F007 wrap-up writes end_reason='completed' (the natural finish: plan exhausted / duration
-- reached), which the original G1 CHECK predates — on a live DB every wrap-up's end_session
-- silently failed the constraint and the session stayed open. Recreate the CHECK with the new
-- value (drop+add in one transaction-safe block; existing rows all satisfy the wider set).
ALTER TABLE voice_session DROP CONSTRAINT IF EXISTS voice_session_end_reason_chk;
DO $$ BEGIN
    ALTER TABLE voice_session ADD CONSTRAINT voice_session_end_reason_chk
        CHECK (end_reason IS NULL OR end_reason IN
               ('student_ended','inactivity_timeout','dropped','error','completed'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
"""


# F003 (G3) additive extension — the async Feedback Report Engine's tables. All additive
# (CREATE TABLE IF NOT EXISTS), hung off voice_session with ON DELETE CASCADE so the existing bounded
# hard-delete purges them automatically (FR-311). Scores live ONLY here — the interview tables gain no
# score columns, preserving F002's no-score guarantee. See specs/003-.../data-model.md.
F003_SQL = """
-- One row per completed, scored session (FR-302/303/304). Scores are absolute on a fixed
-- level-independent rubric; difficulty + rubric_version are recorded as CONTEXT, never blended.
CREATE TABLE IF NOT EXISTS report (
    id                  UUID PRIMARY KEY,
    session_id          UUID NOT NULL REFERENCES voice_session(session_id) ON DELETE CASCADE,
    status              TEXT NOT NULL DEFAULT 'queued',
    overall             NUMERIC(4,2),
    score_content       NUMERIC(4,2),
    score_structure     NUMERIC(4,2),
    score_communication NUMERIC(4,2),
    score_confidence    NUMERIC(4,2),
    difficulty          TEXT,
    rubric_version      TEXT,
    summary_strengths    JSONB NOT NULL DEFAULT '[]',
    summary_improvements JSONB NOT NULL DEFAULT '[]',
    metrics             JSONB NOT NULL DEFAULT '{}',
    competency_scorecard JSONB NOT NULL DEFAULT '[]',
    scoring_model       TEXT,
    generated_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT report_status_chk CHECK (status IN ('queued','processing','scored','failed')),
    CONSTRAINT report_session_uniq UNIQUE (session_id)
);
CREATE INDEX IF NOT EXISTS report_session_idx ON report (session_id);
CREATE INDEX IF NOT EXISTS report_status_idx  ON report (status);

-- Per assessed interview question — self-referential, resume-grounded feedback (FR-306).
CREATE TABLE IF NOT EXISTS question_feedback (
    id                    UUID PRIMARY KEY,
    report_id             UUID NOT NULL REFERENCES report(id) ON DELETE CASCADE,
    session_id            UUID NOT NULL REFERENCES voice_session(session_id) ON DELETE CASCADE,
    turn_index            INT,
    archetype_id          UUID,
    competency            TEXT,
    question_text         TEXT NOT NULL,
    student_transcript    TEXT NOT NULL,
    what_worked           TEXT,
    what_to_improve       TEXT,
    strong_answer_example TEXT,
    q_score               NUMERIC(4,2),
    star_coverage         JSONB NOT NULL DEFAULT '{}',
    evidence_quote        TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS question_feedback_report_idx  ON question_feedback (report_id);
CREATE INDEX IF NOT EXISTS question_feedback_session_idx ON question_feedback (session_id);

-- The async unit of work (queued -> processing -> scored | failed), idempotent per session.
CREATE TABLE IF NOT EXISTS report_job (
    id              UUID PRIMARY KEY,
    session_id      UUID NOT NULL REFERENCES voice_session(session_id) ON DELETE CASCADE,
    status          TEXT NOT NULL DEFAULT 'queued',
    attempts        INT  NOT NULL DEFAULT 0,
    last_error      TEXT,
    enqueued_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    CONSTRAINT report_job_status_chk CHECK (status IN ('queued','processing','scored','failed')),
    CONSTRAINT report_job_session_uniq UNIQUE (session_id)
);
CREATE INDEX IF NOT EXISTS report_job_status_idx ON report_job (status);
"""


# F008 (G5) additive extension — Session Review & Coaching Insights. One CURRENT cross-session
# coaching-guidance row per user (regenerated whole by the report-worker after each scoring;
# specs/008-session-review-coaching/data-model.md). DERIVED data only: it summarizes the user's own
# scored reports — no raw transcripts, no third-party data (Constitution III). Deliberately NO FK to
# users: the delete fan-out removes it explicitly (bounded blast radius), matching the existing
# loose user_sub coupling. The voice_session owner/created index pins the session-list query path.
F008_SQL = """
CREATE TABLE IF NOT EXISTS coaching_guidance (
    user_sub           TEXT PRIMARY KEY,
    generated_at       TIMESTAMPTZ NOT NULL,
    sessions_analyzed  INT  NOT NULL,
    rubric_versions    TEXT[] NOT NULL,
    strengths          JSONB NOT NULL,
    improvement_areas  JSONB NOT NULL,
    trend_note         TEXT NOT NULL,
    next_actions       JSONB NOT NULL,
    model_id           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS voice_session_owner_created
    ON voice_session (user_sub, created_at DESC);
"""


async def migrate(database_url: str, password_provider=None) -> None:
    # password_provider: the rotation-proof Secrets Manager callable (db_secret) — REQUIRED when
    # the DSN is passwordless (DB_SECRET_ARN set, e.g. running as a one-off ECS task with the
    # service env). Without it asyncpg's no-password SSL attempt fails and its plaintext retry is
    # refused by RDS force_ssl ("no pg_hba.conf entry ... no encryption").
    kwargs = {"password": password_provider} if password_provider is not None else {}
    conn = await asyncpg.connect(database_url, **kwargs)
    try:
        # 1) pgvector extension (F002 preamble) — required before the vector column / IVFFlat index.
        await conn.execute(EXTENSION_SQL)
        # 2) G1 slice (unchanged, idempotent).
        await conn.execute(SCHEMA_SQL)
        # 3) F002 additive extension (idempotent; never drops/renames G1 columns).
        await conn.execute(F002_SQL)
        # 4) F003 additive extension — report engine tables (idempotent; CASCADE off voice_session).
        await conn.execute(F003_SQL)
        # 5) F008 additive extension — coaching guidance + session-list index (idempotent).
        await conn.execute(F008_SQL)
    finally:
        await conn.close()


async def _main() -> None:
    cfg = Config.load()
    log = setup_logging(cfg.log_file)
    if not cfg.database_url:
        raise SystemExit("DATABASE_URL is not set; cannot run migration.")
    provider = None
    if cfg.db_secret_arn:
        from .db_secret import make_password_provider

        provider = make_password_provider(cfg.db_secret_arn, cfg.aws_region)
    await migrate(cfg.database_url, password_provider=provider)
    log.info(
        "db_migrate: applied G1 slice + F002 additive extension "
        "(users, question_archetype, difficulty_profile, interview_blueprint; "
        "voice_session/conversation_turn columns; pgvector + IVFFlat) "
        "+ F003 report engine (report, question_feedback, report_job) "
        "+ F008 coaching_guidance + session-list index"
    )


if __name__ == "__main__":
    asyncio.run(_main())
