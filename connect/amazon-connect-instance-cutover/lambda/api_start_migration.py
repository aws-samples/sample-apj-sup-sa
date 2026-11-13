"""
POST /start: validate the request and start the migration state machine.

The ExternalId from the request body is validated, stored in a per execution
Secrets Manager secret, and only the secret ARN goes into the state machine
input. Migration steps refuse a raw 'externalId' (region_utils.resolve_external_id),
so anyone starting the state machine directly must create the secret first.
The value is never logged or returned.
"""
import json
import os
import uuid
from datetime import datetime, timezone

import boto3

from region_utils import (
    BOTO_CONFIG,
    EXTERNAL_ID_PATTERN,
    ROLE_ARN_PATTERN,
    describe_error,
    error_code_of,
    error_text,
    external_id_from_api_request,
    extract_account_id_from_arn,
    extract_instance_id_from_arn,
    extract_partition_from_arn,
    extract_region_from_arn,
    sdk_region_warning,
)

CROSS_PARTITION_ERROR = ('The source instance, the target instance and the target role must be in the same AWS '
                         'partition (CrossPartitionNotSupported)')

# Only these keys reach the state machine input.
ALLOWED_KEYS = ('sourceInstanceId', 'destinationInstanceId', 'crossAccountRoleArn', 'externalIdSecretArn',
                'features', 'userOptions', 'queueOptions', 'selectedPhoneNumbers')


def cors_headers():
    return {
        'Access-Control-Allow-Origin': os.environ['ALLOWED_ORIGIN'],
        'Access-Control-Allow-Headers': 'Content-Type,Authorization',
        'Access-Control-Allow-Methods': 'POST,OPTIONS',
    }


def response(status, body):
    return {'statusCode': status, 'headers': cors_headers(), 'body': json.dumps(body)}


def build_execution_input(body):
    """Return (input_dict, external_id, None) or (None, None, error_message).

    The ExternalId is returned separately so the caller can put it in a secret;
    it is never part of the input dict and never echoed in an error.
    """
    source = (body.get('sourceInstanceId') or '').strip()
    dest = (body.get('destinationInstanceId') or '').strip()
    if not source or not dest:
        return None, None, 'Missing required instance ARNs'
    try:
        extract_instance_id_from_arn(source)
        extract_instance_id_from_arn(dest)
        extract_region_from_arn(source)  # format only, never a Region list
        extract_region_from_arn(dest)
        partition = extract_partition_from_arn(source)
        cross_account = extract_account_id_from_arn(source) != extract_account_id_from_arn(dest)
    except ValueError as err:
        return None, None, error_text(err)
    # A role cannot be assumed across partitions (for example aws and aws-us-gov).
    if extract_partition_from_arn(dest) != partition:
        return None, None, CROSS_PARTITION_ERROR

    role = (body.get('crossAccountRoleArn') or '').strip() or None
    external_id = external_id_from_api_request(body)
    if cross_account and not role:
        return None, None, 'Cross-account migration requires crossAccountRoleArn and externalId'
    if role:
        if not ROLE_ARN_PATTERN.match(role):
            return None, None, 'crossAccountRoleArn is not a valid IAM role ARN'
        if role.split(':')[1] != partition:
            return None, None, CROSS_PARTITION_ERROR
        if not external_id:
            return None, None, 'externalId is required when a target role is used'
        if not EXTERNAL_ID_PATTERN.match(external_id):
            return None, None, 'externalId has an invalid format'
    else:
        external_id = None

    execution_input = {key: body.get(key) for key in ALLOWED_KEYS}
    execution_input.update({
        'sourceInstanceId': source,
        'destinationInstanceId': dest,
        'crossAccountRoleArn': role,
        # Filled in by lambda_handler with the secret ARN; the value itself never
        # enters the execution input or history.
        'externalIdSecretArn': None,
        'features': body.get('features') or {},
        'userOptions': body.get('userOptions') or {},
        'queueOptions': body.get('queueOptions') or {},
        'selectedPhoneNumbers': selected_phone_number_ids(body.get('selectedPhoneNumbers')),
    })
    return execution_input, external_id, None


def selected_phone_number_ids(selected):
    """IDs and ARNs of the selected numbers only: the numbers never enter the execution input or history."""
    if not isinstance(selected, list):
        return []
    return [{'PhoneNumberId': n['PhoneNumberId'], 'PhoneNumberArn': n.get('PhoneNumberArn')}
            for n in selected if isinstance(n, dict) and isinstance(n.get('PhoneNumberId'), str)]


def create_external_id_secret(name, external_id):
    """Store the ExternalId in a per execution secret (default aws/secretsmanager key, no tags)."""
    secret_name = f"{os.environ['EXTERNAL_ID_SECRET_PREFIX']}{name}"
    created = boto3.client('secretsmanager', config=BOTO_CONFIG).create_secret(
        Name=secret_name,
        Description='ExternalId for one connect migration execution; deleted when the execution ends',
        SecretString=external_id,
    )
    return created['ARN']


def delete_external_id_secret(secret_arn):
    boto3.client('secretsmanager', config=BOTO_CONFIG).delete_secret(
        SecretId=secret_arn, ForceDeleteWithoutRecovery=True)


def lambda_handler(event, context):
    if event.get('httpMethod') == 'OPTIONS':
        return response(200, {})
    try:
        body = json.loads(event['body']) if event.get('body') else {}
    except (json.JSONDecodeError, TypeError):
        return response(400, {'error': 'Request body must be JSON'})
    if not isinstance(body, dict):
        return response(400, {'error': 'Request body must be a JSON object'})

    execution_input, external_id, error = build_execution_input(body)
    if error:
        return response(400, {'error': error})

    state_machine_arn = os.environ['STATE_MACHINE_ARN']
    name = f"web-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    # Cognito subject (a pseudonymous ID), never the email.
    caller_sub = (event.get('requestContext') or {}).get('authorizer', {}).get('claims', {}).get('sub', 'unknown')
    print(f"Starting execution {name} requested by user sub {caller_sub}")

    secret_arn = create_external_id_secret(name, external_id) if external_id else None
    execution_input['externalIdSecretArn'] = secret_arn
    try:
        result = boto3.client('stepfunctions', config=BOTO_CONFIG).start_execution(
            stateMachineArn=state_machine_arn, name=name, input=json.dumps(execution_input)
        )
    except Exception as start_error:
        # The execution never started, so neither its cleanup step nor the
        # EventBridge backstop will ever delete the secret: delete it here.
        if secret_arn:
            discard_secret_after_failed_start(name, secret_arn)
        print(f"StartExecution failed for {name}: {describe_error(start_error)}")
        raise  # always the original start error, never the delete error
    body = {'executionArn': result['executionArn'], 'message': 'Migration started successfully'}
    # Soft warnings only: a Region this SDK does not list still runs.
    warnings = [w for w in (sdk_region_warning(execution_input['sourceInstanceId']),
                            sdk_region_warning(execution_input['destinationInstanceId'])) if w]
    for warning in warnings:
        print(f"Warning: {warning}")
    if warnings:
        body['warnings'] = warnings
    return response(200, body)


def discard_secret_after_failed_start(name, secret_arn):
    """Best effort delete after a failed start; a delete failure is logged by error code only."""
    try:
        delete_external_id_secret(secret_arn)
    except Exception as delete_error:  # never mask the start error
        print(f"Start failed and the ExternalId secret for {name} could not be deleted: "
              f"{error_code_of(delete_error)}")
        return False
    print(f"Start failed; deleted the ExternalId secret for {name}")
    return True
