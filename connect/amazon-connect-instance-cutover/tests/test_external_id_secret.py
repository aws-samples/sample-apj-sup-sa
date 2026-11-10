"""ExternalId through a per execution secret: never in the input, step outputs or logs (fix 7)."""
import json
import pathlib
import re

import pytest
from botocore.exceptions import ClientError

import api_start_migration
import cleanup_external_id
import create_views
import region_utils
from conftest import (EXTERNAL_ID, SOURCE_INSTANCE_ARN, TARGET_INSTANCE_ARN, TARGET_INSTANCE_ID,
                      TARGET_ROLE_ARN)
from region_utils import MigrationStepError

TEST_RESOURCE_ARN = 'arn:aws:secretsmanager:eu-west-1:111111111111:secret:connect-migration/test/web-run-AbCdEf'


@pytest.fixture
def secrets(stubbed, monkeypatch):
    client, stub = stubbed('secretsmanager', 'eu-west-1')
    monkeypatch.setattr(region_utils, '_secrets_client', lambda region: client)
    return stub


def test_resolver_reads_the_secret_and_prints_nothing(secrets, capsys):
    secrets.add_response('get_secret_value', {'ARN': TEST_RESOURCE_ARN, 'Name': 'x', 'SecretString': EXTERNAL_ID},
                         {'SecretId': TEST_RESOURCE_ARN})
    assert region_utils.resolve_external_id({'externalIdSecretArn': TEST_RESOURCE_ARN}) == EXTERNAL_ID
    assert capsys.readouterr().out == ''


def test_resolver_access_denied_fails_the_step(secrets):
    secrets.add_client_error('get_secret_value', service_error_code='AccessDeniedException', http_status_code=403)
    with pytest.raises(MigrationStepError) as info:
        region_utils.resolve_external_id({'externalIdSecretArn': TEST_RESOURCE_ARN})
    assert info.value.action == 'GetSecretValue'


def test_resolver_rejects_non_secret_arns():
    with pytest.raises(MigrationStepError):
        region_utils.resolve_external_id({'externalIdSecretArn': 'not-an-arn'})


def test_workers_refuse_a_raw_external_id():
    # Fail closed: the key alone is refused, whatever its value, and nothing is echoed.
    for event in ({'externalId': EXTERNAL_ID}, {'externalId': None},
                  {'externalId': EXTERNAL_ID, 'externalIdSecretArn': TEST_RESOURCE_ARN}):
        with pytest.raises(MigrationStepError) as info:
            region_utils.resolve_external_id(event)
        assert info.value.code == 'RawExternalIdRejected'
        assert 'externalIdSecretArn' in str(info.value) and EXTERNAL_ID not in str(info.value)
    assert region_utils.resolve_external_id({}) is None
    assert region_utils.resolve_external_id(None) is None


def test_a_worker_given_a_raw_external_id_fails_before_any_aws_call(monkeypatch):
    monkeypatch.setattr(create_views, 'create_destination_client',
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError('must not build a client')))
    with pytest.raises(MigrationStepError) as info:
        create_views.lambda_handler({
            'destinationInstanceId': TARGET_INSTANCE_ARN, 'crossAccountRoleArn': TARGET_ROLE_ARN,
            'externalId': EXTERNAL_ID, 'views': []}, None)
    assert info.value.code == 'RawExternalIdRejected'


def test_api_helper_reads_only_the_raw_request_value_and_never_a_secret(monkeypatch):
    monkeypatch.setattr(region_utils, '_secrets_client',
                        lambda region: (_ for _ in ()).throw(AssertionError('must not read a secret')))
    assert region_utils.external_id_from_api_request({'externalId': EXTERNAL_ID}) == EXTERNAL_ID
    assert region_utils.external_id_from_api_request({'externalIdSecretArn': TEST_RESOURCE_ARN}) is None
    assert region_utils.external_id_from_api_request({'externalId': ''}) is None
    assert region_utils.external_id_from_api_request(None) is None


LAMBDA_DIR = pathlib.Path(__file__).resolve().parent.parent / 'lambda'
STATE_MACHINE = (LAMBDA_DIR.parent / 'step-functions' / 'migration-state-machine.json').read_text()
# API Gateway handlers the UI calls directly with the ExternalId in the request body.
API_HANDLERS = {'api_start_migration', 'check_service_quotas', 'check_flow_dependencies', 'verify_migration'}


def handler_modules_of_state_machine():
    """Module names of every Lambda the state machine invokes (from the templates)."""
    called = set(re.findall(r'(connect-migration-[a-z0-9-]+)-\$\{Environment\}', STATE_MACHINE))
    modules = set()
    for template in (LAMBDA_DIR.parent / 'cloudformation').glob('*.yaml'):
        text = template.read_text()
        for name, handler in re.findall(
                r"FunctionName: !Sub '(connect-migration-[a-z0-9-]+)-\$\{Environment\}'\s+"
                r"Runtime: [\w.]+\s+Handler: (\w+)\.lambda_handler", text):
            if name in called:
                modules.add(handler)
    assert len(modules) == len(called), sorted(called)
    return modules


def test_only_api_handlers_use_the_raw_helper_and_every_worker_uses_the_resolver():
    workers = handler_modules_of_state_machine()
    assert not workers & API_HANDLERS
    for path in LAMBDA_DIR.glob('*.py'):
        text = path.read_text()
        if path.stem in API_HANDLERS:
            assert 'external_id_from_api_request(' in text and 'resolve_external_id(' not in text, path.name
        elif path.stem != 'region_utils':
            assert 'external_id_from_api_request' not in text, path.name
            assert "get('externalId')" not in text and "['externalId']" not in text, path.name
    for module in workers:
        text = (LAMBDA_DIR / f'{module}.py').read_text()
        if 'create_destination' in text:
            assert 'resolve_external_id(event)' in text, module


@pytest.fixture
def start_aws(stubbed, monkeypatch):
    sfn_client, sfn_stub = stubbed('stepfunctions', 'eu-west-1')
    sm_client, sm_stub = stubbed('secretsmanager', 'eu-west-1')
    clients = {'stepfunctions': sfn_client, 'secretsmanager': sm_client}
    monkeypatch.setattr(api_start_migration.boto3, 'client', lambda service, *a, **k: clients[service])
    monkeypatch.setenv('STATE_MACHINE_ARN', 'arn:aws:states:eu-west-1:111111111111:stateMachine:connect-user-migration-test')
    monkeypatch.setenv('EXTERNAL_ID_SECRET_PREFIX', 'connect-migration/test/')
    monkeypatch.setenv('ALLOWED_ORIGIN', 'https://d111111abcdef8.cloudfront.net')
    return sfn_stub, sm_stub


def start_event():
    return {'httpMethod': 'POST', 'body': json.dumps({
        'sourceInstanceId': SOURCE_INSTANCE_ARN, 'destinationInstanceId': TARGET_INSTANCE_ARN,
        'crossAccountRoleArn': TARGET_ROLE_ARN, 'externalId': EXTERNAL_ID})}


def test_failed_start_with_failed_delete_raises_the_start_error_and_logs_only_the_code(start_aws, capsys):
    sfn_stub, sm_stub = start_aws
    sm_stub.add_response('create_secret', {'ARN': TEST_RESOURCE_ARN, 'Name': 'x'})
    sfn_stub.add_client_error('start_execution', service_error_code='ExecutionLimitExceeded',
                              service_message=f'limit {EXTERNAL_ID}', http_status_code=400)
    sm_stub.add_client_error('delete_secret', service_error_code='InternalServiceError',
                             service_message=f'boom {EXTERNAL_ID}', http_status_code=500)
    with pytest.raises(ClientError) as info:
        api_start_migration.lambda_handler(start_event(), None)
    assert info.value.operation_name == 'StartExecution'
    assert region_utils.error_code(info.value) == 'ExecutionLimitExceeded'
    out = capsys.readouterr().out
    assert 'could not be deleted: InternalServiceError' in out
    assert EXTERNAL_ID not in out and 'boom' not in out


def test_failed_start_with_successful_delete_still_raises_the_start_error(start_aws):
    sfn_stub, sm_stub = start_aws
    sm_stub.add_response('create_secret', {'ARN': TEST_RESOURCE_ARN, 'Name': 'x'})
    sfn_stub.add_client_error('start_execution', service_error_code='ExecutionLimitExceeded', http_status_code=400)
    sm_stub.add_response('delete_secret', {'ARN': TEST_RESOURCE_ARN},
                         {'SecretId': TEST_RESOURCE_ARN, 'ForceDeleteWithoutRecovery': True})
    with pytest.raises(ClientError) as info:
        api_start_migration.lambda_handler(start_event(), None)
    assert info.value.operation_name == 'StartExecution'


def test_worker_gets_the_value_but_never_outputs_or_logs_it(secrets, stubbed, monkeypatch, capsys):
    secrets.add_response('get_secret_value', {'ARN': TEST_RESOURCE_ARN, 'Name': 'x', 'SecretString': EXTERNAL_ID})
    connect, connect_stub = stubbed('connect', 'ap-southeast-2')
    seen = []

    def fake_destination_client(arn, role, external_id):
        seen.append(external_id)
        return connect, TARGET_INSTANCE_ID, 'ap-southeast-2'

    monkeypatch.setattr(create_views, 'create_destination_client', fake_destination_client)
    connect_stub.add_response('list_views', {'ViewsSummaryList': [{'Id': 'dst-v', 'Name': 'Card'}]})
    result = create_views.lambda_handler({
        'destinationInstanceId': TARGET_INSTANCE_ARN, 'crossAccountRoleArn': TARGET_ROLE_ARN,
        'externalIdSecretArn': TEST_RESOURCE_ARN,
        'views': [{'Id': 'src-v', 'Name': 'Card', 'Content': {'Template': '{}'}}]}, None)
    assert seen == [EXTERNAL_ID]
    assert EXTERNAL_ID not in json.dumps(result)
    assert EXTERNAL_ID not in capsys.readouterr().out


@pytest.fixture
def cleanup(stubbed, monkeypatch):
    client, stub = stubbed('secretsmanager', 'eu-west-1')
    monkeypatch.setattr(cleanup_external_id.boto3, 'client', lambda *a, **k: client)
    monkeypatch.setenv('EXTERNAL_ID_SECRET_PREFIX', 'connect-migration/test/')
    return stub


def test_cleanup_force_deletes_the_secret(cleanup):
    cleanup.add_response('delete_secret', {'ARN': TEST_RESOURCE_ARN},
                         {'SecretId': TEST_RESOURCE_ARN, 'ForceDeleteWithoutRecovery': True})
    assert cleanup_external_id.lambda_handler({'externalIdSecretArn': TEST_RESOURCE_ARN}, None) == {'status': 'DELETED'}


def test_cleanup_tolerates_a_missing_secret_and_no_secret(cleanup):
    cleanup.add_client_error('delete_secret', service_error_code='ResourceNotFoundException', http_status_code=400)
    assert cleanup_external_id.lambda_handler({'externalIdSecretArn': TEST_RESOURCE_ARN}, None)['status'] == 'ALREADY_DELETED'
    assert cleanup_external_id.lambda_handler({'externalIdSecretArn': None}, None) == {'status': 'NOT_NEEDED'}


def test_cleanup_refuses_secrets_outside_the_prefix(cleanup):
    other = 'arn:aws:secretsmanager:eu-west-1:111111111111:secret:someone-else/db-AbCdEf'
    assert cleanup_external_id.lambda_handler({'externalIdSecretArn': other}, None)['status'] == 'FAILED'
