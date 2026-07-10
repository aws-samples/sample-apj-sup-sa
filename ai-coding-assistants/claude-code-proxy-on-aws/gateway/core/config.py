"""Gateway configuration helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    return int(raw)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.lower() == "true"


def _default_database_url() -> str:
    explicit = os.getenv("DATABASE_URL")
    if explicit:
        return explicit

    endpoint = os.getenv("DB_ENDPOINT")
    username = os.getenv("DB_USERNAME")
    password = os.getenv("DB_PASSWORD")
    if endpoint and username and password:
        db_name = os.getenv("DB_NAME", "claude_proxy")
        return f"postgresql+asyncpg://{username}:{password}@{endpoint}:5432/{db_name}"

    return "postgresql+asyncpg://dev:dev@localhost:5432/claude_proxy"


def _default_read_database_url() -> str:
    endpoint = os.getenv("DB_READ_ENDPOINT")
    if not endpoint:
        return _default_database_url()
    username = os.getenv("DB_USERNAME")
    password = os.getenv("DB_PASSWORD")
    if username and password:
        db_name = os.getenv("DB_NAME", "claude_proxy")
        return f"postgresql+asyncpg://{username}:{password}@{endpoint}:5432/{db_name}"
    return _default_database_url()


@dataclass(frozen=True)
class Settings:
    """Environment-backed application settings."""

    app_name: str = field(
        default_factory=lambda: _env("APP_NAME", "claude-code-proxy-gateway")
    )
    aws_region: str = field(default_factory=lambda: _env("AWS_REGION", "ap-northeast-2"))
    identity_store_id: str = field(default_factory=lambda: _env("IDENTITY_STORE_ID", ""))
    identity_store_region: str = field(default_factory=lambda: _env("IDENTITY_STORE_REGION", ""))
    database_url: str = field(default_factory=_default_database_url)
    read_database_url: str = field(default_factory=_default_read_database_url)
    admin_origin_header: str = field(
        default_factory=lambda: _env("ADMIN_ORIGIN_HEADER", "x-admin-origin")
    )
    admin_origin_value: str = field(default_factory=lambda: _env("ADMIN_ORIGIN_VALUE", "apigw"))
    admin_principal_header: str = field(
        default_factory=lambda: _env("ADMIN_PRINCIPAL_HEADER", "x-admin-principal")
    )
    auth_origin_header: str = field(
        default_factory=lambda: _env("AUTH_ORIGIN_HEADER", "x-auth-origin")
    )
    auth_origin_value: str = field(
        default_factory=lambda: _env("AUTH_ORIGIN_VALUE", "apigw")
    )
    admin_origin_enforce: bool = field(
        default_factory=lambda: _env("ADMIN_ORIGIN_ENFORCE", "true").lower() == "true"
    )
    environment: str = field(
        default_factory=lambda: _env("ENVIRONMENT", "production")
    )
    auth_principal_header: str = field(
        default_factory=lambda: _env("AUTH_PRINCIPAL_HEADER", "x-auth-principal")
    )
    request_id_header: str = field(
        default_factory=lambda: _env("REQUEST_ID_HEADER", "x-request-id")
    )
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO"))
    bedrock_runtime_service: str = field(
        default_factory=lambda: _env("BEDROCK_RUNTIME_SERVICE", "bedrock-runtime")
    )
    otlp_endpoint: str = field(
        default_factory=lambda: _env("OTLP_GRPC_ENDPOINT", "http://127.0.0.1:4317")
    )
    otlp_export_interval_millis: int = field(
        default_factory=lambda: _env_int("OTLP_EXPORT_INTERVAL_MILLIS", 60_000)
    )
    runtime_log_full_payloads: bool = field(
        default_factory=lambda: _env_bool("RUNTIME_LOG_FULL_PAYLOADS", False)
    )
    runtime_log_stream_events: bool = field(
        default_factory=lambda: _env_bool("RUNTIME_LOG_STREAM_EVENTS", False)
    )
    virtual_key_ttl_ms: int = field(
        default_factory=lambda: _env_int("VIRTUAL_KEY_TTL_MS", 14_400_000)
    )
    anthropic_api_key_secret_arn: str = field(
        default_factory=lambda: _env("ANTHROPIC_API_KEY_SECRET_ARN", "")
    )
    anthropic_api_base_url: str = field(
        default_factory=lambda: _env("ANTHROPIC_API_BASE_URL", "https://api.anthropic.com")
    )
    anthropic_api_version: str = field(
        default_factory=lambda: _env("ANTHROPIC_API_VERSION", "2023-06-01")
    )
    anthropic_request_timeout_seconds: float = field(
        default_factory=lambda: float(_env("ANTHROPIC_REQUEST_TIMEOUT_SECONDS", "60"))
    )
    bedrock_breaker_open_seconds: float = field(
        default_factory=lambda: float(_env("BEDROCK_BREAKER_OPEN_SECONDS", "300"))
    )

    def __post_init__(self) -> None:
        if not self.admin_origin_enforce and self.environment not in ("local", "test"):
            msg = "ADMIN_ORIGIN_ENFORCE=false is only allowed when ENVIRONMENT is 'local' or 'test'"
            raise ValueError(msg)
        if self.otlp_export_interval_millis <= 0:
            msg = "OTLP_EXPORT_INTERVAL_MILLIS must be greater than 0"
            raise ValueError(msg)
        if self.virtual_key_ttl_ms < 0:
            msg = "VIRTUAL_KEY_TTL_MS must be greater than or equal to 0"
            raise ValueError(msg)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached application settings."""

    return Settings()
