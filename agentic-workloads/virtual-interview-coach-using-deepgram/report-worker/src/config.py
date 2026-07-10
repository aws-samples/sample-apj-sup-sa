"""Config for the async Feedback Report Engine worker (F003 / G3).

All tunables in one place. The worker runs OFF the live path (consumes SQS after the interview is
over), so nothing here touches the response_gap clock. No secrets are logged; only presence is asserted
where required.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _get(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name, default)


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


def _database_url() -> str | None:
    """Resolve DATABASE_URL, or assemble it from DB_* parts (mirrors the voice-worker pattern).

    The deployed ECS task injects the RDS password from Secrets Manager as DB_PASSWORD rather than
    baking the full URL into the task definition, so when DATABASE_URL is absent we build it from
    DB_HOST/DB_PORT/DB_NAME/DB_USER + DB_PASSWORD. The password is URL-encoded (RDS passwords can carry
    reserved characters).

    When DB_SECRET_ARN is set, the password is fetched LIVE per connection instead (see
    db_secret.make_password_provider) so RDS password rotation is transparent — the URL here is then
    passwordless and asyncpg is given the provider callable. Falls back to the static password
    (DB_PASSWORD / DATABASE_URL) when no secret ARN is configured (local dev / harness)."""
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
    if _get("DB_SECRET_ARN"):
        # Passwordless DSN; the live password arrives via the provider callable on each connect.
        return f"postgres://{user}@{host}:{port}/{name}"
    password = _get("DB_PASSWORD")
    if password is not None:
        return f"postgres://{user}:{quote(password, safe='')}@{host}:{port}/{name}"
    return None


def _db_secret_arn() -> str | None:
    return _get("DB_SECRET_ARN")


@dataclass(frozen=True)
class Config:
    # Storage
    database_url: str | None
    # When set, the live RDS password is fetched per connection from this Secrets Manager secret
    # (rotation-proof). None -> use the static password baked into database_url.
    db_secret_arn: str | None

    # Queue (SQS). Absent in local dev -> the consume loop is not started; scoring can still be driven
    # directly (e.g. by the harness or a one-shot) against a session id.
    sqs_queue_url: str | None
    sqs_wait_seconds: int          # long-poll wait
    sqs_visibility_timeout: int    # message invisibility while a job is processed

    # Bedrock scoring model (Haiku 4.5 — the model already in use). converse API, temperature 0.
    aws_region: str
    bedrock_model_id: str

    # Scoring knobs (R2): self-consistency sample count + temperature. N=3 + temp 0 meets NFR-8.
    scoring_samples: int
    scoring_temperature: float
    scoring_max_tokens: int

    # The rubric version stamped on every report (NFR-10). Bump when the rubric changes.
    rubric_version: str

    # Voice-metric heuristics (R4).
    long_pause_ms: int             # inter-turn gap over this counts as a long pause

    # F006 (G6): the retention TTL sweep runs in this always-on worker, off the live path. Audio S3
    # config (unset bucket -> sweep S3 deletes are no-ops). Interval between sweeps; 0 disables.
    audio_bucket: str | None
    audio_kms_key_id: str | None
    retention_sweep_interval_s: int

    log_file: str | None

    @staticmethod
    def load() -> "Config":
        return Config(
            database_url=_database_url(),
            db_secret_arn=_db_secret_arn(),
            sqs_queue_url=_get("REPORT_QUEUE_URL"),
            sqs_wait_seconds=_get_int("SQS_WAIT_SECONDS", 20),
            sqs_visibility_timeout=_get_int("SQS_VISIBILITY_TIMEOUT", 300),
            aws_region=_get("AWS_REGION", "us-east-1") or "us-east-1",
            bedrock_model_id=_get("BEDROCK_MODEL_ID", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
            or "us.anthropic.claude-haiku-4-5-20251001-v1:0",
            scoring_samples=_get_int("SCORING_SAMPLES", 3),
            scoring_temperature=_get_float("SCORING_TEMPERATURE", 0.0),
            scoring_max_tokens=_get_int("SCORING_MAX_TOKENS", 2000),
            rubric_version=_get("RUBRIC_VERSION", "g3-2026.1") or "g3-2026.1",
            long_pause_ms=_get_int("LONG_PAUSE_MS", 3000),
            audio_bucket=_get("AUDIO_BUCKET"),
            audio_kms_key_id=_get("AUDIO_KMS_KEY_ID"),
            retention_sweep_interval_s=_get_int("RETENTION_SWEEP_INTERVAL_S", 3600),
            log_file=_get("LOG_FILE"),
        )
