"""
Migration report built from a Step Functions execution history.

Shared by the BuildMigrationReport state (which decides whether the
execution ends Succeeded or Failed) and the status API (which shows the
report in the UI), so both always agree.

The report holds state names, resource IDs, error codes and counts only:
never names, contact data, flow content or the ExternalId. It also says
whether the per execution ExternalId secret was deleted; a failed cleanup
fails the report, because the secret may be left behind.

The VerifyTarget step result is read from the history like every other step
(its failedChecks count as failed items) and is also summarised in the
`verification` section. A FAIL or ERROR verdict, or a VerifyTarget step that
failed outright, fails the report. PASS_WITH_FOLLOW_UPS does not; its flow and
module follow ups replace the create steps' own lists (CONTENT_CHECK_STEPS),
so reruns list them too.
"""
import json

MAX_HISTORY_PAGES = 50
FAILURE_LIST_KEYS = ('failedItems', 'not_created', 'notMigrated')
SUCCEEDED, FAILED, COMPLETED_WITH_FAILURES = 'SUCCEEDED', 'FAILED', 'COMPLETED_WITH_FAILURES'
# The report step itself and the secret cleanup are not migration steps.
REPORT_STATE = 'BuildMigrationReport'
REPORT_FAILED_STATE = 'BuildMigrationReportFailed'
CLEANUP_STATE = 'CleanupExternalIdSecret'
CLEANUP_FAILED_STATE = 'CleanupExternalIdSecretFailed'
FATAL_STATE = 'HandleError'
UNKNOWN_STATE = 'UnknownState'
VERIFY_STATE = 'VerifyTarget'
VERIFY_FAILED_STATE = 'VerifyTargetFailed'
VERIFY_VERDICTS = frozenset({'PASS', 'PASS_WITH_FOLLOW_UPS', 'FAIL', 'ERROR'})
VERIFY_FAILING = frozenset({'FAIL', 'ERROR'})
VERIFY_NOT_RUN = 'NOT_RUN'
# Keys of the VerifyTarget result copied into the report (IDs, codes and counts only).
VERIFY_KEYS = ('errorCode', 'action', 'checkCount', 'failedCheckCount', 'checks', 'notChecked')
# VerifyTarget content check -> the create step whose manual follow ups it re-derives from the target
# flows on every run. A create step lists follow ups only for flows it creates, so a rerun whose flows
# already exist would list none; the scan lists them every time. When the scan completed (no issue
# other than a mappable reference), its list replaces the create step's.
CONTENT_CHECK_STEPS = {'flowContent': 'CreateContactFlows', 'moduleContent': 'CreateContactFlowModules'}
SCAN_COMPLETE_ISSUES = frozenset({'MappableSourceReference'})
IGNORED_STATES = {REPORT_STATE, CLEANUP_STATE}
# CleanupExternalIdSecret results that mean no secret is left behind.
CLEANUP_OK = frozenset({'DELETED', 'ALREADY_DELETED', 'NOT_NEEDED'})
CLEANUP_NOT_RUN = 'NOT_RUN'
CLEANUP_WARNING_TEXT = ('The ExternalId secret of this execution may be left behind in Secrets Manager. '
                        'The EventBridge backstop retries the delete, and a delete that still fails is kept in '
                        'the cleanup failure queue; if the secret is still there, delete the secret named after '
                        'this execution under the tool prefix (see the README).')
TASK_FAILED_EVENTS = ('TaskFailed', 'LambdaFunctionFailed', 'LambdaFunctionTimedOut', 'TaskTimedOut',
                      'LambdaFunctionScheduleFailed', 'LambdaFunctionStartFailed', 'TaskStartFailed',
                      'TaskSubmitFailed')
# What a run is for, stated in every report (fixed text: no names or IDs).
SCOPE = {
    'kind': 'ONE_TIME_CUTOVER',
    'message': ('This is a one time cutover: the configuration is copied once, traffic and phone numbers move to '
                'the target, and the source instance is retired afterwards. It is not a warm standby, a backup or '
                'a multi Region setup, and the instances are not kept in sync. For multi Region resiliency AWS '
                'supports only Amazon Connect Customer Global Resiliency (ACGR); a custom copy used as a '
                'secondary instance can get reduced service quotas.'),
}


def _parse(text):
    if isinstance(text, (dict, list)):
        return text
    try:
        return json.loads(text) if text else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _payload(task_output):
    """The Lambda result inside a task output ({'Payload': ...} for lambda:invoke)."""
    output = _parse(task_output)
    payload = output.get('Payload', output) if isinstance(output, dict) else {}
    if isinstance(payload, dict) and isinstance(payload.get('body'), str):
        body = _parse(payload['body'])
        if isinstance(body, dict):
            payload = {**payload, **body}
    return payload if isinstance(payload, dict) else {}


def failure_entries(node, in_failure_list=False):
    """Yield failure entries (dicts or plain identifiers) found in a step result."""
    if isinstance(node, dict):
        if in_failure_list or node.get('status') == 'failed':
            yield node
            return
        for key, value in node.items():
            failure_key = isinstance(value, list) and (key in FAILURE_LIST_KEYS or key.startswith('failed'))
            yield from failure_entries(value, failure_key)
    elif isinstance(node, list):
        for value in node:
            if in_failure_list and not isinstance(value, (dict, list)):
                yield {'originalId': value}
            else:
                yield from failure_entries(value, in_failure_list)


def follow_up_items(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == 'manualFollowUp' and isinstance(value, list):
                yield from value
            else:
                yield from follow_up_items(value)
    elif isinstance(node, list):
        for value in node:
            yield from follow_up_items(value)


def steps_covered_by_verify(verify_payload):
    """Create steps whose follow ups VerifyTarget's completed content scan re-derived."""
    covered = set()
    for check in (verify_payload or {}).get('checks') or []:
        step = CONTENT_CHECK_STEPS.get(check.get('check'))
        if step and set(check.get('issues') or {}) <= SCAN_COMPLETE_ISSUES:
            covered.add(step)
    return covered


def _iter_events(sfn, execution_arn):
    kwargs = {'executionArn': execution_arn, 'maxResults': 1000}
    for _ in range(MAX_HISTORY_PAGES):
        page = sfn.get_execution_history(**kwargs)
        yield from page.get('events', [])
        if not page.get('nextToken'):
            return
        kwargs['nextToken'] = page['nextToken']


def _event_details(item):
    """The '...EventDetails' dict of a history event (each event type has its own key)."""
    for key, value in item.items():
        if key.endswith('EventDetails') and isinstance(value, dict):
            return value
    return {}


def secret_cleanup_info(cleanup_status):
    """Report section for the per execution ExternalId secret."""
    status = cleanup_status or CLEANUP_NOT_RUN
    info = {'cleanupStatus': status, 'mayRemain': status not in CLEANUP_OK and status != CLEANUP_NOT_RUN}
    if info['mayRemain']:
        info['message'] = CLEANUP_WARNING_TEXT
    return info


def verification_info(payload=None, error=None):
    """Report section for the VerifyTarget step: its result, or ERROR with the step's error code."""
    if payload is None:
        if error is None:
            return {'verdict': VERIFY_NOT_RUN}
        return {'verdict': 'ERROR', 'errorCode': error}
    verdict = payload.get('verdict')
    info = {'verdict': verdict if verdict in VERIFY_VERDICTS else 'ERROR'}
    info.update({key: payload[key] for key in VERIFY_KEYS if key in payload})
    if verdict not in VERIFY_VERDICTS:
        info.setdefault('errorCode', 'InvalidVerifyResult')
    return info


def build_report(sfn, execution_arn, fatal_error=None, extra_results=None, cleanup_status=None):
    """Return the migration report dict for one execution.

    fatal_error: the state machine reached its top level error handler. None
    (the status API) derives it from the history (the HandleError state ran).
    extra_results: {state name: task result} not yet in the history (the
    caller's own step, for example the verification result).
    cleanup_status: status returned by CleanupExternalIdSecret, passed by the
    state machine. None reads it from the history. A failed cleanup fails the
    report and says the secret may be left behind.
    """
    events = list(_iter_events(sfn, execution_arn))
    # Every event belongs to the state whose '...StateEntered' event starts its
    # previousEventId chain. Retries chain through the failed attempts
    # (TaskStateEntered, Scheduled, Started, Failed, Scheduled, ...), so the walk
    # can be long; it is bounded by the history length, and memoised, so each
    # event costs one hop when events are processed in order.
    max_hops = len(events) + 1
    state_of, previous = {}, {}
    steps, follow_up, seen_follow_up, last_failure = {}, [], set(), {}
    stored_report = None
    fatal_seen = report_step_failed = False
    history_cleanup = None
    verify_payload = verify_error = None

    def state_for(event_id):
        path, name = [], ''
        for _ in range(max_hops):
            if event_id is None:
                break
            if event_id in state_of:
                name = state_of[event_id]
                break
            path.append(event_id)
            event_id = previous.get(event_id)
        for seen_id in path:
            state_of[seen_id] = name
        return name

    def record_result(state, payload):
        step = steps.setdefault(state, {'state': state, 'status': SUCCEEDED, 'failedItems': 0, 'errorCodes': {}})
        status_code = payload.get('statusCode')
        if isinstance(status_code, int) and status_code >= 400:
            # A step that caught its own error and returned an error response.
            last_failure[state] = f'StatusCode{status_code}'
        markers = set()
        for entry in failure_entries(payload):
            marker = json.dumps(entry, sort_keys=True, default=str)
            if marker in markers:
                continue
            markers.add(marker)
            code = entry.get('errorCode') or 'Unknown'
            step['failedItems'] += 1
            step['errorCodes'][code] = step['errorCodes'].get(code, 0) + 1
            step['status'] = COMPLETED_WITH_FAILURES
        for item in follow_up_items(payload):
            marker = json.dumps(item, sort_keys=True, default=str)
            if marker not in seen_follow_up:
                seen_follow_up.add(marker)
                follow_up.append((state, item))

    for item in events:
        kind = item.get('type') or ''
        previous[item['id']] = item.get('previousEventId')
        if kind.endswith('StateEntered'):
            name = (item.get('stateEnteredEventDetails') or {}).get('name', '')
            state_of[item['id']] = name
            if name == FATAL_STATE:
                fatal_seen = True
            elif name == REPORT_FAILED_STATE:
                report_step_failed = True
            elif name == CLEANUP_FAILED_STATE:
                history_cleanup = FAILED
            elif name == VERIFY_FAILED_STATE and verify_payload is None:
                verify_error = verify_error or 'VerifyTargetFailed'
            continue
        state = state_for(item['id'])
        if kind in ('TaskSucceeded', 'LambdaFunctionSucceeded'):
            payload = _payload(_event_details(item).get('output'))
            if state == REPORT_STATE:
                stored_report, report_step_failed = payload, False
            elif state == CLEANUP_STATE:
                history_cleanup = payload.get('status') or FAILED
            elif state == VERIFY_STATE:
                verify_payload, verify_error = payload, None
            if state in IGNORED_STATES:
                continue
            last_failure.pop(state, None)
            record_result(state, payload)
        elif kind in TASK_FAILED_EVENTS:
            if state == REPORT_STATE:
                report_step_failed = True
            elif state == CLEANUP_STATE:
                history_cleanup = FAILED
            elif state == VERIFY_STATE:
                verify_error = _event_details(item).get('error') or kind
            if state in IGNORED_STATES:
                continue
            # Fail closed: a failure that cannot be tied to a state still fails the run.
            state = state or UNKNOWN_STATE
            # Only the last attempt counts: a failure followed by a successful retry is not a failed step.
            last_failure[state] = _event_details(item).get('error') or kind

    effective_cleanup = cleanup_status if cleanup_status is not None else history_cleanup
    secret = secret_cleanup_info(effective_cleanup)
    if VERIFY_STATE in (extra_results or {}):
        verify_payload, verify_error = _payload(extra_results[VERIFY_STATE]), None
    verification = verification_info(verify_payload, verify_error)
    verification_failed = verification['verdict'] in VERIFY_FAILING

    if stored_report and stored_report.get('status') in (SUCCEEDED, FAILED) and not report_step_failed:
        report = dict(stored_report)
        # Reports stored before the cleanup moved ahead of the report step lack these keys.
        report.setdefault('externalIdSecret', secret)
        report.setdefault('reportStepFailed', False)
        report.setdefault('verification', verification)
        report['scope'] = dict(SCOPE)
        if report['externalIdSecret'].get('mayRemain') or report['verification'].get('verdict') in VERIFY_FAILING:
            report['status'] = FAILED
        return report

    failed_steps = [{'state': state, 'error': error} for state, error in sorted(last_failure.items())]
    for state in last_failure:
        steps.setdefault(state, {'state': state, 'status': FAILED, 'failedItems': 0, 'errorCodes': {}})
        steps[state]['status'] = FAILED

    for state, payload in (extra_results or {}).items():
        record_result(state, payload)

    fatal = bool(fatal_error) or fatal_seen
    failed_item_count = sum(step['failedItems'] for step in steps.values())
    failed = (fatal or failed_steps or failed_item_count or secret['mayRemain'] or report_step_failed
              or verification_failed)
    covered = steps_covered_by_verify(verify_payload)
    return {
        'status': FAILED if failed else SUCCEEDED,
        'fatalError': fatal,
        'steps': sorted(steps.values(), key=lambda s: s['state']),
        'failedSteps': failed_steps,
        'failedItemCount': failed_item_count,
        'manualFollowUp': [item for state, item in follow_up if state not in covered],
        'externalIdSecret': secret,
        'reportStepFailed': report_step_failed,
        'verification': verification,
        'scope': dict(SCOPE),
    }
