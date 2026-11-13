"""Static checks on the state machine definition (no AWS calls)."""
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFINITION = (ROOT / 'step-functions' / 'migration-state-machine.json').read_text()
SUBSTITUTIONS = {'Partition': 'aws', 'Region': 'eu-west-1', 'AccountId': '123456789012', 'Environment': 'test'}
# The definition is valid JSON as stored; the remaining substitutions are strings and stay as
# placeholders for these checks.
NUMBER_SUBSTITUTIONS = ()


def rendered_numbers():
    text = DEFINITION
    for key in NUMBER_SUBSTITUTIONS:
        text = text.replace('${' + key + '}', SUBSTITUTIONS[key])
    return text


def walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def test_no_hardcoded_account_region_or_environment():
    assert re.search(r'\b\d{12}\b', DEFINITION) is None
    assert re.search(r'(us|eu|ap|ca|sa|me|af)-[a-z]+-\d', DEFINITION) is None
    assert '-prod"' not in DEFINITION


def test_substitutions_produce_valid_json_with_only_known_variables():
    variables = set(re.findall(r'\$\{(\w+)\}', DEFINITION))
    assert variables == set(SUBSTITUTIONS)
    rendered = DEFINITION
    for key, value in SUBSTITUTIONS.items():
        rendered = rendered.replace('${' + key + '}', value)
    json.loads(rendered)


def test_every_payload_with_a_role_passes_the_secret_arn_never_the_external_id():
    definition = json.loads(rendered_numbers())
    payloads = [n['Payload'] for n in walk(definition) if isinstance(n.get('Payload'), dict)]
    with_role = [p for p in payloads if 'crossAccountRoleArn.$' in p]
    assert len(with_role) == 24
    assert all(p.get('externalIdSecretArn.$') == '$.externalIdSecretArn' for p in with_role)
    assert 'externalId.$' not in DEFINITION and '$.externalId"' not in DEFINITION


def test_success_and_failure_paths_both_end_through_secret_cleanup():
    states = json.loads(rendered_numbers())['States']
    assert states['CleanupExternalIdSecret']['Parameters']['FunctionName'].startswith(
        'connect-migration-cleanup-external-id-')
    for terminal in ('MigrationComplete', 'HandleError'):
        assert states[terminal]['Next'] == 'CleanupExternalIdSecret' or \
            reaches(states, terminal, 'CleanupExternalIdSecret'), terminal


def reaches(states, start, target, seen=None):
    """True if every path from start reaches target (top level states only)."""
    seen = seen or set()
    if start == target:
        return True
    if start in seen or start not in states:
        return False
    seen.add(start)
    state = states[start]
    nexts = [state['Next']] if 'Next' in state else []
    nexts += [c['Next'] for c in state.get('Choices', [])] + ([state['Default']] if 'Default' in state else [])
    nexts += [c['Next'] for c in state.get('Catch', [])]
    if not nexts:
        return False
    return all(reaches(states, n, target, set(seen)) for n in nexts)


def test_every_function_name_is_environment_scoped():
    definition = json.loads(rendered_numbers())
    names = [n['FunctionName'] for n in walk(definition) if 'FunctionName' in n]
    resources = [n['Resource'] for n in walk(definition) if isinstance(n.get('Resource'), str)
                 and ':function:' in n['Resource']]
    assert names and all(name.endswith('-${Environment}') for name in names)
    assert all(r.startswith('arn:${Partition}:lambda:${Region}:${AccountId}:function:') for r in resources)


def test_every_arn_takes_the_partition_from_the_substitution():
    """No 'arn:aws:' literal: AWS GovCloud (US) needs arn:aws-us-gov (CloudFormation fills ${Partition})."""
    assert 'arn:aws:' not in DEFINITION
    definition = json.loads(rendered_numbers())
    resources = [n['Resource'] for n in walk(definition) if isinstance(n.get('Resource'), str)]
    assert resources and all(r.startswith('arn:${Partition}:') for r in resources)
    assert sum(r == 'arn:${Partition}:states:::lambda:invoke' for r in resources) == 48


def test_create_queues_gets_source_instance_for_flow_mapping():
    definition = json.loads(rendered_numbers())
    payload = next(n['Payload'] for n in walk(definition)
                   if n.get('FunctionName', '').startswith('connect-migration-create-queues-'))
    assert payload['sourceInstanceId.$'] == '$.sourceInstanceId'


POST_REPORT_STATES = {'BuildMigrationReport', 'BuildMigrationReportFailed', 'CleanupExternalIdSecret',
                      'CleanupExternalIdSecretFailed', 'FinalMigrationStatus', 'MigrationSucceeded', 'MigrationFailed',
                      'TargetVerificationErrored', 'TargetVerificationFailed'}
FAIL_STATES = {'MigrationFailed', 'TargetVerificationErrored', 'TargetVerificationFailed'}


def successors(state):
    nexts = [state['Next']] if 'Next' in state else []
    nexts += [c['Next'] for c in state.get('Choices', [])] + ([state['Default']] if 'Default' in state else [])
    return nexts + [c['Next'] for c in state.get('Catch', [])]


def test_every_path_cleans_up_then_builds_the_report_before_the_final_status():
    states = json.loads(rendered_numbers())['States']
    for name in states:
        if name not in POST_REPORT_STATES:
            assert reaches(states, name, 'CleanupExternalIdSecret'), name
    # The cleanup result feeds the report, even when the cleanup itself failed.
    for name in ('CleanupExternalIdSecret', 'CleanupExternalIdSecretFailed'):
        assert states[name]['Next'] == 'BuildMigrationReport' and 'End' not in states[name], name
    assert [c['Next'] for c in states['CleanupExternalIdSecret']['Catch']] == ['CleanupExternalIdSecretFailed']
    assert states['CleanupExternalIdSecretFailed']['Result'] == {'status': 'FAILED'}
    for name in ('BuildMigrationReport', 'BuildMigrationReportFailed'):
        assert states[name]['Next'] == 'FinalMigrationStatus', name


def test_execution_fails_unless_the_report_succeeded_and_the_secret_is_gone():
    states = json.loads(rendered_numbers())['States']
    terminal = {name for name, s in states.items() if s['Type'] in ('Succeed', 'Fail') or s.get('End')}
    assert terminal == {'MigrationSucceeded'} | FAIL_STATES
    assert all(states[name]['Type'] == 'Fail' for name in FAIL_STATES)
    final = states['FinalMigrationStatus']
    assert final['Default'] == 'MigrationFailed'
    choice = final['Choices'][0]
    assert choice['Next'] == 'MigrationSucceeded'
    report_rule, cleanup_rule = choice['And']
    assert report_rule == {'Variable': '$.migrationReport.status', 'StringEquals': 'SUCCEEDED'}
    assert {r['Variable'] for r in cleanup_rule['Or']} == {'$.externalIdSecretCleanup.status'}
    assert {r['StringEquals'] for r in cleanup_rule['Or']} == {'DELETED', 'ALREADY_DELETED', 'NOT_NEEDED'}
    # Every later choice only picks a more specific Fail state.
    assert {c['Next'] for c in final['Choices'][1:]} <= FAIL_STATES
    assert states['BuildMigrationReport']['ResultPath'] == '$.migrationReport'
    assert states['BuildMigrationReportFailed']['Result']['status'] == 'FAILED'
    assert states['HandleError']['Result']['fatalError'] is True
    assert states['MigrationComplete']['Result']['fatalError'] is False
    payload = states['BuildMigrationReport']['Parameters']['Payload']
    assert payload == {'executionArn.$': '$$.Execution.Id', 'fatalError.$': '$.migrationStatus.fatalError',
                       'externalIdSecretCleanup.$': '$.externalIdSecretCleanup.status'}


def choice_matches(rule, data):
    """Evaluate a Choice rule (And, Or, IsPresent, StringEquals) locally."""
    if 'And' in rule:
        return all(choice_matches(r, data) for r in rule['And'])
    if 'Or' in rule:
        return any(choice_matches(r, data) for r in rule['Or'])
    node, present = data, True
    for part in rule['Variable'][2:].split('.'):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            node, present = None, False
    if 'IsPresent' in rule:
        return present == rule['IsPresent']
    if not present:
        # Step Functions fails the execution (States.Runtime) on a missing path.
        raise AssertionError(f"missing path {rule['Variable']} without an IsPresent guard")
    return node == rule['StringEquals']


def final_outcome(report, cleanup, verdict=None):
    final = json.loads(rendered_numbers())['States']['FinalMigrationStatus']
    data = {'migrationReport': {'status': report}, 'externalIdSecretCleanup': {'status': cleanup}}
    if verdict is not None:
        data['verifyTarget'] = {'verdict': verdict}
    return next((c['Next'] for c in final['Choices'] if choice_matches(c, data)), final['Default'])


def test_final_status_choice_behaves_as_intended():
    """Evaluate the Choice rules locally for each report, cleanup and verification outcome."""
    for verdict in (None, 'PASS', 'PASS_WITH_FOLLOW_UPS'):
        for cleanup in ('DELETED', 'ALREADY_DELETED', 'NOT_NEEDED'):
            assert final_outcome('SUCCEEDED', cleanup, verdict) == 'MigrationSucceeded'
            assert final_outcome('FAILED', cleanup, verdict) == 'MigrationFailed'
        assert final_outcome('SUCCEEDED', 'FAILED', verdict) == 'MigrationFailed'


def test_verify_target_runs_after_the_migration_phases_and_before_the_secret_cleanup():
    states = json.loads(rendered_numbers())['States']
    assert states['MigrationComplete']['Next'] == 'VerifyTarget'
    verify = states['VerifyTarget']
    assert verify['Next'] == 'CleanupExternalIdSecret'
    assert verify['Parameters']['FunctionName'] == 'connect-migration-verify-target-${Environment}'
    assert verify['Parameters']['Payload'] == {
        'sourceInstanceId.$': '$.sourceInstanceId', 'targetInstanceId.$': '$.destinationInstanceId',
        'crossAccountRoleArn.$': '$.crossAccountRoleArn', 'externalIdSecretArn.$': '$.externalIdSecretArn',
        'features.$': '$.features', 'queueOptions.$': '$.queueOptions', 'userOptions.$': '$.userOptions'}
    assert verify['ResultSelector'] == {'verdict.$': '$.Payload.verdict'}
    assert verify['ResultPath'] == '$.verifyTarget'
    # Only the verify states and the fatal handler lead into the cleanup, so every
    # non fatal end of the migration phases passes VerifyTarget first.
    into_cleanup = {name for name, s in states.items() if 'CleanupExternalIdSecret' in successors(s)}
    assert into_cleanup == {'VerifyTarget', 'VerifyTargetFailed', 'HandleError'}
    into_verify = {name for name, s in states.items() if 'VerifyTarget' in successors(s)}
    assert into_verify == {'MigrationComplete'}


def test_a_verify_error_still_reaches_cleanup_and_the_report_and_ends_failed():
    states = json.loads(rendered_numbers())['States']
    (catch,) = states['VerifyTarget']['Catch']
    assert catch['ErrorEquals'] == ['States.ALL'] and catch['Next'] == 'VerifyTargetFailed'
    # The caught error (which may carry exception text) is overwritten by the verdict.
    assert catch['ResultPath'] == '$.verifyTarget'
    failed = states['VerifyTargetFailed']
    assert failed['Type'] == 'Pass' and failed['Result'] == {'verdict': 'ERROR'}
    assert failed['ResultPath'] == '$.verifyTarget' and failed['Next'] == 'CleanupExternalIdSecret'
    for start in ('VerifyTarget', 'VerifyTargetFailed'):
        assert reaches(states, start, 'CleanupExternalIdSecret')
        assert reaches(states, start, 'BuildMigrationReport')
        assert reaches(states, start, 'FinalMigrationStatus')
    # The report fails on an ERROR verdict (tests/test_verify_target.py); the run then ends Failed with the reason.
    for cleanup in ('DELETED', 'FAILED'):
        assert final_outcome('FAILED', cleanup, 'ERROR') == 'TargetVerificationErrored'
    assert states['TargetVerificationErrored']['Error'] == 'TargetVerificationError'


def test_a_verify_fail_ends_the_run_failed():
    states = json.loads(rendered_numbers())['States']
    for cleanup in ('DELETED', 'FAILED'):
        assert final_outcome('FAILED', cleanup, 'FAIL') == 'TargetVerificationFailed'
    assert final_outcome('FAILED', 'DELETED', 'PASS') == 'MigrationFailed'
    assert states['TargetVerificationFailed']['Type'] == 'Fail'


def test_a_failed_phase_does_not_skip_later_phases():
    states = json.loads(rendered_numbers())['States']
    users = ['ListSecurityProfiles', 'CreateSecurityProfiles', 'ListRoutingProfiles',
             'CreateRoutingProfiles', 'ListUsers', 'CreateUsers']
    queues = ['ListHoursOfOperations', 'CreateHoursOfOperations', 'ListQueues', 'CreateQueues']
    for name in users:
        assert [c['Next'] for c in states[name]['Catch']] == ['UsersPhaseError'], name
    for name in queues:
        assert [c['Next'] for c in states[name]['Catch']] == ['QueuesPhaseError'], name
    assert reaches(states, 'UsersPhaseError', 'DecideQuickConnectsMigration')
    assert reaches(states, 'QueuesPhaseError', 'DecideUserMigration')


def test_users_phase_inputs_are_seeded_when_earlier_phases_did_not_run():
    states = json.loads(rendered_numbers())['States']
    defaults = states['CheckMigrationOptions']['Result']
    assert states['CheckMigrationOptions']['ResultPath'] == '$.defaults'
    for key in ('createHoursResult', 'createQueuesResult', 'phase1Results'):
        assert key in defaults, key
    # Only the seeding chain enters DecideUserMigration, and the chain seeds every key it reads.
    entries = {name for name, s in states.items() if 'DecideUserMigration' in successors(s)}
    assert entries == {'EnsurePhase1Result', 'SeedPhase1Result'}
    seeded = {s['ResultPath'] for name, s in states.items() if name.startswith('Seed')}
    assert seeded == {'$.createHoursResult', '$.createQueuesResult', '$.phase1Results'}
    for name, s in states.items():
        if name.startswith('Seed'):
            assert s['InputPath'] == '$.defaults.' + s['ResultPath'][2:], name
    for name in ('DecideQueueMigration', 'CreateQueues', 'QueuesPhaseError'):
        assert 'EnsureHoursResult' in successors(states[name]), name
