"""Log hygiene for lambda/*.py (static check plus behaviour checks, no AWS).

Policy: print() may only interpolate resource IDs, ARNs, Regions, counts,
statuses, types, action names and error codes. Anything else (names,
usernames, emails, phone numbers, attribute or data table values, payloads,
exception messages) must go through region_utils.redact() or
region_utils.describe_error().
"""
import ast
import json
import pathlib
import re

import pytest
from botocore.exceptions import ClientError

import api_list_phone_numbers
import api_start_migration
import check_flow_dependencies
import list_queues
import region_utils
from conftest import (
    EXTERNAL_ID,
    SOURCE_INSTANCE_ARN,
    SOURCE_INSTANCE_ID,
    SOURCE_REGION,
    TARGET_INSTANCE_ARN,
    TARGET_ROLE_ARN,
)

LAMBDA = pathlib.Path(__file__).resolve().parent.parent / 'lambda'

SAFE_IDENTIFIER = re.compile(
    r'(?:^|_)(?:id|ids|arn|arns|region|regions|count|code|codes|status|state|type|offset|idx|version|level)$'
    r'|(?:Id|Ids|Arn|Region|Status|Type|Code)$'
    r'|^(?:is|has|migrate|include|total|start|end)_'
)
SAFE_NAMES = {
    'quota_code', 'model_id', 'mapped_model', 'language_code', 'list_func', 'resource_type',
    'temp_bucket', 'bucket_name', 's3_uri', 'temp_file_path', 'prompt_field', 'guardrail_field',
    'agent_type', 'destination_arn', 'instance_id', 'region', 'offset', 'attributes_created',
    'values_created', 'alias_arn', 'lambda_arn', 'name',  # 'name' is the execution name in api_start_migration
    'caller_sub',  # Cognito subject, a pseudonymous ID
    'action',    # an AWS operation name (MigrationStepError.action, fatal_error)
    'warning',   # check_flow_dependencies: fixed text plus an error code
    'boto3.__version__',
}
SAFE_CALLS = {'len', 'redact', 'describe_error', 'error_code', 'error_code_of', 'error_text',
              'http_status'}  # verify_migration.http_status: an HTTP status number for the error
SAFE_STRING_KEYS = re.compile(
    r'(?:Id|Arn|Status|Type|type|Code|LanguageCode|ValueType|Primary|TimeZone|ValueLockLevel|matched|missing)$')
# Reviewed by hand: counts per mapping kind (the keys are fixed kind names such as 'queues').
SAFE_EXACT = {
    "'Mappings - ' + ', '.join((f'{k}: {len(v)}' for k, v in id_maps.items())) + "
    "f', lambdas: {len(lambda_map)}, lex: {len(lex_map)}'",
}


def terminal_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def is_safe(node):
    if isinstance(node, ast.Constant):
        return True
    source = ast.unparse(node)
    if source in SAFE_EXACT or source in SAFE_NAMES:
        return True
    if isinstance(node, ast.JoinedStr):
        return all(is_safe(v.value) for v in node.values if isinstance(v, ast.FormattedValue))
    if isinstance(node, ast.Call):
        func = terminal_name(node.func)
        if func in SAFE_CALLS:
            return True
        # x.get('SomeId'), x.get('SomeId', default)
        if func == 'get' and node.args and isinstance(node.args[0], ast.Constant) \
                and SAFE_STRING_KEYS.search(str(node.args[0].value)):
            return True
        return False
    if isinstance(node, ast.Subscript):
        key = node.slice
        if isinstance(key, ast.Constant) and SAFE_STRING_KEYS.search(str(key.value)):
            return True
        return False
    name = terminal_name(node)
    return bool(name and (SAFE_IDENTIFIER.search(name) or name in SAFE_NAMES))


def print_expressions(tree):
    """Yield (print_call, expression_node) for every non constant value a print() emits."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, 'id', None) == 'print':
            for arg in node.args:
                parts = [v.value for v in arg.values if isinstance(v, ast.FormattedValue)] \
                    if isinstance(arg, ast.JoinedStr) else [arg]
                for part in parts:
                    if not isinstance(part, ast.Constant):
                        yield node, part


def unsafe_log_expressions():
    found = []
    for path in sorted(LAMBDA.glob('*.py')):
        tree = ast.parse(path.read_text())
        for call, expr in print_expressions(tree):
            if not is_safe(expr):
                found.append(f'{path.name}:{call.lineno}: {ast.unparse(expr)}')
    return found


def test_every_logged_value_is_an_id_count_or_code():
    assert unsafe_log_expressions() == []


def test_classifier_rejects_personal_data_and_accepts_ids():
    def check(src):
        return is_safe(ast.parse(src, mode='eval').body)
    for bad in ('username', 'user["Username"]', 'pn.get("PhoneNumber")', 'str(e)', 'attr', 'qc', 'flow_name',
                'json.dumps(table)', 'email', 'caller'):
        assert not check(bad), bad
    for good in ('user_id', 'pn["PhoneNumberId"]', 'len(users)', 'describe_error(e)', 'redact(name_of_thing)',
                 'destination_region', 'q.get("QueueId")'):
        assert check(good), good


# ---------------------------------------------------------------------------
# Step output hygiene: a step's return payload becomes Step Functions execution
# history, so the same policy applies to it as to logs. Result entries carry
# resource IDs, ARNs, statuses and error codes, never resource names, usernames,
# phone numbers or exception text. Covers the create, associate and verify steps
# (verify_migration answers the UI with the same verify_target result).
# ---------------------------------------------------------------------------

STEP_FILES = sorted(p for p in LAMBDA.glob('*.py') if p.name.startswith(('create_', 'associate_', 'verify_')))
NAME_KEYS = {'name', 'Name', 'username', 'Username', 'phoneNumber', 'PhoneNumber', 'displayName', 'firstName',
             'lastName', 'email', 'Email', 'label'}
NAME_VALUE_KEYS = {'Name', 'Username', 'PhoneNumber', 'name', 'username', 'Email'}
# Resources whose only identifier is their name (no ID or ARN exists), reviewed by hand.
NAME_IS_THE_ID = {
    ('create_predefined_attributes.py', 'failure'),  # predefined attributes are addressed by name only
}


def _names_value(node):
    """True if node reads a name like value: x['Name'], x.get('Username'), or a *_name / username variable."""
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
        return node.slice.value in NAME_VALUE_KEYS
    if isinstance(node, ast.Call) and terminal_name(node.func) == 'get' and node.args \
            and isinstance(node.args[0], ast.Constant):
        return node.args[0].value in NAME_VALUE_KEYS
    name = terminal_name(node)
    return bool(name) and (name.endswith('_name') or name in ('name', 'username', 'label', 'flow_name'))


def _exception_names(tree):
    return {h.name for h in ast.walk(tree) if isinstance(h, ast.ExceptHandler) and h.name}


SAFE_EXCEPTION_ATTRIBUTES = {'code', 'action', 'resource', 'operation_name'}


def _carries_exception_text(node, exception_names):
    """True if node formats an exception (str(e), f'{e}', bare e) outside describe_error() and friends.

    e.code, e.action, e.resource and e.operation_name are an error code, an
    action name and a resource ID, so reading them is allowed.
    """
    if isinstance(node, ast.Call) and terminal_name(node.func) in SAFE_CALLS:
        return False
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in exception_names \
            and node.attr in SAFE_EXCEPTION_ATTRIBUTES:
        return False
    if isinstance(node, ast.Compare):  # `err is not None` and the like emit no text
        return False
    if isinstance(node, ast.Call) and terminal_name(node.func) == 'str' and node.args:
        return True
    if isinstance(node, ast.Name) and node.id in exception_names:
        return True
    return any(_carries_exception_text(child, exception_names) for child in ast.iter_child_nodes(node))


def _output_dicts(tree):
    """Dict literals that end up in a step result: .append({...}) arguments and dicts in return values."""
    seen = set()
    for node in ast.walk(tree):
        found = []
        if isinstance(node, ast.Call) and terminal_name(node.func) == 'append' and node.args \
                and isinstance(node.args[0], ast.Dict):
            found.append(node.args[0])
        if isinstance(node, ast.Return) and node.value is not None:
            found += [d for d in ast.walk(node.value) if isinstance(d, ast.Dict)]
        for d in found:
            if id(d) not in seen:
                seen.add(id(d))
                yield d


def step_output_offenders():
    offenders = []
    for path in STEP_FILES:
        tree = ast.parse(path.read_text())
        exceptions = _exception_names(tree)
        for d in _output_dicts(tree):
            for key, value in zip(d.keys, d.values):
                if not isinstance(key, ast.Constant):
                    continue
                where = f'{path.name}:{key.lineno}'
                if key.value in NAME_KEYS and (path.name, key.value) not in NAME_IS_THE_ID:
                    offenders.append(f'{where}: name key {key.value!r}')
                elif key.value in ('originalId', 'newId', 'existingId', 'id') and _names_value(value):
                    offenders.append(f'{where}: {key.value!r} holds a name: {ast.unparse(value)}')
                elif key.value in ('error', 'reason', 'message') and _carries_exception_text(value, exceptions):
                    offenders.append(f'{where}: exception text in {key.value!r}: {ast.unparse(value)}')
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = terminal_name(node.func)
            if func == 'append' and not isinstance(node.args[0], ast.Dict) and _names_value(node.args[0]):
                offenders.append(f'{path.name}:{node.lineno}: appends a name: {ast.unparse(node.args[0])}')
            if func == 'failure_entry' and _names_value(node.args[0]) \
                    and (path.name, 'failure') not in NAME_IS_THE_ID:
                offenders.append(f'{path.name}:{node.lineno}: failure entry keyed by a name')
            if func == 'merge_follow_up' and len(node.args) > 1 and _names_value(node.args[1]):
                offenders.append(f'{path.name}:{node.lineno}: follow up keyed by a name')
    return offenders


def test_step_outputs_carry_ids_not_names_or_exception_text():
    assert step_output_offenders() == []


def test_step_output_check_catches_names_and_exception_text(tmp_path, monkeypatch):
    bad = tmp_path / 'create_example.py'
    bad.write_text(
        "def lambda_handler(event, context):\n"
        "    created, failed = [], []\n"
        "    for item in event['items']:\n"
        "        try:\n"
        "            created.append(item['Name'])\n"
        "            created.append({'originalId': item['Id'], 'name': item['Name']})\n"
        "            created.append({'originalId': flow_name})\n"
        "        except Exception as e:\n"
        "            failed.append({'originalId': item['Id'], 'error': str(e), 'errorCode': 'X'})\n"
        "            failed.append({'originalId': item['Id'], 'error': f'boom {e}', 'errorCode': 'X'})\n"
        "    return {'created': created, 'failed': failed, 'username': event['u']}\n")
    monkeypatch.setattr(__import__(__name__), 'STEP_FILES', [bad])
    found = step_output_offenders()
    assert len(found) == 6, found


# ---------------------------------------------------------------------------
# AWS error messages: a ClientError's Message can
# echo request values, so no Lambda reads it, and no log line, API response or
# step output carries exception text. Only the error code, the action name and
# the resource ID are kept (describe_error, error_code_of, error_text).
# ---------------------------------------------------------------------------

ALL_LAMBDA_FILES = sorted(LAMBDA.glob('*.py'))
SENSITIVE_MESSAGE = 'User jane.doe@example.com with phone +15555550123 is not authorized: policy p-SECRETVALUE'
# Calls whose arguments are logged, returned to the UI or become a step output or exception text.
EMITTING_CALLS = {'print', 'response', 'MigrationStepError', 'ArnFormatError', 'dumps'}
# region_utils helpers that turn an exception into its code and operation (reviewed by hand).
REVIEWED_HELPERS = {'error_code', 'describe_error', 'error_code_of', 'failure_entry', 'error_text',
                    'sanitized_error'}  # sanitized_error: tests/test_step_errors.py proves it drops the text


def _reads_error_message(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and node.slice.value == 'Message':
            yield node
        if isinstance(node, ast.Call) and terminal_name(node.func) == 'get' and node.args \
                and isinstance(node.args[0], ast.Constant) and node.args[0].value == 'Message':
            yield node


def exception_text_offenders(paths):
    """Places that log, return or wrap exception text (str(e), f'{e}', bare e, e.response['Error']['Message']).

    The bodies of the reviewed helpers in region_utils (REVIEWED_HELPERS) are
    skipped: they turn an exception into its code and operation.
    """
    offenders = []
    for path in paths:
        tree = ast.parse(path.read_text())
        exceptions = _exception_names(tree)
        if path.name == 'region_utils.py':
            exceptions.add('err')  # the helpers there take the exception as a parameter named err
        for node in _reads_error_message(tree):
            offenders.append(f'{path.name}:{node.lineno}: reads the AWS error Message')
        skipped = {id(n) for f in ast.walk(tree) if isinstance(f, ast.FunctionDef) and f.name in REVIEWED_HELPERS
                   and path.name == 'region_utils.py' for n in ast.walk(f)}
        for node in ast.walk(tree):
            if id(node) in skipped:
                continue
            values = []
            if isinstance(node, ast.Return) and node.value is not None:
                values.append(node.value)
            elif isinstance(node, ast.Dict):
                values.extend(v for v in node.values if v is not None)
            elif isinstance(node, ast.Call) and terminal_name(node.func) in EMITTING_CALLS:
                values.extend(node.args)
                values.extend(k.value for k in node.keywords)
            for value in values:
                if _carries_exception_text(value, exceptions):
                    offenders.append(f'{path.name}:{node.lineno}: exception text: {ast.unparse(value)}')
    return sorted(set(offenders))


def test_no_lambda_logs_or_returns_exception_text():
    assert exception_text_offenders(ALL_LAMBDA_FILES) == []


def test_exception_text_check_catches_the_scan_4_patterns(tmp_path):
    bad = tmp_path / 'region_example.py'
    bad.write_text(
        "def fatal_error(err):\n"
        "    message = err.response.get('Error', {}).get('Message', '')\n"
        "    raise MigrationStepError(f'AccessDenied: {err}')\n"
        "\n"
        "def handler(event, context):\n"
        "    try:\n"
        "        pass\n"
        "    except Exception as e:\n"
        "        print(f'failed: {e}')\n"
        "        error_response = {'error': str(e)}\n"
        "        return None, str(e)\n"
        "    except ValueError as err:\n"
        "        return response(500, {'error': err.response['Error']['Message']})\n")
    found = exception_text_offenders([bad])
    assert {int(line.split(':')[1]) for line in found} == {2, 3, 9, 10, 11, 13}, found
    assert exception_text_offenders([LAMBDA / 'region_utils.py']) == []


def access_denied(message=SENSITIVE_MESSAGE, operation='ListQueues'):
    return ClientError({'Error': {'Code': 'AccessDeniedException', 'Message': message}}, operation)


def test_fatal_error_keeps_code_action_and_resource_but_never_the_message(capsys):
    err = region_utils.fatal_error(access_denied(), resource='queue q-1')
    logged = capsys.readouterr().out
    for text in (logged, str(err)):
        assert 'AccessDeniedException' in text and 'ListQueues' in text and 'queue q-1' in text
        for fragment in ('jane.doe', '+15555550123', 'SECRETVALUE', 'not authorized'):
            assert fragment not in text
    assert (err.code, err.action, err.resource) == ('AccessDeniedException', 'ListQueues', 'queue q-1')


def test_reraise_if_fatal_never_logs_or_wraps_the_message(capsys):
    throttled = ClientError({'Error': {'Code': 'ThrottlingException', 'Message': SENSITIVE_MESSAGE}}, 'ListUsers')
    with pytest.raises(region_utils.MigrationStepError) as info:
        region_utils.reraise_if_fatal(throttled, resource='instance i-1')
    out = capsys.readouterr().out
    assert 'jane.doe' not in out and 'jane.doe' not in str(info.value)
    assert str(info.value) == 'Throttling (ThrottlingException) calling ListUsers on instance i-1'


def test_error_text_keeps_own_texts_and_drops_aws_messages():
    assert region_utils.error_text(access_denied()) == 'AccessDeniedException (ListQueues)'
    assert region_utils.error_text(ValueError(SENSITIVE_MESSAGE)) == 'ValueError'
    own = region_utils.MigrationStepError('ExternalId is required', code='MissingExternalId')
    assert region_utils.error_text(own) == 'ExternalId is required'
    with pytest.raises(region_utils.ArnFormatError) as info:
        region_utils.extract_region_from_arn('not-an-arn')
    assert region_utils.error_text(info.value).startswith('Invalid Connect instance ARN format')


def test_list_step_output_has_the_error_code_not_the_message(stubbed, monkeypatch, capsys):
    client, stub = stubbed('connect', SOURCE_REGION)
    stub.add_client_error('list_queues', service_error_code='ResourceNotFoundException',
                          service_message=SENSITIVE_MESSAGE, http_status_code=404)
    monkeypatch.setattr(list_queues, 'create_connect_client_from_arn',
                        lambda arn: (client, SOURCE_INSTANCE_ID, SOURCE_REGION))
    result = list_queues.lambda_handler({'sourceInstanceId': SOURCE_INSTANCE_ARN}, None)
    assert result == {'statusCode': 400, 'error': 'ResourceNotFoundException (ListQueues)'}
    assert 'jane.doe' not in capsys.readouterr().out


def test_api_responses_have_the_error_code_not_the_message(stubbed, monkeypatch, capsys):
    monkeypatch.setenv('ALLOWED_ORIGIN', 'https://d111111abcdef8.cloudfront.net')
    client, stub = stubbed('connect', SOURCE_REGION)
    stub.add_client_error('list_phone_numbers_v2', service_error_code='InvalidRequestException',
                          service_message=SENSITIVE_MESSAGE, http_status_code=400)
    monkeypatch.setattr(api_list_phone_numbers, 'create_connect_client_from_arn',
                        lambda arn: (client, SOURCE_INSTANCE_ID, SOURCE_REGION))
    event = {'httpMethod': 'POST', 'body': json.dumps({'sourceInstanceId': SOURCE_INSTANCE_ARN})}
    result = api_list_phone_numbers.lambda_handler(event, None)
    assert result['statusCode'] == 500
    assert json.loads(result['body']) == {'error': 'InvalidRequestException (ListPhoneNumbersV2)'}

    def denied(*args, **kwargs):
        raise region_utils.fatal_error(access_denied(operation='AssumeRole'), TARGET_ROLE_ARN)

    monkeypatch.setattr(check_flow_dependencies, 'create_destination_session', denied)
    result = check_flow_dependencies.lambda_handler({'httpMethod': 'POST', 'body': json.dumps({
        'sourceInstanceId': SOURCE_INSTANCE_ARN, 'destinationInstanceId': TARGET_INSTANCE_ARN,
        'crossAccountRoleArn': TARGET_ROLE_ARN, 'externalId': EXTERNAL_ID})}, None)
    body = json.loads(result['body'])
    assert result['statusCode'] == 403 and body['action'] == 'AssumeRole'
    assert body['error'] == f'AccessDenied (AccessDeniedException) calling AssumeRole on {TARGET_ROLE_ARN}'
    assert 'jane.doe' not in capsys.readouterr().out


def test_start_api_still_explains_a_malformed_arn(monkeypatch):
    execution_input, external_id, error = api_start_migration.build_execution_input(
        {'sourceInstanceId': 'arn:aws:connect:eu-west-1:111111111111:instance/not-a-uuid',
         'destinationInstanceId': TARGET_INSTANCE_ARN})
    assert execution_input is None and error.startswith('Invalid instance')
