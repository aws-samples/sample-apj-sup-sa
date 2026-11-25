import json
import time
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

from .principals import extract_role_name, extract_username, is_iam_role_arn

iam = boto3.client('iam')

POLICY_NAME = 'AutoBlocked-BedrockMeter-QuotaExceeded'

DENY_POLICY_DOC = json.dumps({
    "Version": "2012-10-17",
    "Statement": [{
        "Sid": "AutoBlockBedrockQuotaExceeded",
        "Effect": "Deny",
        "Action": [
            "bedrock:Invoke*",
            "bedrock:Converse*",
            "bedrock:RetrieveAndGenerate*",
            "bedrock:InvokeAgent*",
        ],
        "Resource": "*",
    }],
})


def attach_deny_policy(user_arn: str) -> None:
    if is_iam_role_arn(user_arn):
        iam.put_role_policy(
            RoleName=extract_role_name(user_arn),
            PolicyName=POLICY_NAME,
            PolicyDocument=DENY_POLICY_DOC,
        )
    else:
        iam.put_user_policy(
            UserName=extract_username(user_arn),
            PolicyName=POLICY_NAME,
            PolicyDocument=DENY_POLICY_DOC,
        )


def remove_deny_policy(user_arn: str) -> bool:
    """Returns True if a policy was removed, False if it was already absent."""
    try:
        if is_iam_role_arn(user_arn):
            iam.delete_role_policy(RoleName=extract_role_name(user_arn), PolicyName=POLICY_NAME)
        else:
            iam.delete_user_policy(UserName=extract_username(user_arn), PolicyName=POLICY_NAME)
        return True
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchEntity':
            return False
        raise


def ensure_deny_policy(user_arn: str) -> bool:
    """Restore the managed deny policy if STATE says blocked but IAM drifted."""
    is_role = is_iam_role_arn(user_arn)
    name = extract_role_name(user_arn) if is_role else extract_username(user_arn)
    try:
        if is_role:
            iam.get_role_policy(RoleName=name, PolicyName=POLICY_NAME)
        else:
            iam.get_user_policy(UserName=name, PolicyName=POLICY_NAME)
        return False
    except ClientError as e:
        if e.response['Error']['Code'] != 'NoSuchEntity':
            raise
    attach_deny_policy(user_arn)
    return True


def mark_blocked_if_new(usage_table, user_arn: str, scope: str, total: int, limit: int) -> bool:
    """Atomic block: returns True iff this call newly transitioned the user to blocked.

    Conditional ensures only one concurrent caller wins the publish-notification race.
    """
    attach_deny_policy(user_arn)
    try:
        usage_table.update_item(
            Key={'UserARN': user_arn, 'SK': 'STATE'},
            UpdateExpression=(
                'SET blocked = :t, blockedScope = :s, blockedAt = :ts, '
                'blockedReason = :r, blockedAtEpoch = :e'
            ),
            ConditionExpression='attribute_not_exists(blocked) OR blocked = :f',
            ExpressionAttributeValues={
                ':t': True,
                ':f': False,
                ':s': scope,
                ':ts': datetime.now(timezone.utc).isoformat(),
                ':r': f'{scope} limit exceeded: {total}/{limit} credits',
                ':e': int(time.time()),
            },
        )
        return True
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            return False
        raise


def _set_unblocking(usage_table, user_arn: str) -> None:
    usage_table.update_item(
        Key={'UserARN': user_arn, 'SK': 'STATE'},
        UpdateExpression='SET blocked = :f',
        ExpressionAttributeValues={':f': False},
    )


def _finish_unblock(usage_table, user_arn: str) -> bool:
    try:
        usage_table.update_item(
            Key={'UserARN': user_arn, 'SK': 'STATE'},
            UpdateExpression=(
                'REMOVE blockedScope, blockedReason, blockedAt, blockedAtEpoch, '
                'warnedKey, warnedWeekKey, warnedMonthKey'
            ),
            ConditionExpression='blocked = :f',
            ExpressionAttributeValues={':f': False},
        )
        return True
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            return False
        raise


def unblock_with_reconciliation(usage_table, user_arn: str) -> dict[str, Any]:
    """Transition STATE and IAM policy with compensation for failures and races."""
    _set_unblocking(usage_table, user_arn)
    try:
        removed = remove_deny_policy(user_arn)
    except Exception:
        # Fail closed. Keeping blocked=true makes the next periodic pass retry safely.
        usage_table.update_item(
            Key={'UserARN': user_arn, 'SK': 'STATE'},
            UpdateExpression='SET blocked = :t',
            ExpressionAttributeValues={':t': True},
        )
        raise

    if not _finish_unblock(usage_table, user_arn):
        # A concurrent invocation moved false -> true while IAM deletion was in flight.
        ensure_deny_policy(user_arn)
        return {
            'unblocked': False,
            'policy_removed': removed,
            'reason': 'concurrent reblock',
        }
    return {'unblocked': True, 'policy_removed': removed}


def reevaluate_and_unblock(
    usage_table,
    sns_client,
    topic_arn: str,
    user_arn: str,
    scope_totals: dict[str, tuple[int, int]],
    *,
    source: str = 'reevaluate',
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Unblock iff every enforced scope is below limit. Totals must be in the same
    unit as limits (credits) — the caller computes them so block/unblock match.

    scope_totals: {'DAY': (total, limit), 'WEEK': (...), 'MONTH': (...)}. A scope
    with limit <= 0 is treated as unenforced and ignored. Returns a result dict;
    when unblocked, removes the deny policy, clears STATE, and publishes RESOLVED.
    """
    over = [
        (scope, total, limit)
        for scope, (total, limit) in scope_totals.items()
        if limit > 0 and total >= limit
    ]
    if over:
        ensure_deny_policy(user_arn)
        return {'unblocked': False, 'reason': 'still over limits', 'over': over}

    result = unblock_with_reconciliation(usage_table, user_arn)
    if not result['unblocked']:
        return result
    payload_extra = {'policy_removed': result['policy_removed'], 'source': source}
    if extra:
        payload_extra.update(extra)
    publish_alert(
        sns_client, topic_arn,
        event='RESOLVED', severity='INFO', scope='RESET',
        user_arn=user_arn,
        extra=payload_extra,
    )
    return result


def publish_alert(
    sns_client,
    topic_arn: str,
    *,
    event: str,
    severity: str,
    scope: str,
    user_arn: str,
    total: int | None = None,
    limit: int | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Publish a structured JSON alert. Subject is human-readable; Message is parseable JSON."""
    payload: dict[str, Any] = {
        'event': event,
        'severity': severity,
        'scope': scope,
        'user_arn': user_arn,
        'timestamp': datetime.now(timezone.utc).isoformat(),
    }
    if total is not None:
        payload['total'] = total
    if limit is not None and limit > 0:
        payload['limit'] = limit
        payload['percentage'] = total * 100 // limit if total is not None else None
    if extra:
        payload.update(extra)

    sns_client.publish(
        TopicArn=topic_arn,
        Subject=f'Bedrock Meter - {event} ({scope})'[:99],
        Message=json.dumps(payload),
        MessageAttributes={
            'severity': {'DataType': 'String', 'StringValue': severity},
            'scope': {'DataType': 'String', 'StringValue': scope},
            'event': {'DataType': 'String', 'StringValue': event},
        },
    )
