"""
Delete the per execution ExternalId secret. Two callers, same role:

* The CleanupExternalIdSecret state (step-functions/migration-state-machine.json)
  on the normal success and failure paths. Input: {'externalIdSecretArn': ...}.
* The EventBridge backstop rule (cloudformation/4-step-functions.yaml) for
  executions that never reach that state: ABORTED, TIMED_OUT, or FAILED by a
  States.Runtime or DataLimitExceeded error. Input: the Step Functions
  Execution Status Change event. The secret name is derived from the
  execution name: EXTERNAL_ID_SECRET_PREFIX + execution name (the start API
  names it that way; direct starts must too).

The role can only delete secrets under the tool's prefix. A missing secret
counts as success (same account runs have none, and the state machine step and
the backstop may both run for one execution). Only IDs and error codes are logged.
"""
import os
import re

import boto3
from botocore.exceptions import ClientError

from region_utils import BOTO_CONFIG, SECRET_ARN_PATTERN, MigrationStepError, error_code
from region_utils import step_handler

EVENT_SOURCE = 'aws.states'
EVENT_DETAIL_TYPE = 'Step Functions Execution Status Change'
BACKSTOP_STATUSES = frozenset({'ABORTED', 'TIMED_OUT', 'FAILED'})
# Characters valid in both an execution name and a secret name, and never '/',
# so a derived name cannot leave the prefix.
EXECUTION_NAME_PATTERN = re.compile(r'^[A-Za-z0-9_.@+=-]{1,80}$')


def _delete(secret_id, region=None):
    """Force delete one secret. Returns 'DELETED' or 'ALREADY_DELETED'; raises other ClientErrors."""
    kwargs = {'config': BOTO_CONFIG}
    if region:
        kwargs['region_name'] = region
    try:
        boto3.client('secretsmanager', **kwargs).delete_secret(SecretId=secret_id, ForceDeleteWithoutRecovery=True)
    except ClientError as err:
        if error_code(err) == 'ResourceNotFoundException':
            return 'ALREADY_DELETED'
        raise
    return 'DELETED'


def handle_state_machine_step(event):
    secret_arn = (event or {}).get('externalIdSecretArn')
    if not secret_arn:
        return {'status': 'NOT_NEEDED'}
    match = SECRET_ARN_PATTERN.match(secret_arn)
    prefix = os.environ['EXTERNAL_ID_SECRET_PREFIX']
    if not match or f':secret:{prefix}' not in secret_arn:
        return {'status': 'FAILED', 'errorCode': 'InvalidExternalIdSecret'}
    try:
        status = _delete(secret_arn, match.group(1))
    except ClientError as err:
        print(f"Could not delete the ExternalId secret: {error_code(err)}")
        return {'status': 'FAILED', 'errorCode': error_code(err)}
    print(f"ExternalId secret cleanup: {status}")
    return {'status': status}


def handle_status_change_event(event):
    """Backstop: delete the secret of an execution that ended without its cleanup step.

    Raises on unexpected delete errors so Lambda's asynchronous retries run again;
    after the last retry Lambda sends the event to the function's dead letter queue
    (the cleanup failure queue). The raised text holds the exception type, the error
    code and the action only (step_handler), never the AWS error message or the
    secret name, because Lambda copies it into the queue message.
    """
    detail = event.get('detail') or {}
    if detail.get('stateMachineArn') != os.environ['STATE_MACHINE_ARN']:
        print("Ignored a status change event for another state machine")
        return {'status': 'IGNORED', 'errorCode': 'ForeignStateMachine'}
    status = detail.get('status')
    execution_arn = detail.get('executionArn')
    if status not in BACKSTOP_STATUSES:
        return {'status': 'NOT_NEEDED'}
    execution_name = detail.get('name') or ''
    if not EXECUTION_NAME_PATTERN.match(execution_name):
        print(f"Backstop for {execution_arn}: execution name cannot map to a secret name")
        return {'status': 'FAILED', 'errorCode': 'InvalidExecutionName'}
    secret_name = os.environ['EXTERNAL_ID_SECRET_PREFIX'] + execution_name
    try:
        delete_status = _delete(secret_name)
    except ClientError as err:
        code = error_code(err) or 'ClientError'
        print(f"Backstop for {execution_arn} ({status}): could not delete the ExternalId secret: {code}")
        raise MigrationStepError(f"DeleteSecret failed ({code}) on {secret_name}", code=code,
                                 action='DeleteSecret', resource=secret_name) from None
    print(f"Backstop for {execution_arn} ({status}): ExternalId secret {delete_status}")
    return {'status': delete_status}


@step_handler
def lambda_handler(event, context):
    event = event or {}
    if event.get('source') == EVENT_SOURCE and event.get('detail-type') == EVENT_DETAIL_TYPE:
        return handle_status_change_event(event)
    return handle_state_machine_step(event)
