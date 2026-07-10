"""Backend app-API config."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _get(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw else default


def _get_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _database_url() -> str | None:
    """Resolve DATABASE_URL, or assemble it from DB_* parts.

    The deployed ECS task injects the RDS password from Secrets Manager as DB_PASSWORD rather
    than baking the full URL into the task definition. The password is URL-encoded because
    RDS-generated passwords can contain reserved characters.
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
    # DB_SECRET_ARN set -> passwordless DSN; the pool fetches the live password per connection so
    # RDS password rotation is transparent. Else the static DB_PASSWORD baked at container start.
    if _get("DB_SECRET_ARN"):
        return f"postgres://{user}@{host}:{port}/{name}"
    password = _get("DB_PASSWORD")
    if password is not None:
        return f"postgres://{user}:{quote(password, safe='')}@{host}:{port}/{name}"
    return None


@dataclass(frozen=True)
class Settings:
    database_url: str | None
    # When set, the pool fetches the live RDS password per connection from this Secrets Manager
    # secret (rotation-proof). None -> static password baked into database_url.
    db_secret_arn: str | None
    cognito_region: str
    cognito_user_pool_id: str | None
    cognito_app_client_id: str | None
    media_endpoint: str
    reply_provider: str
    turn_provider: str
    turn_api_key: str | None
    turn_api_secret: str | None
    stun_url: str
    voice_token_secret: str | None
    voice_token_ttl_s: int
    aws_region: str
    # --- F002 (G2) additions: all off the response_gap clock (setup/prep window) ---
    # Bedrock text model for off-gap-clock structured extraction (resume parse-back, R4). This is
    # NOT the live reply model (the worker owns that on the response_gap path); it runs only in the
    # setup window, so a slower/cheaper general model is fine here.
    bedrock_model_id: str
    # Bedrock Titan embedding model, pinned (R3). Used at session-prep to embed the JD once and
    # by the offline bank pipeline (bank/embed.py); a model change forces a re-embed of the bank
    # because question_archetype.embedding_model is pinned alongside each vector.
    bedrock_embedding_model_id: str
    # S3 bucket (SSE-KMS) that holds the raw resume file; the sole durable home for that PII
    # alongside RDS (Constitution III). Empty until provisioned (T003).
    resume_bucket: str | None
    # KMS key for SSE-KMS on the resume bucket; None falls back to the bucket's default SSE.
    resume_kms_key_id: str | None
    # Pinned rubric/tier-context version stamped on each personalized session (FR-215 / Principle II).
    # F002 does NOT score, but it records WHICH rubric+difficulty context a session ran under so a
    # later feature's cross-session progress comparison can account for rubric changes and NEVER blend
    # tiers into a single always-up score. The difficulty tier is stored separately (its own column),
    # so a session always carries (difficulty, rubric_version) unblended. Bump when the rubric changes.
    rubric_version: str
    # --- F003 (G3) addition ---
    # SQS queue the backend enqueues a report job to on session end (FR-301). The dedicated Report
    # Worker consumes it and scores asynchronously — this is the ONLY live-path touch F003 adds (one
    # SQS send). Unset in local dev -> enqueue is a no-op (run the worker one-shot to score).
    report_queue_url: str | None
    # --- F006 (G6) additions: consent-gated audio recording + playback (Constitution III) ---
    # S3 bucket (SSE-KMS) holding per-turn interview audio; the sole durable home for that PII
    # alongside the uri in RDS. Unset -> recording/playback/delete are no-ops (local/dev fallback).
    audio_bucket: str | None
    # KMS key for SSE-KMS on the audio bucket; None falls back to the bucket's default SSE.
    audio_kms_key_id: str | None
    # TTL (seconds) for a minted playback pre-signed GET URL (FR-008); short by design (minutes).
    audio_url_ttl_s: int
    # --- F009 (Generative Mode, Constitution VII) ---
    # When True, session-prep generates the FULL question plan (general + domain) with Bedrock even
    # when an approved bank could serve it (operator force, for testing/demo). When False (default),
    # generation only fires as the fallback for an empty bank at the chosen difficulty. Either way,
    # generation runs ONLY in the prep window (never on the response_gap clock — Principle I).
    generative_mode: bool

    @staticmethod
    def load() -> "Settings":
        return Settings(
            database_url=_database_url(),
            db_secret_arn=_get("DB_SECRET_ARN"),
            cognito_region=_get("COGNITO_REGION", "us-east-1") or "us-east-1",
            cognito_user_pool_id=_get("COGNITO_USER_POOL_ID"),
            cognito_app_client_id=_get("COGNITO_APP_CLIENT_ID"),
            # An explicitly-set MEDIA_ENDPOINT (including "") is preserved: in the hosted demo
            # it is "" so the SPA posts to /offer on the SAME CloudFront origin (which routes
            # /offer to the worker). Only an entirely-absent var falls back to the placeholder.
            media_endpoint=_get("MEDIA_ENDPOINT", "wss://media.example.com"),
            reply_provider=_get("REPLY_PROVIDER", "agentcore") or "agentcore",
            turn_provider=_get("TURN_PROVIDER", "twilio") or "twilio",
            turn_api_key=_get("TURN_API_KEY"),
            turn_api_secret=_get("TURN_API_SECRET"),
            stun_url=_get("STUN_URL", "stun:stun.l.google.com:19302")
            or "stun:stun.l.google.com:19302",
            voice_token_secret=_get("VOICE_TOKEN_SECRET"),
            voice_token_ttl_s=_get_int("VOICE_TOKEN_TTL_S", 120),
            aws_region=_get("AWS_REGION", "us-east-1") or "us-east-1",
            bedrock_model_id=_get("BEDROCK_MODEL_ID", "anthropic.claude-sonnet-4-6")
            or "anthropic.claude-sonnet-4-6",
            bedrock_embedding_model_id=_get(
                "BEDROCK_EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0"
            )
            or "amazon.titan-embed-text-v2:0",
            resume_bucket=_get("RESUME_BUCKET"),
            resume_kms_key_id=_get("RESUME_KMS_KEY_ID"),
            rubric_version=_get("RUBRIC_VERSION", "g2-2026.1") or "g2-2026.1",
            report_queue_url=_get("REPORT_QUEUE_URL"),
            audio_bucket=_get("AUDIO_BUCKET"),
            audio_kms_key_id=_get("AUDIO_KMS_KEY_ID"),
            audio_url_ttl_s=_get_int("AUDIO_URL_TTL_S", 300),
            generative_mode=_get_bool("GENERATIVE_MODE", False),
        )


settings = Settings.load()
