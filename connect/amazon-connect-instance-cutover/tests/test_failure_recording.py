"""Create and associate steps never drop a failed item silently (a security profile once was)."""
import ast
import pathlib

import create_hours_of_operations
from conftest import TARGET_INSTANCE_ARN, TARGET_INSTANCE_ID
from botocore.stub import ANY

LAMBDA = pathlib.Path(__file__).resolve().parent.parent / 'lambda'
STEP_FILES = sorted(p for p in LAMBDA.glob('*.py') if p.name.startswith(('create_', 'associate_')))


def failure_dicts(tree):
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'append'
                and node.args and isinstance(node.args[0], ast.Dict)):
            target = ast.unparse(node.func.value)
            d = node.args[0]
            keys = {k.value: ast.unparse(v) for k, v in zip(d.keys, d.values) if isinstance(k, ast.Constant)}
            if target.startswith(('failed', 'not_created', 'not_migrated')) or keys.get('status') == "'failed'":
                yield node.lineno, keys


def test_every_failure_entry_has_an_error_code():
    missing = [f'{p.name}:{line}' for p in STEP_FILES for line, keys in failure_dicts(ast.parse(p.read_text()))
               if 'errorCode' not in keys]
    assert missing == []


def test_no_per_item_handler_drops_an_item():
    silent = []
    for path in STEP_FILES:
        for loop in ast.walk(ast.parse(path.read_text())):
            if not isinstance(loop, ast.For):
                continue
            for handler in (n for n in ast.walk(loop) if isinstance(n, ast.ExceptHandler)):
                records = any(
                    (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ('append', 'extend'))
                    or (isinstance(n, ast.Assign) and any(isinstance(t, ast.Subscript) for t in n.targets))
                    # A counter (skipped += 1) records an existing item without its name.
                    or isinstance(n, ast.AugAssign)
                    or isinstance(n, ast.Raise)
                    for n in ast.walk(handler))
                if not records:
                    silent.append(f'{path.name}:{handler.lineno}')
    assert silent == []


def test_hours_existing_but_not_found_is_reported(stubbed, monkeypatch):
    client, stub = stubbed('connect', 'ap-southeast-2')
    monkeypatch.setattr(create_hours_of_operations, 'create_destination_client',
                        lambda *a: (client, TARGET_INSTANCE_ID, 'ap-southeast-2'))
    stub.add_response('list_hours_of_operations', {'HoursOfOperationSummaryList': []}, {'InstanceId': ANY})
    stub.add_client_error('create_hours_of_operation', service_error_code='InvalidParameterException',
                          http_status_code=400)
    stub.add_response('list_hours_of_operations', {'HoursOfOperationSummaryList': []}, {'InstanceId': ANY})
    result = create_hours_of_operations.lambda_handler({
        'destinationInstanceId': TARGET_INSTANCE_ARN,
        'hoursOfOperations': [{'HoursOfOperationId': 'src-h-1', 'Name': 'Office', 'TimeZone': 'UTC', 'Config': []}],
    }, None)
    assert result['failedItems'] == [{'originalId': 'src-h-1', 'status': 'failed',
                                      'errorCode': 'InvalidParameterException',
                                      'reason': 'exists but not found by name'}]
