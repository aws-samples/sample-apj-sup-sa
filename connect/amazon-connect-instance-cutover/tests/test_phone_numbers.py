"""Phone number step: the move path from account and Region, opt in self move, IDs only (no AWS)."""
import json

import pytest

import api_start_migration
import create_phone_numbers
import list_phone_numbers
import migration_report
from conftest import (EXTERNAL_ID, EXTERNAL_ID_SECRET_ARN, SOURCE_ACCOUNT, SOURCE_INSTANCE_ARN, SOURCE_REGION,
                      TARGET_ACCOUNT, TARGET_INSTANCE_ARN, TARGET_INSTANCE_ID, TARGET_REGION, TARGET_ROLE_ARN)
from create_phone_numbers import PORTING, SELF_MOVE, SUPPORT_MOVE, phone_number_path

PHONE = '+15555550100'
NUMBERS = [{'PhoneNumberId': 'pn-1', 'PhoneNumber': PHONE, 'PhoneNumberArn': 'arn:pn-1'},
           {'PhoneNumberId': 'pn-2', 'PhoneNumber': '+15555550101', 'PhoneNumberArn': 'arn:pn-2'}]
SAME_ACCOUNT_SAME_REGION = f'arn:aws:connect:{SOURCE_REGION}:{SOURCE_ACCOUNT}:instance/{TARGET_INSTANCE_ID}'
OTHER_ACCOUNT_SAME_REGION = f'arn:aws:connect:{SOURCE_REGION}:{TARGET_ACCOUNT}:instance/{TARGET_INSTANCE_ID}'
SAME_ACCOUNT_OTHER_REGION = f'arn:aws:connect:{TARGET_REGION}:{SOURCE_ACCOUNT}:instance/{TARGET_INSTANCE_ID}'
CASES = [(SAME_ACCOUNT_SAME_REGION, SELF_MOVE), (OTHER_ACCOUNT_SAME_REGION, SUPPORT_MOVE),
         (SAME_ACCOUNT_OTHER_REGION, SUPPORT_MOVE), (TARGET_INSTANCE_ARN, PORTING)]


@pytest.fixture(autouse=True)
def _secret(external_id_secret):
    return external_id_secret


def event(dest, numbers=NUMBERS):
    return {'sourceInstanceId': SOURCE_INSTANCE_ARN, 'destinationInstanceId': dest,
            'crossAccountRoleArn': TARGET_ROLE_ARN, 'externalIdSecretArn': EXTERNAL_ID_SECRET_ARN,
            'selectedPhoneNumbers': numbers}


def no_client(*a, **k):
    raise AssertionError('must not create a target client')


@pytest.mark.parametrize('dest, path', CASES)
def test_path_is_computed_from_account_and_region(dest, path):
    assert phone_number_path(SOURCE_INSTANCE_ARN, dest) == path


@pytest.mark.parametrize('enabled', ['true', 'false'])
@pytest.mark.parametrize('dest, path', CASES)
def test_report_follow_up_names_the_path_with_ids_and_count_only(monkeypatch, enabled, dest, path):
    if path == SELF_MOVE and enabled == 'true':
        pytest.skip('the enabled self move is covered below')
    monkeypatch.setenv('ENABLE_PHONE_NUMBER_MOVE', enabled)
    monkeypatch.setattr(create_phone_numbers, 'create_destination_client', no_client)
    result = create_phone_numbers.lambda_handler(event(dest), None)
    assert result['status'] == ('DISABLED' if path == SELF_MOVE else 'NOT_SUPPORTED')
    assert result['migrated'] == [] and result['path'] == path
    [entry] = result['manualFollowUp']
    assert entry['type'] == 'phoneNumber' and entry['reasonCode'] == path
    assert entry['count'] == 2 and entry['phoneNumberIds'] == ['pn-1', 'pn-2']
    assert set(entry) == {'type', 'reasonCode', 'count', 'phoneNumberIds', 'action'}
    assert '+1555' not in json.dumps(result)


def test_action_texts_describe_each_path():
    actions = create_phone_numbers.PATH_ACTIONS
    assert 'ListPhoneNumbersV2 and UpdatePhoneNumber' in actions[SELF_MOVE]
    support = actions[SUPPORT_MOVE]
    for phrase in ('AWS Support moves', 'one phone number migration case from each account', 'Monday to Friday',
                   '20 to 30 minutes per number', 'can fail while it moves', 'must not be the outbound caller ID'):
        assert phrase in support
    assert 'port them' in actions[PORTING]
    assert all('outbound caller ID' in text and 'set again' in text for text in actions.values())


def test_enabled_same_account_and_region_moves_with_the_target_role(monkeypatch, stubbed):
    monkeypatch.setenv('ENABLE_PHONE_NUMBER_MOVE', 'true')
    client, stub = stubbed('connect', SOURCE_REGION)
    calls = []

    def fake_destination_client(dest_arn, role_arn, external_id):
        calls.append((dest_arn, role_arn, external_id))
        return client, 'id', SOURCE_REGION

    monkeypatch.setattr(create_phone_numbers, 'create_destination_client', fake_destination_client)
    stub.add_response('update_phone_number', {'PhoneNumberId': 'pn-1', 'PhoneNumberArn': 'arn:new'},
                      {'PhoneNumberId': 'pn-1', 'TargetArn': SAME_ACCOUNT_SAME_REGION})
    stub.add_client_error('update_phone_number', service_error_code='InvalidParameterException',
                          http_status_code=400,
                          expected_params={'PhoneNumberId': 'pn-2', 'TargetArn': SAME_ACCOUNT_SAME_REGION})
    result = create_phone_numbers.lambda_handler(event(SAME_ACCOUNT_SAME_REGION), None)
    assert calls == [(SAME_ACCOUNT_SAME_REGION, TARGET_ROLE_ARN, EXTERNAL_ID)]
    assert result['status'] == 'COMPLETED' and result['migrated'][0]['newArn'] == 'arn:new'
    [entry] = result['manualFollowUp']
    assert entry['reasonCode'] == SELF_MOVE and entry['phoneNumberIds'] == ['pn-2'] and entry['count'] == 1


def test_numbers_listed_by_the_step_hold_no_number(monkeypatch, stubbed):
    client, stub = stubbed('connect', SOURCE_REGION)
    summary = {'PhoneNumberId': 'pn-1', 'PhoneNumberArn': 'arn:pn-1', 'PhoneNumber': PHONE,
               'PhoneNumberCountryCode': 'US', 'PhoneNumberType': 'DID', 'PhoneNumberDescription': 'Main line',
               'TargetArn': SOURCE_INSTANCE_ARN, 'InstanceId': 'src'}
    stub.add_response('list_phone_numbers_v2', {'ListPhoneNumbersSummaryList': [summary]})
    monkeypatch.setattr(list_phone_numbers, 'create_connect_client_from_arn',
                        lambda arn: (client, 'src', SOURCE_REGION))
    body = list_phone_numbers.lambda_handler({'sourceInstanceId': SOURCE_INSTANCE_ARN}, None)['body']
    assert PHONE not in body and 'Main line' not in body and json.loads(body)['count'] == 1


def test_start_api_keeps_only_ids_and_arns_of_selected_numbers():
    execution_input, _, error = api_start_migration.build_execution_input({
        'sourceInstanceId': SOURCE_INSTANCE_ARN, 'destinationInstanceId': TARGET_INSTANCE_ARN,
        'crossAccountRoleArn': TARGET_ROLE_ARN, 'externalId': EXTERNAL_ID,
        'selectedPhoneNumbers': NUMBERS + ['pn-3', {'PhoneNumber': PHONE}]})
    assert error is None
    assert execution_input['selectedPhoneNumbers'] == [{'PhoneNumberId': 'pn-1', 'PhoneNumberArn': 'arn:pn-1'},
                                                       {'PhoneNumberId': 'pn-2', 'PhoneNumberArn': 'arn:pn-2'}]
    assert PHONE not in json.dumps(execution_input)


def test_migration_report_carries_the_phone_number_follow_up():
    from test_migration_report import EXECUTION_ARN, FakeSfn, history
    payload = {'statusCode': 200, 'status': 'NOT_SUPPORTED', 'migrated': [], 'notMigrated': [],
               'manualFollowUp': create_phone_numbers.follow_up_for(NUMBERS, PORTING)}
    report = migration_report.build_report(FakeSfn(history(('CreatePhoneNumbers', 'ok', payload))), EXECUTION_ARN,
                                           cleanup_status='DELETED')
    assert [f['reasonCode'] for f in report['manualFollowUp']] == [PORTING]
    assert report['status'] == 'SUCCEEDED' and PHONE not in json.dumps(report)


def test_ui_renders_the_phone_follow_up_escaped_and_sends_ids_only():
    import pathlib
    html = (pathlib.Path(__file__).resolve().parent.parent / 'frontend' / 'index.html').read_text()
    assert "if (f.type === 'phoneNumber' && f.reasonCode)" in html
    assert '${escapeHtml(`phoneNumber ${f.reasonCode} (${f.count})`)}' in html
    assert "${escapeHtml((f.phoneNumberIds || []).join(', '))}" in html
    assert 'selected.push({ PhoneNumberId: pn.PhoneNumberId, PhoneNumberArn: pn.PhoneNumberArn });' in html
    assert 'not supported for cross-account' not in html
