"""Every state machine handler re-raises unexpected errors sanitized (no AWS).

Step Functions copies a Lambda function error (errorType, errorMessage and
stackTrace) into the execution history as the Catch Cause. These tests fail
if a state machine handler is not wrapped by region_utils.step_handler, if
the error name changes (Retry and Catch match on it), or if an AWS message
reaches the history output.
"""
import importlib
import json
import pathlib
import re
import sys
import traceback

import pytest
from botocore.exceptions import ClientError
from cfnlint.decode import cfn_yaml

import list_phone_numbers
import region_utils
from conftest import SOURCE_INSTANCE_ARN, SOURCE_INSTANCE_ID
from test_state_machine import rendered_numbers, walk

ROOT = pathlib.Path(__file__).resolve().parent.parent
FAKE_EMAIL, FAKE_NAME = 'jane.doe@example.com', 'Jane Doe'
MESSAGE = f'User {FAKE_NAME} ({FAKE_EMAIL}) is not valid for queue Sales queue'


def state_machine_handler_modules():
    called = set(re.findall(r'(connect-migration-[a-z0-9-]+-\$\{Environment\})',
                            (ROOT / 'step-functions' / 'migration-state-machine.json').read_text()))
    modules = {}
    for path in (ROOT / 'cloudformation').glob('*.yaml'):
        for resource in cfn_yaml.load(str(path)).get('Resources', {}).values():
            if resource.get('Type') == 'AWS::Lambda::Function':
                props = resource['Properties']
                if props['FunctionName']['Fn::Sub'] in called:
                    module, function = props['Handler'].split('.')
                    modules[module] = function
    assert len(called) == len(modules) == 49
    return modules


@pytest.mark.parametrize('module, function', sorted(state_machine_handler_modules().items()))
def test_every_state_machine_handler_is_wrapped(module, function):
    handler = getattr(importlib.import_module(module), function)
    assert getattr(handler, 'sanitizes_errors', False) is True, f'{module}.{function} is not @step_handler'
    source = (ROOT / 'lambda' / f'{module}.py').read_text()
    assert f'@step_handler\ndef {function}(' in source


def test_retry_and_catch_error_names_still_match():
    """Retry matches Lambda service errors and Catch matches States.ALL; neither depends on the message.

    A name that ever matches a function error must be the original class name, which the sanitized
    error keeps.
    """
    names = {name for node in walk(json.loads(rendered_numbers())) if 'ErrorEquals' in node
             for name in node['ErrorEquals']}
    assert names == {'States.ALL', 'Lambda.ServiceException', 'Lambda.AWSLambdaException',
                     'Lambda.SdkClientException', 'Lambda.TooManyRequestsException'}
    for err in (ClientError({'Error': {'Code': 'X', 'Message': MESSAGE}}, 'ListQueues'),
                region_utils.MigrationStepError(MESSAGE, code='AccessDeniedException', action='ListQueues'),
                KeyError(MESSAGE), ValueError(MESSAGE)):
        clean = region_utils.sanitized_error(err)
        assert type(clean).__name__ == type(err).__name__ and isinstance(clean, type(err))
        assert MESSAGE not in str(clean) and FAKE_EMAIL not in repr(clean)


def lambda_runtime_error(handler, event):
    """Invoke like the Lambda Python runtime and build the Catch Cause Step Functions records."""
    try:
        handler(event, None)
    except Exception:
        etype, value, tb = sys.exc_info()
        frames = traceback.format_list(traceback.extract_tb(tb))
        return value, json.dumps({'errorMessage': str(value), 'errorType': etype.__name__, 'stackTrace': frames})
    raise AssertionError('the handler did not raise')


def test_a_client_error_reaches_the_history_without_its_message(stubbed, monkeypatch, capsys):
    client, stub = stubbed('connect', 'us-east-1')
    stub.add_client_error('list_phone_numbers_v2', service_error_code='InvalidParameterException',
                          service_message=MESSAGE, http_status_code=400)
    monkeypatch.setattr(list_phone_numbers, 'create_connect_client_from_arn',
                        lambda arn: (client, SOURCE_INSTANCE_ID, 'us-east-1'))
    err, cause = lambda_runtime_error(list_phone_numbers.lambda_handler, {'sourceInstanceId': SOURCE_INSTANCE_ARN})
    # botocore raises the modeled class; its name (what Retry and Catch see) is unchanged.
    assert json.loads(cause)['errorType'] == 'InvalidParameterException'
    assert json.loads(cause)['errorMessage'] == ('InvalidParameterException: error code InvalidParameterException, '
                                                 'action ListPhoneNumbersV2')
    assert isinstance(err, ClientError) and region_utils.error_code(err) == 'InvalidParameterException'
    assert err.__cause__ is None and err.__suppress_context__
    for text in (cause, ''.join(traceback.format_exception(err)), capsys.readouterr().out):
        for value in (FAKE_EMAIL, FAKE_NAME, 'Sales queue', 'not valid'):
            assert value not in text, value


def test_a_fatal_error_keeps_its_code_and_action_but_not_the_resource(monkeypatch):
    def failing(arn):
        raise region_utils.fatal_error(ClientError({'Error': {'Code': 'AccessDeniedException', 'Message': MESSAGE}},
                                                   'ListPhoneNumbersV2'), resource=f'queue {FAKE_NAME}')

    monkeypatch.setattr(list_phone_numbers, 'create_connect_client_from_arn', failing)
    err, cause = lambda_runtime_error(list_phone_numbers.lambda_handler, {'sourceInstanceId': SOURCE_INSTANCE_ARN})
    assert json.loads(cause)['errorType'] == 'MigrationStepError' and isinstance(err, region_utils.MigrationStepError)
    assert (err.code, err.action, err.resource) == ('AccessDeniedException', 'ListPhoneNumbersV2', None)
    assert FAKE_NAME not in cause and FAKE_EMAIL not in cause


def test_results_pass_through_unchanged():
    @region_utils.step_handler
    def handler(event, context):
        return {'statusCode': 200, 'echo': event}

    assert handler({'a': 1}, None) == {'statusCode': 200, 'echo': {'a': 1}}
