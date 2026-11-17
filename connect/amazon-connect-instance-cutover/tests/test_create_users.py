"""create_users maps users that already exist in the target (rerun regression; no AWS).

On a rerun the target already had the 10 usernames the first run created. The step called
CreateUser blind, Connect answered InvalidRequestException (not
DuplicateResourceException), and all 10 users failed with no mapping.

Connect usernames are case sensitive (Admin Guide, "Configure SAML with IAM":
"All Connect usernames are case sensitive, even when using SAML"; the botocore
model does not say), so usernames match exactly.
"""
import json

from botocore.stub import ANY

import create_users
from conftest import TARGET_INSTANCE_ARN, TARGET_INSTANCE_ID

LIST_PARAMS = {'InstanceId': TARGET_INSTANCE_ID}
USER = {'Id': 'src-u1', 'Username': 'jane.doe', 'RoutingProfileId': 'rp-src', 'SecurityProfileIds': ['sp-src'],
        'IdentityInfo': {'FirstName': 'Jane', 'LastName': 'Doe', 'Email': 'jane.doe@example.com'}}
TARGET_JANE = {'Id': 'dst-u1', 'Arn': 'arn:dst-u1', 'Username': 'jane.doe'}
CREATE_PARAMS = {'InstanceId': TARGET_INSTANCE_ID, 'Username': 'jane.doe', 'Password': ANY,
                 'RoutingProfileId': 'rp-dst', 'SecurityProfileIds': ['sp-dst'],
                 'IdentityInfo': {'FirstName': 'Jane', 'LastName': 'Doe', 'Email': 'jane.doe@example.com'}}


def run(stubbed, monkeypatch, setup):
    client, stub = stubbed('connect', 'ap-southeast-2')
    monkeypatch.setattr(create_users, 'create_destination_client',
                        lambda *a: (client, TARGET_INSTANCE_ID, 'ap-southeast-2'))
    setup(stub)  # no create_user response unless the test adds one: an unexpected call fails
    return create_users.lambda_handler({'destinationInstanceId': TARGET_INSTANCE_ARN,
                                        'routingProfileMapping': {'rp-src': 'rp-dst'},
                                        'securityProfileMapping': {'sp-src': 'sp-dst'},
                                        'users': [USER]}, None)


def assert_existing(result):
    assert result['userMapping'] == {'src-u1': 'dst-u1'}
    assert result['createdUsers'] == [{'originalId': 'src-u1', 'newId': 'dst-u1', 'status': 'existing'}]
    assert result['failedUsers'] == []
    assert result['summary'] == {'totalUsers': 1, 'successfullyCreated': 1, 'existing': 1, 'failed': 0}
    assert 'jane' not in json.dumps(result).lower()


def test_a_username_in_the_target_is_mapped_without_create_user(stubbed, monkeypatch):
    def setup(stub):
        stub.add_response('list_users', {'UserSummaryList': [TARGET_JANE]}, LIST_PARAMS)
    assert_existing(run(stubbed, monkeypatch, setup))


def test_invalid_request_with_the_username_found_maps_the_user(stubbed, monkeypatch):
    def setup(stub):
        stub.add_response('list_users', {'UserSummaryList': []}, LIST_PARAMS)
        stub.add_client_error('create_user', service_error_code='InvalidRequestException', http_status_code=400,
                              service_message='User jane.doe already exists', expected_params=CREATE_PARAMS)
        stub.add_response('list_users', {'UserSummaryList': [TARGET_JANE]}, LIST_PARAMS)
    assert_existing(run(stubbed, monkeypatch, setup))


def test_invalid_request_with_the_username_absent_keeps_the_real_code(stubbed, monkeypatch):
    def setup(stub):
        stub.add_response('list_users', {'UserSummaryList': []}, LIST_PARAMS)
        stub.add_client_error('create_user', service_error_code='InvalidRequestException', http_status_code=400,
                              service_message='Bad email jane.doe@example.com', expected_params=CREATE_PARAMS)
        stub.add_response('list_users', {'UserSummaryList': []}, LIST_PARAMS)
    result = run(stubbed, monkeypatch, setup)
    assert result['userMapping'] == {} and result['createdUsers'] == []
    assert result['failedUsers'] == [{'originalId': 'src-u1', 'status': 'failed',
                                      'errorCode': 'InvalidRequestException',
                                      'reason': create_users.USERNAME_NOT_FOUND}]
    assert 'jane' not in json.dumps(result).lower()


def test_a_username_that_differs_only_in_case_is_a_different_user(stubbed, monkeypatch):
    def setup(stub):
        stub.add_response('list_users', {'UserSummaryList': [{**TARGET_JANE, 'Username': 'Jane.Doe'}]}, LIST_PARAMS)
        stub.add_response('create_user', {'UserId': 'new-u1', 'UserArn': 'arn:new-u1'}, CREATE_PARAMS)
    result = run(stubbed, monkeypatch, setup)
    assert result['userMapping'] == {'src-u1': 'new-u1'}
    assert result['createdUsers'][0]['status'] == 'created'


def test_a_failed_user_lookup_is_recorded_with_its_code_and_nothing_is_created(stubbed, monkeypatch):
    def setup(stub):
        stub.add_client_error('list_users', service_error_code='InvalidRequestException', http_status_code=400,
                              expected_params=LIST_PARAMS)
    result = run(stubbed, monkeypatch, setup)
    assert result['userMapping'] == {} and result['createdUsers'] == []
    assert result['failedUsers'] == [{'originalId': 'src-u1', 'status': 'failed',
                                      'errorCode': 'InvalidRequestException', 'reason': 'target lookup failed'}]
