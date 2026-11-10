"""Migration report: per step status, failure counts and agreement with the execution status."""
import json

import pytest

import build_migration_report
import migration_report

EXECUTION_ARN = 'arn:aws:states:us-east-1:111111111111:execution:connect-user-migration-test:run-1'


class FakeSfn:
    """Pages a fixed event list through get_execution_history."""

    def __init__(self, events, page_size=1000):
        self.events, self.page_size, self.calls = events, page_size, []

    def get_execution_history(self, executionArn, maxResults, nextToken=None):
        self.calls.append(executionArn)
        start = int(nextToken or 0)
        page = {'events': self.events[start:start + self.page_size]}
        if start + self.page_size < len(self.events):
            page['nextToken'] = str(start + self.page_size)
        return page


def history(*steps):
    """steps: (state name, 'ok', payload) or (state name, 'fail', error)."""
    events, next_id = [], 1
    for name, outcome, value in steps:
        events.append({'id': next_id, 'type': 'TaskStateEntered', 'stateEnteredEventDetails': {'name': name}})
        if outcome == 'ok':
            events.append({'id': next_id + 1, 'previousEventId': next_id, 'type': 'TaskSucceeded',
                           'taskSucceededEventDetails': {'output': json.dumps({'Payload': value})}})
        else:
            events.append({'id': next_id + 1, 'previousEventId': next_id, 'type': 'TaskFailed',
                           'taskFailedEventDetails': {'error': value}})
        next_id += 2
    return events


def statuses(report):
    return {s['state']: s['status'] for s in report['steps']}


def test_clean_run_succeeds():
    report = migration_report.build_report(FakeSfn(history(('CreateQueues', 'ok', {'queueMapping': {'a': 'b'}}))),
                                           EXECUTION_ARN)
    assert report['status'] == 'SUCCEEDED'
    assert statuses(report) == {'CreateQueues': 'SUCCEEDED'}
    assert report['failedSteps'] == [] and report['failedItemCount'] == 0


def test_failed_items_fail_the_run_and_are_counted_by_error_code():
    users = {'userMapping': {}, 'failedItems': [
        {'originalId': 'u-1', 'status': 'failed', 'errorCode': 'DuplicateResourceException'},
        {'originalId': 'u-2', 'status': 'failed', 'errorCode': 'InvalidParameterException'},
        {'originalId': 'u-3', 'status': 'failed', 'errorCode': 'InvalidParameterException'}]}
    quick = {'statusCode': 200, 'body': json.dumps({'not_created': [
        {'originalId': 'qc-1', 'status': 'failed', 'errorCode': 'UserNotMigrated'}]})}
    report = migration_report.build_report(
        FakeSfn(history(('CreateUsers', 'ok', users), ('CreateQuickConnectsPost', 'ok', quick))), EXECUTION_ARN)
    assert report['status'] == 'FAILED'
    assert statuses(report) == {'CreateUsers': 'COMPLETED_WITH_FAILURES',
                                'CreateQuickConnectsPost': 'COMPLETED_WITH_FAILURES'}
    users_step = next(s for s in report['steps'] if s['state'] == 'CreateUsers')
    assert users_step['errorCodes'] == {'DuplicateResourceException': 1, 'InvalidParameterException': 2}
    assert report['failedItemCount'] == 4


def test_error_response_fails_the_step():
    quick = {'statusCode': 500, 'body': json.dumps({'error': 'boom'})}
    report = migration_report.build_report(FakeSfn(history(('CreateQuickConnectsPost', 'ok', quick))), EXECUTION_ARN)
    assert report['status'] == 'FAILED'
    assert report['failedSteps'] == [{'state': 'CreateQuickConnectsPost', 'error': 'StatusCode500'}]
    assert statuses(report) == {'CreateQuickConnectsPost': 'FAILED'}


def test_failed_step_fails_the_run_but_a_successful_retry_does_not():
    events = history(('CreateUsers', 'fail', 'Lambda.TooManyRequestsException'), ('CreateUsers', 'ok', {}),
                     ('CreateQueues', 'fail', 'MigrationStepError'))
    report = migration_report.build_report(FakeSfn(events), EXECUTION_ARN)
    assert report['failedSteps'] == [{'state': 'CreateQueues', 'error': 'MigrationStepError'}]
    assert statuses(report) == {'CreateUsers': 'SUCCEEDED', 'CreateQueues': 'FAILED'}
    assert report['status'] == 'FAILED'


def test_fatal_error_fails_the_run():
    report = migration_report.build_report(FakeSfn(history(('CreateQueues', 'ok', {}))), EXECUTION_ARN,
                                           fatal_error=True)
    assert report['status'] == 'FAILED' and report['fatalError'] is True


def test_follow_up_is_collected_once_and_history_is_paged():
    item = {'type': 'queueOutboundCallerIdNumber', 'queueId': 'q-1'}
    events = history(*[(f'Step{i}', 'ok', {'manualFollowUp': [item]}) for i in range(3)])
    sfn = FakeSfn(events, page_size=2)
    report = migration_report.build_report(sfn, EXECUTION_ARN)
    assert report['manualFollowUp'] == [item]
    assert len(sfn.calls) == 3


def test_status_api_returns_the_stored_report():
    cleanup = ('CleanupExternalIdSecret', 'ok', {'status': 'DELETED'})
    stored = migration_report.build_report(FakeSfn(history(('CreateUsers', 'fail', 'X'), cleanup)), EXECUTION_ARN)
    assert stored['externalIdSecret'] == {'cleanupStatus': 'DELETED', 'mayRemain': False}
    events = history(('CreateUsers', 'fail', 'X'), cleanup, ('BuildMigrationReport', 'ok', stored))
    again = migration_report.build_report(FakeSfn(events), EXECUTION_ARN)
    assert again == stored


def test_cleanup_is_not_a_migration_step_and_a_clean_delete_succeeds():
    for cleanup_status in ('DELETED', 'ALREADY_DELETED', 'NOT_NEEDED'):
        events = history(('CreateUsers', 'ok', {}), ('CleanupExternalIdSecret', 'ok', {'status': cleanup_status}))
        report = migration_report.build_report(FakeSfn(events), EXECUTION_ARN)
        assert report['status'] == 'SUCCEEDED' and statuses(report) == {'CreateUsers': 'SUCCEEDED'}
        assert report['externalIdSecret']['mayRemain'] is False


def test_cleanup_not_yet_run_does_not_fail_a_running_report():
    report = migration_report.build_report(FakeSfn(history(('CreateUsers', 'ok', {}))), EXECUTION_ARN)
    assert report['status'] == 'SUCCEEDED'
    assert report['externalIdSecret'] == {'cleanupStatus': 'NOT_RUN', 'mayRemain': False}


@pytest.mark.parametrize('cleanup_events', [
    [('CleanupExternalIdSecret', 'ok', {'status': 'FAILED', 'errorCode': 'InternalServiceError'})],
    [('CleanupExternalIdSecret', 'fail', 'Lambda.Unknown')],
])
def test_failed_cleanup_in_the_history_fails_the_report_and_says_the_secret_may_remain(cleanup_events):
    events = history(('CreateUsers', 'ok', {}), *cleanup_events)
    report = migration_report.build_report(FakeSfn(events), EXECUTION_ARN)
    assert report['status'] == 'FAILED' and report['failedSteps'] == []
    assert report['externalIdSecret']['cleanupStatus'] == 'FAILED'
    assert report['externalIdSecret']['mayRemain'] is True
    assert 'may be left behind' in report['externalIdSecret']['message']


def test_cleanup_failed_pass_state_and_the_state_machine_input_fail_the_report():
    events = history(('CreateUsers', 'ok', {})) + [
        {'id': 100, 'previousEventId': 4, 'type': 'PassStateEntered',
         'stateEnteredEventDetails': {'name': 'CleanupExternalIdSecretFailed'}}]
    assert migration_report.build_report(FakeSfn(events), EXECUTION_ARN)['status'] == 'FAILED'
    # The state machine passes the cleanup status explicitly; it wins over the history.
    clean = history(('CreateUsers', 'ok', {}))
    report = migration_report.build_report(FakeSfn(clean), EXECUTION_ARN, cleanup_status='FAILED')
    assert report['status'] == 'FAILED' and report['externalIdSecret']['mayRemain'] is True
    report = migration_report.build_report(FakeSfn(clean), EXECUTION_ARN, cleanup_status='DELETED')
    assert report['status'] == 'SUCCEEDED'


def test_fatal_flag_is_derived_from_the_history_when_not_passed():
    events = history(('CreateUsers', 'ok', {})) + [
        {'id': 100, 'previousEventId': 2, 'type': 'PassStateEntered',
         'stateEnteredEventDetails': {'name': 'HandleError'}}]
    report = migration_report.build_report(FakeSfn(events), EXECUTION_ARN)
    assert report['status'] == 'FAILED' and report['fatalError'] is True


@pytest.mark.parametrize('report_failure', ['task', 'pass'])
def test_a_failed_report_step_is_never_a_success(report_failure):
    events = history(('CreateUsers', 'ok', {}), ('CleanupExternalIdSecret', 'ok', {'status': 'DELETED'}))
    if report_failure == 'task':
        events += [
            {'id': 50, 'type': 'TaskStateEntered', 'stateEnteredEventDetails': {'name': 'BuildMigrationReport'}},
            {'id': 51, 'previousEventId': 50, 'type': 'TaskFailed', 'taskFailedEventDetails': {'error': 'X'}}]
    else:
        events += [{'id': 60, 'previousEventId': 4, 'type': 'PassStateEntered',
                    'stateEnteredEventDetails': {'name': 'BuildMigrationReportFailed'}}]
    report = migration_report.build_report(FakeSfn(events), EXECUTION_ARN)
    assert report['reportStepFailed'] is True and report['status'] == 'FAILED'


def attempts_history(name, outcomes, first_id=1, payload=None):
    """Realistic Step Functions events for one task state with retries.

    outcomes: e.g. ['fail', 'fail', 'ok']. Each attempt is Scheduled, Started,
    then Succeeded or Failed, chained through previousEventId, so a later
    attempt is many hops away from the TaskStateEntered event.
    """
    events = [{'id': first_id, 'type': 'TaskStateEntered', 'stateEnteredEventDetails': {'name': name}}]
    for outcome in outcomes:
        last = events[-1]['id']
        events.append({'id': last + 1, 'previousEventId': last, 'type': 'TaskScheduled',
                       'taskScheduledEventDetails': {'resourceType': 'lambda', 'resource': 'invoke'}})
        events.append({'id': last + 2, 'previousEventId': last + 1, 'type': 'TaskStarted',
                       'taskStartedEventDetails': {'resourceType': 'lambda', 'resource': 'invoke'}})
        if outcome == 'ok':
            events.append({'id': last + 3, 'previousEventId': last + 2, 'type': 'TaskSucceeded',
                           'taskSucceededEventDetails': {'output': json.dumps({'Payload': payload or {}})}})
        else:
            events.append({'id': last + 3, 'previousEventId': last + 2, 'type': 'TaskFailed',
                           'taskFailedEventDetails': {'error': 'Lambda.TooManyRequestsException'}})
    last = events[-1]['id']
    events.append({'id': last + 1, 'previousEventId': last, 'type': 'TaskStateExited',
                   'stateExitedEventDetails': {'name': name}})
    return events


def test_task_that_succeeds_on_its_third_attempt_is_not_failed():
    events = attempts_history('CreateUsers', ['fail', 'fail', 'ok'], payload={'userMapping': {'u': 'v'}})
    # The success is 9 hops from TaskStateEntered: beyond the old fixed limit of 8.
    assert events[-2]['type'] == 'TaskSucceeded' and events[-2]['id'] - events[0]['id'] == 9
    report = migration_report.build_report(FakeSfn(events), EXECUTION_ARN)
    assert report['status'] == 'SUCCEEDED' and report['failedSteps'] == []
    assert statuses(report) == {'CreateUsers': 'SUCCEEDED'}


def test_retries_are_mapped_to_their_state_across_interleaved_states_and_pages():
    users = attempts_history('CreateUsers', ['fail', 'fail', 'fail', 'fail', 'ok'])
    queues = attempts_history('CreateQueues', ['fail', 'fail', 'fail'], first_id=100)
    events = users + queues
    report = migration_report.build_report(FakeSfn(events, page_size=3), EXECUTION_ARN)
    assert statuses(report) == {'CreateUsers': 'SUCCEEDED', 'CreateQueues': 'FAILED'}
    assert report['failedSteps'] == [{'state': 'CreateQueues', 'error': 'Lambda.TooManyRequestsException'}]


def test_a_failure_that_cannot_be_tied_to_a_state_still_fails_the_run():
    events = [{'id': 7, 'previousEventId': 6, 'type': 'TaskFailed', 'taskFailedEventDetails': {'error': 'X'}}]
    report = migration_report.build_report(FakeSfn(events), EXECUTION_ARN)
    assert report['status'] == 'FAILED' and report['failedSteps'] == [{'state': 'UnknownState', 'error': 'X'}]


def test_handler_reads_only_its_own_state_machine(monkeypatch):
    monkeypatch.setenv('EXECUTION_ARN_PREFIX', EXECUTION_ARN.rsplit(':', 1)[0] + ':')
    fake = FakeSfn(history(('CreateQueues', 'ok', {})))
    monkeypatch.setattr(build_migration_report.boto3, 'client', lambda *a, **k: fake)
    report = build_migration_report.lambda_handler({'executionArn': EXECUTION_ARN, 'fatalError': False,
                                                    'externalIdSecretCleanup': 'NOT_NEEDED'}, None)
    assert report['status'] == 'SUCCEEDED' and fake.calls == [EXECUTION_ARN]
    report = build_migration_report.lambda_handler({'executionArn': EXECUTION_ARN, 'fatalError': False,
                                                    'externalIdSecretCleanup': 'FAILED'}, None)
    assert report['status'] == 'FAILED' and report['externalIdSecret']['mayRemain'] is True
    with pytest.raises(ValueError):
        build_migration_report.lambda_handler(
            {'executionArn': 'arn:aws:states:us-east-1:111111111111:execution:other:run-1'}, None)
