"""POST /check-flow-dependencies: placeholder issues, the API time budget and error handling.

Regression: the endpoint answered 502 after 28.3 s. Its log showed a KeyError
('name') from the placeholder report, which carries the source ARN under
'source', after about 22 s of flow analysis.
"""
import json
import time
import types

import pytest

import check_flow_dependencies as cfd
import region_utils
from arn_mapping import ArnRewriter, InstanceRef
from conftest import SOURCE_ACCOUNT, SOURCE_INSTANCE_ARN, TARGET_INSTANCE_ARN, TARGET_ROLE_ARN

ORIGIN = 'https://d111111abcdef8.cloudfront.net'
LAMBDA_ARN = f'arn:aws:lambda:us-east-1:{SOURCE_ACCOUNT}:function:order-lookup'
FLOW_WITH_LAMBDA = json.dumps({'Actions': [{'Type': 'InvokeLambdaFunction',
                                            'Parameters': {'LambdaFunctionARN': LAMBDA_ARN}}]})


def rewriter():
    return ArnRewriter(InstanceRef.from_arn(SOURCE_INSTANCE_ARN), InstanceRef.from_arn(TARGET_INSTANCE_ARN))


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


class FakeSource:
    """Source Connect client: ListContactFlows and DescribeContactFlow only."""

    def __init__(self, count, content=FLOW_WITH_LAMBDA, clock=None, seconds_per_describe=0.0, error=None):
        self.flows = [{'Id': f'flow-{i}', 'Name': f'Flow {i}', 'ContactFlowType': 'CONTACT_FLOW'}
                      for i in range(count)]
        self.content, self.clock, self.seconds, self.error = content, clock, seconds_per_describe, error
        self.described = []

    def get_paginator(self, operation):
        assert operation == 'list_contact_flows'
        return types.SimpleNamespace(paginate=lambda **kw: [{'ContactFlowSummaryList': self.flows}])

    def describe_contact_flow(self, InstanceId, ContactFlowId):
        if self.error:
            raise self.error
        self.described.append(ContactFlowId)
        if self.clock:
            self.clock.now += self.seconds
        return {'ContactFlow': {'Id': ContactFlowId, 'Content': self.content}}


def empty_inventory(client, instance_id):
    return {'flows_by_name': {}, 'warnings': []}


@pytest.fixture
def handler(monkeypatch):
    monkeypatch.setenv('ALLOWED_ORIGIN', ORIGIN)
    monkeypatch.setattr(cfd, 'create_destination_session', lambda *a: object())
    monkeypatch.setattr(cfd, 'create_destination_client', lambda *a, **k: (object(), 'target-id', 'ap-southeast-2'))
    monkeypatch.setattr(cfd, 'build_rewriter', lambda *a, **k: rewriter())
    monkeypatch.setattr(cfd, 'get_instance_resources', empty_inventory)
    paced = []
    monkeypatch.setattr(cfd, 'RateLimiter', lambda rate: types.SimpleNamespace(wait=lambda: paced.append(rate)))

    def call(source, offset=0):
        monkeypatch.setattr(cfd, 'create_connect_client_from_arn', lambda arn: (source, 'source-id', 'us-east-1'))
        body = {'sourceInstanceId': SOURCE_INSTANCE_ARN, 'destinationInstanceId': TARGET_INSTANCE_ARN,
                'crossAccountRoleArn': TARGET_ROLE_ARN, 'externalId': 'test-external-id-0123456789',
                'offset': offset}
        result = cfd.lambda_handler({'httpMethod': 'POST', 'body': json.dumps(body)}, None)
        return result['statusCode'], json.loads(result['body'])

    call.paced = paced
    return call


def test_placeholder_issue_uses_the_source_arn():
    """The 502 regression: placeholder entries have no 'name' key."""
    _, report = rewriter().rewrite(FLOW_WITH_LAMBDA)
    assert report['placeholders'] and 'name' not in report['placeholders'][0]
    issues = cfd.analyze_flow_dependencies(rewriter(), FLOW_WITH_LAMBDA)
    assert issues == [{'category': 'lambdas', 'name': LAMBDA_ARN, 'reason': issues[0]['reason']}]
    assert issues[0]['reason'].startswith('No match in the target.')


def test_flow_with_unmatched_lambda_answers_200(handler):
    status, body = handler(FakeSource(3))
    assert status == 200
    assert body['summary']['status'] == 'COMPLETE' and body['summary']['stopReason'] is None
    assert body['summary']['analyzedFlows'] == 3 and body['summary']['hasIssues'] == 3
    assert body['missingResources']['lambdas'] == [{'name': LAMBDA_ARN, 'reason': body['missingResources']['lambdas'][0]['reason']}]
    assert [f['name'] for f in body['flowAnalysis']] == ['Flow 0', 'Flow 1', 'Flow 2']
    # Every DescribeContactFlow waited for a slot at the API's default rate (2 per second).
    assert handler.paced == [2, 2, 2]


def test_rate_limiter_spreads_calls_at_the_rate():
    clock = FakeClock()
    slept = []

    def sleep(seconds):
        slept.append(round(seconds, 3))

    limiter = region_utils.RateLimiter(2, clock=clock.monotonic, sleep=sleep)
    for _ in range(3):
        limiter.wait()
    assert slept == [0.5, 1.0]  # slots at +0, +0.5 and +1.0 s
    clock.now += 5
    limiter.wait()
    assert slept == [0.5, 1.0]  # idle time is not banked as a burst beyond the next slot


def test_pacing_rates_are_the_documented_quota_defaults():
    """Service Quotas defaults (L-41C7214A, L-56B34560, L-FAC57D08, L-2DE43297, L-EE6F0D82)."""
    assert region_utils.CONNECT_DESCRIBE_RATES == {
        'describe_contact_flow': 2, 'describe_contact_flow_module': 2, 'describe_queue': 10,
        'describe_quick_connect': 10, 'describe_user': 10}


def test_a_paced_flow_whose_slot_is_after_the_deadline_is_not_started(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(cfd, 'time', clock)

    def pace():
        clock.now += 8.0  # each slot comes 8 s later

    analyzed = cfd.analyze_within_budget(['a', 'b', 'c', 'd'], str.upper, deadline=clock.now + 20, workers=1,
                                         pace=pace)
    assert analyzed == ['A', 'B']  # c's slot is at +24 s, past the 20 s deadline


def test_time_budget_returns_a_partial_batch_and_resumes(handler, monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(cfd, 'time', clock)
    monkeypatch.setattr(cfd, 'FLOW_WORKERS', 1)  # deterministic order for the fake clock
    source = FakeSource(5, clock=clock, seconds_per_describe=8.0)  # budget 20 s: flows 0, 1, 2 start in time

    status, body = handler(source)
    summary = body['summary']
    assert status == 200
    assert summary['status'] == 'PARTIAL' and summary['stopReason'] == cfd.STOP_TIME_BUDGET
    assert (summary['analyzedFlows'], summary['batchEnd'], summary['nextOffset'], summary['hasMore']) == (3, 3, 3, True)
    assert source.described == ['flow-0', 'flow-1', 'flow-2']

    status, body = handler(source, offset=summary['nextOffset'])
    assert status == 200 and body['summary']['status'] == 'COMPLETE'
    assert body['summary']['batchStart'] == 3 and body['summary']['batchEnd'] == 5
    assert source.described[3:] == ['flow-3', 'flow-4']


def test_batch_size_stop_is_reported(handler, monkeypatch):
    monkeypatch.setattr(cfd, 'FLOWS_PER_BATCH', 2)
    status, body = handler(FakeSource(3))
    assert status == 200
    assert body['summary']['status'] == 'PARTIAL' and body['summary']['stopReason'] == cfd.STOP_BATCH_SIZE
    assert body['summary']['nextOffset'] == 2


def test_first_flow_always_runs_after_the_deadline():
    """Every request makes progress, so the UI's paging loop always advances."""
    analyzed = cfd.analyze_within_budget(['a', 'b', 'c'], str.upper, deadline=time.monotonic() - 1, workers=4)
    assert analyzed == ['A']


def test_parallel_analysis_keeps_flow_order():
    def slow(flow):
        time.sleep(0.02 * (5 - flow))  # later flows finish first
        return flow

    assert cfd.analyze_within_budget(list(range(6)), slow, deadline=time.monotonic() + 60, workers=4) == list(range(6))


def test_unexpected_error_is_a_500_with_a_code_not_a_crash(handler, capsys):
    status, body = handler(FakeSource(2, error=KeyError('secret-looking-detail')))
    assert status == 500 and body == {'error': 'KeyError'}
    assert 'secret-looking-detail' not in capsys.readouterr().out
