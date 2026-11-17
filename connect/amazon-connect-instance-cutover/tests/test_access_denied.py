"""AccessDenied and throttling: logged with action and resource, and the step fails."""
import json

import pytest
from botocore.exceptions import ClientError

import check_flow_dependencies
import check_service_quotas
import region_utils
import verify_migration
from conftest import (
    EXTERNAL_ID,
    SOURCE_INSTANCE_ARN,
    SOURCE_INSTANCE_ID,
    TARGET_INSTANCE_ARN,
    TARGET_ROLE_ARN,
)
from region_utils import MigrationStepError


def client_error(code, operation='ListQueues', message='not authorized'):
    return ClientError({'Error': {'Code': code, 'Message': message}}, operation)


@pytest.mark.parametrize('code', ['AccessDenied', 'AccessDeniedException', 'ThrottlingException',
                                  'TooManyRequestsException'])
def test_reraise_if_fatal_raises_for_access_denied_and_throttling(code, capsys):
    with pytest.raises(MigrationStepError) as info:
        region_utils.reraise_if_fatal(client_error(code), resource='instance x')
    assert info.value.action == 'ListQueues' and info.value.resource == 'instance x'
    out = capsys.readouterr().out
    assert 'ListQueues' in out and 'instance x' in out


@pytest.mark.parametrize('error', [client_error('ResourceNotFoundException'), ValueError('x')])
def test_reraise_if_fatal_ignores_other_errors(error):
    region_utils.reraise_if_fatal(error)


def test_dependency_inventory_fails_on_access_denied(stubbed):
    client, stub = stubbed('connect', 'us-east-1')
    stub.add_client_error('list_queues', service_error_code='AccessDeniedException',
                          service_message='User is not authorized to perform: connect:ListQueues',
                          http_status_code=403)
    with pytest.raises(MigrationStepError) as info:
        check_flow_dependencies.get_instance_resources(client, SOURCE_INSTANCE_ID)
    assert info.value.action == 'ListQueues'


def test_dependency_inventory_lists_only_lex_v2_and_fails_closed_on_its_errors(stubbed):
    client, stub = stubbed('connect', 'us-east-1')
    for operation, key in (('list_queues', 'QueueSummaryList'), ('list_prompts', 'PromptSummaryList'),
                           ('list_hours_of_operations', 'HoursOfOperationSummaryList'),
                           ('list_users', 'UserSummaryList'), ('list_contact_flows', 'ContactFlowSummaryList')):
        stub.add_response(operation, {key: []})
    stub.add_response('list_lambda_functions', {'LambdaFunctions': []})
    stub.add_response('list_bots', {'LexBots': []},
                      {'InstanceId': SOURCE_INSTANCE_ID, 'LexVersion': 'V2', 'MaxResults': 25})
    resources = check_flow_dependencies.get_instance_resources(client, SOURCE_INSTANCE_ID)
    stub.assert_no_pending_responses()  # one ListBots call, for V2 only
    assert resources['warnings'] == [] and 'lex_v1_bots' not in resources

    client, stub = stubbed('connect', 'us-east-1')
    for operation, key in (('list_queues', 'QueueSummaryList'), ('list_prompts', 'PromptSummaryList'),
                           ('list_hours_of_operations', 'HoursOfOperationSummaryList'),
                           ('list_users', 'UserSummaryList'), ('list_contact_flows', 'ContactFlowSummaryList')):
        stub.add_response(operation, {key: []})
    stub.add_response('list_lambda_functions', {'LambdaFunctions': []})
    stub.add_client_error('list_bots', service_error_code='InvalidRequestException', http_status_code=400)
    with pytest.raises(ClientError):
        check_flow_dependencies.get_instance_resources(client, SOURCE_INSTANCE_ID)


def api_event(**body):
    return {'httpMethod': 'POST', 'body': json.dumps(body)}


def test_pre_check_api_returns_403_with_action(monkeypatch):
    monkeypatch.setenv('ALLOWED_ORIGIN', 'https://d111111abcdef8.cloudfront.net')

    def denied(*args, **kwargs):
        raise MigrationStepError('AccessDenied calling AssumeRole', code='AccessDenied', action='AssumeRole')

    monkeypatch.setattr(check_flow_dependencies, 'create_destination_session', denied)
    result = check_flow_dependencies.lambda_handler(api_event(
        sourceInstanceId=SOURCE_INSTANCE_ARN, destinationInstanceId=TARGET_INSTANCE_ARN,
        crossAccountRoleArn=TARGET_ROLE_ARN, externalId=EXTERNAL_ID), None)
    assert result['statusCode'] == 403
    assert json.loads(result['body'])['action'] == 'AssumeRole'


def test_verify_api_returns_429_on_throttling(monkeypatch):
    monkeypatch.setenv('ALLOWED_ORIGIN', 'https://d111111abcdef8.cloudfront.net')

    def throttled(*args, **kwargs):
        raise region_utils.fatal_error(client_error('ThrottlingException', 'ListUsers'), 'instance x')

    monkeypatch.setattr(verify_migration, 'create_destination_session', throttled)
    result = verify_migration.lambda_handler(api_event(
        sourceInstanceId=SOURCE_INSTANCE_ARN, destinationInstanceId=TARGET_INSTANCE_ARN,
        crossAccountRoleArn=TARGET_ROLE_ARN, externalId=EXTERNAL_ID, features={}), None)
    assert result['statusCode'] == 429


class FakeSession:
    def __init__(self, clients):
        self.clients = clients

    def client(self, service, **kwargs):
        return self.clients[service]


def test_quota_check_reads_target_through_target_role(monkeypatch, stubbed):
    monkeypatch.setenv('ALLOWED_ORIGIN', 'https://d111111abcdef8.cloudfront.net')
    src_sq, src_sq_stub = stubbed('service-quotas', 'us-east-1')
    src_connect, src_connect_stub = stubbed('connect', 'us-east-1')
    dst_sq, dst_sq_stub = stubbed('service-quotas', 'ap-southeast-2')
    dst_connect, dst_connect_stub = stubbed('connect', 'ap-southeast-2')
    calls = []

    def fake_session(dest_arn, role, external_id):
        calls.append((dest_arn, role, external_id))
        return FakeSession({'service-quotas': dst_sq, 'connect': dst_connect})

    monkeypatch.setattr(check_service_quotas, 'create_destination_session', fake_session)
    monkeypatch.setitem(check_service_quotas.FEATURE_TO_QUOTAS, 'queues', ['queues'])
    # One quota only here; the always checked capacity quota is covered in test_check_service_quotas.py.
    monkeypatch.setattr(check_service_quotas, 'ALWAYS_CHECKED', frozenset())
    monkeypatch.setattr(check_service_quotas.boto3, 'client', lambda *a, **k: src_sq)
    monkeypatch.setattr(check_service_quotas, 'create_connect_client_from_arn',
                        lambda arn: (src_connect, SOURCE_INSTANCE_ID, 'us-east-1'))

    queue_code = check_service_quotas.CONNECT_QUOTAS['queues']['quota_code']
    quota = {'Quota': {'ServiceCode': 'connect', 'QuotaCode': queue_code, 'Value': 120.0}}
    src_sq_stub.add_response('get_service_quota', quota,
                             {'ServiceCode': 'connect', 'QuotaCode': queue_code, 'ContextId': SOURCE_INSTANCE_ARN})
    dst_sq_stub.add_client_error('get_service_quota', service_error_code='NoSuchResourceException', http_status_code=400,
                                 expected_params={'ServiceCode': 'connect', 'QuotaCode': queue_code,
                                                  'ContextId': TARGET_INSTANCE_ARN})
    dst_sq_stub.add_client_error('get_service_quota', service_error_code='NoSuchResourceException', http_status_code=400,
                                 expected_params={'ServiceCode': 'connect', 'QuotaCode': queue_code})
    dst_sq_stub.add_response('get_aws_default_service_quota',
                             {'Quota': {'ServiceCode': 'connect', 'QuotaCode': queue_code, 'Value': 100.0}},
                             {'ServiceCode': 'connect', 'QuotaCode': queue_code})
    src_connect_stub.add_response('list_queues', {'QueueSummaryList': [{'Id': 'q1'}, {'Id': 'q2'}]})
    dst_connect_stub.add_response('list_queues', {'QueueSummaryList': [{'Id': 'q9'}]})

    result = check_service_quotas.lambda_handler(api_event(
        sourceInstanceId=SOURCE_INSTANCE_ARN, destinationInstanceId=TARGET_INSTANCE_ARN,
        crossAccountRoleArn=TARGET_ROLE_ARN, externalId=EXTERNAL_ID, features={'queues': True}), None)
    body = json.loads(result['body'])
    assert calls == [(TARGET_INSTANCE_ARN, TARGET_ROLE_ARN, EXTERNAL_ID)]
    assert result['statusCode'] == 200, body
    queues = next(q for q in body['quotas'] if q['resource'] == 'Queues per instance')
    assert queues['sourceCount'] == 2 and queues['destinationCount'] == 1
    assert queues['sourceQuota'] == 120 and queues['sourceQuotaSource'] == 'applied-instance'
    assert queues['destinationQuota'] == 100 and queues['destinationQuotaSource'] == 'aws-default'
    assert body['defaultsUsed'] == [{'resource': 'Queues per instance', 'quotaCode': queue_code, 'side': 'destination'}]
    assert body['summary']['appliedValues'] == 1 and body['summary']['defaultValues'] == 1
