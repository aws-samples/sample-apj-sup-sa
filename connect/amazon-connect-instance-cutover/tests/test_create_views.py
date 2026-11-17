"""create_views: the new view ID comes from View.Id (not ViewId, which raised KeyError)."""
from botocore.stub import ANY

import create_views
from conftest import TARGET_INSTANCE_ARN, TARGET_INSTANCE_ID

VIEW = {'Id': 'src-view-1', 'Name': 'Customer card', 'Content': {'Template': '{}', 'Actions': ['Submit']}}


def test_created_view_is_mapped_from_view_id(stubbed, monkeypatch):
    client, stub = stubbed('connect', 'ap-southeast-2')
    monkeypatch.setattr(create_views, 'create_destination_client',
                        lambda *a: (client, TARGET_INSTANCE_ID, 'ap-southeast-2'))
    stub.add_response('list_views', {'ViewsSummaryList': []},
                      {'InstanceId': TARGET_INSTANCE_ID, 'Type': 'CUSTOMER_MANAGED'})
    stub.add_response('create_view', {'View': {'Id': 'dst-view-9', 'Name': 'Customer card'}}, {
        'InstanceId': TARGET_INSTANCE_ID, 'Name': 'Customer card', 'Status': 'PUBLISHED',
        'Content': {'Template': '{}', 'Actions': ['Submit']}})
    result = create_views.lambda_handler({'destinationInstanceId': TARGET_INSTANCE_ARN, 'views': [VIEW]}, None)
    assert result['viewMapping'] == {'src-view-1': 'dst-view-9'}
    assert result['createdViews'][0]['newId'] == 'dst-view-9'
    assert not [v for v in result['skippedViews'] if v['status'] == 'failed']


def test_existing_view_is_mapped_not_failed(stubbed, monkeypatch):
    client, stub = stubbed('connect', 'ap-southeast-2')
    monkeypatch.setattr(create_views, 'create_destination_client',
                        lambda *a: (client, TARGET_INSTANCE_ID, 'ap-southeast-2'))
    stub.add_response('list_views', {'ViewsSummaryList': [{'Id': 'dst-view-3', 'Name': 'Customer card'}]},
                      {'InstanceId': ANY, 'Type': 'CUSTOMER_MANAGED'})
    result = create_views.lambda_handler({'destinationInstanceId': TARGET_INSTANCE_ARN, 'views': [VIEW]}, None)
    assert result['viewMapping'] == {'src-view-1': 'dst-view-3'}
    assert result['skippedViews'][0]['status'] == 'existing'
