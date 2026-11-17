"""
POST /verify-migration: on demand read only verification from the UI.

Runs the same checks, with the same output, as the VerifyTarget state machine
step: it calls verify_target.run_verification, so the two cannot diverge.
Only the entry point differs:

* The ExternalId comes from the UI request body (API handlers only, never a
  secret), and is never logged or returned.
* The Describe based checks stop after verify_target.API_TIME_BUDGET_SECONDS
  so the answer fits in the API Gateway timeout. A check that stopped early
  fails with VerificationIncomplete; the VerifyTarget result in the
  migration report has the full run.

Errors are answered with an error code and the denied action, never the
exception text.
"""
import json
import os

import verify_target
from region_utils import (
    THROTTLING_CODES,
    MigrationStepError,
    create_destination_session,
    describe_error,
    error_code_of,
    external_id_from_api_request,
)


def cors_headers():
    return {
        'Access-Control-Allow-Origin': os.environ['ALLOWED_ORIGIN'],
        'Access-Control-Allow-Headers': 'Content-Type,Authorization',
        'Access-Control-Allow-Methods': 'POST,OPTIONS',
    }


def response(status, body):
    return {'statusCode': status, 'headers': cors_headers(), 'body': json.dumps(body)}


def http_status(err):
    if err.code in THROTTLING_CODES:
        return 429
    if err.code and err.code.startswith(('Missing', 'Invalid')):
        return 400
    return 403


def lambda_handler(event, context):
    if event.get('httpMethod') == 'OPTIONS':
        return response(200, {})
    try:
        body = json.loads(event['body']) if event.get('body') else event
    except (json.JSONDecodeError, TypeError):
        return response(400, {'error': 'InvalidRequestBody'})
    if not isinstance(body, dict):
        return response(400, {'error': 'InvalidRequestBody'})

    source_arn = body.get('sourceInstanceId')
    target_arn = body.get('destinationInstanceId')
    if not source_arn or not target_arn:
        return response(400, {'error': 'MissingInstanceArn'})
    options = {'queueOptions': body.get('queueOptions') or {}, 'userOptions': body.get('userOptions') or {}}
    try:
        external_id = external_id_from_api_request(body)  # UI request only, never logged
        target_session = create_destination_session(target_arn, body.get('crossAccountRoleArn'), external_id)
        result = verify_target.run_verification(source_arn, target_arn, target_session, body.get('features') or {},
                                                options, verify_target.Budget.for_api())
    except MigrationStepError as err:
        # AccessDenied, throttling or a missing role or ExternalId: never a partial result.
        code, action = err.code or 'MigrationStepError', err.action
        return response(http_status(err), {'error': code, 'code': code, 'action': action})
    except ValueError:
        return response(400, {'error': 'InvalidInstanceArn'})
    except Exception as err:
        print(f"Error in verify_migration: {describe_error(err)}")
        return response(500, {'error': error_code_of(err)})
    verify_target.log_summary(result)
    return response(200, result)
