"""
State machine step BuildMigrationReport: summarise the execution so far.

Input: {'executionArn': $$.Execution.Id, 'fatalError': bool,
        'externalIdSecretCleanup': status of the CleanupExternalIdSecret step}.
Output: the migration report (see migration_report.build_report). The state
machine ends in its Fail state when the report status is FAILED or the secret
cleanup failed, so the execution status, the report status and the per step
statuses agree.
"""
import os

import boto3

from migration_report import build_report
from region_utils import BOTO_CONFIG
from region_utils import step_handler


@step_handler
def lambda_handler(event, context):
    execution_arn = event['executionArn']
    if not execution_arn.startswith(os.environ['EXECUTION_ARN_PREFIX']):
        raise ValueError('executionArn does not belong to this state machine')
    sfn = boto3.client('stepfunctions', config=BOTO_CONFIG)
    report = build_report(sfn, execution_arn, fatal_error=bool(event.get('fatalError')),
                          extra_results=event.get('extraResults'),
                          cleanup_status=event.get('externalIdSecretCleanup'))
    status, failed_item_count = report['status'], report['failedItemCount']
    cleanup_status = report['externalIdSecret']['cleanupStatus']
    verification_status = report['verification']['verdict']
    print(f"Migration report: {status}, {len(report['failedSteps'])} failed steps, "
          f"{failed_item_count} failed items, {len(report['manualFollowUp'])} follow up items, "
          f"ExternalId secret cleanup {cleanup_status}, target verification {verification_status}")
    return report
