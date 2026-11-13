"""Assume role with ExternalId (the single target side AssumeRole path)."""
import datetime

import pytest

import region_utils
from conftest import (EXTERNAL_ID, SOURCE_ACCOUNT, SOURCE_INSTANCE_ARN, TARGET_INSTANCE_ARN, TARGET_REGION,
                      TARGET_ROLE_ARN)
from region_utils import MigrationStepError

CREDS = {
    # AWS documented example key ID, returned by a stubbed STS call only.
    'AccessKeyId': 'AKIAIOSFODNN7EXAMPLE',  # nosec B105 nosemgrep: AWS documentation example, not a credential
    'SecretAccessKey': 'secret',  # nosec B105 nosemgrep: dummy value returned by a stubbed STS call
    'SessionToken': 'token',
    'Expiration': datetime.datetime(2030, 1, 1),
}


@pytest.fixture
def sts(stubbed, monkeypatch):
    client, stub = stubbed('sts', 'us-east-1')
    monkeypatch.setattr(region_utils, '_sts_client', lambda: client)
    return stub


def test_arn_parsing():
    assert region_utils.extract_region_from_arn(TARGET_INSTANCE_ARN) == TARGET_REGION
    assert region_utils.extract_account_id_from_arn(TARGET_INSTANCE_ARN) == '222222222222'
    assert region_utils.extract_instance_id_from_arn(TARGET_INSTANCE_ARN) == '22222222-2222-4222-8222-222222222222'
    with pytest.raises(ValueError):
        region_utils.extract_region_from_arn('arn:aws:lambda:us-east-1:111111111111:function:x')


def test_assume_role_sends_external_id_and_never_logs_it(sts, capsys):
    sts.add_response('assume_role', {'Credentials': CREDS}, {
        'RoleArn': TARGET_ROLE_ARN,
        'RoleSessionName': 'ConnectMigration-222222222222',
        'ExternalId': EXTERNAL_ID,
        'DurationSeconds': 3600,
    })
    creds = region_utils.assume_cross_account_role(TARGET_INSTANCE_ARN, TARGET_ROLE_ARN, EXTERNAL_ID)
    assert creds['AccessKeyId'] == CREDS['AccessKeyId']
    assert EXTERNAL_ID not in capsys.readouterr().out


@pytest.mark.parametrize('external_id', [None, ''])
def test_missing_external_id_fails_before_calling_sts(sts, external_id):
    with pytest.raises(MigrationStepError) as info:
        region_utils.assume_cross_account_role(TARGET_INSTANCE_ARN, TARGET_ROLE_ARN, external_id)
    assert info.value.code == 'MissingExternalId'


def test_invalid_external_id_is_not_echoed(sts):
    bad = 'contains spaces'
    with pytest.raises(MigrationStepError) as info:
        region_utils.assume_cross_account_role(TARGET_INSTANCE_ARN, TARGET_ROLE_ARN, bad)
    assert bad not in str(info.value)


def test_access_denied_on_assume_role_names_action_and_role(sts):
    sts.add_client_error('assume_role', service_error_code='AccessDenied',
                         service_message='not authorized to perform: sts:AssumeRole', http_status_code=403)
    with pytest.raises(MigrationStepError) as info:
        region_utils.assume_cross_account_role(TARGET_INSTANCE_ARN, TARGET_ROLE_ARN, EXTERNAL_ID)
    assert info.value.action == 'AssumeRole'
    assert info.value.resource == TARGET_ROLE_ARN
    assert 'AccessDenied' in str(info.value)


def test_destination_session_uses_assumed_credentials_in_target_region(sts):
    sts.add_response('assume_role', {'Credentials': CREDS}, {
        'RoleArn': TARGET_ROLE_ARN, 'RoleSessionName': 'ConnectMigration-222222222222',
        'ExternalId': EXTERNAL_ID, 'DurationSeconds': 3600})
    session = region_utils.create_destination_session(TARGET_INSTANCE_ARN, TARGET_ROLE_ARN, EXTERNAL_ID)
    assert session.region_name == TARGET_REGION
    assert session.get_credentials().access_key == CREDS['AccessKeyId']
    client, instance_id, region = region_utils.create_destination_client(TARGET_INSTANCE_ARN, session=session)
    assert client.meta.region_name == TARGET_REGION and region == TARGET_REGION


def test_cross_account_without_role_fails(sts):
    sts.add_response('get_caller_identity', {'Account': SOURCE_ACCOUNT, 'Arn': 'arn:aws:sts::111111111111:assumed-role/x/y', 'UserId': 'AROAEXAMPLEID'})
    with pytest.raises(MigrationStepError) as info:
        region_utils.create_destination_session(TARGET_INSTANCE_ARN)
    assert info.value.code == 'MissingTargetRole'


def test_cross_account_without_role_masks_both_account_ids_and_names_the_sides(sts):
    sts.add_response('get_caller_identity', {'Account': SOURCE_ACCOUNT, 'Arn': 'arn:aws:sts::111111111111:assumed-role/x/y', 'UserId': 'AROAEXAMPLEID'})
    with pytest.raises(MigrationStepError) as info:
        region_utils.create_destination_session(TARGET_INSTANCE_ARN)
    for text in (str(info.value), region_utils.error_text(info.value)):
        assert SOURCE_ACCOUNT not in text and '222222222222' not in text
        assert 'tool account: <account>' in text and 'destination account: <account>' in text
        assert 'no crossAccountRoleArn provided' in text


def test_flow_check_api_returns_the_masked_cross_account_error(sts, monkeypatch):
    import json

    import check_flow_dependencies
    monkeypatch.setenv('ALLOWED_ORIGIN', 'https://d111111abcdef8.cloudfront.net')
    sts.add_response('get_caller_identity', {'Account': SOURCE_ACCOUNT, 'Arn': 'arn:aws:sts::111111111111:assumed-role/x/y', 'UserId': 'AROAEXAMPLEID'})
    result = check_flow_dependencies.lambda_handler({'httpMethod': 'POST', 'body': json.dumps({
        'sourceInstanceId': SOURCE_INSTANCE_ARN, 'destinationInstanceId': TARGET_INSTANCE_ARN})}, None)
    body = json.loads(result['body'])
    assert result['statusCode'] == 400 and body['code'] == 'MissingTargetRole'
    assert 'tool account: <account>' in body['error'] and 'destination account: <account>' in body['error']
    assert SOURCE_ACCOUNT not in body['error'] and '222222222222' not in body['error']


def test_same_account_without_role_uses_own_credentials(sts):
    sts.add_response('get_caller_identity', {'Account': '222222222222', 'Arn': 'arn:aws:sts::111111111111:assumed-role/x/y', 'UserId': 'AROAEXAMPLEID'})
    session = region_utils.create_destination_session(TARGET_INSTANCE_ARN)
    assert session.region_name == TARGET_REGION
