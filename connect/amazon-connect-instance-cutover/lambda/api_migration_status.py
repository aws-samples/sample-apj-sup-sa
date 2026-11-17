"""
POST /check-migration-status: execution status and the migration report
(per step status, failed steps and items, manual follow up, and the
VerifyTarget result in `verification`).

ExternalId values are redacted from everything returned.
"""
import json
import os
import re

import boto3

from migration_report import CLEANUP_WARNING_TEXT, SCOPE, build_report
from region_utils import BOTO_CONFIG

REDACT_KEY = re.compile(r'external_?id', re.IGNORECASE)
FAILED_EXECUTION_STATUSES = frozenset({'ABORTED', 'TIMED_OUT', 'FAILED'})


def cors_headers():
    return {
        'Access-Control-Allow-Origin': os.environ['ALLOWED_ORIGIN'],
        'Access-Control-Allow-Headers': 'Content-Type,Authorization',
        'Access-Control-Allow-Methods': 'POST,OPTIONS',
    }


def response(status, body):
    return {'statusCode': status, 'headers': cors_headers(), 'body': json.dumps(body, default=str)}


def redact(value):
    if isinstance(value, dict):
        return {k: ('***' if REDACT_KEY.fullmatch(k) else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def parse(text):
    try:
        return json.loads(text) if text else {}
    except (json.JSONDecodeError, TypeError):
        return {'raw': text}


def lambda_handler(event, context):
    if event.get('httpMethod') == 'OPTIONS':
        return response(200, {})
    body = parse(event.get('body'))
    execution_arn = body.get('executionArn') if isinstance(body, dict) else None
    allowed_prefix = os.environ['EXECUTION_ARN_PREFIX']
    if not execution_arn or not execution_arn.startswith(allowed_prefix):
        return response(400, {'error': 'Missing or invalid executionArn'})

    sfn = boto3.client('stepfunctions', config=BOTO_CONFIG)
    execution = sfn.describe_execution(executionArn=execution_arn)
    status = execution.get('status', 'UNKNOWN')

    # Same report the state machine's BuildMigrationReport step uses. The fatal
    # flag is derived from the history (HandleError ran), like the state machine
    # passes it, and the result is cross checked with the execution status.
    report = build_report(sfn, execution_arn, fatal_error=None)
    return response(200, redact({
        'status': status,
        'migrationStatus': reconcile_migration_status(status, report),
        'reportStatus': report['status'],
        'fatalError': report['fatalError'],
        'reportStepFailed': report.get('reportStepFailed', False),
        'externalIdSecret': secret_status(status, report),
        'steps': report['steps'],
        'failedSteps': report['failedSteps'],
        'failedItemCount': report['failedItemCount'],
        'manualFollowUp': report['manualFollowUp'],
        'verification': report.get('verification') or {'verdict': 'NOT_RUN'},
        'scope': report.get('scope') or dict(SCOPE),
        'startDate': execution.get('startDate'),
        'stopDate': execution.get('stopDate'),
    }))


def reconcile_migration_status(execution_status, report):
    """migrationStatus never says SUCCEEDED unless the execution and the report both succeeded."""
    if execution_status == 'RUNNING':
        return 'RUNNING'
    if execution_status in FAILED_EXECUTION_STATUSES or report.get('reportStepFailed'):
        return 'FAILED'
    if execution_status == 'SUCCEEDED' and report['status'] == 'SUCCEEDED':
        return 'SUCCEEDED'
    # Any other combination (unknown execution status, or a report that disagrees) is not a success.
    return 'FAILED'


def secret_status(execution_status, report):
    """The report's secret section; a run that ended without its cleanup step may have left the secret."""
    secret = dict(report.get('externalIdSecret') or {'cleanupStatus': 'NOT_RUN', 'mayRemain': False})
    if execution_status in FAILED_EXECUTION_STATUSES and secret.get('cleanupStatus') == 'NOT_RUN':
        secret['mayRemain'] = True
        secret['message'] = CLEANUP_WARNING_TEXT
    return secret
