"""POST /start and /check-migration-status: ExternalId handling and the migration report."""
import json

import pytest
from botocore.stub import ANY

import api_migration_status
import api_start_migration
from conftest import EXTERNAL_ID, SOURCE_INSTANCE_ARN, TARGET_INSTANCE_ARN, TARGET_ROLE_ARN, make_client

STATE_MACHINE_ARN = 'arn:aws:states:eu-west-1:111111111111:stateMachine:connect-user-migration-test'
EXECUTION_PREFIX = 'arn:aws:states:eu-west-1:111111111111:execution:connect-user-migration-test:'


def start_event(**body):
    return {'httpMethod': 'POST', 'body': json.dumps(body)}


TEST_NAME_PREFIX = 'connect-migration/test/'
TEST_RESOURCE_ARN = 'arn:aws:secretsmanager:eu-west-1:111111111111:secret:connect-migration/test/web-run-AbCdEf'


@pytest.fixture
def aws(monkeypatch, stubbed):
    """Stubbed Step Functions and Secrets Manager clients behind boto3.client."""
    sfn_client, sfn_stub = stubbed('stepfunctions', 'eu-west-1')
    sm_client, sm_stub = stubbed('secretsmanager', 'eu-west-1')
    clients = {'stepfunctions': sfn_client, 'secretsmanager': sm_client}
    monkeypatch.setattr('boto3.client', lambda service, *a, **k: clients[service])
    monkeypatch.setenv('STATE_MACHINE_ARN', STATE_MACHINE_ARN)
    monkeypatch.setenv('EXECUTION_ARN_PREFIX', EXECUTION_PREFIX)
    monkeypatch.setenv('EXTERNAL_ID_SECRET_PREFIX', TEST_NAME_PREFIX)
    monkeypatch.setenv('ALLOWED_ORIGIN', 'https://d111111abcdef8.cloudfront.net')
    return sfn_stub, sm_stub


@pytest.fixture
def sfn(aws):
    return aws[0]


def test_cross_account_requires_role_and_external_id():
    _, _, error = api_start_migration.build_execution_input(
        {'sourceInstanceId': SOURCE_INSTANCE_ARN, 'destinationInstanceId': TARGET_INSTANCE_ARN})
    assert 'crossAccountRoleArn' in error
    _, _, error = api_start_migration.build_execution_input(
        {'sourceInstanceId': SOURCE_INSTANCE_ARN, 'destinationInstanceId': TARGET_INSTANCE_ARN,
         'crossAccountRoleArn': TARGET_ROLE_ARN})
    assert 'externalId is required' in error


def test_invalid_external_id_is_rejected_without_echo():
    bad = 'has spaces & symbols'
    _, _, error = api_start_migration.build_execution_input(
        {'sourceInstanceId': SOURCE_INSTANCE_ARN, 'destinationInstanceId': TARGET_INSTANCE_ARN,
         'crossAccountRoleArn': TARGET_ROLE_ARN, 'externalId': bad})
    assert 'invalid format' in error and bad not in error


def test_execution_input_never_contains_the_external_id():
    execution_input, external_id, error = api_start_migration.build_execution_input(
        {'sourceInstanceId': SOURCE_INSTANCE_ARN, 'destinationInstanceId': TARGET_INSTANCE_ARN,
         'crossAccountRoleArn': TARGET_ROLE_ARN, 'externalId': EXTERNAL_ID, 'unexpected': 'dropped'})
    assert error is None and external_id == EXTERNAL_ID
    assert EXTERNAL_ID not in json.dumps(execution_input)
    assert 'externalId' not in execution_input and 'unexpected' not in execution_input
    # Keys the state machine reads with JSONPath always exist.
    for key in ('features', 'queueOptions', 'userOptions', 'selectedPhoneNumbers', 'crossAccountRoleArn',
                'externalIdSecretArn'):
        assert key in execution_input


def expected_input(secret_arn):
    return {
        'sourceInstanceId': SOURCE_INSTANCE_ARN, 'destinationInstanceId': TARGET_INSTANCE_ARN,
        'crossAccountRoleArn': TARGET_ROLE_ARN, 'externalIdSecretArn': secret_arn,
        'features': {'queues': True}, 'userOptions': {}, 'queueOptions': {}, 'selectedPhoneNumbers': [],
    }


def start_with_role():
    return api_start_migration.lambda_handler(start_event(
        sourceInstanceId=SOURCE_INSTANCE_ARN, destinationInstanceId=TARGET_INSTANCE_ARN,
        crossAccountRoleArn=TARGET_ROLE_ARN, externalId=EXTERNAL_ID, features={'queues': True}), None)


def test_start_stores_external_id_in_a_secret_and_passes_only_its_arn(aws, capsys):
    sfn_stub, sm_stub = aws
    sm_stub.add_response('create_secret', {'ARN': TEST_RESOURCE_ARN, 'Name': 'x'}, {
        'Name': ANY, 'Description': ANY, 'SecretString': EXTERNAL_ID})
    sfn_stub.add_response('start_execution', {'executionArn': EXECUTION_PREFIX + 'x', 'startDate': '2026-01-01'},
                          {'stateMachineArn': STATE_MACHINE_ARN, 'name': ANY,
                           'input': json.dumps(expected_input(TEST_RESOURCE_ARN))})
    result = start_with_role()
    assert result['statusCode'] == 200
    assert EXTERNAL_ID not in result['body']
    assert EXTERNAL_ID not in capsys.readouterr().out
    assert result['headers']['Access-Control-Allow-Origin'] == 'https://d111111abcdef8.cloudfront.net'


def test_secret_name_uses_the_fixed_prefix(aws):
    sfn_stub, sm_stub = aws
    names = []

    def capture(params, **kwargs):
        names.append(params['Name'])

    sm_stub.add_response('create_secret', {'ARN': TEST_RESOURCE_ARN, 'Name': 'x'})
    sfn_stub.add_response('start_execution', {'executionArn': EXECUTION_PREFIX + 'x', 'startDate': '2026-01-01'})
    sm_stub.client.meta.events.register('provide-client-params.secrets-manager.CreateSecret', capture)
    start_with_role()
    assert names and names[0].startswith(TEST_NAME_PREFIX + 'web-')


def test_failed_start_deletes_the_secret(aws):
    sfn_stub, sm_stub = aws
    sm_stub.add_response('create_secret', {'ARN': TEST_RESOURCE_ARN, 'Name': 'x'})
    sfn_stub.add_client_error('start_execution', service_error_code='ExecutionLimitExceeded', http_status_code=400)
    sm_stub.add_response('delete_secret', {'ARN': TEST_RESOURCE_ARN},
                         {'SecretId': TEST_RESOURCE_ARN, 'ForceDeleteWithoutRecovery': True})
    with pytest.raises(Exception):
        start_with_role()


def test_status_redacts_external_id_and_collects_follow_up(sfn):
    arn = EXECUTION_PREFIX + 'run-1'
    queues = {'Payload': {'externalId': EXTERNAL_ID, 'queueMapping': {'q-1': 'tq-1'},
                          'manualFollowUp': [{'type': 'queueOutboundCallerIdNumber', 'queueId': 'q-1'}]}}
    sfn.add_response('describe_execution', {
        'executionArn': arn, 'stateMachineArn': STATE_MACHINE_ARN, 'status': 'FAILED',
        'startDate': '2026-01-01T00:00:00Z', 'input': '{}'}, {'executionArn': arn})
    sfn.add_response('get_execution_history', {'events': [
        {'id': 1, 'timestamp': '2026-01-01T00:00:00Z', 'type': 'TaskStateEntered',
         'stateEnteredEventDetails': {'name': 'CreateQueues'}},
        {'id': 2, 'previousEventId': 1, 'timestamp': '2026-01-01T00:00:00Z', 'type': 'TaskSucceeded',
         'taskSucceededEventDetails': {'resourceType': 'lambda', 'resource': 'invoke',
                                       'output': json.dumps(queues)}},
        {'id': 3, 'previousEventId': 2, 'timestamp': '2026-01-01T00:00:00Z', 'type': 'TaskStateEntered',
         'stateEnteredEventDetails': {'name': 'CreateUsers'}},
        {'id': 4, 'previousEventId': 3, 'timestamp': '2026-01-01T00:00:00Z', 'type': 'TaskFailed',
         'taskFailedEventDetails': {'resourceType': 'lambda', 'resource': 'invoke',
                                    'error': 'MigrationStepError', 'cause': 'AccessDenied calling CreateUser'}},
    ]}, {'executionArn': arn, 'maxResults': 1000})

    result = api_migration_status.lambda_handler({'httpMethod': 'POST', 'body': json.dumps({'executionArn': arn})}, None)
    body = json.loads(result['body'])
    assert EXTERNAL_ID not in result['body']
    assert 'output' not in body
    assert body['status'] == 'FAILED' and body['migrationStatus'] == 'FAILED'
    assert body['manualFollowUp'] == [{'type': 'queueOutboundCallerIdNumber', 'queueId': 'q-1'}]
    assert body['failedSteps'] == [{'state': 'CreateUsers', 'error': 'MigrationStepError'}]
    assert {s['state']: s['status'] for s in body['steps']} == {'CreateQueues': 'SUCCEEDED', 'CreateUsers': 'FAILED'}


def test_status_rejects_foreign_execution_arn(sfn):
    result = api_migration_status.lambda_handler(
        {'httpMethod': 'POST', 'body': json.dumps({'executionArn': 'arn:aws:states:eu-west-1:999999999999:execution:x:y'})},
        None)
    assert result['statusCode'] == 400


def clean_history():
    """A run whose steps and secret cleanup all succeeded (the report alone would say SUCCEEDED)."""
    return [
        {'id': 1, 'timestamp': '2026-01-01T00:00:00Z', 'type': 'TaskStateEntered',
         'stateEnteredEventDetails': {'name': 'CreateQueues'}},
        {'id': 2, 'previousEventId': 1, 'timestamp': '2026-01-01T00:00:00Z', 'type': 'TaskSucceeded',
         'taskSucceededEventDetails': {'resourceType': 'lambda', 'resource': 'invoke',
                                       'output': json.dumps({'Payload': {'queueMapping': {}}})}},
        {'id': 3, 'previousEventId': 2, 'timestamp': '2026-01-01T00:00:00Z', 'type': 'TaskStateEntered',
         'stateEnteredEventDetails': {'name': 'CleanupExternalIdSecret'}},
        {'id': 4, 'previousEventId': 3, 'timestamp': '2026-01-01T00:00:00Z', 'type': 'TaskSucceeded',
         'taskSucceededEventDetails': {'resourceType': 'lambda', 'resource': 'invoke',
                                       'output': json.dumps({'Payload': {'status': 'DELETED'}})}},
    ]


def status_of(sfn, execution_status, events):
    arn = EXECUTION_PREFIX + 'run-2'
    sfn.add_response('describe_execution', {
        'executionArn': arn, 'stateMachineArn': STATE_MACHINE_ARN, 'status': execution_status,
        'startDate': '2026-01-01T00:00:00Z', 'input': '{}'}, {'executionArn': arn})
    sfn.add_response('get_execution_history', {'events': events}, {'executionArn': arn, 'maxResults': 1000})
    result = api_migration_status.lambda_handler({'httpMethod': 'POST', 'body': json.dumps({'executionArn': arn})},
                                                 None)
    return json.loads(result['body'])


def test_status_returns_the_one_time_cutover_scope(sfn):
    import migration_report
    assert status_of(sfn, 'SUCCEEDED', clean_history())['scope'] == migration_report.SCOPE


@pytest.mark.parametrize('execution_status', ['ABORTED', 'TIMED_OUT', 'FAILED'])
def test_status_is_never_succeeded_when_the_execution_did_not_succeed(sfn, execution_status):
    body = status_of(sfn, execution_status, clean_history())
    assert body['reportStatus'] == 'SUCCEEDED'  # the history alone looks clean
    assert body['status'] == execution_status and body['migrationStatus'] == 'FAILED'


@pytest.mark.parametrize('execution_status', ['ABORTED', 'TIMED_OUT'])
def test_status_says_the_secret_may_remain_when_cleanup_never_ran(sfn, execution_status):
    events = clean_history()[:2]
    body = status_of(sfn, execution_status, events)
    assert body['migrationStatus'] == 'FAILED'
    assert body['externalIdSecret']['mayRemain'] is True and 'may be left behind' in body['externalIdSecret']['message']


def test_status_is_failed_when_the_report_step_failed(sfn):
    events = clean_history() + [
        {'id': 5, 'previousEventId': 4, 'timestamp': '2026-01-01T00:00:00Z', 'type': 'TaskStateEntered',
         'stateEnteredEventDetails': {'name': 'BuildMigrationReport'}},
        {'id': 6, 'previousEventId': 5, 'timestamp': '2026-01-01T00:00:00Z', 'type': 'TaskFailed',
         'taskFailedEventDetails': {'resourceType': 'lambda', 'resource': 'invoke', 'error': 'Lambda.Unknown'}}]
    # Even if the execution status somehow said SUCCEEDED, a failed report is never a success.
    body = status_of(sfn, 'SUCCEEDED', events)
    assert body['reportStepFailed'] is True and body['migrationStatus'] == 'FAILED'


def test_status_passes_the_fatal_flag_from_the_history(sfn):
    events = clean_history() + [
        {'id': 5, 'previousEventId': 4, 'timestamp': '2026-01-01T00:00:00Z', 'type': 'PassStateEntered',
         'stateEnteredEventDetails': {'name': 'HandleError'}}]
    body = status_of(sfn, 'SUCCEEDED', events)
    assert body['fatalError'] is True and body['reportStatus'] == 'FAILED' and body['migrationStatus'] == 'FAILED'


def test_status_succeeds_only_when_execution_and_report_succeed_and_is_running_while_running(sfn):
    assert status_of(sfn, 'SUCCEEDED', clean_history())['migrationStatus'] == 'SUCCEEDED'
    assert status_of(sfn, 'RUNNING', clean_history()[:2])['migrationStatus'] == 'RUNNING'
    assert status_of(sfn, 'PENDING_REDRIVE', clean_history())['migrationStatus'] == 'FAILED'


def test_make_client_helper_is_offline():
    # Sanity: building a client never sends a request.
    assert make_client('sts', 'eu-west-1').meta.region_name == 'eu-west-1'
