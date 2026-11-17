"""Region dependent helpers across Regions, partitions and a Region code launched later.

The tool never carries a Region list: Regions are checked by format only. These cases cover
Amazon Connect Customer Regions, a commercial Region without the service (ap-south-1), the
AWS GovCloud (US) partition and a made up future Region code with a valid format.
"""
import datetime
import pathlib
import re
import subprocess

import pytest

import api_start_migration
import create_phone_numbers
import create_rules
import region_utils
from conftest import EXTERNAL_ID, ROOT

ACCOUNT_A, ACCOUNT_B = '111111111111', '222222222222'
INSTANCE_A = '11111111-1111-4111-8111-111111111111'
INSTANCE_B = '22222222-2222-4222-8222-222222222222'
FUTURE_REGION = 'zz-future-10'
REGIONS = ['us-east-1', 'us-west-2', 'ca-central-1', 'eu-west-2', 'eu-central-1', 'af-south-1', 'ap-south-1',
           'ap-southeast-1', 'ap-southeast-2', 'ap-northeast-1', 'ap-northeast-2', 'us-gov-west-1', FUTURE_REGION]
INVALID_REGIONS = ['', 'US-EAST-1', 'useast1', 'us-east', 'us-east-1a', 'us_east_1', '-east-1', 'us-east-1 ']
CREDS = {
    'AccessKeyId': 'AKIAIOSFODNN7EXAMPLE',  # nosec B105 nosemgrep: AWS documentation example, not a credential
    'SecretAccessKey': 'secret',  # nosec B105 nosemgrep: dummy value returned by a stubbed STS call
    'SessionToken': 'token',
    'Expiration': datetime.datetime(2030, 1, 1),
}


def partition_for(region):
    return 'aws-us-gov' if region.startswith('us-gov-') else 'aws'


def instance_arn(region, account=ACCOUNT_A, instance=INSTANCE_A, partition=None):
    return f'arn:{partition or partition_for(region)}:connect:{region}:{account}:instance/{instance}'


def other_region(region):
    """A different Region in the same partition (the made up one for the commercial partition)."""
    if partition_for(region) == 'aws-us-gov':
        return 'us-gov-east-1'
    return FUTURE_REGION if region != FUTURE_REGION else 'us-east-1'


@pytest.mark.parametrize('region', REGIONS)
def test_region_and_partition_come_from_the_arn(region):
    arn = instance_arn(region)
    assert region_utils.extract_region_from_arn(arn) == region
    assert region_utils.extract_partition_from_arn(arn) == partition_for(region)
    assert region_utils.extract_instance_id_from_arn(arn) == INSTANCE_A


@pytest.mark.parametrize('region', REGIONS)
def test_connect_client_targets_the_arn_region_and_partition(region):
    client, instance_id, client_region = region_utils.create_connect_client_from_arn(instance_arn(region))
    assert (client_region, instance_id) == (region, INSTANCE_A)
    assert client.meta.region_name == region
    assert client.meta.partition == partition_for(region)
    assert client.meta.endpoint_url == f'https://connect.{region}.amazonaws.com'


@pytest.mark.parametrize('region', REGIONS)
def test_destination_session_uses_the_target_region(region, stubbed, monkeypatch):
    sts, stub = stubbed('sts', 'us-east-1')
    monkeypatch.setattr(region_utils, '_sts_client', lambda: sts)
    role = f'arn:{partition_for(region)}:iam::{ACCOUNT_B}:role/ConnectMigrationTargetWriter'
    stub.add_response('assume_role', {'Credentials': CREDS}, {
        'RoleArn': role, 'RoleSessionName': f'ConnectMigration-{ACCOUNT_B}',
        'ExternalId': EXTERNAL_ID, 'DurationSeconds': 3600})
    target = instance_arn(region, ACCOUNT_B, INSTANCE_B)
    session = region_utils.create_destination_session(target, role, EXTERNAL_ID)
    assert session.region_name == region
    client, _, client_region = region_utils.create_destination_client(target, session=session)
    assert client.meta.region_name == client_region == region


@pytest.mark.parametrize('region', REGIONS)
def test_phone_number_path_compares_only_account_and_region(region):
    same = instance_arn(region)
    other_account = instance_arn(region, ACCOUNT_B, INSTANCE_B)
    other_place = instance_arn(other_region(region), instance=INSTANCE_B)
    both_differ = instance_arn(other_region(region), ACCOUNT_B, INSTANCE_B)
    path = create_phone_numbers.phone_number_path
    assert path(same, instance_arn(region, instance=INSTANCE_B)) == create_phone_numbers.SELF_MOVE
    assert path(same, other_account) == create_phone_numbers.SUPPORT_MOVE
    assert path(same, other_place) == create_phone_numbers.SUPPORT_MOVE
    assert path(same, both_differ) == create_phone_numbers.PORTING


@pytest.mark.parametrize('region', REGIONS)
def test_start_accepts_any_region_with_a_valid_format(region):
    execution_input, _, error = api_start_migration.build_execution_input({
        'sourceInstanceId': instance_arn(region), 'destinationInstanceId': instance_arn(region, instance=INSTANCE_B)})
    assert error is None
    assert execution_input['destinationInstanceId'].split(':')[3] == region


@pytest.mark.parametrize('region', INVALID_REGIONS)
def test_region_with_an_invalid_format_is_refused(region):
    arn = f'arn:aws:connect:{region}:{ACCOUNT_A}:instance/{INSTANCE_A}'
    with pytest.raises(region_utils.ArnFormatError):
        region_utils.extract_region_from_arn(arn)
    _, _, error = api_start_migration.build_execution_input(
        {'sourceInstanceId': arn, 'destinationInstanceId': instance_arn('us-east-1', instance=INSTANCE_B)})
    assert error


@pytest.mark.parametrize('region', REGIONS)
def test_sdk_region_warning_is_soft(region):
    warning = region_utils.sdk_region_warning(instance_arn(region))
    assert warning is None or (region in warning and 'continues' in warning)


def test_sdk_region_warning_names_an_unknown_region_and_not_a_known_one():
    assert region_utils.sdk_region_warning(instance_arn('us-east-1')) is None
    assert FUTURE_REGION in region_utils.sdk_region_warning(instance_arn(FUTURE_REGION))


def teardown_region_pattern():
    text = (ROOT / 'scripts' / 'teardown.sh').read_text()
    return re.search(r"^REGION_PATTERN='([^']+)'$", text, re.MULTILINE).group(1)


def bash_matches(value, pattern):
    script = 'v="$1"; p="$2"; [[ "$v" =~ $p ]]'
    return subprocess.run(['bash', '-c', script, 'check', value, pattern], check=False).returncode == 0


# Partition: derived from the ARNs, never the literal 'aws'.

@pytest.mark.parametrize('region', REGIONS)
def test_rule_references_keep_the_partition_and_move_to_the_target(region):
    partition = partition_for(region)
    target_region = other_region(region)
    source = f'arn:{partition}:connect:{region}:{ACCOUNT_A}:instance/{INSTANCE_A}/queue/aaaa-1111'
    mapped = create_rules.map_arn(source, INSTANCE_B, target_region, ACCOUNT_B, {'aaaa-1111': 'bbbb-2222'})
    assert mapped == f'arn:{partition}:connect:{target_region}:{ACCOUNT_B}:instance/{INSTANCE_B}/queue/bbbb-2222'
    actions = create_rules.map_actions([{'Target': source}], INSTANCE_B, target_region, ACCOUNT_B,
                                       {'aaaa-1111': 'bbbb-2222'}, {}, {})
    assert actions == [{'Target': mapped}]
    trigger = create_rules.map_trigger_event_source(
        {'IntegrationAssociationId': f'arn:{partition}:connect:{region}:{ACCOUNT_A}:instance/{INSTANCE_A}/x/y'},
        INSTANCE_B, target_region, ACCOUNT_B, {})
    assert trigger['IntegrationAssociationId'] == (
        f'arn:{partition}:connect:{target_region}:{ACCOUNT_B}:instance/{INSTANCE_B}/x/y')


def test_start_refuses_a_cross_partition_pair():
    commercial = instance_arn('us-east-1')
    gov = instance_arn('us-gov-west-1', ACCOUNT_B, INSTANCE_B)
    _, _, error = api_start_migration.build_execution_input(
        {'sourceInstanceId': commercial, 'destinationInstanceId': gov})
    assert 'CrossPartitionNotSupported' in error
    _, _, error = api_start_migration.build_execution_input({
        'sourceInstanceId': instance_arn('us-gov-west-1'), 'destinationInstanceId': gov,
        'crossAccountRoleArn': f'arn:aws:iam::{ACCOUNT_B}:role/ConnectMigrationTargetWriter',
        'externalId': EXTERNAL_ID})
    assert 'CrossPartitionNotSupported' in error


@pytest.mark.parametrize('target', REGIONS)
@pytest.mark.parametrize('source', REGIONS)
def test_every_region_pair_starts_within_a_partition_and_never_across(source, target):
    """Every pair of the Regions above, cross account with a target role in the target's partition."""
    role = f'arn:{partition_for(target)}:iam::{ACCOUNT_B}:role/ConnectMigrationTargetWriter'
    execution_input, _, error = api_start_migration.build_execution_input({
        'sourceInstanceId': instance_arn(source), 'destinationInstanceId': instance_arn(target, ACCOUNT_B, INSTANCE_B),
        'crossAccountRoleArn': role, 'externalId': EXTERNAL_ID})
    if partition_for(source) == partition_for(target):
        assert error is None
        assert execution_input['sourceInstanceId'].split(':')[3] == source
        assert execution_input['destinationInstanceId'].split(':')[3] == target
    else:
        assert execution_input is None and 'CrossPartitionNotSupported' in error


def test_start_accepts_a_govcloud_pair_with_a_govcloud_role():
    execution_input, external_id, error = api_start_migration.build_execution_input({
        'sourceInstanceId': instance_arn('us-gov-west-1'),
        'destinationInstanceId': instance_arn('us-gov-east-1', ACCOUNT_B, INSTANCE_B),
        'crossAccountRoleArn': f'arn:aws-us-gov:iam::{ACCOUNT_B}:role/ConnectMigrationTargetWriter',
        'externalId': EXTERNAL_ID})
    assert error is None and external_id == EXTERNAL_ID
    assert execution_input['crossAccountRoleArn'].startswith('arn:aws-us-gov:iam::')


HTML = (ROOT / 'frontend' / 'index.html').read_text()


def ui_arn_patterns():
    found = re.findall(r'const arnPattern = /(.+?)/i;', HTML)
    assert len(found) == 3 and len(set(found)) == 1
    return re.compile(found[0].replace('\\/', '/'), re.IGNORECASE)


@pytest.mark.parametrize('region', REGIONS)
def test_ui_accepts_instance_arns_in_every_region_and_partition(region):
    assert ui_arn_patterns().match(instance_arn(region))


def test_ui_console_link_comes_from_the_execution_arn_partition():
    assert HTML.count('console.aws.amazon.com') == 1  # only in CONSOLE_HOSTS
    assert "'aws-us-gov': 'console.amazonaws-us-gov.com'" in HTML
    assert 'CONSOLE_HOSTS[executionPartition]' in HTML


@pytest.mark.parametrize('region', REGIONS)
def test_teardown_accepts_every_region_format(region):
    assert bash_matches(region, teardown_region_pattern())


@pytest.mark.parametrize('region', [r for r in INVALID_REGIONS if r])
def test_teardown_refuses_an_invalid_region(region):
    assert not bash_matches(region, teardown_region_pattern())


def test_readme_states_the_region_support_without_a_list():
    readme = (ROOT / 'README.md').read_text()
    section = readme[readme.index('## Supported Regions'):readme.index('## Scope: a one time cutover')]
    assert 'https://docs.aws.amazon.com/connect/latest/adminguide/regions.html' in section
    for text in ('FeatureNotAvailableInRegion', 'CrossPartitionNotSupported', 'AWS GovCloud (US)', 'unknown'):
        assert text in section
    assert not re.search(r'(?<![a-z])(us-gov|us|eu|ap|ca|sa|me|af|il|mx|cn)-[a-z]+-\d', section)
    assert not any(c in section for c in '\u2014\u2013')


def test_no_region_list_in_shipped_code():
    """No Lambda, script or UI file names two or more Region codes (a list in disguise)."""
    region = re.compile(r'(?<![a-z])(us-gov|us|eu|ap|ca|sa|me|af|il|mx|cn)-[a-z]+-\d(?![0-9])')
    files = [*pathlib.Path(ROOT / 'lambda').glob('*.py'), *pathlib.Path(ROOT / 'scripts').glob('*.sh'),
             ROOT / 'frontend' / 'index.html']
    offenders = {}
    for path in files:
        found = {m.group(0) for m in region.finditer(path.read_text())}
        found.discard('us-east-1')  # S3 CreateBucket: us-east-1 takes no LocationConstraint
        if found:
            offenders[path.name] = sorted(found)
    assert offenders == {}
