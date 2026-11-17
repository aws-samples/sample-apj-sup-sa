"""Quick connects: a USER quick connect whose user was not migrated is recorded, the rest still run."""
import json

from botocore.exceptions import ClientError

import create_quick_connects
from conftest import SOURCE_INSTANCE_ARN, TARGET_INSTANCE_ARN


class FakeDest:
    def __init__(self, existing=None):
        self.created, self.existing = [], existing or []

    def create_quick_connect(self, **params):
        self.created.append(params['QuickConnectConfig']['QuickConnectType'])
        return {'QuickConnectId': f'new-{len(self.created)}'}

    def get_paginator(self, method):
        assert method == 'list_quick_connects'
        existing = self.existing

        class Paginator:
            def paginate(self, InstanceId):
                return [{'QuickConnectSummaryList': existing}]
        return Paginator()


class FakeSource:
    def describe_user(self, InstanceId, UserId):
        return {'User': {'Username': 'jane.doe'}}


def test_user_not_in_target_is_a_failed_item_and_other_types_are_created(monkeypatch, capsys):
    dest = FakeDest()
    monkeypatch.setattr(create_quick_connects, 'create_destination_client', lambda *a: (dest, 'dst', 'us-east-1'))
    monkeypatch.setattr(create_quick_connects, 'create_connect_client_from_arn',
                        lambda arn: (FakeSource(), 'src', 'us-east-1'))
    for builder in ('build_user_mapping', 'build_contact_flow_mapping'):
        monkeypatch.setattr(create_quick_connects, builder, lambda *a: {})
    monkeypatch.setattr(create_quick_connects, 'build_queue_mapping', lambda *a: {'q-1': 'tq-1'})
    monkeypatch.setattr(create_quick_connects, 'find_user_by_username', lambda *a: None)

    result = create_quick_connects.lambda_handler({
        'sourceInstanceId': SOURCE_INSTANCE_ARN, 'targetInstanceId': TARGET_INSTANCE_ARN,
        'quickConnects': [
            {'QuickConnectId': 'qc-user', 'Name': 'Call Jane Doe',
             'QuickConnectConfig': {'QuickConnectType': 'USER', 'UserConfig': {'UserId': 'u-1', 'ContactFlowId': 'f-1'}}},
            {'QuickConnectId': 'qc-queue', 'Name': 'Support',
             'QuickConnectConfig': {'QuickConnectType': 'QUEUE', 'QueueConfig': {'QueueId': 'q-1', 'ContactFlowId': 'f-2'}}},
            {'QuickConnectId': 'qc-phone', 'Name': 'Desk',
             'QuickConnectConfig': {'QuickConnectType': 'PHONE_NUMBER', 'PhoneConfig': {'PhoneNumber': '+15550100'}}},
        ],
    }, None)

    assert result['failedItems'] == [{'originalId': 'qc-user', 'status': 'failed', 'errorCode': 'UserNotMigrated',
                                      'reason': 'User not found in target'}]
    assert json.loads(result['body'])['not_created'] == result['failedItems']
    assert 'PHONE_NUMBER' in dest.created and 'USER' not in dest.created
    # The step output lists the new quick connect IDs, never names.
    body = json.loads(result['body'])
    assert body['created'] == ['new-1', 'new-2'] and body['qc_mapping'] == {'qc-queue': 'new-1', 'qc-phone': 'new-2'}
    for name in ('Call Jane Doe', 'Support', 'Desk', '+15550100'):
        assert name not in json.dumps(result)
    # Names and usernames stay out of the result and the logs.
    out = capsys.readouterr().out
    for text in (json.dumps(result['failedItems']), out):
        assert 'jane' not in text.lower()


class FakeAgentStatusConnect:
    """Raises the real duplicate error (a ClientError) and lists the existing status by name."""

    def create_agent_status(self, InstanceId, Name, State):
        if Name == 'Existing Break':
            raise ClientError({'Error': {'Code': 'DuplicateResourceException', 'Message': Name}}, 'CreateAgentStatus')
        return {'AgentStatusId': f'dst-{Name[:3].lower()}'}

    def get_paginator(self, method):
        assert method == 'list_agent_statuses'

        class Paginator:
            def paginate(self, InstanceId):
                return [{'AgentStatusSummaryList': [{'Id': 'dst-existing', 'Name': 'Existing Break'}]}]
        return Paginator()


def test_agent_status_output_lists_ids_not_names(monkeypatch):
    import create_agent_status
    monkeypatch.setattr(create_agent_status, 'create_destination_client',
                        lambda *a: (FakeAgentStatusConnect(), 'dst', 'us-east-1'))
    result = create_agent_status.lambda_handler({'targetInstanceId': TARGET_INSTANCE_ARN, 'agentStatuses': [
        {'Id': 'as-1', 'Name': 'Lunch With Jane', 'Type': 'CUSTOM'},
        {'Id': 'as-2', 'Name': 'Existing Break', 'Type': 'CUSTOM'},
        {'Id': 'as-3', 'Name': 'Available', 'Type': 'ROUTABLE'}]}, None)
    assert result['migratedStatuses'] == ['as-1'] and result['skippedStatuses'] == ['as-2']
    assert result['statusMapping'] == {'as-1': 'dst-lun', 'as-2': 'dst-existing'}
    assert 'Jane' not in json.dumps(result) and 'Existing Break' not in json.dumps(result)
