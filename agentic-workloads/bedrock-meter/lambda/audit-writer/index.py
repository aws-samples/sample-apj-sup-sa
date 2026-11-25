import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

from common.principals import display_name

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ddb = boto3.resource('dynamodb')

AUDIT_TABLE = os.environ['AUDIT_TABLE']
AUDIT_TTL_DAYS = int(os.environ.get('AUDIT_TTL_DAYS', '730'))

audit_table = ddb.Table(AUDIT_TABLE)

# publish_alert always carries these; everything else lives under the flattened extra.
_CORE_FIELDS = {'event', 'severity', 'scope', 'user_arn', 'timestamp', 'total', 'limit', 'percentage'}

# Who initiated the event. Admin actions carry caller_*; system decisions don't.
_SYSTEM_ACTOR = 'system'


def _record_timestamp(payload: dict[str, Any]) -> str:
    ts = payload.get('timestamp')
    if isinstance(ts, str) and ts:
        return ts
    return datetime.now(timezone.utc).isoformat()


def _actor(payload: dict[str, Any]) -> str:
    # Prefer the real human operator (forwarded by the console BFF and trusted by Core only when
    # the SigV4 caller is the admin BFF role) over the proxy caller. For direct IAM-user admin
    # calls there is no operator_*, so caller_username/caller_arn stands.
    return (
        payload.get('operator_email')
        or payload.get('operator_username')
        or payload.get('caller_username')
        or payload.get('caller_arn')
        or _SYSTEM_ACTOR
    )


def _target(payload: dict[str, Any]) -> str:
    return payload.get('username') or display_name(payload.get('user_arn', '')) or 'unknown'


def _reason(payload: dict[str, Any]) -> str:
    # Operator-supplied reason (admin actions) always wins when present.
    operator_reason = payload.get('reason')
    if isinstance(operator_reason, str) and operator_reason.strip():
        return operator_reason.strip()

    event = payload.get('event', '')
    scope = payload.get('scope', '')
    total, limit = payload.get('total'), payload.get('limit')
    if event in ('BLOCKED', 'WARNING') and total is not None and limit:
        verb = 'blocked' if event == 'BLOCKED' else 'warned'
        return f'{scope} {verb}: {total}/{limit} credits'
    if event == 'LIMIT_CHANGED':
        return f'limits updated: {json.dumps(payload.get("changes", {}))}'
    if event == 'LIMIT_REMOVED':
        return 'per-user override removed'
    if event == 'QUOTA_TIMEZONE_CHANGED':
        return f'quota timezone changed: {json.dumps(payload.get("changes", {}))}'
    if event == 'MANUAL_UNBLOCK':
        return 'operator force-unblock'
    if event == 'RESOLVED':
        return 'auto-unblock: all scopes below limit'
    return event


def _diff(payload: dict[str, Any]) -> dict[str, str]:
    """Optional before/after, rendered as compact strings for the console."""
    out: dict[str, str] = {}
    before = payload.get('before')
    after = payload.get('after')
    if before is not None:
        out['before'] = before if isinstance(before, str) else json.dumps(before)
    if after is not None:
        out['after'] = after if isinstance(after, str) else json.dumps(after)
    if 'changes' in payload and 'after' not in out:
        out['after'] = json.dumps(payload['changes'])
    return out


def _build_item(payload: dict[str, Any], message_id: str) -> dict[str, Any]:
    ts = _record_timestamp(payload)
    month = ts[:7]  # 'yyyy-mm' from ISO 8601
    # SK ties the record to the SNS message: same message → same SK → idempotent.
    item: dict[str, Any] = {
        'PK': f'AUDIT#{month}',
        'SK': f'{ts}#{message_id}',
        'eventId': message_id,
        'timestamp': ts,
        'event': payload.get('event', 'UNKNOWN'),
        'severity': payload.get('severity', 'INFO'),
        'scope': payload.get('scope', ''),
        'actor': _actor(payload),
        'target': _target(payload),
        'targetArn': payload.get('user_arn', ''),
        'reason': _reason(payload),
        'expiresAt': int(datetime.fromisoformat(ts).timestamp()) + AUDIT_TTL_DAYS * 86400,
    }
    # When the actor is a proxied operator, keep the BFF role ARN for forensics (who relayed it),
    # demoted from `actor`. Only set when it actually differs from the actor.
    if (payload.get('operator_email') or payload.get('operator_username')) and payload.get('caller_arn'):
        item['proxyCallerArn'] = payload['caller_arn']
    item.update(_diff(payload))
    return item


def _persist(payload: dict[str, Any], message_id: str) -> None:
    item = _build_item(payload, message_id)
    try:
        # Idempotent: a redelivered SNS message has the same MessageId → same SK.
        audit_table.put_item(
            Item=item,
            ConditionExpression='attribute_not_exists(SK)',
        )
        logger.info('Recorded audit %s for %s (%s)', item['event'], item['target'], item['SK'])
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            logger.info('Duplicate audit message %s ignored', message_id)
            return
        raise


def handler(event: dict, context: Any) -> None:
    for record in event.get('Records', []):
        sns = record.get('Sns', {})
        message_id = sns.get('MessageId')
        raw = sns.get('Message', '')
        if not message_id or not raw:
            logger.warning('Skipping SNS record without MessageId/Message')
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning('Non-JSON SNS message %s skipped', message_id)
            continue
        if not isinstance(payload, dict) or 'event' not in payload:
            logger.warning('SNS message %s missing event field, skipped', message_id)
            continue
        _persist(payload, message_id)
