"""Centralized environment/config for the voice worker (T010).

All tunables live here so the loop, harness, and tests share one source of truth.
No secrets are logged; only presence is asserted at startup where required.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _get(name: str, default: str | None = None) -> str | None:
    val = os.environ.get(name, default)
    return val


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _get_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return float(raw)


def _get_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _get_csv(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """Comma-separated env list -> tuple of trimmed non-empty items; unset -> default."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return tuple(item.strip() for item in raw.split(",") if item.strip())


# Default STT keyword-boost vocabulary (nova-2 `keywords` feature). DELIBERATELY proper nouns /
# technical jargon that Deepgram mis-hears in interviews (observed: "Kinesis"->"kamito",
# "Glue"->"crew"), NOT common English words — boosting everyday words would dilute general accuracy.
# Override or extend per deployment via the STT_KEYWORDS env var (comma-separated). No PII (FR-218).
_DEFAULT_STT_KEYWORDS: tuple[str, ...] = (
    # AWS services
    "Kinesis", "Firehose", "Redshift", "DynamoDB", "OpenSearch", "Glue", "Athena", "Lambda",
    "S3", "EC2", "SageMaker", "CloudFormation", "Fargate", "EKS", "ECS",
    # Data / streaming / infra
    "Kafka", "Flink", "Spark", "Airflow", "Snowflake", "Databricks", "Kubernetes", "Terraform",
    "PostgreSQL", "Postgres", "pgvector", "Redis", "GraphQL", "Parquet",
    # Cloud platforms / general tech
    "GCP", "BigQuery", "Azure", "DevOps", "CI/CD", "OAuth", "gRPC", "Bedrock",
)


def _database_url() -> str | None:
    """Resolve DATABASE_URL, or assemble it from DB_* parts.

    The deployed ECS task injects the RDS password from Secrets Manager as DB_PASSWORD (a
    separate field) rather than baking the full URL into the task definition, so when
    DATABASE_URL is absent we build it from DB_HOST/DB_PORT/DB_NAME/DB_USER + DB_PASSWORD.
    The password is URL-encoded because RDS-generated passwords can contain reserved chars.
    """
    explicit = _get("DATABASE_URL")
    if explicit:
        return explicit
    host = _get("DB_HOST")
    if not host:
        return None
    from urllib.parse import quote

    user = _get("DB_USER", "icadmin")
    port = _get("DB_PORT", "5432")
    name = _get("DB_NAME", "interviewcoach")
    # DB_SECRET_ARN set -> passwordless DSN; the live password is fetched per connection (the pool
    # re-evaluates it on each new connection) so RDS password rotation is transparent. Else use the
    # static DB_PASSWORD baked at container start (local dev / harness).
    if _get("DB_SECRET_ARN"):
        return f"postgres://{user}@{host}:{port}/{name}"
    password = _get("DB_PASSWORD")
    if password is not None:
        return f"postgres://{user}:{quote(password, safe='')}@{host}:{port}/{name}"
    return None


@dataclass(frozen=True)
class Config:
    # Speech
    deepgram_api_key: str | None
    # STT keyword-boost vocabulary (nova-2 `keywords` feature) + its intensifier. Proper nouns /
    # jargon that STT mishears (defaults in _DEFAULT_STT_KEYWORDS); override via STT_KEYWORDS (CSV).
    # Off the response_gap clock (a connection query param). No PII (FR-218).
    stt_keywords: tuple[str, ...]
    stt_keyword_intensifier: int
    # Deepgram endpointing silence: how long the student may pause WITHIN an answer before Deepgram
    # calls it the end of speech (fires speech_final -> the coach takes the turn). This is the
    # governing hands-free pause tolerance. Tuned to 1200ms (up from 600): a normal mid-answer
    # pause while composing a thought routinely exceeds 0.6s, which made the coach cut in before
    # the student finished her first sentence. The trade-off (chosen deliberately) is slightly
    # slower turn-taking; push-to-talk mode removes the guesswork entirely for users who want it.
    stt_endpointing_ms: int
    # Deepgram UtteranceEnd fallback (word-timing based): fires after this much silence even if
    # speech_final did not. Kept ABOVE stt_endpointing_ms so endpointing is the normal boundary and
    # this only backstops it; was previously hardcoded at 1000ms in STTConfig (a third, unconfigured
    # premature trigger) and is now wired through here. Deepgram requires this to be >= 1000.
    stt_utterance_end_ms: int
    # Turn-budget watchdog (FR-221 / R8). Max wall-clock the live SUBSTANTIVE reply generation may
    # take before the loop gives up and speaks a contained fallback probe from the resident plan,
    # rather than stalling or dropping the turn. Generous (the reply normally completes in 1-3s) — a
    # safety net, not a tight gate. With lead-clause the LLM is already off the response_gap clock, so
    # this never affects SC-001; it only bounds how long the coach may go quiet after the backchannel.
    turn_budget_ms: int
    # Max MAIN questions a personalized interview asks before the coach wraps up. Follow-ups within a
    # competency do NOT count against this; it bounds how many distinct competencies are covered so the
    # interview ends cleanly instead of running until the student leaves. Off the gap clock (a turn
    # counter only). Generic G1 sessions ignore it (no plan).
    max_main_questions: int
    # Optional hard ceiling on follow-ups per competency, on top of the tier budget. 0 = unset (use
    # the tier's probing_intensity unchanged — keeps SC-004 distinctness intact). A positive value
    # keeps any one competency from being drilled too long in a real interview.
    followup_ceiling: int
    # Session-length bounds (off the gap clock). The chosen interview duration sizes the question
    # count (backend), which reaches the worker as the number of planned questions; both the total
    # coach-turn budget and the wall-clock budget derive from it so the duration is actually honored.
    #   followups_per_question: opener + up to this many follow-ups per main question. Also caps each
    #     archetype's follow-ups so the budget spreads across mains (every planned main gets asked).
    #     total coach-turn budget = num_planned_questions * (1 + followups_per_question).
    #   seconds_per_question: wall-clock seconds budgeted per main question (mirrors the backend's
    #     ~90s/question sizing); session wall-clock budget = num_planned_questions * this.
    #   session_budget_ms: optional hard wall-clock override; 0 = derive from seconds_per_question.
    #   duration_grace_s: seconds added to the student's CHOSEN duration before wrap-up (lets the
    #     in-flight question finish; the chosen length is otherwise the authoritative cap).
    # None of these touch the response_gap clock; the wrap-up they trigger is a deterministic line.
    followups_per_question: int
    seconds_per_question: int
    session_budget_ms: int
    duration_grace_s: int
    # F004: speak a generated, SCORE-FREE qualitative debrief (one strength + one improvement, grounded
    # in the transcript) at wrap-up before the closing line. Off the response_gap clock (the interview
    # is over). Falls back to the fixed closing line if generation fails. Disable to keep the fixed line.
    wrap_up_debrief: bool
    # Max wall-clock the debrief generation may take before falling back to the fixed closing line, so
    # a slow/failed model never leaves the candidate hanging in silence at the end.
    debrief_budget_ms: int
    # Acoustic end-of-speech -> final transcript constant added to the real-loop response_gap.
    # The worker starts the gap clock at Deepgram's speech_final event; this constant accounts
    # for the finalization the event lags behind the true acoustic offset (measured ~267-286ms
    # against nova-2). Stated and overridable so the gate number stays honest (Constitution II).
    stt_finalization_ms: int

    # Reply generator selection: "agentcore" | "bedrock_direct"
    reply_provider: str

    # F007 (Pipecat adoption): which latency strategy the Pipecat pipeline uses —
    #   "processor" -> LeadClauseProcessor injects a backchannel, LLM off the gap clock (default);
    #   "native"    -> LLM streams straight to TTS, LLM on the gap clock (the A/B comparison arm).
    # See specs/007-pipecat-adoption/contracts/latency-strategy-ab.md. The default is locked from
    # harness numbers; this selector lets the spike measure both without code changes.
    lead_clause_strategy: str
    # F007: Silero VAD tuning (on-audio end-of-speech detection; replaces the DTX endpoint watchdog
    # and enables voice-activated barge-in). start_secs guards against false-positive barge-in on
    # brief backchannels; stop_secs is the hands-free end-of-turn patience.
    vad_start_secs: float
    vad_stop_secs: float
    vad_confidence: float
    # ISSUE-1 fix: voice barge-in only fires after the student has actually said this many transcribed
    # words (MinWordsUserTurnStartStrategy), so breathing / room noise / coach-audio echo cannot cancel
    # the coach's reply. Raw VAD no longer interrupts (enable_interruptions=False). 0 would disable the
    # min-words gate; default 3.
    voice_barge_in_min_words: int
    # ISSUE (false barge-in): suppress voice barge-in for this many seconds AFTER the coach starts
    # speaking, so a LATE/stale transcript of the student's own just-finished turn (common when STT
    # lags) cannot cancel the coach's reply. A genuine barge-in (student keeps talking past the
    # window) still interrupts. 0 disables the grace window (pure min-words behavior). Default 1.5s.
    voice_barge_in_grace_secs: float

    # Media / signaling
    # The HS256 secret the backend mints voice_token with; the worker verifies the media-join
    # token against the SAME secret (NFR-5). Must match the backend's VOICE_TOKEN_SECRET.
    voice_token_secret: str | None
    server_host: str
    server_port: int
    # STUN servers handed to the worker's RTCPeerConnection. On Fargate the task only knows its
    # private VPC IP; without STUN aiortc gathers only that host candidate, which the browser
    # cannot reach. A STUN server lets aiortc discover the public server-reflexive (srflx)
    # candidate (the task's public IP) so the browser can connect to it DIRECTLY (no media LB).
    # Empty/unset (the local-loopback case) => no STUN, host candidates only, which is correct
    # for the same-host harness. Comma-separated list of stun: URLs.
    ice_stun_urls: tuple[str, ...]

    # AWS / intelligence
    aws_region: str
    agentcore_agent_id: str | None
    agentcore_agent_alias_id: str | None
    bedrock_model_id: str
    # F002 (G2): pinned Bedrock Titan embedding model. NOT on the response_gap clock — used by
    # the offline bank pipeline (bank/embed.py) and session-prep JD embedding only. Pinned so a
    # model change is explicit (question_archetype.embedding_model is stored beside each vector).
    bedrock_embedding_model_id: str

    # Storage
    database_url: str | None
    # When set, the live RDS password is fetched per connection from this Secrets Manager secret
    # (rotation-proof); the pool re-evaluates it on each new connection. None -> static password.
    db_secret_arn: str | None

    # F006 (G6): consent-gated per-turn audio recording. Audio (PII) lives ONLY in this S3 bucket
    # (SSE-KMS, single region) + the uri in RDS. Unset AUDIO_BUCKET -> recording is a no-op (local/dev
    # fallback, mirrors resume_store). RECORD_AUDIO is an operational kill-switch independent of consent.
    audio_bucket: str | None
    audio_kms_key_id: str | None
    record_audio: bool

    # Observability
    cw_metric_namespace: str
    log_file: str
    # When true, emit per-frame inbound-audio + STT-result diagnostics (peak amplitude, byte
    # counts, Deepgram Results). These were decisive in finding the PyAV plane-padding bug; off
    # by default so production logs stay quiet, flip on via MEDIA_DEBUG=true to debug media.
    media_debug: bool

    @staticmethod
    def load() -> "Config":
        provider = (_get("REPLY_PROVIDER", "agentcore") or "agentcore").strip()
        if provider not in ("agentcore", "bedrock_direct"):
            raise ValueError(
                f"REPLY_PROVIDER must be 'agentcore' or 'bedrock_direct', got {provider!r}"
            )
        return Config(
            deepgram_api_key=_get("DEEPGRAM_API_KEY"),
            stt_keywords=_get_csv("STT_KEYWORDS", _DEFAULT_STT_KEYWORDS),
            stt_keyword_intensifier=_get_int("STT_KEYWORD_INTENSIFIER", 2),
            # Pause tolerance for hands-free turn-taking. Tuned UP again for interviews: a candidate
            # composing a STAR answer routinely pauses ~2s mid-thought, and 1.2-1.6s made the coach
            # cut in before they finished. 2200ms endpointing tolerates a normal thinking pause; the
            # ordering endpointing < utterance_end is preserved (utterance_end backstops endpointing).
            # The slightly slower turn boundary is a deliberate trade for not interrupting; push-to-talk
            # removes the guesswork entirely for users who prefer it. (The DTX endpoint watchdog that
            # used to sit above both is retired — Silero VAD endpoints on the audio itself, T033.)
            stt_endpointing_ms=_get_int("STT_ENDPOINTING_MS", 2200),
            stt_utterance_end_ms=_get_int("STT_UTTERANCE_END_MS", 3000),
            turn_budget_ms=_get_int("TURN_BUDGET_MS", 8000),
            max_main_questions=_get_int("MAX_MAIN_QUESTIONS", 8),
            followup_ceiling=_get_int("FOLLOWUP_CEILING", 0),
            followups_per_question=_get_int("FOLLOWUPS_PER_QUESTION", 2),
            seconds_per_question=_get_int("SECONDS_PER_QUESTION", 90),
            session_budget_ms=_get_int("SESSION_BUDGET_MS", 0),
            duration_grace_s=_get_int("DURATION_GRACE_S", 30),
            wrap_up_debrief=_get_bool("WRAP_UP_DEBRIEF", True),
            debrief_budget_ms=_get_int("DEBRIEF_BUDGET_MS", 8000),
            stt_finalization_ms=_get_int("STT_FINALIZATION_MS", 280),
            reply_provider=provider,
            lead_clause_strategy=(_get("LEAD_CLAUSE_STRATEGY", "processor") or "processor").strip(),
            # VAD defaults raised (was 0.2/0.7) so brief noise doesn't register as speech (ISSUE 1).
            vad_start_secs=_get_float("VAD_START_SECS", 0.5),
            vad_stop_secs=_get_float("VAD_STOP_SECS", 0.8),
            vad_confidence=_get_float("VAD_CONFIDENCE", 0.8),
            voice_barge_in_min_words=_get_int("VOICE_BARGE_IN_MIN_WORDS", 3),
            voice_barge_in_grace_secs=_get_float("VOICE_BARGE_IN_GRACE_SECS", 1.5),
            voice_token_secret=_get("VOICE_TOKEN_SECRET"),
            server_host=_get("SERVER_HOST", "0.0.0.0") or "0.0.0.0",
            server_port=_get_int("SERVER_PORT", 8080),
            ice_stun_urls=tuple(
                u.strip()
                for u in (_get("ICE_STUN_URLS", "") or "").split(",")
                if u.strip()
            ),
            aws_region=_get("AWS_REGION", "us-east-1") or "us-east-1",
            agentcore_agent_id=_get("AGENTCORE_AGENT_ID"),
            agentcore_agent_alias_id=_get("AGENTCORE_AGENT_ALIAS_ID"),
            bedrock_model_id=_get("BEDROCK_MODEL_ID", "anthropic.claude-sonnet-4-6")
            or "anthropic.claude-sonnet-4-6",
            bedrock_embedding_model_id=_get(
                "BEDROCK_EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0"
            )
            or "amazon.titan-embed-text-v2:0",
            database_url=_database_url(),
            db_secret_arn=_get("DB_SECRET_ARN"),
            audio_bucket=_get("AUDIO_BUCKET"),
            audio_kms_key_id=_get("AUDIO_KMS_KEY_ID"),
            record_audio=_get_bool("RECORD_AUDIO", True),
            cw_metric_namespace=_get("CW_METRIC_NAMESPACE", "InterviewCoach/G1")
            or "InterviewCoach/G1",
            log_file=_get("LOG_FILE", "voice-worker.log") or "voice-worker.log",
            media_debug=_get_bool("MEDIA_DEBUG", False),
        )
