"""List steps skip agent queues and AWS managed views before describing them (they only add noise)."""
import list_queues
import list_views
from conftest import SOURCE_INSTANCE_ARN, SOURCE_INSTANCE_ID


def test_list_queues_asks_for_standard_only_and_skips_agent_queues(stubbed, monkeypatch):
    client, stub = stubbed('connect', 'us-east-1')
    monkeypatch.setattr(list_queues, 'create_connect_client_from_arn',
                        lambda arn: (client, SOURCE_INSTANCE_ID, 'us-east-1'))
    stub.add_response('list_queues', {'QueueSummaryList': [
        {'Id': 'q-standard', 'QueueType': 'STANDARD'},
        {'Id': 'q-agent', 'QueueType': 'AGENT'},  # defensive: filtered even if returned
    ]}, {'InstanceId': SOURCE_INSTANCE_ID, 'QueueTypes': ['STANDARD']})
    stub.add_response('describe_queue', {'Queue': {'QueueId': 'q-standard', 'Name': 'Sales'}},
                      {'InstanceId': SOURCE_INSTANCE_ID, 'QueueId': 'q-standard'})
    result = list_queues.lambda_handler({'sourceInstanceId': SOURCE_INSTANCE_ARN}, None)
    assert [q['QueueId'] for q in result['queues']] == ['q-standard']


def test_list_views_asks_for_customer_managed_only(stubbed, monkeypatch):
    client, stub = stubbed('connect', 'us-east-1')
    monkeypatch.setattr(list_views, 'create_connect_client_from_arn',
                        lambda arn: (client, SOURCE_INSTANCE_ID, 'us-east-1'))
    stub.add_response('list_views', {'ViewsSummaryList': [
        {'Id': 'v-custom', 'Name': 'Card', 'Type': 'CUSTOMER_MANAGED', 'Status': 'PUBLISHED'},
        {'Id': 'standard-detail', 'Name': 'Detail', 'Type': 'AWS_MANAGED', 'Status': 'PUBLISHED'},
    ]}, {'InstanceId': SOURCE_INSTANCE_ID, 'Type': 'CUSTOMER_MANAGED'})
    stub.add_response('describe_view', {'View': {'Id': 'v-custom', 'Name': 'Card', 'Status': 'PUBLISHED',
                                                 'Content': {'Template': '{}'}}},
                      {'InstanceId': SOURCE_INSTANCE_ID, 'ViewId': 'v-custom'})
    result = list_views.lambda_handler({'sourceInstanceId': SOURCE_INSTANCE_ARN}, None)
    assert [v['Id'] for v in result['views']] == ['v-custom']
