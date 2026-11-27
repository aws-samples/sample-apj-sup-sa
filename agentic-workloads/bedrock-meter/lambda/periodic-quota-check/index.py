import logging
import os
from datetime import date, datetime, timezone
from typing import Any

import boto3

from common.dates import (
    active_day_from_sk,
    iso_week_bounds,
    iso_week_key,
    month_bounds,
    month_key,
    quota_policy,
    quota_today,
)
from common.enforcement import mark_blocked_if_new, publish_alert, reevaluate_and_unblock
from common.pricing import weighted_total, weights_from_config
from common.principals import display_name, is_enforceable_principal

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ddb = boto3.resource('dynamodb')
sns = boto3.client('sns')
cw = boto3.client('cloudwatch')

USAGE_TABLE = os.environ['USAGE_TABLE']
CONFIG_TABLE = os.environ['CONFIG_TABLE']
SNS_TOPIC_ARN = os.environ['SNS_TOPIC_ARN']
METRIC_NAMESPACE = os.environ.get('METRIC_NAMESPACE', 'BedrockMeter')
ROLE_ENFORCEMENT_ENABLED = os.environ.get('ROLE_ENFORCEMENT_ENABLED', 'false').lower() == 'true'

NUMERIC_KEYS = {
    'dailyTokenLimit', 'weeklyTokenLimit', 'monthlyTokenLimit', 'warningThresholdPct',
}

CONFIG_FALLBACK = {
    'dailyTokenLimit': int(os.environ.get('DEFAULT_DAILY_CREDIT_LIMIT', '3000000')),
    'weeklyTokenLimit': int(os.environ.get('DEFAULT_WEEKLY_CREDIT_LIMIT', '15000000')),
    'monthlyTokenLimit': int(os.environ.get('DEFAULT_MONTHLY_CREDIT_LIMIT', '45000000')),
    'warningThresholdPct': 80,
}

usage_table = ddb.Table(USAGE_TABLE)
config_table = ddb.Table(CONFIG_TABLE)


def _day_components(item: dict[str, Any]) -> tuple[int, int, int, int]:
    return (
        int(item.get('rawInputTokens', 0)),
        int(item.get('cacheReadTokens', 0)),
        int(item.get('cacheWriteTokens', 0)),
        int(item.get('outputTokens', 0)),
    )


def scan_usage_by_user(
    today: date,
    week_start: date,
    month_start: date,
    weights: dict[str, float],
    policy_version: int = 1,
) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    """One consistent UsageTable scan → (week_usage, month_usage, day_usage) credit totals.

    Replaces the two Athena GROUP-BY queries. DAY# rows are folded into week/month/day
    component accumulators by date string compare (DAY# keys are fixed-width yyyy-mm-dd,
    so lexicographic == chronological). Weighting is applied once per user at the end so
    cost weights stay runtime-tunable. ConsistentRead since this feeds block decisions.
    """
    today_key = today.isoformat()
    week_key = week_start.isoformat()
    month_key_ = month_start.isoformat()
    # arn -> [raw, cache_read, cache_write, output] per scope
    week_c: dict[str, list[int]] = {}
    month_c: dict[str, list[int]] = {}
    day_c: dict[str, list[int]] = {}

    def _add(acc: dict[str, list[int]], arn: str, comps: tuple[int, int, int, int]) -> None:
        slot = acc.setdefault(arn, [0, 0, 0, 0])
        for i in range(4):
            slot[i] += comps[i]

    last: dict[str, Any] | None = None
    while True:
        kwargs: dict[str, Any] = {
            'ProjectionExpression': (
                'UserARN, #sk, rawInputTokens, '
                'cacheReadTokens, cacheWriteTokens, outputTokens'
            ),
            'ExpressionAttributeNames': {'#sk': 'SK'},
            'ConsistentRead': True,
        }
        if last:
            kwargs['ExclusiveStartKey'] = last
        resp = usage_table.scan(**kwargs)
        for item in resp.get('Items', []):
            arn = item.get('UserARN')
            sk = item.get('SK', '')
            day_key = active_day_from_sk(sk, policy_version)
            if not arn or day_key is None:
                continue
            # Independent gates — no early skip. When the current ISO week straddles a
            # month boundary (week_key < month_key_), prior-month days still belong to
            # the WEEK total; an early `continue` on month would silently drop them and
            # let a weekly-over-limit user escape blocking.
            if day_key < week_key and day_key < month_key_:
                continue
            comps = _day_components(item)
            if day_key >= week_key:
                _add(week_c, arn, comps)
            if day_key >= month_key_:
                _add(month_c, arn, comps)
            if day_key == today_key:
                _add(day_c, arn, comps)
        last = resp.get('LastEvaluatedKey')
        if not last:
            break

    def _weighted(acc: dict[str, list[int]]) -> dict[str, int]:
        return {arn: weighted_total(c[0], c[1], c[2], c[3], weights) for arn, c in acc.items()}

    return _weighted(week_c), _weighted(month_c), _weighted(day_c)


def _normalize_config(item: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in item.items():
        if k == 'ConfigKey':
            continue
        if isinstance(v, bool):
            out[k] = v
        elif k in NUMERIC_KEYS:
            out[k] = int(v)
        else:
            out[k] = v
    return out


def load_all_configs() -> dict[str, dict[str, Any]]:
    items: list[dict[str, Any]] = []
    last: dict[str, Any] | None = None
    while True:
        kwargs: dict[str, Any] = {'ConsistentRead': True}
        if last:
            kwargs['ExclusiveStartKey'] = last
        resp = config_table.scan(**kwargs)
        items.extend(resp.get('Items', []))
        last = resp.get('LastEvaluatedKey')
        if not last:
            break

    default_raw = next((i for i in items if i.get('ConfigKey') == 'default'), {})
    default_cfg = _normalize_config(default_raw)
    result: dict[str, dict[str, Any]] = {'default': {**CONFIG_FALLBACK, **default_cfg}}
    for item in items:
        key = item.get('ConfigKey')
        if not key or key == 'default':
            continue
        result[key] = {**result['default'], **_normalize_config(item)}
    return result


def load_state_index() -> tuple[set[str], dict[str, dict[str, str]]]:
    """Single scan to build:
      - blocked_arns: users currently blocked (skip evaluation)
      - warned_keys: user_arn → scope → 'SCOPE#period_key' to dedupe warnings
    """
    blocked: set[str] = set()
    warned: dict[str, dict[str, str]] = {}
    last: dict[str, Any] | None = None
    while True:
        kwargs: dict[str, Any] = {
            'FilterExpression': '#sk = :sk',
            'ExpressionAttributeNames': {'#sk': 'SK'},
            'ExpressionAttributeValues': {':sk': 'STATE'},
        }
        if last:
            kwargs['ExclusiveStartKey'] = last
        resp = usage_table.scan(**kwargs)
        for item in resp.get('Items', []):
            arn = item['UserARN']
            if item.get('blocked'):
                blocked.add(arn)
            scope_keys: dict[str, str] = {}
            for scope, attr in (('WEEK', 'warnedWeekKey'), ('MONTH', 'warnedMonthKey')):
                value = item.get(attr)
                if value:
                    scope_keys[scope] = value
            legacy = item.get('warnedKey')
            if isinstance(legacy, str) and '#' in legacy:
                scope = legacy.split('#', 1)[0]
                if scope in ('WEEK', 'MONTH'):
                    scope_keys.setdefault(scope, legacy)
            if scope_keys:
                warned[arn] = scope_keys
        last = resp.get('LastEvaluatedKey')
        if not last:
            break
    return blocked, warned


def mark_warned_state(user_arn: str, scope: str, period_key: str) -> None:
    attr = {
        'WEEK': 'warnedWeekKey',
        'MONTH': 'warnedMonthKey',
    }[scope]
    usage_table.update_item(
        Key={'UserARN': user_arn, 'SK': 'STATE'},
        UpdateExpression='SET #warned = :k',
        ExpressionAttributeNames={'#warned': attr},
        ExpressionAttributeValues={':k': f'{scope}#{period_key}'},
    )


def evaluate_user(
    user_arn: str,
    cfg: dict[str, Any],
    week_total: int,
    month_total: int,
    week_k: str,
    month_k: str,
    warned_keys: dict[str, str],
    policy_version: int = 1,
) -> None:
    if cfg.get('exempt'):
        return

    warning_pct = int(cfg['warningThresholdPct']) / 100

    checks = [
        ('MONTH', month_total, int(cfg['monthlyTokenLimit']), month_k),
        ('WEEK', week_total, int(cfg['weeklyTokenLimit']), week_k),
    ]

    for scope, total, limit, period_k in checks:
        if limit <= 0:
            continue
        if total >= limit:
            if mark_blocked_if_new(usage_table, user_arn, scope, total, limit):
                publish_alert(
                    sns, SNS_TOPIC_ARN,
                    event='BLOCKED', severity='CRITICAL', scope=scope,
                    user_arn=user_arn, total=total, limit=limit,
                    extra={'display_name': display_name(user_arn), 'period': period_k},
                )
                logger.info('Blocked %s on %s: %d/%d', display_name(user_arn), scope, total, limit)
            return

    for scope, total, limit, period_k in checks:
        if limit <= 0:
            continue
        if total >= limit * warning_pct:
            expected = (
                f'{scope}#{period_k}'
                if policy_version == 1
                else f'{scope}#V{policy_version}#{period_k}'
            )
            if warned_keys.get(scope) == expected:
                continue
            stored_period_key = period_k if policy_version == 1 else f'V{policy_version}#{period_k}'
            mark_warned_state(user_arn, scope, stored_period_key)
            publish_alert(
                sns, SNS_TOPIC_ARN,
                event='WARNING', severity='WARN', scope=scope,
                user_arn=user_arn, total=total, limit=limit,
                extra={'display_name': display_name(user_arn), 'period': period_k},
            )
            logger.info('Warned %s on %s: %d/%d', display_name(user_arn), scope, total, limit)
            return


def reevaluate_blocked(
    blocked_arns: set[str],
    configs: dict[str, dict[str, Any]],
    week_usage: dict[str, int],
    month_usage: dict[str, int],
    day_usage: dict[str, int],
) -> int:
    """Auto-unblock any blocked user now under every enforced scope (credit units).

    Safety net so a limit increase that forgot to re-evaluate still resolves within
    one poll. exempt users are unblocked outright (enforcement no longer applies).
    day/week/month totals come from the same consistent scan that drives blocking,
    so block and unblock decisions agree by construction.
    """
    unblocked = 0
    for user_arn in blocked_arns:
        if not is_enforceable_principal(user_arn, ROLE_ENFORCEMENT_ENABLED):
            continue
        cfg = configs.get(user_arn, configs['default'])
        if cfg.get('exempt'):
            scope_totals: dict[str, tuple[int, int]] = {}  # exempt → no scope enforced → unblock
        else:
            scope_totals = {
                'DAY': (day_usage.get(user_arn, 0), int(cfg['dailyTokenLimit'])),
                'WEEK': (week_usage.get(user_arn, 0), int(cfg['weeklyTokenLimit'])),
                'MONTH': (month_usage.get(user_arn, 0), int(cfg['monthlyTokenLimit'])),
            }
        result = reevaluate_and_unblock(
            usage_table, sns, SNS_TOPIC_ARN, user_arn, scope_totals,
            source='periodic-reevaluate', extra={'display_name': display_name(user_arn)},
        )
        if result['unblocked']:
            unblocked += 1
            logger.info('Auto-unblocked %s (all scopes below limit)', display_name(user_arn))
    return unblocked


def emit_blocked_count(count: int) -> None:
    cw.put_metric_data(
        Namespace=METRIC_NAMESPACE,
        MetricData=[{
            'MetricName': 'BlockedUserCount',
            'Timestamp': datetime.now(timezone.utc),
            'Value': count,
            'Unit': 'Count',
        }],
    )


def handler(event: dict, context: Any) -> dict:
    configs = load_all_configs()
    time_zone, policy_version = quota_policy(configs['default'])
    today = quota_today(time_zone)
    week_start = iso_week_bounds(today)[0]
    month_start = month_bounds(today)[0]
    week_k = iso_week_key(week_start)
    month_k = month_key(month_start)

    # Credit weights are global — read from the default config row.
    weights = weights_from_config(configs['default'])

    blocked_arns, warned_map = load_state_index()

    week_usage, month_usage, day_usage = scan_usage_by_user(
        today, week_start, month_start, weights, policy_version,
    )

    candidates = (set(week_usage) | set(month_usage)) - blocked_arns
    logger.info(
        'Evaluating %d candidates (already_blocked=%d) timezone=%s policy=v%d week=%s month=%s',
        len(candidates), len(blocked_arns), time_zone, policy_version, week_k, month_k,
    )

    for user_arn in candidates:
        if not is_enforceable_principal(user_arn, ROLE_ENFORCEMENT_ENABLED):
            logger.info('Skipping non-enforceable principal: %s', user_arn)
            continue
        cfg = configs.get(user_arn, configs['default'])
        evaluate_user(
            user_arn, cfg,
            week_usage.get(user_arn, 0),
            month_usage.get(user_arn, 0),
            week_k, month_k,
            warned_map.get(user_arn, {}),
            policy_version,
        )

    # Auto-unblock pass: any already-blocked user now under every scope is resolved.
    unblocked = reevaluate_blocked(
        blocked_arns, configs, week_usage, month_usage, day_usage,
    )

    # Refresh blocked count metric: re-scan since this run may have added/removed blocks
    final_blocked, _ = load_state_index()
    emit_blocked_count(len(final_blocked))

    return {
        'evaluated': len(candidates),
        'already_blocked_at_start': len(blocked_arns),
        'unblocked': unblocked,
        'blocked_total': len(final_blocked),
        'week': week_k,
        'month': month_k,
    }
