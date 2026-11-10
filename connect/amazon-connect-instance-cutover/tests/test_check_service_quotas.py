"""Quota check: real Service Quotas codes and the applied/default lookup order (stubbed, no AWS)."""
import json
import re

import pytest
from botocore.exceptions import ClientError

import check_service_quotas
from check_service_quotas import CONNECT_QUOTAS, COUNTERS, FEATURE_TO_QUOTAS, get_quota_value
from conftest import SOURCE_INSTANCE_ARN, TARGET_INSTANCE_ARN
from region_utils import MigrationStepError

# Codes and names from list-aws-default-service-quotas --service-code connect, us-east-1 and
# ap-southeast-2 (identical in both), 2026-11-08. Defaults kept here for documentation only:
# the code never uses them.
REAL = {
    'users': ('L-9A46857E', 'Users per instance', 500),
    'queues': ('L-19A87C94', 'Queues per instance', 100),
    'quickConnects': ('L-68BBE2E8', 'Quick connects per instance', 100),
    'contactFlows': ('L-22922690', 'Contact flows per instance', 100),
    'contactFlowModules': ('L-19755C7E', 'Flow modules per instance', 200),
    'hoursOfOperation': ('L-20CD02F7', 'Hours of operation per instance', 100),
    'routingProfiles': ('L-D3E7BE26', 'Routing profiles per instance', 500),
    'securityProfiles': ('L-F325A715', 'Security profiles per instance', 100),
    'phoneNumbers': ('L-8F812903', 'Phone numbers per instance', 10),
    'prompts': ('L-0865B754', 'Prompts per instance', 500),
    'agentStatus': ('L-D945C9A8', 'Agent status per instance', 50),
    'predefinedAttributes': ('L-3828FBF0', 'Predefined Attributes', 150),
    'concurrentActiveCalls': ('L-12AB7C57', 'Concurrent active calls per instance', 10),
    'lexBots': ('L-B93A6612', 'Amazon Lex bots per instance', 70),
}
INVENTED = {'L-AA1E0A9C', 'L-F5E3E6D4', 'L-B5C7D8E9', 'L-6B4D5E6F', 'L-C7D8E9F0', 'L-D8E9F0A1',
            'L-E9F0A1B2', 'L-F0A1B2C3', 'L-A1B2C3D4', 'L-B2C3D4E5', 'L-C3D4E5F6', 'L-D4E5F6G7'}
CODE = 'L-19A87C94'


def test_codes_and_names_match_service_quotas():
    assert {k: (v['quota_code'], v['name']) for k, v in CONNECT_QUOTAS.items()} == \
        {k: (code, name) for k, (code, name, _) in REAL.items()}


def test_no_invented_code_or_built_in_default_remains():
    for info in CONNECT_QUOTAS.values():
        assert re.fullmatch(r'L-[0-9A-F]{8}', info['quota_code'])
        assert info['quota_code'] not in INVENTED
        assert 'default' not in info
    assert len({i['quota_code'] for i in CONNECT_QUOTAS.values()}) == len(CONNECT_QUOTAS)


def test_only_agent_status_is_not_adjustable():
    assert [k for k, v in CONNECT_QUOTAS.items() if not v['adjustable']] == ['agentStatus']


def test_every_quota_has_a_counter_and_every_feature_maps_to_known_quotas():
    assert set(COUNTERS) == set(CONNECT_QUOTAS)
    assert set().union(*FEATURE_TO_QUOTAS.values()) <= set(CONNECT_QUOTAS)


def quota_response(value):
    return {'Quota': {'ServiceCode': 'connect', 'QuotaCode': CODE, 'Value': value}}


def no_such(stub, operation, **params):
    stub.add_client_error(operation, service_error_code='NoSuchResourceException', http_status_code=400,
                          expected_params={'ServiceCode': 'connect', 'QuotaCode': CODE, **params})


@pytest.fixture
def sq(stubbed):
    return stubbed('service-quotas', 'us-east-1')


def test_instance_applied_value_is_used_first(sq):
    client, stub = sq
    stub.add_response('get_service_quota', quota_response(250.0),
                      {'ServiceCode': 'connect', 'QuotaCode': CODE, 'ContextId': SOURCE_INSTANCE_ARN})
    assert get_quota_value(client, CODE, SOURCE_INSTANCE_ARN) == (250.0, 'applied-instance')


def test_account_applied_value_when_no_instance_value(sq):
    client, stub = sq
    no_such(stub, 'get_service_quota', ContextId=SOURCE_INSTANCE_ARN)
    stub.add_response('get_service_quota', quota_response(200.0), {'ServiceCode': 'connect', 'QuotaCode': CODE})
    assert get_quota_value(client, CODE, SOURCE_INSTANCE_ARN) == (200.0, 'applied-account')


def test_default_only_after_no_such_resource_with_the_same_code(sq):
    client, stub = sq
    no_such(stub, 'get_service_quota', ContextId=SOURCE_INSTANCE_ARN)
    no_such(stub, 'get_service_quota')
    stub.add_response('get_aws_default_service_quota', quota_response(100.0),
                      {'ServiceCode': 'connect', 'QuotaCode': CODE})
    assert get_quota_value(client, CODE, SOURCE_INSTANCE_ARN) == (100.0, 'aws-default')


def test_unknown_instead_of_a_guess(sq):
    client, stub = sq
    no_such(stub, 'get_service_quota', ContextId=SOURCE_INSTANCE_ARN)
    no_such(stub, 'get_service_quota')
    no_such(stub, 'get_aws_default_service_quota')
    assert get_quota_value(client, CODE, SOURCE_INSTANCE_ARN) == (None, 'unknown')


def test_other_errors_do_not_fall_back(sq):
    client, stub = sq
    stub.add_client_error('get_service_quota', service_error_code='IllegalArgumentException', http_status_code=400)
    with pytest.raises(ClientError):
        get_quota_value(client, CODE, SOURCE_INSTANCE_ARN)


def test_access_denied_fails(sq):
    client, stub = sq
    stub.add_client_error('get_service_quota', service_error_code='AccessDeniedException', http_status_code=403)
    with pytest.raises(MigrationStepError) as info:
        get_quota_value(client, CODE, SOURCE_INSTANCE_ARN)
    assert info.value.action == 'GetServiceQuota'


def run_handler(monkeypatch, quota_key, dest_values, source_count, dest_count, source_values=(500.0, 'applied-account')):
    """Run the handler for one selected quota with patched lookups and counts; return the body."""
    monkeypatch.setenv('ALLOWED_ORIGIN', 'https://d111111abcdef8.cloudfront.net')
    monkeypatch.setitem(FEATURE_TO_QUOTAS, 'only', [quota_key])

    class Session:
        def client(self, *args, **kwargs):
            return object()

    monkeypatch.setattr(check_service_quotas, 'create_destination_session', lambda *a: Session())
    monkeypatch.setattr(check_service_quotas.boto3, 'client', lambda *a, **k: object())
    monkeypatch.setattr(check_service_quotas, 'create_connect_client_from_arn',
                        lambda arn: (object(), 'src-id', 'us-east-1'))
    monkeypatch.setattr(check_service_quotas, 'get_quota_value',
                        lambda client, code, arn: source_values if arn == SOURCE_INSTANCE_ARN else dest_values)

    def count(client, instance_id, key):
        if key in check_service_quotas.CAPACITY_QUOTAS:
            return None
        return source_count if instance_id == 'src-id' else dest_count

    monkeypatch.setattr(check_service_quotas, 'get_resource_count', count)
    result = check_service_quotas.lambda_handler({'httpMethod': 'POST', 'body': json.dumps({
        'sourceInstanceId': SOURCE_INSTANCE_ARN, 'destinationInstanceId': TARGET_INSTANCE_ARN,
        'features': {'only': True}})}, None)
    return json.loads(result['body'])


def row_for(body, quota_key):
    return next(q for q in body['quotas'] if q['quotaCode'] == CONNECT_QUOTAS[quota_key]['quota_code'])


def warning_for(body, quota_key):
    return [w for w in body['warnings'] if w['resource'] == CONNECT_QUOTAS[quota_key]['name']]


def test_not_adjustable_quota_warning_does_not_suggest_an_increase(monkeypatch):
    body = run_handler(monkeypatch, 'agentStatus', (50.0, 'aws-default'), 30, 30)
    [warning] = warning_for(body, 'agentStatus')
    assert 'cannot be increased' in warning['message'] and 'aws-default' in warning['message']
    assert warning['recommendedQuota'] is None
    assert row_for(body, 'agentStatus')['adjustable'] is False


def test_unknown_destination_quota_is_reported_not_guessed(monkeypatch):
    body = run_handler(monkeypatch, 'users', (None, 'unknown'), 10, 0)
    row = row_for(body, 'users')
    assert row['status'] == 'unknown' and row['destinationQuota'] is None and not row['wouldExceed']
    assert body['warnings'] == []


def test_new_quotas_are_adjustable_and_only_agent_status_is_not():
    assert CONNECT_QUOTAS['concurrentActiveCalls'] == {
        'quota_code': 'L-12AB7C57', 'name': 'Concurrent active calls per instance', 'adjustable': True}
    assert CONNECT_QUOTAS['lexBots'] == {
        'quota_code': 'L-B93A6612', 'name': 'Amazon Lex bots per instance', 'adjustable': True}
    assert len(CONNECT_QUOTAS) == 14


def test_concurrent_calls_are_checked_on_every_run(monkeypatch):
    body = run_handler(monkeypatch, 'users', (500.0, 'applied-account'), 1, 1)
    row = row_for(body, 'concurrentActiveCalls')
    assert row['kind'] == 'capacity' and row['sourceCount'] is None and row['projectedCount'] is None


def test_concurrent_calls_warn_when_the_target_quota_is_lower_than_the_source(monkeypatch):
    body = run_handler(monkeypatch, 'concurrentActiveCalls', (10.0, 'aws-default'), None, None,
                       source_values=(40.0, 'applied-instance'))
    row = row_for(body, 'concurrentActiveCalls')
    assert row['wouldExceed'] and row['status'] == 'warning'
    assert row['sourceQuota'] == 40 and row['destinationQuota'] == 10
    [warning] = warning_for(body, 'concurrentActiveCalls')
    assert 'lower than the source quota 40' in warning['message']
    assert 'Request a quota increase' in warning['message'] and 'cannot be increased' not in warning['message']
    assert warning['recommendedQuota'] == 40 and warning['requiredIncrease'] == 30


def test_concurrent_calls_ok_when_the_target_quota_matches(monkeypatch):
    body = run_handler(monkeypatch, 'concurrentActiveCalls', (40.0, 'applied-instance'), None, None,
                       source_values=(40.0, 'applied-instance'))
    assert row_for(body, 'concurrentActiveCalls')['status'] == 'ok' and body['warnings'] == []


def test_lex_bots_warn_as_adjustable_when_over_the_target_quota(monkeypatch):
    body = run_handler(monkeypatch, 'lexBots', (70.0, 'aws-default'), 50, 30)
    row = row_for(body, 'lexBots')
    assert row['kind'] == 'count' and row['projectedCount'] == 80 and row['wouldExceed']
    [warning] = warning_for(body, 'lexBots')
    assert 'Request a quota increase' in warning['message'] and warning['recommendedQuota'] == 90


def test_lex_bots_feature_selects_the_lex_quota():
    assert FEATURE_TO_QUOTAS['lexBots'] == ['lexBots']


def test_lex_bot_counter_lists_v2_associations_with_a_paginator(stubbed):
    client, stub = stubbed('connect', 'us-east-1')
    stub.add_response('list_bots', {'LexBots': [{'LexV2Bot': {'AliasArn': 'arn:a'}}], 'NextToken': 't'},
                      {'InstanceId': 'src-id', 'LexVersion': 'V2'})
    stub.add_response('list_bots', {'LexBots': [{'LexV2Bot': {'AliasArn': 'arn:b'}}]},
                      {'InstanceId': 'src-id', 'LexVersion': 'V2', 'NextToken': 't'})
    assert check_service_quotas.get_resource_count(client, 'src-id', 'lexBots') == 2
    assert check_service_quotas.get_resource_count(client, 'src-id', 'concurrentActiveCalls') is None


def test_values_are_read_live_for_the_new_codes(sq):
    client, stub = sq
    for code, value in (('L-12AB7C57', 25.0), ('L-B93A6612', 90.0)):
        stub.add_response('get_service_quota', {'Quota': {'ServiceCode': 'connect', 'QuotaCode': code,
                                                          'Value': value}},
                          {'ServiceCode': 'connect', 'QuotaCode': code, 'ContextId': SOURCE_INSTANCE_ARN})
        assert get_quota_value(client, code, SOURCE_INSTANCE_ARN) == (value, 'applied-instance')


def test_readme_quota_table_matches_the_code_and_service_quotas():
    import pathlib
    readme = (pathlib.Path(__file__).resolve().parent.parent / 'README.md').read_text()
    section = readme.split('### Quota preflight', 1)[1].split('\n### ', 1)[0]
    rows = {}
    for line in section.split('\n'):
        cells = [c.strip() for c in line.strip('|').split('|')]
        if len(cells) == 4 and cells[1].startswith('`L-'):
            rows[cells[1].strip('`')] = (cells[0], int(cells[2]), cells[3].startswith('Yes'))
    expected = {code: (name, default, CONNECT_QUOTAS[key]['adjustable']) for key, (code, name, default) in REAL.items()}
    assert rows == expected and len(rows) == 14
    assert [code for code, (_, _, adjustable) in rows.items() if not adjustable] == ['L-D945C9A8']
    assert 'read at run time from Service Quotas' in section and 'do not go stale' in section
