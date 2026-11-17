"""Lambda and Lex association steps: no source side writes, name matching, follow up report."""
import boto3
import pytest

import associate_lambda_functions
import associate_lex_bots
from conftest import (
    SOURCE_INSTANCE_ARN,
    SOURCE_INSTANCE_ID,
    TARGET_INSTANCE_ARN,
    TARGET_INSTANCE_ID,
)

SRC_FN = 'arn:aws:lambda:us-east-1:111111111111:function:lookup-customer'
SRC_FN_OTHER = 'arn:aws:lambda:us-east-1:111111111111:function:legacy-dip'
DST_FN = 'arn:aws:lambda:ap-southeast-2:222222222222:function:lookup-customer'


@pytest.fixture
def no_lambda_client(monkeypatch):
    """Fail if any code builds a Lambda client (that would mean resource policy writes)."""
    real_client = boto3.client

    def guarded(service, *args, **kwargs):
        assert service != 'lambda', 'associate step must not touch Lambda resource policies'
        return real_client(service, *args, **kwargs)

    monkeypatch.setattr(boto3, 'client', guarded)


def test_lambda_step_matches_by_name_and_reports_the_rest(stubbed, monkeypatch, no_lambda_client):
    dest, stub = stubbed('connect', 'ap-southeast-2')
    monkeypatch.setattr(associate_lambda_functions, 'create_destination_client',
                        lambda *a, **k: (dest, TARGET_INSTANCE_ID, 'ap-southeast-2'))
    stub.add_response('list_lambda_functions', {'LambdaFunctions': [DST_FN]}, {'InstanceId': TARGET_INSTANCE_ID})

    result = associate_lambda_functions.lambda_handler({
        'sourceInstanceId': SOURCE_INSTANCE_ARN, 'destinationInstanceId': TARGET_INSTANCE_ARN,
        'lambdaFunctions': [SRC_FN, SRC_FN_OTHER]}, None)

    assert result['matched'] == [{'arn': SRC_FN, 'targetArn': DST_FN}]
    assert [f['source'] for f in result['manualFollowUp']] == [SRC_FN_OTHER]
    assert 'name' not in result['manualFollowUp'][0]
    assert 'ap-southeast-2' in result['manualFollowUp'][0]['action']
    assert result['associated'] == []


def test_lambda_step_associates_functions_already_in_target(stubbed, monkeypatch, no_lambda_client):
    dest, stub = stubbed('connect', 'ap-southeast-2')
    monkeypatch.setattr(associate_lambda_functions, 'create_destination_client',
                        lambda *a, **k: (dest, TARGET_INSTANCE_ID, 'ap-southeast-2'))
    other = 'arn:aws:lambda:ap-southeast-2:222222222222:function:already-in-target'
    stub.add_response('list_lambda_functions', {'LambdaFunctions': []}, {'InstanceId': TARGET_INSTANCE_ID})
    stub.add_response('associate_lambda_function', {}, {'InstanceId': TARGET_INSTANCE_ID, 'FunctionArn': other})
    result = associate_lambda_functions.lambda_handler({
        'destinationInstanceId': TARGET_INSTANCE_ARN, 'lambdaFunctions': [other]}, None)
    assert result['associated'] == [other]


class FakeSession:
    def __init__(self, connect, lex):
        self._clients = {'connect': connect, 'lexv2-models': lex}

    def client(self, service, **kwargs):
        return self._clients[service]


def test_lex_step_matches_by_bot_and_alias_name(stubbed, monkeypatch):
    src_connect, src_stub = stubbed('connect', 'us-east-1')
    dst_connect, dst_stub = stubbed('connect', 'ap-southeast-2')
    src_lex, src_lex_stub = stubbed('lexv2-models', 'us-east-1')
    dst_lex, dst_lex_stub = stubbed('lexv2-models', 'ap-southeast-2')
    src_alias = 'arn:aws:lex:us-east-1:111111111111:bot-alias/SRCBOT0001/SRCALIAS01'
    src_alias_2 = 'arn:aws:lex:us-east-1:111111111111:bot-alias/SRCBOT0002/SRCALIAS02'
    dst_alias = 'arn:aws:lex:ap-southeast-2:222222222222:bot-alias/DSTBOT0001/DSTALIAS01'

    monkeypatch.setattr(associate_lex_bots, 'create_destination_session',
                        lambda *a, **k: FakeSession(dst_connect, dst_lex))
    monkeypatch.setattr(associate_lex_bots, 'create_connect_client_from_arn',
                        lambda arn: (src_connect, SOURCE_INSTANCE_ID, 'us-east-1'))
    real_client = boto3.client
    monkeypatch.setattr(associate_lex_bots.boto3, 'client',
                        lambda service, **kw: src_lex if service == 'lexv2-models' else real_client(service, **kw))

    dst_stub.add_response('list_bots', {'LexBots': [{'LexV2Bot': {'AliasArn': dst_alias}}]})
    dst_lex_stub.add_response('describe_bot', {'botId': 'DSTBOT0001', 'botName': 'OrderBot'})
    dst_lex_stub.add_response('describe_bot_alias', {'botAliasId': 'DSTALIAS01', 'botAliasName': 'live'})
    src_stub.add_response('list_bots', {'LexBots': [{'LexV2Bot': {'AliasArn': src_alias}},
                                                    {'LexV2Bot': {'AliasArn': src_alias_2}}]})
    src_lex_stub.add_response('describe_bot', {'botId': 'SRCBOT0001', 'botName': 'OrderBot'})
    src_lex_stub.add_response('describe_bot_alias', {'botAliasId': 'SRCALIAS01', 'botAliasName': 'live'})
    src_lex_stub.add_response('describe_bot', {'botId': 'SRCBOT0002', 'botName': 'SurveyBot'})
    src_lex_stub.add_response('describe_bot_alias', {'botAliasId': 'SRCALIAS02', 'botAliasName': 'prod'})

    result = associate_lex_bots.lambda_handler({
        'sourceInstanceId': SOURCE_INSTANCE_ARN, 'destinationInstanceId': TARGET_INSTANCE_ARN,
        'lexV2Bots': [{'aliasArn': src_alias}, {'aliasArn': src_alias_2}]}, None)

    assert result['matchedV2'] == [{'aliasArn': src_alias, 'targetAliasArn': dst_alias}]
    follow = {(f['type'], f['source']) for f in result['manualFollowUp']}
    # V2 entries carry the alias ARN, never names.
    assert follow == {('lexV2', src_alias_2)}
    assert 'associatedV1' not in result and 'totalV1' not in result['summary']
    assert 'SurveyBot' not in str(result) and 'OrderBot' not in str(result)
    assert result['associatedV2'] == []


def test_lex_list_step_lists_only_lex_v2_bots(stubbed, monkeypatch):
    import list_lex_bots
    client, stub = stubbed('connect', 'us-east-1')
    alias = 'arn:aws:lex:us-east-1:111111111111:bot-alias/SRCBOT0001/SRCALIAS01'
    stub.add_response('list_bots', {'LexBots': [{'LexV2Bot': {'AliasArn': alias}}]},
                      {'InstanceId': SOURCE_INSTANCE_ID, 'LexVersion': 'V2', 'MaxResults': 25})
    monkeypatch.setattr(list_lex_bots, 'create_connect_client_from_arn',
                        lambda arn: (client, SOURCE_INSTANCE_ID, 'us-east-1'))
    result = list_lex_bots.lambda_handler({'sourceInstanceId': SOURCE_INSTANCE_ARN}, None)
    assert [bot['aliasArn'] for bot in result['lexV2Bots']] == [alias]
    assert 'lexV1Bots' not in result and result['summary'] == {'v2Count': 1, 'total': 1}
