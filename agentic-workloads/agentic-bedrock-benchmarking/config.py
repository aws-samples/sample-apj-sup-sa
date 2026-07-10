"""Runtime configuration. Reads env vars; defaults are safe for local dev."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(env_name: str, default: bool) -> bool:
    val = os.getenv(env_name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _int(env_name: str, default: int) -> int:
    val = os.getenv(env_name)
    if not val:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _list(env_name: str) -> list[str]:
    val = os.getenv(env_name, "")
    return [x.strip() for x in val.split(",") if x.strip()]


@dataclass(frozen=True)
class AppConfig:
    is_production: bool
    deploy_mode: str                     # "production" | "development"

    # Region & model controls
    locked_region: str | None            # in production we lock to one region
    allowed_regions: tuple[str, ...]     # which regions the picker may show

    # Limits
    max_tokens_cap: int                  # upper bound for the max_tokens slider
    runs_per_model_cap: int              # upper bound for the runs slider

    # Allowed model id substrings (empty = no allowlist; production should set this)
    model_allowlist_patterns: tuple[str, ...]

    # Per-user quota (production only; ignored in dev)
    daily_invocation_limit: int
    quota_table_name: str | None

    # Auth (production only; ignored in dev)
    cognito_user_pool_id: str | None
    cognito_client_id: str | None
    cognito_region: str | None


def _load() -> AppConfig:
    deploy_mode = os.getenv("DEPLOY_MODE", "development").strip().lower()
    is_prod = deploy_mode == "production"

    if is_prod:
        locked_region = os.getenv("LOCKED_REGION") or None
        allowed_regions: tuple[str, ...] = (locked_region,) if locked_region else ()
        max_tokens_cap = _int("MAX_TOKENS_CAP", 2048)
        runs_per_model_cap = _int("RUNS_PER_MODEL_CAP", 3)
        allowlist = _list("MODEL_ALLOWLIST")  # empty = show all
        model_allowlist_patterns = tuple(allowlist)
        daily_invocation_limit = _int("DAILY_INVOCATION_LIMIT", 50)
        quota_table_name = os.getenv("QUOTA_TABLE_NAME", "agentic-bedrock-benchmarking-quota")
        cognito_user_pool_id = os.getenv("COGNITO_USER_POOL_ID")
        cognito_client_id = os.getenv("COGNITO_CLIENT_ID")
        cognito_region = os.getenv("COGNITO_REGION", locked_region)
    else:
        locked_region = None
        allowed_regions = ()
        max_tokens_cap = _int("MAX_TOKENS_CAP", 8192)
        runs_per_model_cap = _int("RUNS_PER_MODEL_CAP", 10)
        model_allowlist_patterns = tuple(_list("MODEL_ALLOWLIST"))  # usually empty in dev
        daily_invocation_limit = 0  # 0 = no quota check in dev
        quota_table_name = None
        cognito_user_pool_id = None
        cognito_client_id = None
        cognito_region = None

    return AppConfig(
        is_production=is_prod,
        deploy_mode=deploy_mode,
        locked_region=locked_region,
        allowed_regions=allowed_regions,
        max_tokens_cap=max_tokens_cap,
        runs_per_model_cap=runs_per_model_cap,
        model_allowlist_patterns=model_allowlist_patterns,
        daily_invocation_limit=daily_invocation_limit,
        quota_table_name=quota_table_name,
        cognito_user_pool_id=cognito_user_pool_id,
        cognito_client_id=cognito_client_id,
        cognito_region=cognito_region,
    )


CONFIG: AppConfig = _load()


def model_allowed(model_id: str) -> bool:
    """Check whether a model id should be visible in the picker."""
    if not CONFIG.model_allowlist_patterns:
        return True  # no allowlist configured = everything passes
    needle = model_id.lower()
    return any(p.lower() in needle for p in CONFIG.model_allowlist_patterns)
