"""Create steps map resources that already exist in the target (no AWS).

Regression: an existing agent status was skipped and statusMapping stayed empty, the
same gap the security profile fix closed. These tests cover agent statuses and
the other create steps found with the same gap: quick connects (a rerun failed
them as duplicates), data tables (mapped to None: the summary key is Id) and
the duplicate race branches that recorded nothing when the lookup missed.
"""
import ast
import json
import pathlib

import pytest
from botocore.exceptions import ClientError

import create_agent_status
import create_data_tables
import create_queues
import create_quick_connects
import create_users
from conftest import SOURCE_INSTANCE_ARN, TARGET_INSTANCE_ARN

ROOT = pathlib.Path(__file__).resolve().parent.parent


def client_error(code, operation):
    return ClientError({'Error': {'Code': code, 'Message': 'name Jane Doe'}}, operation)


class Pages:
    def __init__(self, key, items):
        self.key, self.items = key, items

    def paginate(self, **params):
        return [{self.key: self.items}]


class AgentStatusTarget:
    def __init__(self, existing, create_error=None):
        self.existing, self.create_error, self.created = existing, create_error, []

    def create_agent_status(self, InstanceId, Name, State):
        if self.create_error:
            raise client_error(self.create_error, 'CreateAgentStatus')
        self.created.append(Name)
        return {'AgentStatusId': f'new-{len(self.created)}'}

    def get_paginator(self, method):
        assert method == 'list_agent_statuses'
        return Pages('AgentStatusSummaryList', self.existing)


def run_agent_statuses(monkeypatch, target, statuses):
    monkeypatch.setattr(create_agent_status, 'create_destination_client', lambda *a: (target, 'dst', 'r'))
    return create_agent_status.lambda_handler({'targetInstanceId': TARGET_INSTANCE_ARN, 'agentStatuses': statuses},
                                              None)


STATUSES = [{'Id': 'src-break', 'Name': 'Break', 'Type': 'CUSTOM'},
            {'Id': 'src-available', 'Name': 'Available', 'Type': 'ROUTABLE'},
            {'Id': 'src-offline', 'Name': 'Offline', 'Type': 'OFFLINE'}]


@pytest.mark.parametrize('code', ['DuplicateResourceException', 'InvalidParameterException'])
def test_an_existing_agent_status_is_mapped_by_name(monkeypatch, code):
    target = AgentStatusTarget([{'Id': 'dst-break', 'Name': 'Break'}], create_error=code)
    result = run_agent_statuses(monkeypatch, target, STATUSES)
    assert result['statusMapping'] == {'src-break': 'dst-break'}
    assert result['skippedStatuses'] == ['src-break'] and result['failedItems'] == []
    # Built-in statuses are skipped by the shared filter, as before (not created, not failed).
    assert 'Jane' not in json.dumps(result) and 'Break' not in json.dumps(result)


def test_an_agent_status_that_exists_but_is_not_found_fails_with_its_code(monkeypatch):
    target = AgentStatusTarget([], create_error='DuplicateResourceException')
    result = run_agent_statuses(monkeypatch, target, STATUSES[:1])
    assert result['statusMapping'] == {}
    assert result['failedItems'] == [{'originalId': 'src-break', 'status': 'failed',
                                      'errorCode': 'DuplicateResourceException',
                                      'reason': 'exists but not found by name'}]


def test_other_agent_status_errors_are_failures_not_existing(monkeypatch):
    class Target(AgentStatusTarget):
        lists = 0

        def get_paginator(self, method):
            self.lists += 1
            # Break appears after the first listing: a re lookup on this error would wrongly map it.
            if self.lists > 1:
                self.existing = [{'Id': 'dst-break', 'Name': 'Break'}]
            return super().get_paginator(method)

    target = Target([], create_error='LimitExceededException')
    result = run_agent_statuses(monkeypatch, target, STATUSES[:1])
    assert result['statusMapping'] == {} and result['failedItems'][0]['errorCode'] == 'LimitExceededException'
    assert target.lists == 1


class QuickConnectTarget:
    def __init__(self, listed, duplicate_names=()):
        self.listed, self.duplicate_names, self.created, self.lists = listed, set(duplicate_names), [], 0

    def get_paginator(self, method):
        assert method == 'list_quick_connects'
        self.lists += 1
        # The race: the quick connect appears in the target after the step's first listing.
        extra = [{'Id': f'dst-{n}', 'Name': n} for n in self.duplicate_names] if self.lists > 1 else []
        return Pages('QuickConnectSummaryList', self.listed + extra)

    def create_quick_connect(self, **params):
        if params['Name'] in self.duplicate_names:
            raise client_error('DuplicateResourceException', 'CreateQuickConnect')
        self.created.append(params['Name'])
        return {'QuickConnectId': f'new-{len(self.created)}'}


def run_quick_connects(monkeypatch, target, quick_connects):
    monkeypatch.setattr(create_quick_connects, 'create_destination_client', lambda *a: (target, 'dst', 'r'))
    monkeypatch.setattr(create_quick_connects, 'create_connect_client_from_arn', lambda arn: (None, 'src', 'r'))
    for builder in ('build_user_mapping', 'build_queue_mapping', 'build_contact_flow_mapping'):
        monkeypatch.setattr(create_quick_connects, builder, lambda *a: {})
    return create_quick_connects.lambda_handler({'sourceInstanceId': SOURCE_INSTANCE_ARN,
                                                 'targetInstanceId': TARGET_INSTANCE_ARN,
                                                 'quickConnects': quick_connects}, None)


def phone_quick_connect(qc_id, name):
    return {'QuickConnectId': qc_id, 'Name': name,
            'QuickConnectConfig': {'QuickConnectType': 'PHONE_NUMBER', 'PhoneConfig': {'PhoneNumber': '+15550100'}}}


def test_a_rerun_maps_existing_quick_connects_instead_of_failing_them(monkeypatch):
    target = QuickConnectTarget([{'Id': 'dst-desk', 'Name': 'Desk'}], duplicate_names=['Hotline'])
    result = run_quick_connects(monkeypatch, target, [phone_quick_connect('qc-desk', 'Desk'),
                                                      phone_quick_connect('qc-hot', 'Hotline'),
                                                      phone_quick_connect('qc-new', 'Front desk')])
    body = json.loads(result['body'])
    assert result['failedItems'] == [] and body['not_created'] == []
    assert body['qc_mapping'] == {'qc-desk': 'dst-desk', 'qc-hot': 'dst-Hotline', 'qc-new': 'new-1'}
    assert body['existing'] == ['dst-desk', 'dst-Hotline'] and body['created'] == ['new-1']
    assert target.created == ['Front desk']
    text = json.dumps(result)
    assert '+1555' not in text and 'Front desk' not in text and 'Jane' not in text


def test_a_quick_connect_duplicate_that_cannot_be_found_is_still_a_failure(monkeypatch):
    class Target(QuickConnectTarget):
        def get_paginator(self, method):
            return Pages('QuickConnectSummaryList', [])

    result = run_quick_connects(monkeypatch, Target([], duplicate_names=['Hotline']),
                                [phone_quick_connect('qc-hot', 'Hotline')])
    assert result['failedItems'] == [{'originalId': 'qc-hot', 'status': 'failed',
                                      'errorCode': 'DuplicateResourceException',
                                      'reason': 'CreateQuickConnect failed'}]


class DataTableTarget:
    def list_data_tables(self, InstanceId, NextToken=None):
        # The real summary has Id (botocore DataTableSummary), not DataTableId.
        return {'DataTableSummaryList': [{'Id': 'dst-table', 'Arn': 'arn:t', 'Name': 'Rates'}]}


def test_an_existing_data_table_is_mapped_to_its_target_id(monkeypatch):
    monkeypatch.setattr(create_data_tables, 'create_destination_client', lambda *a: (DataTableTarget(), 'dst', 'r'))
    result = create_data_tables.lambda_handler({'destinationInstanceId': TARGET_INSTANCE_ARN,
                                                'dataTables': [{'Id': 'src-table', 'Name': 'Rates'}]}, None)
    assert result['tableMapping'] == {'src-table': 'dst-table'}
    assert result['skippedTables'] == [{'originalId': 'src-table', 'newId': 'dst-table', 'status': 'existing'}]


def existing_entries(tree):
    """Dict literals in a create step that mark a source resource as already in the target."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            pairs = {k.value: v for k, v in zip(node.keys, node.values) if isinstance(k, ast.Constant)}
            status = pairs.get('status')
            if isinstance(status, ast.Constant) and status.value in ('existing', 'duplicate'):
                yield node.lineno, set(pairs)


def test_every_existing_or_duplicate_entry_names_the_target_id_it_was_mapped_to():
    """An item reported as existing must carry its target ID; one that cannot be found must be a failure."""
    missing = []
    for path in sorted((ROOT / 'lambda').glob('create_*.py')):
        for line, keys in existing_entries(ast.parse(path.read_text())):
            if not keys & {'newId', 'existingId'}:
                missing.append(f'{path.name}:{line}')
    assert missing == []


class QueueTarget:
    """Target after a first run: standard queues plus one agent queue per user (agent summaries have no Name)."""

    def __init__(self, standard):
        self.standard, self.created, self.list_params = standard, [], []

    def get_paginator(self, method):
        assert method == 'list_queues'
        target = self

        class P:
            def paginate(self, **params):
                target.list_params.append(params)
                agent = [{'Id': 'dst-agent-q', 'QueueType': 'AGENT'}]
                return [{'QueueSummaryList': agent + target.standard}]
        return P()

    def create_queue(self, **params):
        self.created.append(params['Name'])
        return {'QueueId': f'new-q-{len(self.created)}'}


def test_a_second_run_maps_existing_queues_instead_of_failing_them(monkeypatch):
    target = QueueTarget([{'Id': 'dst-support', 'Name': 'Support', 'QueueType': 'STANDARD'}])
    monkeypatch.setattr(create_queues, 'create_destination_client', lambda *a: (target, 'dst', 'r'))
    result = create_queues.lambda_handler({'destinationInstanceId': TARGET_INSTANCE_ARN,
                                           'hoursMapping': {'h': 'dst-h'},
                                           'queues': [{'Name': 'Support', 'QueueId': 'src-support',
                                                       'HoursOfOperationId': 'h'}]}, None)
    assert target.created == [] and result['failedItems'] == []
    assert result['queueMapping'] == {'src-support': 'dst-support'}
    assert target.list_params == [{'InstanceId': 'dst', 'QueueTypes': ['STANDARD']}]
    assert 'Support' not in json.dumps(result)


class UserTarget:
    """Target after a first run: the username exists, and CreateUser would answer InvalidRequestException."""

    def __init__(self, users):
        self.users, self.created = users, []

    def get_paginator(self, method):
        assert method == 'list_users'
        return Pages('UserSummaryList', self.users)

    def create_user(self, **params):
        self.created.append(params['Username'])
        raise client_error('InvalidRequestException', 'CreateUser')


def test_a_second_run_maps_existing_users_with_their_target_ids(monkeypatch):
    target = UserTarget([{'Id': 'dst-jane', 'Username': 'jane.doe'}])
    monkeypatch.setattr(create_users, 'create_destination_client', lambda *a: (target, 'dst', 'r'))
    result = create_users.lambda_handler({
        'destinationInstanceId': TARGET_INSTANCE_ARN,
        'routingProfileMapping': {'rp': 'dst-rp'}, 'securityProfileMapping': {'sp': 'dst-sp'},
        'users': [{'Id': 'src-jane', 'Username': 'jane.doe', 'RoutingProfileId': 'rp', 'SecurityProfileIds': ['sp']}],
    }, None)
    assert target.created == [] and result['failedUsers'] == []
    assert result['userMapping'] == {'src-jane': 'dst-jane'}
    assert result['createdUsers'] == [{'originalId': 'src-jane', 'newId': 'dst-jane', 'status': 'existing'}]
    assert 'jane.doe' not in json.dumps(result)
