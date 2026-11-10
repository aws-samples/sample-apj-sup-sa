"""Queue OutboundCallerConfig mapping for a target in another account and Region."""
import create_queues
from conftest import SOURCE_INSTANCE_ARN, TARGET_INSTANCE_ID

SRC_FLOW = 'aaaaaaaa-0000-4000-8000-0000000000f1'
DST_FLOW = 'bbbbbbbb-0000-4000-8000-0000000000f1'
SRC_NUMBER = 'aaaaaaaa-0000-4000-8000-00000000000e'


def test_maps_flow_keeps_name_and_drops_phone_number():
    config, follow_up = create_queues.map_outbound_caller_config(
        {'OutboundCallerIdName': 'Acme Support', 'OutboundCallerIdNumberId': SRC_NUMBER,
         'OutboundFlowId': SRC_FLOW},
        {SRC_FLOW: DST_FLOW}, 'Support')
    assert config == {'OutboundCallerIdName': 'Acme Support', 'OutboundFlowId': DST_FLOW}
    assert [f['type'] for f in follow_up] == ['queueOutboundCallerIdNumber']
    assert follow_up[0]['source'] == SRC_NUMBER and follow_up[0]['queue'] == 'Support'


def test_unmapped_flow_is_dropped_and_reported():
    config, follow_up = create_queues.map_outbound_caller_config(
        {'OutboundFlowId': SRC_FLOW}, {}, 'Support')
    assert config is None
    assert follow_up[0]['type'] == 'queueOutboundFlow' and follow_up[0]['source'] == SRC_FLOW


def test_only_phone_number_gives_no_config():
    config, follow_up = create_queues.map_outbound_caller_config(
        {'OutboundCallerIdNumberId': SRC_NUMBER}, {}, 'Support')
    assert config is None and len(follow_up) == 1


def test_empty_config():
    assert create_queues.map_outbound_caller_config(None, {}, 'q') == (None, [])


def test_handler_sends_mapped_config_to_create_queue(stubbed, monkeypatch):
    dest, dest_stub = stubbed('connect', 'ap-southeast-2')
    monkeypatch.setattr(create_queues, 'create_destination_client',
                        lambda *a, **k: (dest, TARGET_INSTANCE_ID, 'ap-southeast-2'))
    monkeypatch.setattr(create_queues, 'build_flow_mapping', lambda *a, **k: {SRC_FLOW: DST_FLOW})

    dest_stub.add_response('list_queues', {'QueueSummaryList': []},
                           {'InstanceId': TARGET_INSTANCE_ID, 'QueueTypes': ['STANDARD']})
    dest_stub.add_response('create_queue', {'QueueId': 'new-q', 'QueueArn': 'arn'}, {
        'InstanceId': TARGET_INSTANCE_ID,
        'Name': 'Support',
        'HoursOfOperationId': 'dst-hours',
        'OutboundCallerConfig': {'OutboundCallerIdName': 'Acme', 'OutboundFlowId': DST_FLOW},
    })
    result = create_queues.lambda_handler({
        'sourceInstanceId': SOURCE_INSTANCE_ARN,
        'destinationInstanceId': 'unused-because-patched',
        'hoursMapping': {'src-hours': 'dst-hours'},
        'queues': [{'Name': 'Support', 'QueueId': 'src-q', 'HoursOfOperationId': 'src-hours',
                    'OutboundCallerConfig': {'OutboundCallerIdName': 'Acme',
                                             'OutboundCallerIdNumberId': SRC_NUMBER,
                                             'OutboundFlowId': SRC_FLOW}}],
    }, None)
    assert result['queueMapping'] == {'src-q': 'new-q'}
    assert result['manualFollowUp'][0]['type'] == 'queueOutboundCallerIdNumber'


# Rerun regression: the target had 10 agent queues (one per user created by the first run).
# Agent queue summaries have no Name; the old lookup listed every type and raised KeyError.
STANDARD_ONLY = {'InstanceId': TARGET_INSTANCE_ID, 'QueueTypes': ['STANDARD']}
AGENT_SUMMARY = {'Id': 'dst-agent-q', 'Arn': 'arn:agent-q', 'QueueType': 'AGENT'}
SUPPORT_SUMMARY = {'Id': 'dst-q', 'Arn': 'arn:dst-q', 'Name': 'Support', 'QueueType': 'STANDARD'}
SUPPORT_QUEUE = {'Name': 'Support', 'QueueId': 'src-q', 'HoursOfOperationId': 'src-hours'}


def run_queue_step(stubbed, monkeypatch, setup, queues=(SUPPORT_QUEUE,)):
    dest, dest_stub = stubbed('connect', 'ap-southeast-2')
    monkeypatch.setattr(create_queues, 'create_destination_client',
                        lambda *a, **k: (dest, TARGET_INSTANCE_ID, 'ap-southeast-2'))
    setup(dest_stub)  # no create_queue response unless the test adds one: an unexpected call fails
    return create_queues.lambda_handler({'destinationInstanceId': 'unused-because-patched',
                                         'hoursMapping': {'src-hours': 'dst-hours'}, 'queues': list(queues)}, None)


def test_an_existing_queue_listed_after_an_agent_queue_is_mapped_without_create(stubbed, monkeypatch):
    def setup(stub):
        stub.add_response('list_queues', {'QueueSummaryList': [AGENT_SUMMARY, SUPPORT_SUMMARY]}, STANDARD_ONLY)
    result = run_queue_step(stubbed, monkeypatch, setup)
    assert result['queueMapping'] == {'src-q': 'dst-q'}
    assert result['createdQueues'] == [{'originalId': 'src-q', 'newId': 'dst-q', 'status': 'existing'}]
    assert result['failedItems'] == []


def test_a_duplicate_on_create_is_looked_up_once_more_and_mapped(stubbed, monkeypatch):
    create_params = {'InstanceId': TARGET_INSTANCE_ID, 'Name': 'Support', 'HoursOfOperationId': 'dst-hours'}

    def setup(stub):
        stub.add_response('list_queues', {'QueueSummaryList': [AGENT_SUMMARY]}, STANDARD_ONLY)
        stub.add_client_error('create_queue', service_error_code='DuplicateResourceException',
                              http_status_code=409, expected_params=create_params)
        stub.add_response('list_queues', {'QueueSummaryList': [AGENT_SUMMARY, SUPPORT_SUMMARY]}, STANDARD_ONLY)
    result = run_queue_step(stubbed, monkeypatch, setup)
    assert result['queueMapping'] == {'src-q': 'dst-q'} and result['failedItems'] == []
    assert result['createdQueues'][0]['status'] == 'existing'


def test_a_duplicate_that_is_still_not_found_keeps_its_real_code(stubbed, monkeypatch):
    def setup(stub):
        stub.add_response('list_queues', {'QueueSummaryList': [AGENT_SUMMARY]}, STANDARD_ONLY)
        stub.add_client_error('create_queue', service_error_code='DuplicateResourceException', http_status_code=409)
        stub.add_response('list_queues', {'QueueSummaryList': [AGENT_SUMMARY]}, STANDARD_ONLY)
    result = run_queue_step(stubbed, monkeypatch, setup)
    assert result['queueMapping'] == {}
    assert result['failedItems'] == [{'originalId': 'src-q', 'status': 'failed',
                                      'errorCode': 'DuplicateResourceException',
                                      'reason': 'exists but not found by name'}]


def test_a_failed_lookup_is_recorded_with_its_code_and_nothing_is_created(stubbed, monkeypatch):
    def setup(stub):
        stub.add_client_error('list_queues', service_error_code='InvalidRequestException', http_status_code=400,
                              expected_params=STANDARD_ONLY)
    result = run_queue_step(stubbed, monkeypatch, setup)
    assert result['queueMapping'] == {} and result['createdQueues'] == []
    assert result['failedItems'] == [{'originalId': 'src-q', 'status': 'failed',
                                      'errorCode': 'InvalidRequestException', 'reason': 'target lookup failed'}]
