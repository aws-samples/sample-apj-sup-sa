import base64
import json
import logging
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import boto3
from botocore.exceptions import ClientError

from common.athena import run_query
from common.cache import TTLCache
from common.dates import (
    active_day_from_sk,
    athena_partition_predicate,
    day_sk,
    iso_week_bounds,
    iso_week_key,
    month_bounds,
    month_key,
    quota_policy,
    quota_today,
    validate_time_zone,
)
from common.enforcement import publish_alert, reevaluate_and_unblock, unblock_with_reconciliation
from common.pricing import athena_weighted_sum, weighted_total, weights_from_config
from common.principals import (
    arn_to_username,
    assumed_role_name,
    display_name,
    is_iam_role_arn,
    is_iam_user_arn,
    username_to_arn,
    validate_username,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ddb = boto3.resource('dynamodb')
sns = boto3.client('sns')

USAGE_TABLE = os.environ['USAGE_TABLE']
CONFIG_TABLE = os.environ['CONFIG_TABLE']
AUDIT_TABLE = os.environ['AUDIT_TABLE']
SNS_TOPIC_ARN = os.environ['SNS_TOPIC_ARN']
ATHENA_DATABASE = os.environ['ATHENA_DATABASE']
ATHENA_TABLE = os.environ['ATHENA_TABLE']
ATHENA_WORKGROUP = os.environ['ATHENA_WORKGROUP']
ACCOUNT_ID = os.environ['ACCOUNT_ID']
ADMIN_USERNAMES = {u.strip() for u in os.environ.get('ADMIN_USERNAMES', '').split(',') if u.strip()}
# Caller-only admin allowlist by IAM role name (for the console BFF's execution role, which
# signs with its own assumed-role creds). Matched on role NAME, not the session-suffixed ARN.
# This grants ADMIN-CALLER status only; enforcement TARGETS remain IAM users (validate_arn).
ADMIN_ROLE_NAMES = {r.strip() for r in os.environ.get('ADMIN_ROLE_NAMES', '').split(',') if r.strip()}
USAGE_RETENTION_DAYS = int(os.environ.get('USAGE_RETENTION_DAYS', '90'))

# Short warm-container query cache (default 10s) to absorb the multi-second
# Athena aggregate on rapid re-fetches without masking writes for long.
# Set ATHENA_CACHE_TTL_SECONDS=0 to disable entirely.
ATHENA_CACHE_TTL = int(os.environ.get('ATHENA_CACHE_TTL_SECONDS', '10'))

NUMERIC_KEYS = {
    'dailyTokenLimit', 'weeklyTokenLimit', 'monthlyTokenLimit', 'warningThresholdPct',
}
GLOBAL_NUMERIC_KEYS = {'quotaTimeZoneVersion'}
ALLOWED_CONFIG_KEYS = NUMERIC_KEYS | {'exempt'}
MAX_TOKEN_LIMIT = 10**12  # 1 trillion: any value above is treated as a typo

usage_table = ddb.Table(USAGE_TABLE)
config_table = ddb.Table(CONFIG_TABLE)
audit_table = ddb.Table(AUDIT_TABLE)

_athena_cache: TTLCache = TTLCache(maxsize=128, ttl=ATHENA_CACHE_TTL)


def respond(status: int, body: Any) -> dict:
    return {
        'statusCode': status,
        'headers': {
            'content-type': 'application/json',
            'cache-control': 'no-store',
        },
        'body': json.dumps(body, default=str),
    }


def caller_arn(event: dict) -> str | None:
    ctx = event.get('requestContext', {})
    return ctx.get('identity', {}).get('userArn') or ctx.get('authorizer', {}).get('iam', {}).get('userArn')


def caller_username(arn: str | None) -> str | None:
    if not arn or not is_iam_user_arn(arn):
        return None
    return arn.rsplit('/', 1)[-1]


def is_admin(arn: str | None) -> bool:
    # Role caller (console BFF execution role): match on role name, never the session ARN.
    role = assumed_role_name(arn) if arn else None
    if role and role in ADMIN_ROLE_NAMES:
        return True
    name = caller_username(arn)
    return bool(name) and name in ADMIN_USERNAMES


def resolve_user(name: str) -> tuple[str, str]:
    """Path param `{username}` → (username, arn). validate_username + ACCOUNT_ID."""
    arn = username_to_arn(name, ACCOUNT_ID)
    return name, arn


def cached_query(sql: str, parameters: list[str]) -> list[dict[str, str]]:
    cache_key = sql + '\0' + '\0'.join(parameters)
    hit = _athena_cache.get(cache_key)
    if hit is not None:
        return hit
    rows = run_query(sql, database=ATHENA_DATABASE, workgroup=ATHENA_WORKGROUP, parameters=parameters, timeout=25)
    _athena_cache.set(cache_key, rows)
    return rows


def _normalize_config(item: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for k, v in item.items():
        if k == 'ConfigKey':
            continue
        if isinstance(v, bool):
            merged[k] = v
        elif k in NUMERIC_KEYS | GLOBAL_NUMERIC_KEYS:
            merged[k] = int(v)
        else:
            merged[k] = v
    return merged


def get_effective_config(user_arn: str) -> dict[str, Any]:
    default_item = config_table.get_item(Key={'ConfigKey': 'default'}).get('Item', {})
    user_item = config_table.get_item(Key={'ConfigKey': user_arn}).get('Item', {})
    merged: dict[str, Any] = {}
    for source in (default_item, user_item):
        merged.update(_normalize_config(source))
    return merged


def load_effective_configs() -> dict[str, dict[str, Any]]:
    """All effective configs in ONE ConfigTable scan: {user_arn: merged config}, plus
    'default'. Avoids the N+1 per-user GetItem pattern in list_users (2 GetItems/user
    → ~15s at 1000 users). ConfigTable is tiny: default + only users with overrides.
    """
    default_cfg: dict[str, Any] = {}
    overrides: dict[str, dict[str, Any]] = {}
    last: dict[str, Any] | None = None
    while True:
        kwargs: dict[str, Any] = {}
        if last:
            kwargs['ExclusiveStartKey'] = last
        resp = config_table.scan(**kwargs)
        for item in resp.get('Items', []):
            key = item.get('ConfigKey')
            if key == 'default':
                default_cfg = _normalize_config(item)
            elif key:
                overrides[key] = _normalize_config(item)
        last = resp.get('LastEvaluatedKey')
        if not last:
            break

    configs: dict[str, dict[str, Any]] = {'default': default_cfg}
    for arn, ov in overrides.items():
        configs[arn] = {**default_cfg, **ov}
    return configs


def get_credit_weights() -> dict[str, float]:
    """Global credit weights from the default config row (cached 60s)."""
    cache_key = '__credit_weights__'
    hit = _athena_cache.get(cache_key)
    if hit is not None:
        return hit
    default_item = config_table.get_item(Key={'ConfigKey': 'default'}).get('Item', {})
    weights = weights_from_config(default_item)
    _athena_cache.set(cache_key, weights)
    return weights


def get_global_quota_policy(*, consistent: bool = True) -> dict[str, Any]:
    item = config_table.get_item(
        Key={'ConfigKey': 'default'},
        ConsistentRead=consistent,
    ).get('Item', {})
    time_zone, policy_version = quota_policy(item)
    return {
        'mode': 'CALENDAR',
        'quotaTimeZone': time_zone,
        'quotaTimeZoneVersion': policy_version,
    }


def get_state(user_arn: str) -> dict:
    return usage_table.get_item(Key={'UserARN': user_arn, 'SK': 'STATE'}).get('Item', {})


def get_day_row(user_arn: str, day: str, policy_version: int = 1) -> dict:
    return usage_table.get_item(
        Key={'UserARN': user_arn, 'SK': day_sk(day, policy_version)}
    ).get('Item', {})


def get_latest_activity(user_arn: str) -> str | None:
    """Latest ingestion timestamp across retained DAY policy namespaces."""
    latest: str | None = None
    last: dict[str, Any] | None = None
    while True:
        kwargs: dict[str, Any] = {
            'KeyConditionExpression': 'UserARN = :u AND begins_with(#sk, :p)',
            'ExpressionAttributeNames': {'#sk': 'SK'},
            'ExpressionAttributeValues': {':u': user_arn, ':p': 'DAY#'},
            'ProjectionExpression': 'lastUpdated',
        }
        if last:
            kwargs['ExclusiveStartKey'] = last
        resp = usage_table.query(**kwargs)
        for item in resp.get('Items', []):
            timestamp = item.get('lastUpdated')
            if isinstance(timestamp, str) and (latest is None or timestamp > latest):
                latest = timestamp
        last = resp.get('LastEvaluatedKey')
        if not last:
            return latest


def sum_day_components(items: list[dict[str, Any]]) -> tuple[int, int, int, int]:
    """Component-wise sum of DAY-row raw 4-tuples. Weighting is applied once by the
    caller (weighted_total) so cost weights stay runtime-tunable via ConfigTable.
    """
    raw = cache_read = cache_write = output = 0
    for it in items:
        raw += int(it.get('rawInputTokens', 0))
        cache_read += int(it.get('cacheReadTokens', 0))
        cache_write += int(it.get('cacheWriteTokens', 0))
        output += int(it.get('outputTokens', 0))
    return raw, cache_read, cache_write, output


def query_day_rows(
    user_arn: str,
    start: date,
    end: date,
    *,
    policy_version: int = 1,
    consistent: bool = False,
) -> list[dict[str, Any]]:
    """Active-policy DAY rows in [start, end] via one key-condition Query.

    DAY SK suffixes are fixed-width yyyy-mm-dd so lexicographic order == chronological;
    SK BETWEEN is an exact server-side range. At most ~31 items, so pagination is
    defensive only.
    """
    items: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {
        'KeyConditionExpression': 'UserARN = :u AND #sk BETWEEN :lo AND :hi',
        'ExpressionAttributeNames': {'#sk': 'SK'},
        'ExpressionAttributeValues': {
            ':u': user_arn,
            ':lo': day_sk(start, policy_version),
            ':hi': day_sk(end, policy_version),
        },
        'ConsistentRead': consistent,
    }
    while True:
        resp = usage_table.query(**kwargs)
        items.extend(resp.get('Items', []))
        last = resp.get('LastEvaluatedKey')
        if not last:
            break
        kwargs['ExclusiveStartKey'] = last
    return items


def query_user_period_total(
    user_arn: str,
    start: date,
    end: date,
    *,
    policy_version: int = 1,
    consistent: bool = False,
) -> int:
    """Credit-weighted total over [start, end] derived from DAY rows (no Athena).

    `end` should be clamped to today by callers — future days have no rows. Weights
    are applied at read time so retuning ConfigTable cost weights re-prices instantly.
    """
    raw, cache_read, cache_write, output = sum_day_components(
        query_day_rows(
            user_arn, start, end,
            policy_version=policy_version,
            consistent=consistent,
        )
    )
    return weighted_total(raw, cache_read, cache_write, output, get_credit_weights())


def usage_snapshot(user_arn: str) -> dict[str, Any]:
    policy = get_global_quota_policy()
    time_zone = policy['quotaTimeZone']
    policy_version = policy['quotaTimeZoneVersion']
    today = quota_today(time_zone)
    week_start, week_end = iso_week_bounds(today)
    month_start, month_end = month_bounds(today)

    day_row = get_day_row(user_arn, today.isoformat(), policy_version)
    day_raw = int(day_row.get('rawInputTokens', 0))
    day_cache_read = int(day_row.get('cacheReadTokens', 0))
    day_cache_write = int(day_row.get('cacheWriteTokens', 0))
    day_output = int(day_row.get('outputTokens', 0))
    day_calls = int(day_row.get('invocationCount', 0))

    # End clamped to today: future days have no DAY rows; period_total derives from them.
    week_total = query_user_period_total(
        user_arn, week_start, today, policy_version=policy_version,
    )
    month_total = query_user_period_total(
        user_arn, month_start, today, policy_version=policy_version,
    )

    state = get_state(user_arn)
    cfg = get_effective_config(user_arn)

    # day total in credits (cost-weighted) to match enforcement & limits.
    day_total = weighted_total(day_raw, day_cache_read, day_cache_write, day_output, get_credit_weights())

    return {
        'username': arn_to_username(user_arn),
        'user_arn': user_arn,
        # as_of = 조회 시각, last_activity = 마지막 토큰 누적 시각.
        # 오늘 활동이 없으면 today row가 비어 있으므로, /usage/aggregate(Users 탭)와
        # 일치하도록 가장 최근 DAY row의 lastUpdated를 본다.
        'as_of': datetime.now(timezone.utc).isoformat(),
        'last_activity': (day_row.get('lastUpdated')
                          or get_latest_activity(user_arn)),
        'quota_policy': policy,
        'day': {
            'date': today.isoformat(),
            'input_tokens': day_raw,
            'cache_read_tokens': day_cache_read,
            'cache_write_tokens': day_cache_write,
            'output_tokens': day_output,
            'total_tokens': day_total,
            'invocation_count': day_calls,
            'limit': cfg.get('dailyTokenLimit'),
        },
        'week': {
            'iso_week': iso_week_key(week_start),
            'start': week_start.isoformat(),
            'end': week_end.isoformat(),
            'total_tokens': week_total,
            'limit': cfg.get('weeklyTokenLimit'),
        },
        'month': {
            'period': month_key(month_start),
            'start': month_start.isoformat(),
            'end': month_end.isoformat(),
            'total_tokens': month_total,
            'limit': cfg.get('monthlyTokenLimit'),
        },
        'state': {
            'blocked': bool(state.get('blocked')),
            'blocked_scope': state.get('blockedScope'),
            'blocked_reason': state.get('blockedReason'),
            'blocked_at': state.get('blockedAt'),
        },
        'config': {
            'exempt': bool(cfg.get('exempt')),
            'warning_threshold_pct': cfg.get('warningThresholdPct'),
        },
    }


def _period_bounds(period: str) -> tuple[date, date]:
    today = date.today()
    if period == 'week':
        return iso_week_bounds(today)
    if period == 'month':
        return month_bounds(today)
    if period == 'day':
        return today, today
    raise ValueError('period must be one of: day, week, month')


METRIC_EXPRS = {
    # inputTokens는 enforcement(DDB inputTokens)와 같은 합계 — raw + cache read + cache write.
    'inputTokens': (
        'SUM(COALESCE(input.inputtokencount,0) + COALESCE(input.cachereadinputtokencount,0)'
        ' + COALESCE(input.cachewriteinputtokencount,0))'
    ),
    'rawInputTokens': 'SUM(COALESCE(input.inputtokencount,0))',
    'cacheReadTokens': 'SUM(COALESCE(input.cachereadinputtokencount,0))',
    'cacheWriteTokens': 'SUM(COALESCE(input.cachewriteinputtokencount,0))',
    'outputTokens': 'SUM(COALESCE(output.outputtokencount,0))',
    'totalTokens': (
        'SUM(COALESCE(input.inputtokencount,0) + COALESCE(input.cachereadinputtokencount,0)'
        ' + COALESCE(input.cachewriteinputtokencount,0) + COALESCE(output.outputtokencount,0))'
    ),
    'invocations': 'COUNT(*)',
}


def query_top_users(period: str, metric: str, limit: int) -> list[dict[str, Any]]:
    # `credits` is the cost-weighted total (matches limits/enforcement); the
    # raw breakdowns stay available for analysis.
    if metric == 'credits':
        metric_expr = athena_weighted_sum(get_credit_weights())
    else:
        metric_expr = METRIC_EXPRS.get(metric)
    if not metric_expr:
        raise ValueError(f"metric must be one of: {sorted(METRIC_EXPRS) + ['credits']}")
    if not 1 <= limit <= 100:
        raise ValueError('limit must be between 1 and 100')

    start, end = _period_bounds(period)
    predicate = athena_partition_predicate(start, end)
    sql = f"""
    SELECT identity.arn AS user_arn,
           CAST({metric_expr} AS VARCHAR) AS value
    FROM {ATHENA_DATABASE}.{ATHENA_TABLE}
    WHERE {predicate}
      AND "timestamp" BETWEEN ? AND ?
      AND identity.arn IS NOT NULL
    GROUP BY identity.arn
    ORDER BY {metric_expr} DESC
    LIMIT {limit}
    """
    rows = cached_query(sql, [
        f"'{start.isoformat()}T00:00:00Z'",
        f"'{end.isoformat()}T23:59:59Z'",
    ])
    return [{'user_arn': r['user_arn'], 'value': int(float(r['value']))} for r in rows if r.get('value')]


def query_by_model(period: str) -> list[dict[str, Any]]:
    start, end = _period_bounds(period)
    predicate = athena_partition_predicate(start, end)
    sql = f"""
    SELECT modelid AS model_id,
           CAST(SUM(COALESCE(input.inputtokencount,0)) AS VARCHAR) AS input_tokens,
           CAST(SUM(COALESCE(input.cachereadinputtokencount,0)) AS VARCHAR) AS cache_read_tokens,
           CAST(SUM(COALESCE(input.cachewriteinputtokencount,0)) AS VARCHAR) AS cache_write_tokens,
           CAST(SUM(COALESCE(output.outputtokencount,0)) AS VARCHAR) AS output_tokens,
           CAST(SUM(COALESCE(input.inputtokencount,0)
                  + COALESCE(input.cachereadinputtokencount,0)
                  + COALESCE(input.cachewriteinputtokencount,0)
                  + COALESCE(output.outputtokencount,0)) AS VARCHAR) AS total_tokens,
           CAST(COUNT(*) AS VARCHAR) AS invocations
    FROM {ATHENA_DATABASE}.{ATHENA_TABLE}
    WHERE {predicate}
      AND "timestamp" BETWEEN ? AND ?
    GROUP BY modelid
    ORDER BY SUM(COALESCE(input.inputtokencount,0)
               + COALESCE(input.cachereadinputtokencount,0)
               + COALESCE(input.cachewriteinputtokencount,0)
               + COALESCE(output.outputtokencount,0)) DESC
    """
    rows = cached_query(sql, [
        f"'{start.isoformat()}T00:00:00Z'",
        f"'{end.isoformat()}T23:59:59Z'",
    ])
    return [{
        'model_id': r['model_id'],
        'input_tokens': int(r['input_tokens']),
        'cache_read_tokens': int(r['cache_read_tokens']),
        'cache_write_tokens': int(r['cache_write_tokens']),
        'output_tokens': int(r['output_tokens']),
        'total_tokens': int(r['total_tokens']),
        'invocations': int(r['invocations']),
    } for r in rows]


def _trend_bounds(period: str) -> tuple[date, date]:
    today = date.today()
    if period == 'week':
        return iso_week_bounds(today)
    if period == 'month':
        return month_bounds(today)
    if period == 'last7d':
        return today - timedelta(days=6), today
    if period == 'last30d':
        return today - timedelta(days=29), today
    raise ValueError('period must be one of: week, month, last7d, last30d')


HOUR_BUCKET_FMT = '%Y-%m-%dT%H:00'
DAY_BUCKET_FMT = '%Y-%m-%d'
# Cap custom ranges so a typo can't scan the whole table; projection covers 2024+.
MAX_TREND_RANGE_DAYS = 366
# Sliding hour-window presets for the Statistics model-trend chart.
HOUR_PERIOD_WINDOWS = {'last24h': 24, 'last48h': 48, 'last7d': 168}
DEFAULT_HOUR_WINDOW = 48
# Per-user detail trend uses a shorter fixed hour window.
USER_HISTORY_HOUR_WINDOW = 24


def _parse_custom_range(date_from: str | None, date_to: str | None) -> tuple[date, date]:
    if not date_from or not date_to:
        raise ValueError('custom range requires both from and to (YYYY-MM-DD)')
    try:
        start = date.fromisoformat(date_from)
        end = date.fromisoformat(date_to)
    except ValueError:
        raise ValueError('from/to must be YYYY-MM-DD dates')
    if end < start:
        raise ValueError('to must be on or after from')
    if (end - start).days > MAX_TREND_RANGE_DAYS:
        raise ValueError(f'date range must be <= {MAX_TREND_RANGE_DAYS} days')
    return start, end


def _hourly_window(hours: int = DEFAULT_HOUR_WINDOW) -> tuple[datetime, datetime]:
    # Floor `now` to the minute so the cache key is stable within a TTL window;
    # microsecond-varying bounds would miss the cache on every call.
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    return now - timedelta(hours=hours), now


def _hour_buckets(start_dt: datetime, end_dt: datetime) -> list[str]:
    cur = start_dt.replace(minute=0, second=0, microsecond=0)
    end_hour = end_dt.replace(minute=0, second=0, microsecond=0)
    buckets: list[str] = []
    while cur <= end_hour:
        buckets.append(cur.strftime(HOUR_BUCKET_FMT))
        cur += timedelta(hours=1)
    return buckets


def _day_buckets(start: date, end: date) -> list[str]:
    cur = start
    buckets: list[str] = []
    while cur <= end:
        buckets.append(cur.strftime(DAY_BUCKET_FMT))
        cur += timedelta(days=1)
    return buckets


def _bucket_query_shape(
    period: str,
    granularity: str,
    date_from: str | None = None,
    date_to: str | None = None,
) -> tuple[str, str, list[str], list[str]]:
    """Return (bucket_fmt, partition_predicate, timestamp_params, all_buckets).

    all_buckets is the complete, gap-free list of bucket keys (UTC) over the
    window so callers can zero-fill empty buckets and keep the time axis even.
    bucket_fmt is a fixed format string (not user input) safe to interpolate.
    `period='custom'` (day granularity only) uses the explicit date_from/date_to.
    """
    if granularity == 'hour':
        # Hour granularity is a sliding window; `period` only picks its length.
        start_dt, end_dt = _hourly_window(HOUR_PERIOD_WINDOWS.get(period, DEFAULT_HOUR_WINDOW))
        return (
            HOUR_BUCKET_FMT,
            athena_partition_predicate(start_dt.date(), end_dt.date()),
            [
                f"'{start_dt.strftime('%Y-%m-%dT%H:%M:%SZ')}'",
                f"'{end_dt.strftime('%Y-%m-%dT%H:%M:%SZ')}'",
            ],
            _hour_buckets(start_dt, end_dt),
        )
    if granularity != 'day':
        raise ValueError('granularity must be one of: day, hour')
    if period == 'custom':
        start, end = _parse_custom_range(date_from, date_to)
    else:
        start, end = _trend_bounds(period)
    return (
        DAY_BUCKET_FMT,
        athena_partition_predicate(start, end),
        [f"'{start.isoformat()}T00:00:00Z'", f"'{end.isoformat()}T23:59:59Z'"],
        _day_buckets(start, end),
    )


TREND_METRICS = {'totalTokens', 'invocations'}


def query_by_model_trend(
    period: str,
    top: int,
    metric: str,
    granularity: str = 'day',
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict[str, Any]:
    """Per-model usage time series, bucketed by day (default) or hour.

    One Athena query (bucket × model GROUP BY); top-N models are picked in memory by
    `metric` so toggling tokens/invocations on the client never re-queries. Models
    outside top-N are folded into a single `other` series so the chart stays legible.
    `granularity='hour'` uses a fixed last-48h window and ignores `period`.
    `period='custom'` uses date_from/date_to (day granularity only).
    """
    if metric not in TREND_METRICS:
        raise ValueError(f'metric must be one of: {sorted(TREND_METRICS)}')
    if not 1 <= top <= 20:
        raise ValueError('top must be between 1 and 20')

    bucket_fmt, predicate, params, all_buckets = _bucket_query_shape(period, granularity, date_from, date_to)
    sql = f"""
    SELECT date_format(from_iso8601_timestamp("timestamp"), '{bucket_fmt}') AS day,
           modelid AS model_id,
           CAST(SUM(COALESCE(input.inputtokencount,0)) AS VARCHAR) AS input_tokens,
           CAST(SUM(COALESCE(input.cachereadinputtokencount,0)) AS VARCHAR) AS cache_read_tokens,
           CAST(SUM(COALESCE(input.cachewriteinputtokencount,0)) AS VARCHAR) AS cache_write_tokens,
           CAST(SUM(COALESCE(output.outputtokencount,0)) AS VARCHAR) AS output_tokens,
           CAST(SUM(COALESCE(input.inputtokencount,0)
                  + COALESCE(input.cachereadinputtokencount,0)
                  + COALESCE(input.cachewriteinputtokencount,0)
                  + COALESCE(output.outputtokencount,0)) AS VARCHAR) AS total_tokens,
           CAST(COUNT(*) AS VARCHAR) AS invocations
    FROM {ATHENA_DATABASE}.{ATHENA_TABLE}
    WHERE {predicate}
      AND "timestamp" BETWEEN ? AND ?
      AND modelid IS NOT NULL
    GROUP BY date_format(from_iso8601_timestamp("timestamp"), '{bucket_fmt}'), modelid
    ORDER BY day
    """
    rows = cached_query(sql, params)

    metric_field = 'total_tokens' if metric == 'totalTokens' else 'invocations'
    model_totals: dict[str, int] = {}
    for r in rows:
        model_totals[r['model_id']] = model_totals.get(r['model_id'], 0) + int(r[metric_field])
    top_models = {
        m for m, _ in sorted(model_totals.items(), key=lambda kv: kv[1], reverse=True)[:top]
    }
    has_other = len(model_totals) > len(top_models)

    fields = ('input_tokens', 'cache_read_tokens', 'cache_write_tokens',
              'output_tokens', 'total_tokens', 'invocations')
    merged: dict[tuple[str, str], dict[str, int]] = {}
    for r in rows:
        model = r['model_id'] if r['model_id'] in top_models else 'other'
        bucket = merged.setdefault((r['day'], model), {f: 0 for f in fields})
        for f in fields:
            bucket[f] += int(r[f])

    out_rows = [
        {'day': day, 'model_id': model, **vals}
        for (day, model), vals in sorted(merged.items())
    ]
    return {
        'period': period,
        'granularity': granularity,
        'metric': metric,
        'top': top,
        'models': sorted(top_models, key=lambda m: model_totals[m], reverse=True)
                  + (['other'] if has_other else []),
        'buckets': all_buckets,
        'rows': out_rows,
    }


def _history_row(bucket: str, r: dict[str, str]) -> dict[str, Any]:
    return {
        'day': bucket,
        'input_tokens': int(r['input_tokens']),
        'cache_read_tokens': int(r['cache_read_tokens']),
        'cache_write_tokens': int(r['cache_write_tokens']),
        'output_tokens': int(r['output_tokens']),
        'total_tokens': int(r['total_tokens']),
        'invocations': int(r['invocations']),
    }


def _query_user_history_hourly(user_arn: str) -> tuple[list[dict[str, Any]], list[str]]:
    start, end = _hourly_window(USER_HISTORY_HOUR_WINDOW)
    predicate = athena_partition_predicate(start.date(), end.date())
    sql = f"""
    SELECT date_format(from_iso8601_timestamp("timestamp"), '{HOUR_BUCKET_FMT}') AS bucket,
           CAST(SUM(COALESCE(input.inputtokencount,0)) AS VARCHAR) AS input_tokens,
           CAST(SUM(COALESCE(input.cachereadinputtokencount,0)) AS VARCHAR) AS cache_read_tokens,
           CAST(SUM(COALESCE(input.cachewriteinputtokencount,0)) AS VARCHAR) AS cache_write_tokens,
           CAST(SUM(COALESCE(output.outputtokencount,0)) AS VARCHAR) AS output_tokens,
           CAST(SUM(COALESCE(input.inputtokencount,0)
                  + COALESCE(input.cachereadinputtokencount,0)
                  + COALESCE(input.cachewriteinputtokencount,0)
                  + COALESCE(output.outputtokencount,0)) AS VARCHAR) AS total_tokens,
           CAST(COUNT(*) AS VARCHAR) AS invocations
    FROM {ATHENA_DATABASE}.{ATHENA_TABLE}
    WHERE {predicate}
      AND "timestamp" BETWEEN ? AND ?
      AND identity.arn = ?
    GROUP BY date_format(from_iso8601_timestamp("timestamp"), '{HOUR_BUCKET_FMT}')
    ORDER BY bucket
    """
    rows = cached_query(sql, [
        f"'{start.strftime('%Y-%m-%dT%H:%M:%SZ')}'",
        f"'{end.strftime('%Y-%m-%dT%H:%M:%SZ')}'",
        f"'{user_arn}'",
    ])
    return [_history_row(r['bucket'], r) for r in rows], _hour_buckets(start, end)


def query_user_history(user_arn: str, period: str, granularity: str = 'day') -> tuple[list[dict[str, Any]], list[str]]:
    """Returns (rows, all_buckets). all_buckets is the gap-free bucket list so the
    caller can zero-fill empty days/hours and keep the time axis even."""
    if granularity == 'hour':
        return _query_user_history_hourly(user_arn)
    if granularity != 'day':
        raise ValueError('granularity must be one of: day, hour')

    today = date.today()
    if period == 'month':
        start, end = month_bounds(today)
    elif period == 'week':
        start, end = iso_week_bounds(today)
    elif period == 'last30d':
        start, end = today - timedelta(days=29), today
    else:
        raise ValueError('period must be one of: week, month, last30d')

    predicate = athena_partition_predicate(start, end)
    sql = f"""
    SELECT date_format(from_iso8601_timestamp("timestamp"), '%Y-%m-%d') AS day,
           CAST(SUM(COALESCE(input.inputtokencount,0)) AS VARCHAR) AS input_tokens,
           CAST(SUM(COALESCE(input.cachereadinputtokencount,0)) AS VARCHAR) AS cache_read_tokens,
           CAST(SUM(COALESCE(input.cachewriteinputtokencount,0)) AS VARCHAR) AS cache_write_tokens,
           CAST(SUM(COALESCE(output.outputtokencount,0)) AS VARCHAR) AS output_tokens,
           CAST(SUM(COALESCE(input.inputtokencount,0)
                  + COALESCE(input.cachereadinputtokencount,0)
                  + COALESCE(input.cachewriteinputtokencount,0)
                  + COALESCE(output.outputtokencount,0)) AS VARCHAR) AS total_tokens,
           CAST(COUNT(*) AS VARCHAR) AS invocations
    FROM {ATHENA_DATABASE}.{ATHENA_TABLE}
    WHERE {predicate}
      AND "timestamp" BETWEEN ? AND ?
      AND identity.arn = ?
    GROUP BY date_format(from_iso8601_timestamp("timestamp"), '%Y-%m-%d')
    ORDER BY day
    """
    rows = cached_query(sql, [
        f"'{start.isoformat()}T00:00:00Z'",
        f"'{end.isoformat()}T23:59:59Z'",
        f"'{user_arn}'",
    ])
    return [_history_row(r['day'], r) for r in rows], _day_buckets(start, end)


def list_blocked_users() -> list[dict[str, Any]]:
    blocked: list[dict[str, Any]] = []
    last: dict[str, Any] | None = None
    while True:
        kwargs: dict[str, Any] = {
            'FilterExpression': '#sk = :sk AND blocked = :t',
            'ExpressionAttributeNames': {'#sk': 'SK'},
            'ExpressionAttributeValues': {':sk': 'STATE', ':t': True},
        }
        if last:
            kwargs['ExclusiveStartKey'] = last
        resp = usage_table.scan(**kwargs)
        for item in resp.get('Items', []):
            blocked.append({
                'user_arn': item['UserARN'],
                'blocked_scope': item.get('blockedScope'),
                'blocked_reason': item.get('blockedReason'),
                'blocked_at': item.get('blockedAt'),
            })
        last = resp.get('LastEvaluatedKey')
        if not last:
            break
    return blocked


def _user_index_entry(user_arn: str) -> dict[str, Any]:
    return {
        'user_arn': user_arn,
        'latest_day': None,
        'latest_day_total_tokens': 0,
        'latest_day_invocation_count': 0,
        # Active quota-calendar day, separate from latest activity.
        'today_total_tokens': 0,
        'today_invocation_count': 0,
        # Raw 4-tuple component sums for the current ISO week / calendar month, summed
        # across DAY rows in the same scan; weighted once in list_users (tunable weights).
        'week_components': [0, 0, 0, 0],
        'month_components': [0, 0, 0, 0],
        'last_updated': None,
        'state': {},
        'has_limit_override': False,
    }


def _accumulate_components(target: list[int], item: dict[str, Any]) -> None:
    target[0] += int(item.get('rawInputTokens', 0))
    target[1] += int(item.get('cacheReadTokens', 0))
    target[2] += int(item.get('cacheWriteTokens', 0))
    target[3] += int(item.get('outputTokens', 0))


def _day_total(item: dict[str, Any], weights: dict[str, float]) -> int:
    # Credits (cost-weighted) for one DAY row.
    return weighted_total(
        int(item.get('rawInputTokens', 0)),
        int(item.get('cacheReadTokens', 0)),
        int(item.get('cacheWriteTokens', 0)),
        int(item.get('outputTokens', 0)),
        weights,
    )


def all_users_daily_history(days: int) -> dict[str, Any]:
    """All-user daily token series for the last `days` days, from ONE DDB scan.

    Replaces the console Overview's per-user history fan-out (one /usage/{user}/history
    Athena query per user). Sums the raw 4-tuple per day across every user; the console
    charts the breakdown (raw/cacheRead/cacheWrite/output), not credits, so no weighting.
    Returns gap-free `buckets` for zero-fill, matching the /history contract.
    """
    policy = get_global_quota_policy()
    policy_version = policy['quotaTimeZoneVersion']
    today = quota_today(policy['quotaTimeZone'])
    start = today - timedelta(days=days - 1)
    start_key = start.isoformat()
    # day -> [raw, cache_read, cache_write, output]
    by_day: dict[str, list[int]] = {}
    last: dict[str, Any] | None = None
    while True:
        kwargs: dict[str, Any] = {
            'ProjectionExpression': (
                '#sk, rawInputTokens, cacheReadTokens, '
                'cacheWriteTokens, outputTokens'
            ),
            'ExpressionAttributeNames': {'#sk': 'SK'},
        }
        if last:
            kwargs['ExclusiveStartKey'] = last
        resp = usage_table.scan(**kwargs)
        for item in resp.get('Items', []):
            sk = item.get('SK', '')
            day_key = active_day_from_sk(sk, policy_version)
            if day_key is None:
                continue
            if day_key < start_key:
                continue
            slot = by_day.setdefault(day_key, [0, 0, 0, 0])
            _accumulate_components(slot, item)
        last = resp.get('LastEvaluatedKey')
        if not last:
            break

    rows = [
        {
            'day': day_key,
            'input_tokens': c[0],
            'cache_read_tokens': c[1],
            'cache_write_tokens': c[2],
            'output_tokens': c[3],
            'total_tokens': c[0] + c[1] + c[2] + c[3],
        }
        for day_key, c in sorted(by_day.items())
    ]
    buckets = [(start + timedelta(days=i)).isoformat() for i in range(days)]
    return {'days': days, 'buckets': buckets, 'rows': rows}


def _scan_user_index() -> dict[str, dict[str, Any]]:
    """Build the per-user index from UsageTable + ConfigTable scans.

    today/latest DAY + STATE come from the UsageTable scan; week/month credit components are
    derived in the same scan by folding DAY rows. String compare on DAY# keys is valid — they're
    fixed-width yyyy-mm-dd.
    """
    policy = get_global_quota_policy()
    policy_version = policy['quotaTimeZoneVersion']
    today = quota_today(policy['quotaTimeZone'])
    today_key = today.isoformat()
    week_start = iso_week_bounds(today)[0]
    month_start = month_bounds(today)[0]
    week_start_key = week_start.isoformat()
    month_start_key = month_start.isoformat()
    weights = get_credit_weights()
    users: dict[str, dict[str, Any]] = {}
    last: dict[str, Any] | None = None
    while True:
        kwargs: dict[str, Any] = {
            'ProjectionExpression': (
                'UserARN, #sk, rawInputTokens, cacheReadTokens, cacheWriteTokens, '
                'outputTokens, invocationCount, lastUpdated, '
                'blocked, blockedScope, blockedReason, blockedAt'
            ),
            'ExpressionAttributeNames': {'#sk': 'SK'},
        }
        if last:
            kwargs['ExclusiveStartKey'] = last
        resp = usage_table.scan(**kwargs)
        for item in resp.get('Items', []):
            user_arn = item.get('UserARN')
            sk = item.get('SK', '')
            if not user_arn or not isinstance(sk, str):
                continue
            if sk != 'STATE' and not sk.startswith('DAY#'):
                continue

            entry = users.setdefault(user_arn, _user_index_entry(user_arn))
            if sk == 'STATE':
                entry['state'] = {
                    'blocked': bool(item.get('blocked')),
                    'blocked_scope': item.get('blockedScope'),
                    'blocked_reason': item.get('blockedReason'),
                    'blocked_at': item.get('blockedAt'),
                }
                continue

            timestamp = item.get('lastUpdated')
            if isinstance(timestamp, str) and (
                entry['last_updated'] is None or timestamp > entry['last_updated']
            ):
                entry['last_updated'] = timestamp

            day_key = active_day_from_sk(sk, policy_version)
            if day_key is None:
                continue
            if entry['latest_day'] is None or day_key > entry['latest_day']:
                entry['latest_day'] = day_key
                entry['latest_day_total_tokens'] = _day_total(item, weights)
                entry['latest_day_invocation_count'] = int(item.get('invocationCount', 0))
            if day_key == today_key:
                entry['today_total_tokens'] = _day_total(item, weights)
                entry['today_invocation_count'] = int(item.get('invocationCount', 0))
            # Derive week/month by folding DAY rows in this same scan.
            if day_key >= week_start_key:
                _accumulate_components(entry['week_components'], item)
            if day_key >= month_start_key:
                _accumulate_components(entry['month_components'], item)

        last = resp.get('LastEvaluatedKey')
        if not last:
            break

    last = None
    while True:
        kwargs = {'ProjectionExpression': 'ConfigKey'}
        if last:
            kwargs['ExclusiveStartKey'] = last
        resp = config_table.scan(**kwargs)
        for item in resp.get('Items', []):
            key = item.get('ConfigKey')
            if key and key != 'default':
                users.setdefault(key, _user_index_entry(key))['has_limit_override'] = True
        last = resp.get('LastEvaluatedKey')
        if not last:
            break

    return users


def _user_status(cfg: dict[str, Any], state: dict[str, Any], day_total: int) -> str:
    if cfg.get('exempt'):
        return 'exempt'
    if state.get('blocked'):
        return 'blocked'
    day_limit = int(cfg.get('dailyTokenLimit', 0) or 0)
    warning_pct = int(cfg.get('warningThresholdPct', 80) or 80)
    if day_limit > 0 and day_total >= day_limit * warning_pct / 100:
        return 'warning'
    return 'normal'


def _build_user_rows() -> list[dict[str, Any]]:
    """All per-user index rows (unsorted, unfiltered) from the single DDB scan.

    Week/month credit totals are derived from DAY rows in the same scan (no Athena);
    weights applied here so retuning ConfigTable cost weights re-prices instantly.
    """
    users = _scan_user_index()
    weights = get_credit_weights()
    configs = load_effective_configs()  # one scan, not 2 GetItems per user
    default_cfg = configs['default']

    rows: list[dict[str, Any]] = []
    for user_arn, entry in users.items():
        cfg = configs.get(user_arn, default_cfg)
        state = entry['state'] or {'blocked': False}
        # Day status uses the configured quota calendar, matching User Detail.
        today_total = int(entry['today_total_tokens'])
        status = _user_status(cfg, state, today_total)

        row: dict[str, Any] = {
            'user_arn': user_arn,
            'display_name': display_name(user_arn),
            'status': status,
            'today_total_tokens': today_total,
            'today_invocation_count': int(entry['today_invocation_count']),
            # latest_day*: 마지막 활동일 (last_updated 표시·발견용). day 사용량 아님.
            'latest_day': entry['latest_day'],
            'latest_day_total_tokens': int(entry['latest_day_total_tokens']),
            'latest_day_invocation_count': int(entry['latest_day_invocation_count']),
            'last_updated': entry['last_updated'],
            'state': state,
            'config': {
                'exempt': bool(cfg.get('exempt')),
                'warning_threshold_pct': cfg.get('warningThresholdPct'),
                'has_override': bool(entry['has_limit_override']),
            },
            'principal_type': 'role' if is_iam_role_arn(user_arn) else 'user',
            'manageable': is_iam_user_arn(user_arn),
            'effective_limits': {
                'daily': cfg.get('dailyTokenLimit'),
                'weekly': cfg.get('weeklyTokenLimit'),
                'monthly': cfg.get('monthlyTokenLimit'),
            },
        }
        row['week_total_tokens'] = weighted_total(*entry['week_components'], weights)
        row['month_total_tokens'] = weighted_total(*entry['month_components'], weights)
        if is_iam_user_arn(user_arn):
            row['username'] = arn_to_username(user_arn)
        elif is_iam_role_arn(user_arn):
            row['username'] = display_name(user_arn)
        rows.append(row)
    return rows


def list_users(limit: int) -> list[dict[str, Any]]:
    """Up to `limit` rows, newest-activity first (legacy all-rows form for Overview)."""
    if not 1 <= limit <= 200:
        raise ValueError('limit must be between 1 and 200')
    rows = _build_user_rows()
    rows.sort(key=lambda r: (r.get('last_updated') or '', r['display_name']), reverse=True)
    return rows[:limit]


def _matches_user_filters(row: dict[str, Any], status: str | None, q_lc: str | None) -> bool:
    if status and status != 'all' and row.get('status') != status:
        return False
    if q_lc:
        hay = f"{row.get('username', '')} {row.get('display_name', '')} {row.get('user_arn', '')}".lower()
        if q_lc not in hay:
            return False
    return True


def list_users_page(
    page_size: int,
    *,
    cursor: str | None = None,
    status: str | None = None,
    q: str | None = None,
) -> dict[str, Any]:
    """Filtered, month-usage-sorted page of users + all-user totals.

    The global sort key (month credits) is computed in memory after the full DDB
    scan, so a native LastEvaluatedKey cursor is impossible — we use an opaque
    offset cursor over the deterministic (month desc, arn) ordering. `totals` is
    summed over ALL users (pre-filter) so the Overview KPI is correct regardless
    of paging. Re-scan per page is inherent to global-sort-without-a-sorted-index
    and acceptable at <=1000 users.
    """
    page_size = max(1, min(200, page_size))
    offset = 0
    if cursor:
        try:
            offset = int(base64.urlsafe_b64decode(cursor.encode()).decode())
        except Exception:
            raise ValueError('invalid cursor')
        if offset < 0:
            raise ValueError('invalid cursor')

    all_rows = _build_user_rows()
    totals = {
        'day': sum(int(r['today_total_tokens']) for r in all_rows),
        'week': sum(int(r['week_total_tokens']) for r in all_rows),
        'month': sum(int(r['month_total_tokens']) for r in all_rows),
    }
    status_counts = {
        name: sum(1 for row in all_rows if row.get('status') == name)
        for name in ('normal', 'warning', 'blocked', 'exempt')
    }

    q_lc = q.strip().lower() if q else None
    filtered = [r for r in all_rows if _matches_user_filters(r, status, q_lc)]
    # Deterministic global order: month credits desc, then arn for a stable tiebreak.
    filtered.sort(key=lambda r: (-int(r['month_total_tokens']), r['user_arn']))

    page = filtered[offset:offset + page_size]
    next_offset = offset + page_size
    next_cursor = (
        base64.urlsafe_b64encode(str(next_offset).encode()).decode()
        if next_offset < len(filtered) else None
    )
    return {
        'rows': page,
        'totals': totals,
        'status_counts': status_counts,
        'all_user_count': len(all_rows),
        'total_count': len(filtered),
        'nextCursor': next_cursor,
    }


def _validate_limit_body(body: dict, target_arn: str) -> None:
    if not body:
        raise ValueError('empty body')

    bad = [k for k in body if k not in ALLOWED_CONFIG_KEYS]
    if bad:
        raise ValueError(f'unknown keys: {bad}')

    if 'exempt' in body and not isinstance(body['exempt'], bool):
        raise ValueError('exempt must be boolean')

    for name in ('dailyTokenLimit', 'weeklyTokenLimit', 'monthlyTokenLimit'):
        val = body.get(name)
        if val is None:
            continue
        if isinstance(val, bool) or not isinstance(val, int):
            raise ValueError(f'{name} must be a positive integer')
        # Reject 0: enforcement treats limit <= 0 as UNENFORCED (usage-aggregator /
        # periodic-quota-check skip when limit <= 0), so a 0 here silently DISABLES the scope
        # instead of blocking all usage. Require >= 1 so the stored value always means what the
        # operator expects; "block everything" isn't expressible via a limit and shouldn't look like it.
        if val < 1 or val > MAX_TOKEN_LIMIT:
            raise ValueError(f'{name} must be between 1 and {MAX_TOKEN_LIMIT} (0 would disable enforcement, not block)')

    pct = body.get('warningThresholdPct')
    if pct is not None:
        if isinstance(pct, bool) or not isinstance(pct, int) or not 1 <= pct <= 100:
            raise ValueError('warningThresholdPct must be integer in 1..100')

    # Hierarchy 검증은 default + 기존 override + body를 merge한 effective config 기준이라야
    # 기존 daily보다 낮은 weekly만 단독 PUT하는 모순을 잡을 수 있다.
    effective = {**get_effective_config(target_arn), **body}
    daily = effective.get('dailyTokenLimit')
    weekly = effective.get('weeklyTokenLimit')
    monthly = effective.get('monthlyTokenLimit')
    if daily is not None and weekly is not None and weekly < daily:
        raise ValueError('weeklyTokenLimit must be >= dailyTokenLimit')
    if weekly is not None and monthly is not None and monthly < weekly:
        raise ValueError('monthlyTokenLimit must be >= weeklyTokenLimit')


MAX_REASON_LEN = 500


def _pop_reason(body: dict) -> str | None:
    """Extract operator reason from a write body before config validation.

    Lets the console attach an auditable reason without it being mistaken for a
    config key. Trimmed and length-capped; missing/blank reason is allowed (None).
    """
    raw = body.pop('reason', None)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError('reason must be a string')
    reason = raw.strip()
    return reason[:MAX_REASON_LEN] if reason else None


def _header(event: dict, name: str) -> str | None:
    # HTTP API payload v2 lowercases header keys.
    headers = event.get('headers') or {}
    val = headers.get(name.lower())
    return val.strip() if isinstance(val, str) and val.strip() else None


def operator_identity(event: dict, caller: str | None) -> dict[str, str]:
    """The human operator behind a proxied console write.

    The console BFF calls Core with its own (admin ROLE) SigV4 creds, so `caller` is the BFF
    role, not the person. The BFF forwards the signed-in operator in SigV4-SIGNED headers; we
    trust them ONLY when the caller is a recognized admin ROLE (assumed_role_name in
    ADMIN_ROLE_NAMES). For a direct IAM-user admin the caller ARN already IS the operator, so we
    return nothing and the existing caller fields stand. Untrusted callers can't spoof the actor:
    a non-admin-role caller's headers are ignored here, and a non-admin caller is rejected upstream.
    """
    role = assumed_role_name(caller) if caller else None
    if not (role and role in ADMIN_ROLE_NAMES):
        return {}
    out: dict[str, str] = {}
    email = _header(event, 'X-Bedrock-Meter-Operator-Email')
    username = _header(event, 'X-Bedrock-Meter-Operator-Username')
    if email:
        out['operator_email'] = email
    if username:
        out['operator_username'] = username
    return out


def _caller_audit_fields(caller: str, operator: dict[str, str] | None = None) -> dict[str, Any]:
    fields: dict[str, Any] = {
        'caller_arn': caller,
        'caller_username': arn_to_username(caller) if is_iam_user_arn(caller) else None,
    }
    # When a console BFF proxies a write, record the real operator (trusted, signed-header
    # identity) so the audit actor is the person, not the BFF role.
    if operator:
        fields.update(operator)
    return fields


def _override_snapshot(target_arn: str) -> dict[str, Any]:
    """Per-user override row (not merged) limited to auditable keys, for diffs."""
    item = config_table.get_item(Key={'ConfigKey': target_arn}).get('Item', {})
    return {k: (int(v) if k in NUMERIC_KEYS else v) for k, v in item.items() if k in ALLOWED_CONFIG_KEYS}


def _scope_credit_totals(user_arn: str, cfg: dict[str, Any]) -> dict[str, tuple[int, int]]:
    """Current day/week/month credit totals paired with their limits, for re-eval.

    Credit-weighted to match enforcement (block uses credits, so unblock must too).
    """
    policy = get_global_quota_policy(consistent=True)
    policy_version = policy['quotaTimeZoneVersion']
    today = quota_today(policy['quotaTimeZone'])
    week_start = iso_week_bounds(today)[0]
    month_start = month_bounds(today)[0]
    weights = get_credit_weights()
    # Consistent reads: this feeds an unblock decision triggered by a limit change.
    day_total = weighted_total(*sum_day_components(query_day_rows(
        user_arn, today, today, policy_version=policy_version, consistent=True,
    )), weights)
    return {
        'DAY': (day_total, int(cfg.get('dailyTokenLimit', 0) or 0)),
        'WEEK': (
            query_user_period_total(
                user_arn, week_start, today,
                policy_version=policy_version, consistent=True,
            ),
            int(cfg.get('weeklyTokenLimit', 0) or 0),
        ),
        'MONTH': (
            query_user_period_total(
                user_arn, month_start, today,
                policy_version=policy_version, consistent=True,
            ),
            int(cfg.get('monthlyTokenLimit', 0) or 0),
        ),
    }


def _reeval_if_blocked(username: str, target_arn: str, caller: str,
                       operator: dict[str, str] | None = None,
                       cfg: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """If target is currently blocked, re-evaluate against the (just-updated) limits
    and unblock when every scope is below limit. Returns the unblock result or None.

    cfg: the effective config to evaluate against. Callers that just wrote the config (put/delete
    limit) MUST pass the post-write merged config — a re-read here is eventually-consistent and can
    return the pre-write limits, making the immediate unblock miss (it self-heals on the next
    periodic pass, but that defeats the point of the instant re-eval). Omitted → read it (the
    daily/manual paths, where no write just happened).

    operator is threaded through so the cascading RESOLVED audit event is attributed to the
    real console operator (not the proxying BFF role), matching the LIMIT_CHANGED event.
    """
    state = get_state(target_arn)
    if not state.get('blocked'):
        return None
    if cfg is None:
        cfg = get_effective_config(target_arn)
    if cfg.get('exempt'):
        scope_totals: dict[str, tuple[int, int]] = {}  # exempt → unblock outright
    else:
        scope_totals = _scope_credit_totals(target_arn, cfg)
    return reevaluate_and_unblock(
        usage_table, sns, SNS_TOPIC_ARN, target_arn, scope_totals,
        source='limit-change',
        extra={**_caller_audit_fields(caller, operator), 'username': username},
    )


def put_user_limit(username: str, target_arn: str, body: dict, caller: str, reason: str | None = None,
                   operator: dict[str, str] | None = None) -> dict:
    _validate_limit_body(body, target_arn)
    before = _override_snapshot(target_arn)
    # Post-write effective config = current effective (default + existing override) with the patch
    # applied on top. Captured BEFORE the write so the immediate re-eval below evaluates against
    # the new limits without an eventually-consistent re-read (which could see the old override).
    effective_after = {**get_effective_config(target_arn), **body}

    update_parts: list[str] = []
    names: dict[str, str] = {}
    values: dict[str, Any] = {}
    for k, v in body.items():
        update_parts.append(f'#{k} = :{k}')
        names[f'#{k}'] = k
        values[f':{k}'] = v

    config_table.update_item(
        Key={'ConfigKey': target_arn},
        UpdateExpression='SET ' + ', '.join(update_parts),
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )

    publish_alert(
        sns, SNS_TOPIC_ARN,
        event='LIMIT_CHANGED', severity='INFO', scope='ADMIN',
        user_arn=target_arn,
        extra={
            **_caller_audit_fields(caller, operator), 'changes': body, 'username': username,
            'before': json.dumps(before), 'after': json.dumps({**before, **body}),
            'reason': reason,
        },
    )

    result = {'updated': True, 'username': username, 'user_arn': target_arn, 'changes': body}
    # A limit increase should free a blocked user immediately, not wait for the daily/periodic
    # re-evaluation. Pass the just-computed effective config so the re-eval uses the NEW limits
    # (not an eventually-consistent re-read of the override we just wrote). Only acts if blocked.
    reeval = _reeval_if_blocked(username, target_arn, caller, operator, cfg=effective_after)
    if reeval is not None:
        result['reevaluated'] = reeval
    return result


def delete_user_limit(username: str, target_arn: str, caller: str, reason: str | None = None,
                      operator: dict[str, str] | None = None) -> dict:
    before = _override_snapshot(target_arn)
    config_table.delete_item(Key={'ConfigKey': target_arn})
    publish_alert(
        sns, SNS_TOPIC_ARN,
        event='LIMIT_REMOVED', severity='INFO', scope='ADMIN',
        user_arn=target_arn,
        extra={
            **_caller_audit_fields(caller, operator), 'username': username,
            'before': json.dumps(before), 'reason': reason,
        },
    )
    return {'deleted': True, 'username': username, 'user_arn': target_arn}


def update_global_quota_time_zone(
    requested_time_zone: str,
    caller: str,
    reason: str,
    operator: dict[str, str] | None = None,
) -> dict[str, Any]:
    time_zone = validate_time_zone(requested_time_zone)
    before = get_global_quota_policy(consistent=True)
    if time_zone == before['quotaTimeZone']:
        return {**before, 'updated': False, 'countersReset': False}

    current_version = int(before['quotaTimeZoneVersion'])
    next_version = current_version + 1
    try:
        config_table.update_item(
            Key={'ConfigKey': 'default'},
            UpdateExpression='SET #tz = :tz, #version = :next',
            ConditionExpression=(
                '(attribute_not_exists(#tz) OR #tz = :before_tz) AND '
                '(attribute_not_exists(#version) OR #version = :current)'
            ),
            ExpressionAttributeNames={
                '#tz': 'quotaTimeZone',
                '#version': 'quotaTimeZoneVersion',
            },
            ExpressionAttributeValues={
                ':tz': time_zone,
                ':before_tz': before['quotaTimeZone'],
                ':current': current_version,
                ':next': next_version,
            },
        )
    except ClientError as exc:
        if exc.response['Error']['Code'] == 'ConditionalCheckFailedException':
            raise ValueError('quota timezone changed concurrently; reload and retry') from exc
        raise

    after = {
        'mode': 'CALENDAR',
        'quotaTimeZone': time_zone,
        'quotaTimeZoneVersion': next_version,
    }
    publish_alert(
        sns, SNS_TOPIC_ARN,
        event='QUOTA_TIMEZONE_CHANGED', severity='INFO', scope='ADMIN',
        user_arn=f'arn:aws:iam::{ACCOUNT_ID}:root',
        extra={
            **_caller_audit_fields(caller, operator),
            'username': 'Global quota policy',
            'changes': {'quotaTimeZone': time_zone},
            'before': json.dumps(before),
            'after': json.dumps(after),
            'reason': reason,
        },
    )
    return {**after, 'updated': True, 'countersReset': True}


def force_unblock(username: str, target_arn: str, caller: str, reason: str | None = None,
                  operator: dict[str, str] | None = None) -> dict:
    transition = unblock_with_reconciliation(usage_table, target_arn)
    if not transition['unblocked']:
        return {
            'unblocked': False,
            'username': username,
            'user_arn': target_arn,
            'reason': transition.get('reason'),
        }
    publish_alert(
        sns, SNS_TOPIC_ARN,
        event='MANUAL_UNBLOCK', severity='INFO', scope='MANUAL',
        user_arn=target_arn,
        extra={
            **_caller_audit_fields(caller, operator), 'username': username,
            'policy_removed': transition['policy_removed'], 'reason': reason,
        },
    )
    return {
        'unblocked': True,
        'username': username,
        'user_arn': target_arn,
        'policy_removed': transition['policy_removed'],
    }


AUDIT_MAX_LIMIT = 200
AUDIT_DEFAULT_LIMIT = 50
# Bound the partition walk so an empty/old query can't scan unbounded months.
AUDIT_MAX_MONTHS = 24
AUDIT_EVENTS = {
    'LIMIT_CHANGED', 'LIMIT_REMOVED', 'EXEMPT_CHANGED',
    'QUOTA_TIMEZONE_CHANGED', 'MANUAL_UNBLOCK', 'RESOLVED', 'BLOCKED', 'WARNING',
}


def _month_keys_desc(start: date, count: int) -> list[str]:
    """`count` 'yyyy-mm' partition keys, newest first, walking back from `start`."""
    keys: list[str] = []
    y, m = start.year, start.month
    for _ in range(count):
        keys.append(f'{y:04d}-{m:02d}')
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return keys


def _audit_item_to_record(item: dict[str, Any]) -> dict[str, Any]:
    record = {
        'id': item.get('eventId') or item.get('SK'),
        'timestamp': item.get('timestamp'),
        'event': item.get('event'),
        'severity': item.get('severity'),
        'scope': item.get('scope'),
        'actor': item.get('actor'),
        'target': item.get('target'),
        'targetArn': item.get('targetArn'),
        'reason': item.get('reason'),
    }
    if 'before' in item:
        record['before'] = item['before']
    if 'after' in item:
        record['after'] = item['after']
    return record


def _encode_audit_cursor(month: str, lek: dict[str, Any] | None) -> str:
    """Opaque pagination cursor = base64(JSON{month, lek}). `month` is the partition
    being walked; `lek` is the DynamoDB LastEvaluatedKey within it (None = month done,
    resume at the next older month)."""
    payload = json.dumps({'m': month, 'lek': lek}, default=str)
    return base64.urlsafe_b64encode(payload.encode()).decode()


def _decode_audit_cursor(cursor: str) -> tuple[str, dict[str, Any] | None]:
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        return payload['m'], payload.get('lek')
    except Exception:
        raise ValueError('invalid cursor')


def query_audit(
    *,
    event: str | None = None,
    target: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = AUDIT_DEFAULT_LIMIT,
    cursor: str | None = None,
) -> dict[str, Any]:
    """Recent-first audit records across monthly partitions, cursor-paginated.

    Walks AUDIT#yyyy-mm partitions newest→oldest, querying each descending by SK,
    until `limit` rows are collected. Returns `nextCursor` (opaque) when more rows
    may exist; pass it back to continue. Filters (event/target/from/to) applied per
    page. Older-than-200 history is reachable by paging, not a single big read.
    """
    if event is not None and event not in AUDIT_EVENTS:
        raise ValueError(f'event must be one of: {sorted(AUDIT_EVENTS)}')
    limit = max(1, min(AUDIT_MAX_LIMIT, limit))

    for label, value in (('from', date_from), ('to', date_to)):
        if value is not None:
            try:
                date.fromisoformat(value)
            except ValueError:
                raise ValueError(f'{label} must be a YYYY-MM-DD date')
    if date_from and date_to and date_to < date_from:
        raise ValueError('to must be on or after from')

    # `to` (inclusive day) caps the newest month we start from; `from` caps how far back.
    end_anchor = date.fromisoformat(date_to) if date_to else date.today()
    floor_month = date_from[:7] if date_from else None

    target_lc = target.lower() if target else None
    records: list[dict[str, Any]] = []

    # SK is '{ts}#{eventId}' where ts = datetime.isoformat() (e.g. '2026-06-29T23:59:59.123+00:00').
    # lo: start-of-day prefix sorts at/below every same-day SK. hi: the date + 'T' + U+FFFF sorts
    # ABOVE any same-day SK (which continues '<time>+00:00#<id>') but below the next day's 'T...',
    # so BETWEEN is inclusive of the whole `to` day. (The old '...999999#ZZ' bound omitted the
    # '+00:00' offset, and the offset's '+' sorts above '#', so it wrongly excluded boundary records.)
    sk_lo = f'{date_from}T00:00:00' if date_from else None
    sk_hi = f'{date_to}T\uffff' if date_to else None
    if sk_lo and sk_hi:
        key_cond, extra_vals = 'PK = :pk AND SK BETWEEN :lo AND :hi', {':lo': sk_lo, ':hi': sk_hi}
    elif sk_lo:
        key_cond, extra_vals = 'PK = :pk AND SK >= :lo', {':lo': sk_lo}
    elif sk_hi:
        key_cond, extra_vals = 'PK = :pk AND SK <= :hi', {':hi': sk_hi}
    else:
        key_cond, extra_vals = 'PK = :pk', {}

    # Resume from a prior page if a cursor was given.
    months = _month_keys_desc(end_anchor, AUDIT_MAX_MONTHS)
    resume_lek: dict[str, Any] | None = None
    if cursor:
        resume_month, resume_lek = _decode_audit_cursor(cursor)
        if resume_month in months:
            months = months[months.index(resume_month):]
        else:
            months = []  # cursor past the window → nothing left

    for mi, month in enumerate(months):
        if floor_month and month < floor_month:
            break

        # Only the first month in this page may start mid-partition (from the cursor).
        last: dict[str, Any] | None = resume_lek if mi == 0 else None
        while True:
            kwargs: dict[str, Any] = {
                'KeyConditionExpression': key_cond,
                'ExpressionAttributeValues': {':pk': f'AUDIT#{month}', **extra_vals},
                'ScanIndexForward': False,
            }
            if last:
                kwargs['ExclusiveStartKey'] = last
            resp = audit_table.query(**kwargs)
            for item in resp.get('Items', []):
                if event and item.get('event') != event:
                    continue
                if target_lc and target_lc not in str(item.get('target', '')).lower():
                    continue
                records.append(_audit_item_to_record(item))
                if len(records) >= limit:
                    # More may remain: resume in THIS month from this item's key.
                    next_lek = {'PK': item['PK'], 'SK': item['SK']}
                    return {
                        'records': records, 'count': len(records),
                        'nextCursor': _encode_audit_cursor(month, next_lek),
                    }
            last = resp.get('LastEvaluatedKey')
            if not last:
                break  # month exhausted → fall through to next older month

    # Window fully walked — no more pages.
    return {'records': records, 'count': len(records), 'nextCursor': None}


def parse_body(event: dict) -> dict:
    body = event.get('body') or '{}'
    if event.get('isBase64Encoded'):
        body = base64.b64decode(body).decode('utf-8')
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise ValueError(f'invalid JSON body: {e}') from e


Route = Callable[[dict, str, dict[str, str], dict[str, str]], dict]
ROUTES: list[tuple[str, re.Pattern[str], Route, bool]] = []


def route(method: str, pattern: str, admin_only: bool = False) -> Callable[[Route], Route]:
    def deco(fn: Route) -> Route:
        ROUTES.append((method, re.compile(f'^{pattern}$'), fn, admin_only))
        return fn
    return deco


USERNAME_PATTERN = r'(?P<username>[A-Za-z0-9+=,.@_-]{1,64})'


@route('GET', r'/usage/me')
def r_usage_me(event, caller, params, qs):
    # /me is caller-relative and only meaningful for an IAM-user caller. A role caller
    # (e.g. the console BFF) has no per-user usage row → 400 rather than a 500 from
    # usage_snapshot's arn_to_username. The console queries /usage/{username}, not /me.
    if not is_iam_user_arn(caller):
        raise ValueError('/usage/me requires an IAM-user caller')
    return respond(200, usage_snapshot(caller))


@route('GET', r'/usage/me/history')
def r_usage_me_history(event, caller, params, qs):
    period = qs.get('period', 'month')
    granularity = qs.get('granularity', 'day')
    rows, buckets = query_user_history(caller, period, granularity)
    return respond(200, {
        'username': caller_username(caller),
        'user_arn': caller,
        'period': period,
        'granularity': granularity,
        'buckets': buckets,
        'rows': rows,
    })


@route('GET', r'/usage/top', admin_only=True)
def r_usage_top(event, caller, params, qs):
    period = qs.get('period', 'month')
    metric = qs.get('metric', 'totalTokens')
    try:
        limit = int(qs.get('limit', '5'))
    except (TypeError, ValueError):
        raise ValueError('limit must be an integer')
    rows = query_top_users(period, metric, limit)
    for r in rows:
        if is_iam_user_arn(r['user_arn']):
            r['username'] = arn_to_username(r['user_arn'])
    return respond(200, {
        'period': period, 'metric': metric, 'limit': limit,
        'rows': rows,
    })


@route('GET', r'/usage/by-model/trend', admin_only=True)
def r_usage_by_model_trend(event, caller, params, qs):
    period = qs.get('period', 'month')
    metric = qs.get('metric', 'totalTokens')
    granularity = qs.get('granularity', 'day')
    date_from = qs.get('from')
    date_to = qs.get('to')
    try:
        top = int(qs.get('top', '10'))
    except (TypeError, ValueError):
        raise ValueError('top must be an integer')
    return respond(200, query_by_model_trend(period, top, metric, granularity, date_from, date_to))


@route('GET', r'/usage/by-model', admin_only=True)
def r_usage_by_model(event, caller, params, qs):
    period = qs.get('period', 'month')
    return respond(200, {'period': period, 'rows': query_by_model(period)})


@route('GET', r'/usage/aggregate', admin_only=True)
def r_usage_aggregate(event, caller, params, qs):
    """Single-call aggregate for the console: per-user day/week/month + limits.

    Day/week/month credit totals are all derived from DAY rows in one UsageTable
    scan (no Athena). Must be declared before the /usage/{username} regex so the
    literal path wins routing.
    """
    try:
        limit = int(qs.get('limit', '100'))
    except (TypeError, ValueError):
        raise ValueError('limit must be an integer')
    rows = list_users(limit)
    return respond(200, {
        'limit': limit,
        'as_of': datetime.now(timezone.utc).isoformat(),
        'rows': rows,
    })


@route('GET', r'/usage/history/all', admin_only=True)
def r_usage_history_all(event, caller, params, qs):
    """All-user daily token series (console Overview trend) from one DDB scan.

    Declared before /usage/{username}/history so the literal path wins routing.
    """
    try:
        days = int(qs.get('days', '30'))
    except (TypeError, ValueError):
        raise ValueError('days must be an integer')
    days = max(1, min(USAGE_RETENTION_DAYS, days))
    return respond(200, all_users_daily_history(days))


@route('GET', f'/usage/{USERNAME_PATTERN}/history', admin_only=True)
def r_usage_history(event, caller, params, qs):
    name, arn = resolve_user(params['username'])
    period = qs.get('period', 'month')
    granularity = qs.get('granularity', 'day')
    rows, buckets = query_user_history(arn, period, granularity)
    return respond(200, {
        'username': name,
        'user_arn': arn,
        'period': period,
        'granularity': granularity,
        'buckets': buckets,
        'rows': rows,
    })


@route('GET', f'/usage/{USERNAME_PATTERN}', admin_only=True)
def r_usage_user(event, caller, params, qs):
    _, arn = resolve_user(params['username'])
    return respond(200, usage_snapshot(arn))


@route('GET', r'/users', admin_only=True)
def r_users(event, caller, params, qs):
    # page_size present → paginated/filtered/sorted page for the console Users tab.
    # Absent → legacy all-rows form (username discovery, etc.).
    if 'page_size' in qs:
        try:
            page_size = int(qs.get('page_size'))
        except (TypeError, ValueError):
            raise ValueError('page_size must be an integer')
        return respond(200, list_users_page(
            page_size,
            cursor=qs.get('cursor') or None,
            status=qs.get('status') or None,
            q=qs.get('q') or None,
        ))
    try:
        limit = int(qs.get('limit', '100'))
    except (TypeError, ValueError):
        raise ValueError('limit must be an integer')
    return respond(200, {'limit': limit, 'rows': list_users(limit)})


@route('GET', f'/users/{USERNAME_PATTERN}/history', admin_only=True)
def r_users_history(event, caller, params, qs):
    return r_usage_history(event, caller, params, qs)


@route('GET', f'/users/{USERNAME_PATTERN}', admin_only=True)
def r_users_user(event, caller, params, qs):
    return r_usage_user(event, caller, params, qs)


@route('GET', r'/limits/me')
def r_limits_me(event, caller, params, qs):
    # caller_username (not arn_to_username) so a role caller — e.g. the console BFF's
    # execution role — gets username=None instead of raising; /me has no IAM user then.
    return respond(200, {
        'username': caller_username(caller),
        'user_arn': caller,
        'effective': get_effective_config(caller),
    })


@route('GET', f'/limits/{USERNAME_PATTERN}', admin_only=True)
def r_limits_user(event, caller, params, qs):
    name, arn = resolve_user(params['username'])
    return respond(200, {'username': name, 'user_arn': arn, 'effective': get_effective_config(arn)})


@route('PUT', f'/limits/{USERNAME_PATTERN}', admin_only=True)
def r_limits_put(event, caller, params, qs):
    name, arn = resolve_user(params['username'])
    body = parse_body(event)
    reason = _pop_reason(body)
    return respond(200, put_user_limit(name, arn, body, caller, reason, operator_identity(event, caller)))


@route('DELETE', f'/limits/{USERNAME_PATTERN}', admin_only=True)
def r_limits_delete(event, caller, params, qs):
    name, arn = resolve_user(params['username'])
    reason = _pop_reason(parse_body(event))
    return respond(200, delete_user_limit(name, arn, caller, reason, operator_identity(event, caller)))


@route('GET', r'/admin/settings/quota', admin_only=True)
def r_admin_settings_quota(event, caller, params, qs):
    return respond(200, get_global_quota_policy(consistent=True))


@route('PUT', r'/admin/settings/quota', admin_only=True)
def r_admin_settings_quota_put(event, caller, params, qs):
    body = parse_body(event)
    reason = _pop_reason(body)
    if not reason:
        raise ValueError('reason is required')
    if set(body) != {'quotaTimeZone'} or not isinstance(body.get('quotaTimeZone'), str):
        raise ValueError('body must contain only quotaTimeZone as an IANA time zone string')
    return respond(200, update_global_quota_time_zone(
        body['quotaTimeZone'],
        caller,
        reason,
        operator_identity(event, caller),
    ))


@route('GET', r'/admin/blocked', admin_only=True)
def r_admin_blocked(event, caller, params, qs):
    rows = list_blocked_users()
    for r in rows:
        if is_iam_user_arn(r['user_arn']):
            r['username'] = arn_to_username(r['user_arn'])
    return respond(200, {'rows': rows})


@route('POST', f'/admin/unblock/{USERNAME_PATTERN}', admin_only=True)
def r_admin_unblock(event, caller, params, qs):
    name, arn = resolve_user(params['username'])
    reason = _pop_reason(parse_body(event)) if event.get('body') else None
    return respond(200, force_unblock(name, arn, caller, reason, operator_identity(event, caller)))


@route('GET', r'/admin/audit', admin_only=True)
def r_admin_audit(event, caller, params, qs):
    try:
        limit = int(qs.get('limit', str(AUDIT_DEFAULT_LIMIT)))
    except (TypeError, ValueError):
        raise ValueError('limit must be an integer')
    return respond(200, query_audit(
        event=qs.get('event') or None,
        target=qs.get('target') or None,
        date_from=qs.get('from') or None,
        date_to=qs.get('to') or None,
        limit=limit,
        cursor=qs.get('cursor') or None,
    ))


def handler(event: dict, context: Any) -> dict:
    method = event.get('requestContext', {}).get('http', {}).get('method') or event.get('httpMethod') or 'GET'
    raw_path = event.get('rawPath') or event.get('requestContext', {}).get('http', {}).get('path') or event.get('path', '/')
    qs = event.get('queryStringParameters') or {}
    caller = caller_arn(event)
    if not caller:
        return respond(401, {'error': 'unauthenticated'})

    for r_method, pattern, fn, admin_only in ROUTES:
        if r_method != method:
            continue
        m = pattern.match(raw_path)
        if not m:
            continue
        if admin_only and not is_admin(caller):
            return respond(403, {'error': 'admin only', 'caller': caller})
        try:
            return fn(event, caller, m.groupdict(), qs)
        except ValueError as e:
            return respond(400, {'error': str(e)})
        except ClientError as e:
            logger.exception('AWS error')
            return respond(500, {'error': str(e)})
        except Exception as e:
            logger.exception('Unhandled')
            return respond(500, {'error': type(e).__name__, 'message': str(e)})

    return respond(404, {'error': 'not found', 'method': method, 'path': raw_path})
