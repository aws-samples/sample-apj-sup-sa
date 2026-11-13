"""Backstop for the ExternalId secret: execution timeout plus an EventBridge rule (local only)."""
import json
import pathlib
import re
import subprocess

import pytest
from cfnlint.decode import cfn_yaml

import cleanup_external_id
from region_utils import MigrationStepError

ROOT = pathlib.Path(__file__).resolve().parent.parent
PREFIX = 'connect-migration/test/'
STATE_MACHINE_ARN = 'arn:aws:states:eu-west-1:111111111111:stateMachine:connect-user-migration-test'


def plain(node):
    return json.loads(json.dumps(node))


def stack4():
    return plain(cfn_yaml.load(str(ROOT / 'cloudformation' / '4-step-functions.yaml')))


def test_state_machine_timeout_is_a_parameter_with_default_3600():
    template = stack4()
    param = template['Parameters']['TimeoutSeconds']
    assert param['Type'] == 'Number' and param['Default'] == 3600
    props = template['Resources']['MigrationStateMachine']['Properties']
    # A substitution can only produce a string; the timeout is written into the packaged copy instead.
    assert 'TimeoutSeconds' not in props['DefinitionSubstitutions']
    definition = json.loads((ROOT / 'step-functions' / 'migration-state-machine.json').read_text())
    assert definition['TimeoutSeconds'] == param['Default']
    deploy = (ROOT / 'scripts' / 'deploy.sh').read_text()
    assert f'DEFAULT_TIMEOUT_SECONDS="{param["Default"]}"' in deploy


def test_definition_file_is_valid_json_as_stored():
    text = (ROOT / 'step-functions' / 'migration-state-machine.json').read_text()
    definition = json.loads(text)
    assert isinstance(definition['TimeoutSeconds'], int)
    assert '${TimeoutSeconds}' not in text


def render(tmp_path, seconds):
    out = tmp_path / 'step-functions' / 'migration-state-machine.json'
    proc = subprocess.run(['bash', str(ROOT / 'scripts' / 'render-state-machine.sh'), str(seconds), str(out)],
                          capture_output=True, text=True)
    return proc, out


@pytest.mark.parametrize('seconds', [60, 7200, 31536000])
def test_render_writes_the_timeout_as_a_json_number(tmp_path, seconds):
    proc, out = render(tmp_path, seconds)
    assert proc.returncode == 0, proc.stderr
    rendered = json.loads(out.read_text())
    original = json.loads((ROOT / 'step-functions' / 'migration-state-machine.json').read_text())
    assert rendered['TimeoutSeconds'] == seconds
    assert {**rendered, 'TimeoutSeconds': None} == {**original, 'TimeoutSeconds': None}


@pytest.mark.parametrize('seconds', ['59', '31536001', 'abc', '3600; rm', '', '0360'])
def test_render_rejects_bad_values(tmp_path, seconds):
    proc, out = render(tmp_path, seconds)
    assert proc.returncode != 0 and not out.exists()


def test_deploy_packages_the_rendered_copy_and_passes_the_same_value():
    deploy = (ROOT / 'scripts' / 'deploy.sh').read_text()
    assert 'scripts/render-state-machine.sh "$SFN_TIMEOUT_SECONDS" "$BUILD_DIR/step-functions/' in deploy
    assert '--template-file "$BUILD_DIR/cloudformation/4-step-functions.yaml"' in deploy
    assert '"TimeoutSeconds=${SFN_TIMEOUT_SECONDS}"' in deploy
    template = (ROOT / 'cloudformation' / '4-step-functions.yaml').read_text()
    # The copied template resolves this relative path to the rendered copy in the build directory.
    assert 'DefinitionS3Location: ../step-functions/migration-state-machine.json' in template


def test_backstop_rule_matches_only_this_state_machine_and_terminal_failures():
    resources = stack4()['Resources']
    rule = resources['ExternalIdSecretBackstopRule']['Properties']
    pattern = rule['EventPattern']
    assert pattern['source'] == ['aws.states']
    assert pattern['detail-type'] == ['Step Functions Execution Status Change']
    assert pattern['detail']['stateMachineArn'] == [{'Ref': 'MigrationStateMachine'}]
    assert sorted(pattern['detail']['status']) == ['ABORTED', 'FAILED', 'TIMED_OUT']
    (target,) = rule['Targets']
    assert target['Arn'] == {'Fn::Sub': 'arn:${AWS::Partition}:lambda:${AWS::Region}:${AWS::AccountId}:function:'
                                        'connect-migration-cleanup-external-id-${Environment}'}


def test_backstop_permission_is_invoke_only_and_scoped_to_the_rule():
    resources = stack4()['Resources']
    permissions = [r['Properties'] for r in resources.values() if r['Type'] == 'AWS::Lambda::Permission']
    assert permissions == [{
        'Action': 'lambda:InvokeFunction',
        'FunctionName': {'Fn::Sub': 'connect-migration-cleanup-external-id-${Environment}'},
        'Principal': 'events.amazonaws.com',
        'SourceAccount': {'Ref': 'AWS::AccountId'},
        'SourceArn': {'Fn::GetAtt': ['ExternalIdSecretBackstopRule', 'Arn']},
    }]


def test_cleanup_function_knows_its_state_machine_and_prefix():
    template = plain(cfn_yaml.load(str(ROOT / 'cloudformation' / '3-lambda-functions.yaml')))
    variables = template['Resources']['CleanupExternalIdFunction']['Properties']['Environment']['Variables']
    assert variables['STATE_MACHINE_ARN'] == {'Fn::Sub': 'arn:${AWS::Partition}:states:${AWS::Region}:'
                                                         '${AWS::AccountId}:stateMachine:connect-user-migration-'
                                                         '${Environment}'}
    assert variables['EXTERNAL_ID_SECRET_PREFIX'] == {'Fn::Sub': 'connect-migration/${Environment}/'}
    # DeleteSecret under the prefix, plus SendMessage on the function's own dead letter queue.
    (policy,) = template['Resources']['CleanupExternalIdRole']['Properties']['Policies']
    delete, send = policy['PolicyDocument']['Statement']
    assert delete['Action'] == 'secretsmanager:DeleteSecret'
    assert send == {'Sid': 'SendToCleanupFailureQueue', 'Effect': 'Allow', 'Action': 'sqs:SendMessage',
                    'Resource': {'Fn::GetAtt': ['CleanupExternalIdFailureQueue', 'Arn']}}


# ---------------------------------------------------------------------------
# Cleanup failure queue (the cleanup function is the only one invoked asynchronously):
# a backstop delete that keeps failing, or an event EventBridge cannot deliver,
# lands in one encrypted, TLS only queue with an alarm instead of being dropped.
# ---------------------------------------------------------------------------

def stack3():
    return plain(cfn_yaml.load(str(ROOT / 'cloudformation' / '3-lambda-functions.yaml')))


def queue_name_sub():
    return stack3()['Resources']['CleanupExternalIdFailureQueue']['Properties']['QueueName']['Fn::Sub']


def test_failure_queue_is_sse_sqs_encrypted_with_a_14_day_retention_parameter():
    template = stack3()
    queue = template['Resources']['CleanupExternalIdFailureQueue']
    assert queue['Type'] == 'AWS::SQS::Queue'
    props = queue['Properties']
    assert props['SqsManagedSseEnabled'] is True and 'KmsMasterKeyId' not in props
    assert props['MessageRetentionPeriod'] == {'Ref': 'CleanupFailureRetentionSeconds'}
    param = template['Parameters']['CleanupFailureRetentionSeconds']
    assert param['Type'] == 'Number' and param['Default'] == 14 * 24 * 3600 and param['MaxValue'] == 1209600
    assert queue_name_sub() == 'connect-migration-cleanup-external-id-failures-${Environment}'


def test_failure_queue_policy_denies_non_tls_and_lets_only_the_backstop_rule_send():
    resources = stack3()['Resources']
    policies = [r['Properties'] for r in resources.values() if r['Type'] == 'AWS::SQS::QueuePolicy']
    (policy,) = policies
    assert policy['Queues'] == [{'Ref': 'CleanupExternalIdFailureQueue'}]
    deny, allow = policy['PolicyDocument']['Statement']
    queue_arn = {'Fn::GetAtt': ['CleanupExternalIdFailureQueue', 'Arn']}
    assert deny['Sid'] == 'DenyInsecureTransport' and deny['Effect'] == 'Deny' and deny['Principal'] == '*'
    assert deny['Condition'] == {'Bool': {'aws:SecureTransport': False}} and deny['Resource'] == queue_arn
    assert {'sqs:SendMessage', 'sqs:ReceiveMessage', 'sqs:DeleteMessage'} <= set(deny['Action'])
    assert not any('*' in action for action in deny['Action'])
    rule_name = stack4()['Resources']['ExternalIdSecretBackstopRule']['Properties']['Name']['Fn::Sub']
    assert allow == {
        'Sid': 'BackstopRuleDeadLetters', 'Effect': 'Allow', 'Principal': {'Service': 'events.amazonaws.com'},
        'Action': 'sqs:SendMessage', 'Resource': queue_arn,
        'Condition': {'ArnEquals': {'aws:SourceArn': {'Fn::Sub': 'arn:${AWS::Partition}:events:${AWS::Region}:'
                                                                 '${AWS::AccountId}:rule/' + rule_name}}}}
    assert rule_name == 'connect-migration-secret-backstop-${Environment}'


def test_cleanup_function_failure_path_is_the_queue_with_two_retries():
    resources = stack3()['Resources']
    function = resources['CleanupExternalIdFunction']['Properties']
    assert function['DeadLetterConfig'] == {'TargetArn': {'Fn::GetAtt': ['CleanupExternalIdFailureQueue', 'Arn']}}
    configs = [r['Properties'] for r in resources.values() if r['Type'] == 'AWS::Lambda::EventInvokeConfig']
    assert configs == [{'FunctionName': {'Ref': 'CleanupExternalIdFunction'}, 'Qualifier': '$LATEST',
                        'MaximumRetryAttempts': 2}]


def test_backstop_rule_target_has_retries_and_the_same_dead_letter_queue():
    (target,) = stack4()['Resources']['ExternalIdSecretBackstopRule']['Properties']['Targets']
    assert target['RetryPolicy'] == {'MaximumRetryAttempts': 10, 'MaximumEventAgeInSeconds': 3600}
    assert target['DeadLetterConfig'] == {'Arn': {'Fn::Sub': 'arn:${AWS::Partition}:sqs:${AWS::Region}:'
                                                            '${AWS::AccountId}:' + queue_name_sub()}}


def test_only_the_cleanup_role_and_the_rule_can_send_to_any_queue():
    grants = []
    for path in (ROOT / 'cloudformation').glob('*.yaml'):
        for logical, resource in plain(cfn_yaml.load(str(path)))['Resources'].items():
            if resource['Type'] not in ('AWS::IAM::Role', 'AWS::IAM::Policy', 'AWS::IAM::ManagedPolicy'):
                continue
            # Statements can be wrapped in Fn::If, so search the serialized properties.
            grants += [(logical, a) for a in re.findall(r'sqs:[A-Za-z*]+', json.dumps(resource['Properties']))]
    assert grants == [('CleanupExternalIdRole', 'sqs:SendMessage')]


def test_failure_alarm_fires_on_any_visible_message_and_notifies_only_when_a_topic_is_set():
    template = stack3()
    assert template['Parameters']['CleanupFailureAlarmTopicArn']['Default'] == ''
    assert template['Conditions']['HasCleanupFailureAlarmTopic'] == {
        'Fn::Not': [{'Fn::Equals': [{'Ref': 'CleanupFailureAlarmTopicArn'}, '']}]}
    alarm = template['Resources']['CleanupExternalIdFailureAlarm']['Properties']
    assert (alarm['Namespace'], alarm['MetricName']) == ('AWS/SQS', 'ApproximateNumberOfMessagesVisible')
    assert alarm['Dimensions'] == [{'Name': 'QueueName',
                                    'Value': {'Fn::GetAtt': ['CleanupExternalIdFailureQueue', 'QueueName']}}]
    assert (alarm['Threshold'], alarm['ComparisonOperator']) == (0, 'GreaterThanThreshold')
    assert alarm['TreatMissingData'] == 'notBreaching'
    assert alarm['AlarmActions'] == {'Fn::If': ['HasCleanupFailureAlarmTopic',
                                                [{'Ref': 'CleanupFailureAlarmTopicArn'}], {'Ref': 'AWS::NoValue'}]}


def status_event(status, name='web-20261108-101500-abcd1234', state_machine=STATE_MACHINE_ARN):
    return {'version': '0', 'source': 'aws.states', 'detail-type': 'Step Functions Execution Status Change',
            'detail': {'executionArn': state_machine.replace(':stateMachine:', ':execution:') + ':' + name,
                       'stateMachineArn': state_machine, 'name': name, 'status': status}}


@pytest.fixture
def secrets(stubbed, monkeypatch):
    client, stub = stubbed('secretsmanager', 'eu-west-1')
    monkeypatch.setattr(cleanup_external_id.boto3, 'client', lambda *a, **k: client)
    monkeypatch.setenv('EXTERNAL_ID_SECRET_PREFIX', PREFIX)
    monkeypatch.setenv('STATE_MACHINE_ARN', STATE_MACHINE_ARN)
    return stub


@pytest.mark.parametrize('status', ['ABORTED', 'TIMED_OUT', 'FAILED'])
def test_backstop_force_deletes_the_secret_named_after_the_execution(secrets, status, capsys):
    secrets.add_response('delete_secret', {'Name': 'x'},
                         {'SecretId': PREFIX + 'web-20261108-101500-abcd1234', 'ForceDeleteWithoutRecovery': True})
    assert cleanup_external_id.lambda_handler(status_event(status), None) == {'status': 'DELETED'}
    assert status in capsys.readouterr().out


def test_backstop_is_idempotent(secrets):
    secrets.add_client_error('delete_secret', service_error_code='ResourceNotFoundException', http_status_code=400)
    assert cleanup_external_id.lambda_handler(status_event('FAILED'), None) == {'status': 'ALREADY_DELETED'}


def test_backstop_raises_other_errors_so_lambda_retries(secrets, capsys):
    secrets.add_client_error('delete_secret', service_error_code='InternalServiceError', http_status_code=500,
                             service_message='Internal failure for jane.doe@example.com')
    with pytest.raises(MigrationStepError) as info:
        cleanup_external_id.lambda_handler(status_event('TIMED_OUT'), None)
    # Lambda copies this text into the execution history, the log and the dead letter queue message:
    # type, code and action only (the secret is found from detail.name in the message body).
    assert str(info.value) == 'MigrationStepError: error code InternalServiceError, action DeleteSecret'
    assert info.value.code == 'InternalServiceError' and info.value.action == 'DeleteSecret'
    assert PREFIX not in str(info.value)
    assert info.value.__cause__ is None and info.value.__suppress_context__
    assert 'jane.doe' not in capsys.readouterr().out


def test_backstop_ignores_other_state_machines_statuses_and_unsafe_names(secrets):
    other = STATE_MACHINE_ARN.replace('-test', '-other')
    assert cleanup_external_id.lambda_handler(status_event('FAILED', state_machine=other), None)['status'] == 'IGNORED'
    assert cleanup_external_id.lambda_handler(status_event('SUCCEEDED'), None) == {'status': 'NOT_NEEDED'}
    assert cleanup_external_id.lambda_handler(status_event('RUNNING'), None) == {'status': 'NOT_NEEDED'}
    for name in ('../db', 'a/b', '', 'x' * 81):
        assert cleanup_external_id.lambda_handler(status_event('ABORTED', name=name), None) == {
            'status': 'FAILED', 'errorCode': 'InvalidExecutionName'}
