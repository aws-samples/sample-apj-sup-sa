import base64
import gzip
import hashlib
import json
import logging
import os
import random
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

from common.dates import day_sk, quota_policy, quota_today
from common.enforcement import mark_blocked_if_new, publish_alert
from common.pricing import weighted_total, weights_from_config
from common.principals import (
    display_name,
    is_enforceable_principal,
    normalize_metering_principal,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ddb = boto3.resource('dynamodb')
ddb_client = boto3.client('dynamodb')
sns = boto3.client('sns')
cw = boto3.client('cloudwatch')

USAGE_TABLE = os.environ['USAGE_TABLE']
CONFIG_TABLE = os.environ['CONFIG_TABLE']
DEDUP_TABLE = os.environ['DEDUP_TABLE']
SNS_TOPIC_ARN = os.environ['SNS_TOPIC_ARN']
METRIC_NAMESPACE = os.environ.get('METRIC_NAMESPACE', 'BedrockMeter')
USAGE_TTL_DAYS = int(os.environ.get('USAGE_TTL_DAYS', '90'))
DEDUP_TTL_DAYS = int(os.environ.get('DEDUP_TTL_DAYS', '7'))
ROLE_ENFORCEMENT_ENABLED = os.environ.get('ROLE_ENFORCEMENT_ENABLED', 'false').lower() == 'true'
DDB_TRANSACTION_MAX_ATTEMPTS = max(1, int(os.environ.get('DDB_TRANSACTION_MAX_ATTEMPTS', '5')))
DDB_TRANSACTION_BASE_BACKOFF_MS = max(1, int(os.environ.get('DDB_TRANSACTION_BASE_BACKOFF_MS', '50')))
DDB_TRANSACTION_MAX_BACKOFF_MS = max(
    DDB_TRANSACTION_BASE_BACKOFF_MS,
    int(os.environ.get('DDB_TRANSACTION_MAX_BACKOFF_MS', '1000')),
)

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
dedup_table = ddb.Table(DEDUP_TABLE)


def get_effective_config(user_arn: str, default_item: dict[str, Any] | None = None) -> dict[str, Any]:
    # No cross-invocation caching: limit/exempt changes must take effect immediately.
    # The handler passes one strongly-consistent default read to every user and each
    # user contributes one eventually-consistent override read.
    if default_item is None:
        default_item = config_table.get_item(Key={'ConfigKey': 'default'}).get('Item', {})
    user_item = config_table.get_item(Key={'ConfigKey': user_arn}).get('Item', {})

    merged: dict[str, Any] = {**CONFIG_FALLBACK}
    for source in (default_item, user_item):
        for k, v in source.items():
            if k == 'ConfigKey':
                continue
            if isinstance(v, bool):
                merged[k] = v
            elif k in NUMERIC_KEYS:
                merged[k] = int(v)
            else:
                merged[k] = v

    return merged


def decode_log_events(event: dict) -> list[dict]:
    payload = base64.b64decode(event['awslogs']['data'])
    data = json.loads(gzip.decompress(payload))
    out: list[dict] = []
    for e in data.get('logEvents', []):
        try:
            out.append(json.loads(e['message']))
        except (json.JSONDecodeError, KeyError, TypeError):
            logger.warning('Skipping non-JSON log event: id=%s', e.get('id') if isinstance(e, dict) else None)
    return out


def claim_and_update(
    user_arn: str,
    quota_day: str,
    batch_id: str,
    raw_input: int,
    cache_read: int,
    cache_write: int,
    output_tokens: int,
    invocations: int,
    policy_version: int = 1,
    time_zone: str = 'UTC',
) -> bool:
    """Atomically claim the batch and apply the DAY ADD in one transaction.

    Subscription Filter delivers at-least-once. Tying the claim + DAY ADD together means a
    duplicate redelivery fails the claim (item 0) and the WHOLE transaction is cancelled, so the
    DAY counter never moves twice. Returns True if newly applied; False if already claimed.

    2 items (dedup claim + DAY ADD). week/month are derived at read time by range-summing DAY
    rows (no rollup table), so there is no second/third counter to keep in sync.

    inputTokens는 enforcement에서 쓰는 합계(raw+cache_read+cache_write)이고, raw/cache 항목은
    분석/응답 분리 노출용으로 별도 누적된다.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    epoch = int(time.time())
    input_tokens = raw_input + cache_read + cache_write
    for attempt in range(1, DDB_TRANSACTION_MAX_ATTEMPTS + 1):
        try:
            ddb_client.transact_write_items(
                TransactItems=[
                    {
                        # Dedup claim lives in its own table so BATCH# rows never bloat
                        # UsageTable scans. Cross-table TransactWriteItems keeps claim+ADD atomic.
                        'Put': {
                            'TableName': DEDUP_TABLE,
                            # SK omits the date on purpose: batch_id = sha256(payload) is identical
                            # on every at-least-once redelivery regardless of wall clock, so a retry
                            # that crosses a quota-calendar midnight still collides on the
                            # SAME claim. A date-prefixed key would let such a retry re-apply the DAY
                            # ADD under a new key, permanently double-counting that day — which the
                            # week/month range-sum reads verbatim (a range query does not heal a
                            # double-counted DAY row). The date-independent claim is the only guard.
                            'Item': {
                                'UserARN': {'S': user_arn},
                                'SK': {'S': f'BATCH#{batch_id}'},
                                'claimedAt': {'S': now_iso},
                                'quotaDay': {'S': quota_day},
                                'quotaTimeZone': {'S': time_zone},
                                'quotaPolicyVersion': {'N': str(policy_version)},
                                'expiresAt': {'N': str(epoch + DEDUP_TTL_DAYS * 86400)},
                            },
                            'ConditionExpression': 'attribute_not_exists(claimedAt)',
                        },
                    },
                    {
                        'Update': {
                            'TableName': USAGE_TABLE,
                            'Key': {
                                'UserARN': {'S': user_arn},
                                'SK': {'S': day_sk(quota_day, policy_version)},
                            },
                            'UpdateExpression': (
                                'ADD inputTokens :inp, rawInputTokens :raw, '
                                'cacheReadTokens :cr, cacheWriteTokens :cw, '
                                'outputTokens :out, invocationCount :n '
                                'SET lastUpdated = :ts, expiresAt = :ttl, '
                                'quotaTimeZone = :tz, quotaPolicyVersion = :pv'
                            ),
                            'ExpressionAttributeValues': {
                                ':inp': {'N': str(input_tokens)},
                                ':raw': {'N': str(raw_input)},
                                ':cr': {'N': str(cache_read)},
                                ':cw': {'N': str(cache_write)},
                                ':out': {'N': str(output_tokens)},
                                ':n': {'N': str(invocations)},
                                ':ts': {'S': now_iso},
                                ':ttl': {'N': str(epoch + USAGE_TTL_DAYS * 86400)},
                                ':tz': {'S': time_zone},
                                ':pv': {'N': str(policy_version)},
                            },
                        },
                    },
                ],
            )
            return True
        except ClientError as e:
            if e.response['Error']['Code'] != 'TransactionCanceledException':
                raise
            # Item 0 is the only conditional write, so its condition failure means this exact
            # subscription batch was already applied. TransactionConflict is transient under
            # concurrent updates to the same user's DAY row and should be absorbed here instead
            # of surfacing as a Lambda error and redelivering the whole subscription batch.
            reasons = e.response.get('CancellationReasons') or []
            first = reasons[0].get('Code') if reasons else None
            if first == 'ConditionalCheckFailed':
                return False
            has_conflict = any(reason.get('Code') == 'TransactionConflict' for reason in reasons)
            if has_conflict and attempt < DDB_TRANSACTION_MAX_ATTEMPTS:
                backoff_ceiling_ms = min(
                    DDB_TRANSACTION_MAX_BACKOFF_MS,
                    DDB_TRANSACTION_BASE_BACKOFF_MS * (2 ** (attempt - 1)),
                )
                delay_ms = random.uniform(0, backoff_ceiling_ms)
                logger.warning(
                    'DynamoDB transaction conflict for %s; retry=%d/%d delay_ms=%.0f',
                    user_arn,
                    attempt,
                    DDB_TRANSACTION_MAX_ATTEMPTS - 1,
                    delay_ms,
                )
                time.sleep(delay_ms / 1000)
                continue
            logger.warning(
                'TransactWriteItems cancelled (non-dedup): attempt=%d reasons=%s',
                attempt,
                reasons,
            )
            raise


def get_day_attrs(user_arn: str, quota_day: str, policy_version: int = 1) -> dict:
    resp = usage_table.get_item(
        Key={'UserARN': user_arn, 'SK': day_sk(quota_day, policy_version)},
        ConsistentRead=True,
    )
    return resp.get('Item', {})


def mark_warned_today(user_arn: str, quota_day: str, policy_version: int = 1) -> None:
    usage_table.update_item(
        Key={'UserARN': user_arn, 'SK': day_sk(quota_day, policy_version)},
        UpdateExpression='SET warned = :t',
        ExpressionAttributeValues={':t': True},
    )


def check_day_threshold(
    user_arn: str,
    quota_day: str,
    day_attrs: dict,
    *,
    policy_version: int = 1,
    config: dict[str, Any] | None = None,
) -> None:
    config = config or get_effective_config(user_arn)
    if config.get('exempt'):
        return

    daily_limit = int(config['dailyTokenLimit'])
    if daily_limit <= 0:
        return  # safety: treat 0/negative as "not enforced"
    warning_pct = int(config['warningThresholdPct']) / 100

    # Compare against cost-weighted credits, not a raw token sum. inputTokens is
    # raw+cacheRead+cacheWrite summed 1:1 by the aggregator, so weight the parts.
    weights = weights_from_config(config)
    total = weighted_total(
        int(day_attrs.get('rawInputTokens', 0)),
        int(day_attrs.get('cacheReadTokens', 0)),
        int(day_attrs.get('cacheWriteTokens', 0)),
        int(day_attrs.get('outputTokens', 0)),
        weights,
    )

    if total >= daily_limit:
        if not is_enforceable_principal(user_arn, ROLE_ENFORCEMENT_ENABLED):
            logger.warning('Skipping DAY block for non-enforceable principal: %s', user_arn)
            return
        if mark_blocked_if_new(usage_table, user_arn, 'DAY', total, daily_limit):
            publish_alert(
                sns, SNS_TOPIC_ARN,
                event='BLOCKED', severity='CRITICAL', scope='DAY',
                user_arn=user_arn, total=total, limit=daily_limit,
                extra={'display_name': display_name(user_arn)},
            )
            logger.info('Blocked %s on DAY: %d/%d', display_name(user_arn), total, daily_limit)
        return

    if total >= daily_limit * warning_pct and not day_attrs.get('warned'):
        mark_warned_today(user_arn, quota_day, policy_version)
        publish_alert(
            sns, SNS_TOPIC_ARN,
            event='WARNING', severity='WARN', scope='DAY',
            user_arn=user_arn, total=total, limit=daily_limit,
            extra={'display_name': display_name(user_arn)},
        )
        logger.info('Warned %s on DAY: %d/%d', display_name(user_arn), total, daily_limit)


def _user_zero() -> dict[str, int]:
    return {'raw': 0, 'cache_read': 0, 'cache_write': 0, 'output': 0, 'count': 0}


def _model_zero() -> dict[str, int]:
    return {'raw': 0, 'cache_read': 0, 'cache_write': 0, 'output': 0}


def aggregate_log_events(log_events: list[dict]) -> tuple[
    dict[str, dict[str, int]],
    dict[str, dict[str, dict[str, int]]],
]:
    user_tokens: dict[str, dict[str, int]] = defaultdict(_user_zero)
    # Per-user, per-model breakdown so the handler can build CloudWatch model
    # metrics from APPLIED users only. A retried batch re-claims some users
    # (applied=False); if model metrics were summed over the whole batch they'd
    # include those users while the aggregate totals exclude them — inconsistent.
    user_models: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(_model_zero))

    for log_event in log_events:
        raw_arn = log_event.get('identity', {}).get('arn')
        if not raw_arn:
            continue
        arn = normalize_metering_principal(raw_arn)
        model_id = log_event.get('modelId', 'unknown')
        input_data = log_event.get('input', {})
        raw_in = input_data.get('inputTokenCount', 0) or 0
        cache_read = input_data.get('cacheReadInputTokenCount', 0) or 0
        cache_write = input_data.get('cacheWriteInputTokenCount', 0) or 0
        output_tokens = log_event.get('output', {}).get('outputTokenCount', 0) or 0
        if not (raw_in or cache_read or cache_write or output_tokens):
            continue

        u = user_tokens[arn]
        u['raw'] += raw_in
        u['cache_read'] += cache_read
        u['cache_write'] += cache_write
        u['output'] += output_tokens
        u['count'] += 1

        m = user_models[arn][model_id]
        m['raw'] += raw_in
        m['cache_read'] += cache_read
        m['cache_write'] += cache_write
        m['output'] += output_tokens

    return user_tokens, user_models


def emit_metrics(model_tokens: dict[str, dict[str, int]], total_input: int, total_output: int) -> None:
    if not (total_input or total_output):
        return
    now = datetime.now(timezone.utc)
    metric_data: list[dict[str, Any]] = [
        {'MetricName': 'InputTokens', 'Timestamp': now, 'Value': total_input, 'Unit': 'Count'},
        {'MetricName': 'OutputTokens', 'Timestamp': now, 'Value': total_output, 'Unit': 'Count'},
    ]
    for model_id, tokens in model_tokens.items():
        dims = [{'Name': 'ModelId', 'Value': model_id}]
        model_input = tokens['raw'] + tokens['cache_read'] + tokens['cache_write']
        metric_data.append({'MetricName': 'InputTokens', 'Timestamp': now, 'Value': model_input, 'Unit': 'Count', 'Dimensions': dims})
        metric_data.append({'MetricName': 'OutputTokens', 'Timestamp': now, 'Value': tokens['output'], 'Unit': 'Count', 'Dimensions': dims})
    cw.put_metric_data(Namespace=METRIC_NAMESPACE, MetricData=metric_data)


def batch_id_from_event(event: dict) -> str:
    raw = event['awslogs']['data']
    payload = raw.encode() if isinstance(raw, str) else raw
    return hashlib.sha256(payload).hexdigest()[:16]


def claim_ingestion_day(user_arn: str, batch_id: str, fallback_day: str, time_zone: str = 'UTC') -> str:
    """The quota date the batch was first applied, read from the dedup claim.

    On a duplicate redelivery the ADD landed on the ORIGINAL ingestion day's DAY# row, which
    may differ from today if the retry crosses local midnight. Re-checking the threshold against
    today's (empty) row would miss a block the failed first attempt should have made, so resolve
    the real day from the claim. Legacy claims only have claimedAt, so convert that UTC instant
    through the active quota timezone. Falls back to today if the claim is unreadable.
    """
    try:
        item = dedup_table.get_item(
            Key={'UserARN': user_arn, 'SK': f'BATCH#{batch_id}'},
            ConsistentRead=True,
        ).get('Item', {})
        quota_day = item.get('quotaDay')
        if isinstance(quota_day, str):
            return quota_day
        claimed_at = item.get('claimedAt')
        if isinstance(claimed_at, str):
            return quota_today(time_zone, datetime.fromisoformat(claimed_at)).isoformat()
    except (ClientError, ValueError):
        logger.warning('Could not read dedup claim for %s %s; using today', user_arn, batch_id)
    return fallback_day


def handler(event: dict, context: Any) -> None:
    log_events = decode_log_events(event)
    default_config = config_table.get_item(
        Key={'ConfigKey': 'default'},
        ConsistentRead=True,
    ).get('Item', {})
    time_zone, policy_version = quota_policy(default_config)
    today_date = quota_today(time_zone)
    today = today_date.isoformat()
    batch_id = batch_id_from_event(event)

    user_tokens, user_models = aggregate_log_events(log_events)

    total_input = 0
    total_output = 0
    # Built from applied users only so per-model metrics stay consistent with the
    # aggregate totals on a retried (partially-applied) batch.
    model_tokens: dict[str, dict[str, int]] = defaultdict(_model_zero)
    for user_arn, tokens in user_tokens.items():
        effective_config = get_effective_config(user_arn, default_config)
        applied = claim_and_update(
            user_arn, today, batch_id,
            tokens['raw'], tokens['cache_read'], tokens['cache_write'],
            tokens['output'], tokens['count'],
            policy_version, time_zone,
        )
        if applied:
            total_input += tokens['raw'] + tokens['cache_read'] + tokens['cache_write']
            total_output += tokens['output']
            for model_id, mt in user_models[user_arn].items():
                m = model_tokens[model_id]
                m['raw'] += mt['raw']
                m['cache_read'] += mt['cache_read']
                m['cache_write'] += mt['cache_write']
                m['output'] += mt['output']
            eval_day = today
        else:
            logger.info('Duplicate batch %s for %s; re-evaluating threshold only', batch_id, user_arn)
            # The original ADD may have landed on a prior day if this retry crossed quota midnight;
            # re-check that day's row, not today's (empty) one, so a block the failed first
            # attempt owed is still made.
            eval_day = claim_ingestion_day(user_arn, batch_id, today, time_zone)
        # Duplicate일지라도 threshold 평가는 수행. claim 성공한 이전 호출이 IAM/SNS 단계에서
        # 실패했을 수 있고, mark_blocked_if_new와 mark_warned_today 모두 idempotent라 재호출 안전.
        day_attrs = get_day_attrs(user_arn, eval_day, policy_version)
        check_day_threshold(
            user_arn,
            eval_day,
            day_attrs,
            policy_version=policy_version,
            config=effective_config,
        )

    emit_metrics(model_tokens, total_input, total_output)
