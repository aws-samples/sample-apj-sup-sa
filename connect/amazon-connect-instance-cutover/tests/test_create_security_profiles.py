"""create_security_profiles: existing profiles are mapped, real failures are recorded."""
from botocore.stub import ANY

import create_security_profiles
from conftest import TARGET_INSTANCE_ARN, TARGET_INSTANCE_ID

PROFILE = {'SecurityProfileId': 'src-sp-1', 'SecurityProfileName': 'Supervisor', 'Permissions': ['BasicAgentAccess']}


def run(stubbed, monkeypatch, setup):
    client, stub = stubbed('connect', 'ap-southeast-2')
    monkeypatch.setattr(create_security_profiles, 'create_destination_client',
                        lambda *a: (client, TARGET_INSTANCE_ID, 'ap-southeast-2'))
    setup(stub)
    return create_security_profiles.lambda_handler(
        {'destinationInstanceId': TARGET_INSTANCE_ARN, 'securityProfiles': [PROFILE]}, None)


def test_invalid_parameter_on_existing_profile_maps_it(stubbed, monkeypatch):
    def setup(stub):
        stub.add_response('list_security_profiles', {'SecurityProfileSummaryList': []}, {'InstanceId': ANY})
        stub.add_client_error('create_security_profile', service_error_code='InvalidParameterException',
                              http_status_code=400)
        stub.add_response('list_security_profiles', {'SecurityProfileSummaryList': [
            {'Id': 'dst-sp-7', 'Name': 'Supervisor'}]}, {'InstanceId': ANY})
    result = run(stubbed, monkeypatch, setup)
    assert result['securityProfileMapping'] == {'src-sp-1': 'dst-sp-7'}
    assert result['createdProfiles'][0]['status'] == 'existing'
    assert result['failedItems'] == []


def test_an_existing_profile_is_mapped_before_create(stubbed, monkeypatch):
    def setup(stub):  # no create_security_profile response: a create call would fail the test
        stub.add_response('list_security_profiles', {'SecurityProfileSummaryList': [
            {'Id': 'dst-sp-7', 'Name': 'Supervisor'}]}, {'InstanceId': TARGET_INSTANCE_ID})
    result = run(stubbed, monkeypatch, setup)
    assert result['securityProfileMapping'] == {'src-sp-1': 'dst-sp-7'}
    assert result['createdProfiles'] == [{'originalId': 'src-sp-1', 'newId': 'dst-sp-7', 'status': 'existing'}]


def test_real_failure_is_recorded_with_error_code(stubbed, monkeypatch):
    def setup(stub):
        stub.add_response('list_security_profiles', {'SecurityProfileSummaryList': []}, {'InstanceId': ANY})
        stub.add_client_error('create_security_profile', service_error_code='InvalidParameterException',
                              http_status_code=400)
        stub.add_response('list_security_profiles', {'SecurityProfileSummaryList': []}, {'InstanceId': ANY})
    result = run(stubbed, monkeypatch, setup)
    assert result['securityProfileMapping'] == {}
    assert result['failedItems'] == [{'originalId': 'src-sp-1', 'status': 'failed',
                                      'errorCode': 'InvalidParameterException',
                                      'reason': 'exists but not found by name'}]


def test_other_errors_are_not_treated_as_existing(stubbed, monkeypatch):
    def setup(stub):
        stub.add_response('list_security_profiles', {'SecurityProfileSummaryList': []}, {'InstanceId': ANY})
        stub.add_client_error('create_security_profile', service_error_code='LimitExceededException',
                              http_status_code=429)
    result = run(stubbed, monkeypatch, setup)
    assert result['failedItems'][0]['errorCode'] == 'LimitExceededException'
