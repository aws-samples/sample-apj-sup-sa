"""Handlers that handle users, quick connects and similar data never log personal data."""
import json

from botocore.exceptions import ClientError

import api_start_migration
import create_data_tables
import create_phone_numbers
import create_predefined_attributes
import create_quick_connects
import create_users
from conftest import EXTERNAL_ID, SOURCE_INSTANCE_ARN, TARGET_INSTANCE_ARN, TARGET_ROLE_ARN
from region_utils import MigrationStepError, describe_error

EMAIL = 'jane.doe@example.com'
PHONE = '+15555550123'
USERNAME = 'jane.doe'
SENSITIVE_SAMPLE = 'loyalty-tier-platinum-0042'


class FakeConnect:
    """Minimal stand in for a Connect client; records calls, returns fixed IDs."""

    class exceptions:
        class DuplicateResourceException(Exception):
            pass

    def __init__(self):
        self.calls = []

    def can_paginate(self, operation):
        return True

    def get_paginator(self, operation):
        """Every target list call (the steps' existing index) finds nothing."""
        calls = self.calls

        class Paginator:
            def paginate(self, **kwargs):
                calls.append((operation, kwargs))
                return [{}]
        return Paginator()

    def __getattr__(self, name):
        def call(**kwargs):
            self.calls.append((name, kwargs))
            return {
                'create_user': {'UserId': 'new-user-1', 'UserArn': 'arn'},
                'list_data_tables': {'DataTableSummaryList': []},
                'create_data_table': {'Id': 'new-table-1'},
                'update_phone_number': {'PhoneNumberId': 'pn-1', 'PhoneNumberArn': 'arn:pn-1'},
                'create_quick_connect': {'QuickConnectId': 'new-qc-1', 'QuickConnectARN': 'arn'},
            }.get(name, {})
        return call


def test_describe_error_drops_the_message():
    err = ClientError({'Error': {'Code': 'DuplicateResourceException',
                                 'Message': f'User with username {USERNAME} already exists'}}, 'CreateUser')
    assert describe_error(err) == 'DuplicateResourceException (CreateUser)'
    assert describe_error(MigrationStepError('x', code='AccessDenied', action='CreateUser')) == 'AccessDenied (CreateUser)'
    assert describe_error(ValueError(EMAIL)) == 'ValueError'


def test_260_start_logs_cognito_sub_not_email(monkeypatch, capsys):
    monkeypatch.setenv('ALLOWED_ORIGIN', 'https://d111111abcdef8.cloudfront.net')
    monkeypatch.setenv('STATE_MACHINE_ARN', 'arn:aws:states:eu-west-1:111111111111:stateMachine:connect-user-migration-test')

    class FakeSfn:
        def start_execution(self, **kwargs):
            return {'executionArn': 'arn:aws:states:eu-west-1:111111111111:execution:x:y'}

        def create_secret(self, **kwargs):
            return {'ARN': 'arn:aws:secretsmanager:eu-west-1:111111111111:secret:connect-migration/test/x-AbCdEf'}

    monkeypatch.setenv('EXTERNAL_ID_SECRET_PREFIX', 'connect-migration/test/')

    monkeypatch.setattr(api_start_migration.boto3, 'client', lambda *a, **k: FakeSfn())
    event = {'httpMethod': 'POST',
             'requestContext': {'authorizer': {'claims': {'email': EMAIL, 'sub': 'sub-1234'}}},
             'body': json.dumps({'sourceInstanceId': SOURCE_INSTANCE_ARN, 'destinationInstanceId': TARGET_INSTANCE_ARN,
                                 'crossAccountRoleArn': TARGET_ROLE_ARN, 'externalId': EXTERNAL_ID})}
    assert api_start_migration.lambda_handler(event, None)['statusCode'] == 200
    out = capsys.readouterr().out
    assert EMAIL not in out and 'sub-1234' in out


def test_261_phone_move_logs_id_not_number(monkeypatch, capsys, external_id_secret):
    monkeypatch.setenv('ENABLE_PHONE_NUMBER_MOVE', 'true')
    fake = FakeConnect()
    monkeypatch.setattr(create_phone_numbers, 'create_destination_client', lambda *a: (fake, 'id', 'us-east-1'))
    same_region_target = SOURCE_INSTANCE_ARN.replace('11111111-1111-4111-8111-111111111111',
                                                     '33333333-3333-4333-8333-333333333333')
    create_phone_numbers.lambda_handler({
        'sourceInstanceId': SOURCE_INSTANCE_ARN, 'destinationInstanceId': same_region_target,
        'crossAccountRoleArn': TARGET_ROLE_ARN, 'externalIdSecretArn': external_id_secret,
        'selectedPhoneNumbers': [{'PhoneNumberId': 'pn-1', 'PhoneNumber': PHONE}]}, None)
    out = capsys.readouterr().out
    assert PHONE not in out and 'pn-1' in out


def test_262_users_log_ids_not_usernames_or_identity(monkeypatch, capsys):
    fake = FakeConnect()
    monkeypatch.setattr(create_users, 'create_destination_client', lambda *a: (fake, 'dst', 'us-east-1'))
    result = create_users.lambda_handler({
        'destinationInstanceId': TARGET_INSTANCE_ARN,
        'routingProfileMapping': {'rp-src': 'rp-dst'}, 'securityProfileMapping': {'sp-src': 'sp-dst'},
        'users': [{'Id': 'src-user-1', 'Username': USERNAME, 'RoutingProfileId': 'rp-src',
                   'SecurityProfileIds': ['sp-src'], 'PhoneConfig': {'PhoneType': 'SOFT_PHONE'},
                   'IdentityInfo': {'FirstName': 'Jane', 'LastName': 'Doe', 'Email': EMAIL}}],
    }, None)
    out = capsys.readouterr().out
    assert [name for name, _ in fake.calls[:2]] == ['list_users', 'create_user']
    assert USERNAME not in out and EMAIL not in out and 'Jane' not in out
    assert 'src-user-1' in out and 'new-user-1' in out
    assert result['statusCode'] == 200


def test_264_data_tables_log_counts_not_values(monkeypatch, capsys):
    fake = FakeConnect()
    monkeypatch.setattr(create_data_tables, 'create_destination_client', lambda *a: (fake, 'dst', 'us-east-1'))
    create_data_tables.lambda_handler({
        'destinationInstanceId': TARGET_INSTANCE_ARN,
        'dataTables': [{'Id': 'tbl-1', 'Name': 'CustomerTiers', 'TimeZone': 'UTC', 'ValueLockLevel': 'NONE',
                        'Attributes': [{'Name': 'tier', 'ValueType': 'TEXT', 'Primary': True}],
                        'Values': [{'PrimaryValue': EMAIL, 'AttributeValues': {'tier': SENSITIVE_SAMPLE}}]}],
    }, None)
    out = capsys.readouterr().out
    assert EMAIL not in out and SENSITIVE_SAMPLE not in out and 'CustomerTiers' not in out
    assert 'tbl-1' in out and '1 values' in out


def test_265_predefined_attributes_do_not_log_values(monkeypatch, capsys):
    fake = FakeConnect()
    monkeypatch.setattr(create_predefined_attributes, 'create_destination_client', lambda *a: (fake, 'dst', 'us-east-1'))
    create_predefined_attributes.lambda_handler({
        'targetInstanceId': TARGET_INSTANCE_ARN,
        'predefinedAttributes': [{'Name': 'VipCustomer', 'Values': {'StringList': [SENSITIVE_SAMPLE, EMAIL]}}],
    }, None)
    out = capsys.readouterr().out
    assert SENSITIVE_SAMPLE not in out and EMAIL not in out and 'VipCustomer' not in out


def test_267_quick_connects_do_not_log_payloads(monkeypatch, capsys):
    fake = FakeConnect()
    monkeypatch.setattr(create_quick_connects, 'create_destination_client', lambda *a: (fake, 'dst', 'us-east-1'))
    monkeypatch.setattr(create_quick_connects, 'create_connect_client_from_arn', lambda arn: (FakeConnect(), 'src', 'us-east-1'))
    for builder in ('build_user_mapping', 'build_queue_mapping', 'build_contact_flow_mapping'):
        monkeypatch.setattr(create_quick_connects, builder, lambda *a: {})
    create_quick_connects.lambda_handler({
        'sourceInstanceId': SOURCE_INSTANCE_ARN, 'targetInstanceId': TARGET_INSTANCE_ARN,
        'quickConnects': [{'QuickConnectId': 'qc-1', 'Name': 'Call Jane Doe',
                           'QuickConnectConfig': {'QuickConnectType': 'PHONE_NUMBER',
                                                  'PhoneConfig': {'PhoneNumber': PHONE}}}],
    }, None)
    out = capsys.readouterr().out
    assert PHONE not in out and 'Jane' not in out
    assert 'qc-1' in out and 'PHONE_NUMBER' in out
    assert fake.calls[-1][0] == 'create_quick_connect'
