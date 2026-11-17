"""scripts/teardown.sh with a fake aws CLI on PATH (no AWS calls)."""
import json
import os
import pathlib
import re
import stat
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = ROOT / 'scripts' / 'teardown.sh'
SOURCE_ACCOUNT, TARGET_ACCOUNT = '111111111111', '222222222222'
FAKE_AWS = r'''#!/usr/bin/env python3
"""Fake aws CLI: logs each call as JSON and answers the few calls teardown.sh makes."""
import json, os, pathlib, sys
args = sys.argv[1:]
with open(os.environ['FAKE_AWS_LOG'], 'a') as log:
    log.write(json.dumps(args) + '\n')
def value(flag):
    return args[args.index(flag) + 1] if flag in args else ''
missing = set(filter(None, os.environ.get('FAKE_MISSING', '').split(',')))
words = [a for a in args if not a.startswith('--')]
if words[:2] == ['sts', 'get-caller-identity'] or 'get-caller-identity' in args:
    print('222222222222' if value('--profile') == 'tgt' else '111111111111')
elif 'describe-stacks' in args:
    stack = value('--stack-name')
    if stack in missing:
        sys.exit(255)
    query = value('--query')
    if 'Outputs' in query:
        key = query.split("OutputKey=='")[1].split("'")[0]
        print({'WebsiteBucket': 'site-bucket', 'ArtifactsBucket': 'artifacts-bucket',
               'AccessLogsBucket': 'logs-bucket'}[key])
    else:
        print('CREATE_COMPLETE')
elif 'list-object-versions' in args:
    marker = pathlib.Path(os.environ['FAKE_AWS_LOG'] + '.' + value('--bucket'))
    if marker.exists():
        print(json.dumps({'Objects': None, 'Quiet': True}))
    else:
        marker.write_text('listed')
        print(json.dumps({'Objects': [{'Key': 'k', 'VersionId': 'v1'}], 'Quiet': True}))
'''


@pytest.fixture
def fake_aws(tmp_path):
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    aws = bin_dir / 'aws'
    aws.write_text(FAKE_AWS)
    aws.chmod(aws.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / 'aws.log'
    log.touch()

    def run(*args, missing='', stdin=subprocess.DEVNULL):
        env = {**os.environ, 'PATH': f"{bin_dir}{os.pathsep}{os.environ['PATH']}", 'FAKE_AWS_LOG': str(log),
               'FAKE_MISSING': missing}
        proc = subprocess.run(['bash', str(SCRIPT), *args], capture_output=True, text=True, env=env, stdin=stdin)
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        return proc, calls
    return run


def mutating(calls):
    return [c for c in calls if any(re.match(r'(delete|put|create|update)-', a) for a in c)]


def services(calls):
    return {c[[i for i, a in enumerate(c) if a == '--region'][0] + 2] for c in calls}


def test_dry_run_prints_the_plan_and_changes_nothing(fake_aws):
    proc, calls = fake_aws('--region', 'eu-west-1', '--environment', 'test', '--target-profile', 'tgt',
                           '--target-region', 'ap-southeast-2', '--dry-run')
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    for line in ('delete stack connect-migration-backend-test', 'delete stack connect-migration-stepfunctions-test',
                 'delete stack connect-migration-lambdas-test', 'empty bucket site-bucket (all versions)',
                 'empty bucket artifacts-bucket', 'empty bucket logs-bucket',
                 'delete stack connect-migration-frontend-test', 'delete stack connect-migration-target-role',
                 f'empty and delete bucket connect-migration-prompts-temp-{TARGET_ACCOUNT}-ap-southeast-2',
                 'Instances are not touched'.lower()):
        assert line in out or line in out.lower()
    assert 'Dry run: nothing was changed.' in out
    assert mutating(calls) == []


def test_without_confirmation_nothing_is_deleted(fake_aws):
    proc, calls = fake_aws('--region', 'eu-west-1')
    assert proc.returncode != 0 and 'pass --yes' in proc.stderr
    assert mutating(calls) == []


def test_wrong_confirmation_text_deletes_nothing(fake_aws, tmp_path):
    # stdin is not a terminal here, so the script refuses before reading an answer.
    answer = tmp_path / 'answer'
    answer.write_text('prod-typo\n')
    with answer.open() as stdin:
        proc, calls = fake_aws('--region', 'eu-west-1', stdin=stdin)
    assert proc.returncode != 0 and mutating(calls) == []


def test_confirmed_run_deletes_in_order_and_never_calls_connect(fake_aws):
    proc, calls = fake_aws('--region', 'eu-west-1', '--environment', 'test', '--profile', 'src',
                           '--target-profile', 'tgt', '--target-region', 'ap-southeast-2', '--yes')
    assert proc.returncode == 0, proc.stderr
    steps = []
    for call in mutating(calls):
        if 'delete-stack' in call:
            steps.append(('stack', call[call.index('--stack-name') + 1]))
        elif 'delete-objects' in call:
            steps.append(('empty', call[call.index('--bucket') + 1]))
        elif 'delete-bucket' in call:
            steps.append(('bucket', call[call.index('--bucket') + 1]))
    prompt_bucket = f'connect-migration-prompts-temp-{TARGET_ACCOUNT}-ap-southeast-2'
    assert steps == [('stack', 'connect-migration-backend-test'), ('stack', 'connect-migration-stepfunctions-test'),
                     ('stack', 'connect-migration-lambdas-test'), ('empty', 'site-bucket'),
                     ('empty', 'artifacts-bucket'), ('empty', 'logs-bucket'),
                     ('stack', 'connect-migration-frontend-test'), ('stack', 'connect-migration-target-role'),
                     ('empty', prompt_bucket), ('bucket', prompt_bucket)]
    assert 'connect' not in services(calls)
    for call in calls:
        target_side = 'connect-migration-target-role' in call or prompt_bucket in call
        if target_side:
            assert call[:4] == ['--profile', 'tgt', '--region', 'ap-southeast-2']
        elif 'get-caller-identity' not in call:
            assert call[:4] == ['--profile', 'src', '--region', 'eu-west-1']
    delete_bucket = next(c for c in calls if 'delete-bucket' in c)
    assert delete_bucket[-2:] == ['--expected-bucket-owner', TARGET_ACCOUNT]


def test_missing_stacks_are_skipped(fake_aws):
    proc, calls = fake_aws('--region', 'eu-west-1', '--yes',
                           missing='connect-migration-backend-prod,connect-migration-frontend-prod')
    assert proc.returncode == 0, proc.stderr
    assert 'skip stack connect-migration-backend-prod (not found)' in proc.stdout
    deleted = [c[c.index('--stack-name') + 1] for c in mutating(calls) if 'delete-stack' in c]
    assert deleted == ['connect-migration-stepfunctions-prod', 'connect-migration-lambdas-prod']
    assert 'Target account: skipped' in proc.stdout


@pytest.mark.parametrize('args', [[], ['--region', 'nowhere'], ['--region', 'eu-west-1', '--environment', 'Prod!']])
def test_bad_arguments_are_rejected_before_any_call(fake_aws, args):
    proc, calls = fake_aws(*args)
    assert proc.returncode != 0 and calls == []


def test_script_never_calls_the_connect_api_and_is_documented():
    text = SCRIPT.read_text()
    assert not re.search(r'\b(?:aws|src|tgt|"\$1")\s+connect\b', text)
    readme = (ROOT / 'README.md').read_text()
    assert 'scripts/teardown.sh' in readme and '--dry-run' in readme
    assert 'teardown.sh' in (ROOT / 'scripts' / 'check.sh').read_text()
