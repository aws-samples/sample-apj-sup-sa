"""A feature a Region does not offer is a clean skip with a FeatureNotAvailableInRegion follow up.

Covers the shared helper (which errors count, and the EndpointConnectionError scoping), every list
step for such a feature (source side), the create steps (target side), VerifyTarget and the report.
"""
import importlib

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

import migration_report
import region_utils
from conftest import ROOT, make_client
from test_region_support import INSTANCE_A, REGIONS, instance_arn

FEATURE_ERRORS = sorted(region_utils.FEATURE_UNAVAILABLE_CODES)


def client_error(code, operation='ListRules'):
    return ClientError({'Error': {'Code': code, 'Message': 'not shown'}}, operation)


def assert_skipped(result, feature, side, region, code, key=None):
    assert result['statusCode'] == 200 and result['status'] == 'SKIPPED'
    assert result['featureNotAvailable'] == {'feature': feature, 'side': side, 'region': region, 'errorCode': code}
    if key:
        assert result[key] == []
    [follow_up] = result['manualFollowUp']
    assert follow_up['type'] == region_utils.FEATURE_NOT_AVAILABLE
    assert (follow_up['feature'], follow_up['side'], follow_up['region'], follow_up['errorCode']) == (
        feature, side, region, code)
    assert region in follow_up['action'] and 'Amazon Connect Customer' in follow_up['action']
    assert not any(c in follow_up['action'] for c in '\u2014\u2013')


# Shared helper ------------------------------------------------------------------------------------

@pytest.mark.parametrize('code', FEATURE_ERRORS)
def test_feature_error_codes_mean_not_available(code):
    assert region_utils.is_feature_unavailable(client_error(code))


@pytest.mark.parametrize('code', ['ResourceNotFoundException', 'InvalidParameterException', 'InternalServiceException'])
def test_other_errors_are_not_a_missing_feature(code):
    assert not region_utils.is_feature_unavailable(client_error(code))


@pytest.mark.parametrize('code', ['AccessDeniedException', 'ThrottlingException'])
def test_access_denied_and_throttling_stay_fatal(code):
    err = client_error(code)
    assert not region_utils.is_feature_unavailable(err)
    with pytest.raises(region_utils.MigrationStepError):
        region_utils.reraise_if_fatal(err)


def test_an_invalid_security_token_fails_the_step_and_the_run(stubbed, monkeypatch):
    """UnrecognizedClientException is an invalid token, not a missing feature (audit E8)."""
    import list_ai_agents
    from test_migration_report import EXECUTION_ARN, FakeSfn, history
    from test_state_machine import final_outcome
    err = client_error('UnrecognizedClientException', 'ListAssistants')
    assert not region_utils.is_feature_unavailable(err)
    with pytest.raises(region_utils.MigrationStepError) as raised:
        region_utils.reraise_if_fatal(err)
    assert raised.value.code == 'UnrecognizedClientException' and str(raised.value).startswith('InvalidToken')
    # The step: an AI agents list in a Region that offers them, called with an invalid token.
    qconnect, stub = stubbed('qconnect', 'ap-southeast-2')
    stub.add_client_error('list_assistants', service_error_code='UnrecognizedClientException')
    use_qconnect(list_ai_agents, monkeypatch, 'ap-southeast-2', qconnect, stubbed)
    with pytest.raises(region_utils.MigrationStepError):
        list_ai_agents.lambda_handler({'sourceInstanceId': instance_arn('ap-southeast-2')}, None)
    # The run: the failed task fails the report, and the final choice ends the execution failed.
    report = migration_report.build_report(FakeSfn(history(('ListAIAgents', 'fail', 'MigrationStepError'))),
                                           EXECUTION_ARN)
    assert report['status'] == 'FAILED' and 'featureNotAvailable' not in str(report)
    assert report['manualFollowUp'] == []
    assert final_outcome(report['status'], 'DELETED') == 'MigrationFailed'


@pytest.mark.parametrize('region', REGIONS)
def test_endpoint_error_counts_only_for_the_feature_service_endpoint(region):
    qconnect, connect = make_client('qconnect', region), make_client('connect', region)
    own = EndpointConnectionError(endpoint_url=f'{qconnect.meta.endpoint_url}/assistants')
    other = EndpointConnectionError(endpoint_url=f'{connect.meta.endpoint_url}/rules')
    assert region_utils.is_feature_unavailable(own, qconnect)
    assert not region_utils.is_feature_unavailable(other, qconnect)
    assert not region_utils.is_feature_unavailable(own)  # no feature client: never a missing feature
    # A connect endpoint that cannot be reached is a failure, even when it names that client.
    assert not region_utils.is_feature_unavailable(other, connect)


def test_create_step_with_nothing_to_create_adds_no_follow_up():
    result = region_utils.feature_skipped_result('rules', 'af-south-1', 'target', 'UnknownOperationException',
                                                 count=0)
    assert result['status'] == 'SKIPPED' and result['manualFollowUp'] == []
    counted = region_utils.feature_skipped_result('rules', 'af-south-1', 'target', 'UnknownOperationException',
                                                  count=3)
    assert counted['manualFollowUp'][0]['count'] == 3


def test_every_feature_key_has_fixed_wording():
    assert set(region_utils.FEATURE_LABELS) == {
        'rules', 'evaluationForms', 'dataTables', 'views', 'predefinedAttributes', 'vocabularies',
        'aiAgents', 'aiPrompts', 'aiGuardrails', 'lexBots'}


# E1: Connect hosted list steps (source side) -------------------------------------------------------

CONNECT_LISTS = [
    ('list_rules', 'list_rules', 'rules', 'rules'),
    ('list_evaluation_forms', 'list_evaluation_forms', 'evaluationForms', 'evaluationForms'),
    ('list_views', 'list_views', 'views', 'views'),
    ('list_predefined_attributes', 'list_predefined_attributes', 'predefinedAttributes', 'predefinedAttributes'),
    ('list_vocabularies', 'search_vocabularies', 'vocabularies', 'vocabularies'),
    ('list_data_tables', 'list_data_tables', 'dataTables', 'dataTables'),
]


@pytest.mark.parametrize('region', REGIONS)
@pytest.mark.parametrize('module_name, operation, key, feature', CONNECT_LISTS)
def test_list_step_skips_a_feature_the_source_region_does_not_offer(module_name, operation, key, feature, region,
                                                                    stubbed, monkeypatch):
    module = importlib.import_module(module_name)
    client, stub = stubbed('connect', region)
    stub.add_client_error(operation, service_error_code='UnknownOperationException')
    monkeypatch.setattr(module, 'create_connect_client_from_arn', lambda arn: (client, INSTANCE_A, region))
    result = module.lambda_handler({'sourceInstanceId': instance_arn(region)}, None)
    assert_skipped(result, feature, 'source', region, 'UnknownOperationException', key)


@pytest.mark.parametrize('code', ['AccessDeniedException', 'UnrecognizedClientException'])
@pytest.mark.parametrize('module_name, operation, key, feature', CONNECT_LISTS)
def test_list_step_still_fails_on_access_denied(module_name, operation, key, feature, code, stubbed, monkeypatch):
    module = importlib.import_module(module_name)
    client, stub = stubbed('connect', 'af-south-1')
    stub.add_client_error(operation, service_error_code=code)
    monkeypatch.setattr(module, 'create_connect_client_from_arn', lambda arn: (client, INSTANCE_A, 'af-south-1'))
    with pytest.raises(region_utils.MigrationStepError):
        module.lambda_handler({'sourceInstanceId': instance_arn('af-south-1')}, None)


# E2 and E7: AI agents list steps (qconnect, source side) ------------------------------------------

AI_LISTS = [('list_ai_agents', 'aiAgents', 'list_ai_agents', 'aiAgentSummaries'),
            ('list_ai_prompts', 'aiPrompts', 'list_ai_prompts', 'aiPromptSummaries'),
            ('list_ai_guardrails', 'aiGuardrails', 'list_ai_guardrails', 'aiGuardrailSummaries')]
ASSISTANT = {'assistantSummaries': [{'assistantId': 'a1b2c3d4-0000-4000-8000-000000000001', 'name': 'x',
                                     'assistantArn': 'arn:aws:wisdom:us-east-1:111111111111:assistant/a1',
                                     'type': 'AGENT', 'status': 'ACTIVE'}]}


class UnreachableQConnect:
    """A qconnect client whose own endpoint cannot be reached (what botocore raises after its retries)."""

    def __init__(self, region):
        self.meta = make_client('qconnect', region).meta

    def get_paginator(self, name):
        raise EndpointConnectionError(endpoint_url=f'{self.meta.endpoint_url}/{name}')


def use_qconnect(module, monkeypatch, region, qconnect, stubbed):
    connect, _ = stubbed('connect', region)
    monkeypatch.setattr(module, 'create_connect_client_from_arn', lambda arn: (connect, INSTANCE_A, region))
    monkeypatch.setattr(module.boto3, 'client', lambda service, **kw: qconnect)


@pytest.mark.parametrize('region', REGIONS)
@pytest.mark.parametrize('module_name, key, operation, summaries', AI_LISTS)
@pytest.mark.parametrize('code', FEATURE_ERRORS)
def test_ai_list_skips_when_the_source_region_has_no_ai_agents(module_name, key, operation, summaries, code,
                                                               region, stubbed, monkeypatch):
    module = importlib.import_module(module_name)
    qconnect, stub = stubbed('qconnect', region)
    stub.add_client_error('list_assistants', service_error_code=code)
    use_qconnect(module, monkeypatch, region, qconnect, stubbed)
    result = module.lambda_handler({'sourceInstanceId': instance_arn(region)}, None)
    assert_skipped(result, key, 'source', region, code, key)


@pytest.mark.parametrize('module_name, key, operation, summaries', AI_LISTS)
def test_ai_list_skips_when_its_own_endpoint_is_unreachable(module_name, key, operation, summaries, stubbed,
                                                            monkeypatch):
    module = importlib.import_module(module_name)
    use_qconnect(module, monkeypatch, 'af-south-1', UnreachableQConnect('af-south-1'), stubbed)
    result = module.lambda_handler({'sourceInstanceId': instance_arn('af-south-1')}, None)
    assert_skipped(result, key, 'source', 'af-south-1', 'EndpointConnectionError', key)


@pytest.mark.parametrize('module_name, key, operation, summaries', AI_LISTS)
def test_ai_list_skips_when_the_list_operation_is_unknown(module_name, key, operation, summaries, stubbed,
                                                          monkeypatch):
    module = importlib.import_module(module_name)
    qconnect, stub = stubbed('qconnect', 'us-gov-west-1')
    stub.add_response('list_assistants', ASSISTANT)
    stub.add_client_error(operation, service_error_code='UnknownOperationException')
    use_qconnect(module, monkeypatch, 'us-gov-west-1', qconnect, stubbed)
    result = module.lambda_handler({'sourceInstanceId': instance_arn('us-gov-west-1')}, None)
    assert_skipped(result, key, 'source', 'us-gov-west-1', 'UnknownOperationException', key)


@pytest.mark.parametrize('module_name, key, operation, summaries', AI_LISTS)
def test_ai_list_in_a_region_with_ai_agents_lists_as_before(module_name, key, operation, summaries, stubbed,
                                                            monkeypatch):
    module = importlib.import_module(module_name)
    qconnect, stub = stubbed('qconnect', 'ap-southeast-2')
    stub.add_response('list_assistants', ASSISTANT)
    stub.add_response(operation, {summaries: []})
    use_qconnect(module, monkeypatch, 'ap-southeast-2', qconnect, stubbed)
    result = module.lambda_handler({'sourceInstanceId': instance_arn('ap-southeast-2')}, None)
    assert result['statusCode'] == 200 and result[key] == [] and 'featureNotAvailable' not in result


# E3: Lex V2 (lexv2-models, both sides) ------------------------------------------------------------

def lex_alias(region, account, bot):
    return f'arn:aws:lex:{region}:{account}:bot-alias/{bot}/ALIAS00001'


def run_lex_step(stubbed, monkeypatch, source_region, target_region, target_bots, setup):
    import associate_lex_bots
    from test_associations import FakeSession
    from test_region_support import ACCOUNT_A, ACCOUNT_B, INSTANCE_B
    src_connect, src_stub = stubbed('connect', source_region)
    dst_connect, dst_stub = stubbed('connect', target_region)
    src_lex, src_lex_stub = stubbed('lexv2-models', source_region)
    dst_lex, dst_lex_stub = stubbed('lexv2-models', target_region)
    monkeypatch.setattr(associate_lex_bots, 'create_destination_session',
                        lambda *a, **k: FakeSession(dst_connect, dst_lex))
    monkeypatch.setattr(associate_lex_bots, 'create_connect_client_from_arn',
                        lambda arn: (src_connect, INSTANCE_A, source_region))
    monkeypatch.setattr(associate_lex_bots.boto3, 'client', lambda service, **kw: src_lex)
    source_alias = lex_alias(source_region, ACCOUNT_A, 'SRCBOT0001')
    dst_stub.add_response('list_bots', {'LexBots': [{'LexV2Bot': {'AliasArn': lex_alias(target_region, ACCOUNT_B, b)}}
                                                    for b in target_bots]})
    src_stub.add_response('list_bots', {'LexBots': [{'LexV2Bot': {'AliasArn': source_alias}}]})
    setup(src_lex_stub, dst_lex_stub)
    return associate_lex_bots.lambda_handler({
        'sourceInstanceId': instance_arn(source_region),
        'destinationInstanceId': instance_arn(target_region, ACCOUNT_B, INSTANCE_B),
        'lexV2Bots': [{'aliasArn': source_alias}]}, None), source_alias


def test_lex_step_skips_when_the_target_region_has_no_lex_v2(stubbed, monkeypatch):
    def setup(src, dst):
        dst.add_client_error('describe_bot', service_error_code='UnknownOperationException')
        src.add_response('describe_bot', {'botId': 'SRCBOT0001', 'botName': 'OrderBot'})
        src.add_response('describe_bot_alias', {'botAliasId': 'ALIAS00001', 'botAliasName': 'live'})
    result, _ = run_lex_step(stubbed, monkeypatch, 'us-east-1', 'af-south-1', ['DSTBOT0001'], setup)
    assert_skipped(result, 'lexBots', 'target', 'af-south-1', 'UnknownOperationException')
    assert result['manualFollowUp'][0]['count'] == 1 and result['failed'] == []
    assert 'OrderBot' not in str(result)


def test_lex_step_reports_a_source_region_without_lex_v2_and_lists_the_bot(stubbed, monkeypatch):
    def setup(src, dst):
        src.add_client_error('describe_bot', service_error_code='UnknownOperationException')
    result, source_alias = run_lex_step(stubbed, monkeypatch, 'zz-future-10', 'ap-southeast-2', [], setup)
    assert result['statusCode'] == 200 and 'featureNotAvailable' not in result and result['failed'] == []
    kinds = {(f['type'], f.get('side'), f.get('region') or f.get('source')) for f in result['manualFollowUp']}
    assert kinds == {('FeatureNotAvailableInRegion', 'source', 'zz-future-10'), ('lexV2', None, source_alias)}


def test_lex_step_with_lex_v2_on_both_sides_matches_as_before(stubbed, monkeypatch):
    def setup(src, dst):
        dst.add_response('describe_bot', {'botId': 'DSTBOT0001', 'botName': 'OrderBot'})
        dst.add_response('describe_bot_alias', {'botAliasId': 'ALIAS00001', 'botAliasName': 'live'})
        src.add_response('describe_bot', {'botId': 'SRCBOT0001', 'botName': 'OrderBot'})
        src.add_response('describe_bot_alias', {'botAliasId': 'ALIAS00001', 'botAliasName': 'live'})
    result, source_alias = run_lex_step(stubbed, monkeypatch, 'eu-west-2', 'eu-central-1', ['DSTBOT0001'], setup)
    assert [m['aliasArn'] for m in result['matchedV2']] == [source_alias] and result['manualFollowUp'] == []


def test_lex_list_step_skips_when_the_source_region_lacks_it(stubbed, monkeypatch):
    import list_lex_bots
    client, stub = stubbed('connect', 'ap-northeast-2')
    stub.add_client_error('list_bots', service_error_code='UnknownOperationException')
    monkeypatch.setattr(list_lex_bots, 'create_connect_client_from_arn',
                        lambda arn: (client, INSTANCE_A, 'ap-northeast-2'))
    result = list_lex_bots.lambda_handler({'sourceInstanceId': instance_arn('ap-northeast-2')}, None)
    assert_skipped(result, 'lexBots', 'source', 'ap-northeast-2', 'UnknownOperationException', 'lexV2Bots')


def test_flow_rewriter_treats_a_region_without_lex_v2_as_unmapped(stubbed):
    import arn_mapping
    lex, stub = stubbed('lexv2-models', 'af-south-1')
    stub.add_client_error('describe_bot', service_error_code='UnknownOperationException')
    found = {}
    alias = lex_alias('af-south-1', '111111111111', 'SRCBOT0001')
    assert arn_mapping.describe_lex_alias(lambda region: lex, alias, found) is None
    assert found == {'af-south-1': 'UnknownOperationException'}


# E4: create steps (target side) --------------------------------------------------------------------

# (module, feature, event key for the items, target instance key, first target call that fails, item)
CONNECT_CREATES = [
    ('create_rules', 'rules', 'rules', 'destinationInstanceId', 'list_rules', {'RuleId': 'r1', 'Name': 'x'}),
    ('create_evaluation_forms', 'evaluationForms', 'evaluationForms', 'destinationInstanceId',
     'list_evaluation_forms', {'EvaluationFormId': 'f1', 'Title': 'x'}),
    ('create_views', 'views', 'views', 'destinationInstanceId', 'list_views', {'Id': 'v1', 'Name': 'x'}),
    ('create_predefined_attributes', 'predefinedAttributes', 'predefinedAttributes', 'targetInstanceId',
     'list_predefined_attributes', {'Name': 'x'}),
    ('create_data_tables', 'dataTables', 'dataTables', 'destinationInstanceId', 'list_data_tables',
     {'Id': 't1', 'Name': 'x'}),
]


def target_arn(region):
    from test_region_support import ACCOUNT_B, INSTANCE_B
    return instance_arn(region, ACCOUNT_B, INSTANCE_B)


def run_connect_create(module_name, key, target_key, operation, items, region, stubbed, monkeypatch):
    module = importlib.import_module(module_name)
    client, stub = stubbed('connect', region)
    stub.add_client_error(operation, service_error_code='UnknownOperationException')
    monkeypatch.setattr(module, 'create_destination_client', lambda *a, **k: (client, INSTANCE_A, region))
    event = {target_key: target_arn(region), key: items}
    if module_name == 'create_rules':
        source, _ = stubbed('connect', 'us-east-1')
        monkeypatch.setattr(module, 'create_connect_client_from_arn', lambda arn: (source, INSTANCE_A, 'us-east-1'))
        for helper in ('build_queue_mapping', 'build_user_mapping', 'build_contact_flow_mapping'):
            monkeypatch.setattr(module, helper, lambda *a: {})
        event['sourceInstanceId'] = instance_arn('us-east-1')
    return module.lambda_handler(event, None)


@pytest.mark.parametrize('region', REGIONS)
@pytest.mark.parametrize('module_name, feature, key, target_key, operation, item', CONNECT_CREATES)
def test_create_step_skips_a_feature_the_target_region_does_not_offer(module_name, feature, key, target_key,
                                                                      operation, item, region, stubbed,
                                                                      monkeypatch):
    result = run_connect_create(module_name, key, target_key, operation, [item, dict(item)], region, stubbed,
                                monkeypatch)
    assert_skipped(result, feature, 'target', region, 'UnknownOperationException')
    assert result['manualFollowUp'][0]['count'] == 2
    assert not list(migration_report.failure_entries(result))  # no failed item: a clean skip


@pytest.mark.parametrize('module_name, feature, key, target_key, operation, item',
                         [c for c in CONNECT_CREATES if c[0] != 'create_data_tables'])  # it returns early when empty
def test_create_step_with_nothing_to_create_skips_without_a_follow_up(module_name, feature, key, target_key,
                                                                      operation, item, stubbed, monkeypatch):
    result = run_connect_create(module_name, key, target_key, operation, [], 'af-south-1', stubbed, monkeypatch)
    assert result['status'] == 'SKIPPED' and result['manualFollowUp'] == []


def test_vocabulary_create_skips_when_the_target_region_lacks_the_api(stubbed, monkeypatch):
    import create_vocabularies
    client, stub = stubbed('connect', 'us-gov-west-1')
    stub.add_client_error('search_vocabularies', service_error_code='UnknownOperationException')
    stub.add_client_error('create_vocabulary', service_error_code='UnknownOperationException')
    monkeypatch.setattr(create_vocabularies, 'create_destination_client',
                        lambda *a, **k: (client, INSTANCE_A, 'us-gov-west-1'))
    result = create_vocabularies.lambda_handler({
        'destinationInstanceId': target_arn('us-gov-west-1'),
        'vocabularies': [{'Id': 'v1', 'Name': 'x', 'LanguageCode': 'en-US', 'Content': 'Phrase\tIPA\n'}]}, None)
    assert_skipped(result, 'vocabularies', 'target', 'us-gov-west-1', 'UnknownOperationException')
    assert result['manualFollowUp'][0]['count'] == 1 and result['failedVocabularies'] == []


class QConnectSession:
    def __init__(self, qconnect):
        self.qconnect = qconnect

    def client(self, service, **kwargs):
        assert service == 'qconnect'
        return self.qconnect


@pytest.mark.parametrize('region', ['af-south-1', 'us-gov-west-1', 'zz-future-10'])
@pytest.mark.parametrize('module_name, feature', [('create_ai_prompts', 'aiPrompts'),
                                                  ('create_ai_guardrails', 'aiGuardrails')])
def test_ai_create_skips_when_the_target_region_has_no_ai_agents(module_name, feature, region, stubbed,
                                                                 monkeypatch):
    module = importlib.import_module(module_name)
    qconnect, stub = stubbed('qconnect', region)
    stub.add_client_error('list_assistants', service_error_code='UnknownOperationException')
    monkeypatch.setattr(module, 'create_destination_session', lambda *a, **k: QConnectSession(qconnect))
    result = module.lambda_handler({'destinationInstanceId': target_arn(region),
                                    feature: [{'name': 'x'}]}, None)
    assert_skipped(result, feature, 'target', region, 'UnknownOperationException')


def test_ai_agent_create_skips_when_the_target_region_has_no_ai_agents(stubbed, monkeypatch):
    import create_ai_agents
    source, source_stub = stubbed('qconnect', 'us-east-1')
    target = UnreachableQConnect('af-south-1')
    source_stub.add_response('list_assistants', ASSISTANT)
    monkeypatch.setattr(create_ai_agents.boto3, 'client', lambda service, **kw: source)
    monkeypatch.setattr(create_ai_agents, 'get_qconnect_client', lambda *a, **k: target)
    result = create_ai_agents.lambda_handler({'sourceInstanceId': instance_arn('us-east-1'),
                                              'destinationInstanceId': target_arn('af-south-1'),
                                              'aiAgents': [{'name': 'x'}, {'name': 'y'}]}, None)
    assert_skipped(result, 'aiAgents', 'target', 'af-south-1', 'EndpointConnectionError')
    assert result['manualFollowUp'][0]['count'] == 2


def test_ai_agent_create_with_no_source_ai_agents_and_none_to_create_adds_nothing(stubbed, monkeypatch):
    """Source Region without AI agents (the list step already reported it), target Region without them too."""
    import create_ai_agents
    source, source_stub = stubbed('qconnect', 'af-south-1')
    target, target_stub = stubbed('qconnect', 'af-south-1')
    source_stub.add_client_error('list_assistants', service_error_code='UnknownOperationException')
    target_stub.add_client_error('list_assistants', service_error_code='UnknownOperationException')
    monkeypatch.setattr(create_ai_agents.boto3, 'client', lambda service, **kw: source)
    monkeypatch.setattr(create_ai_agents, 'get_qconnect_client', lambda *a, **k: target)
    result = create_ai_agents.lambda_handler({'sourceInstanceId': instance_arn('af-south-1'),
                                              'destinationInstanceId': target_arn('af-south-1'),
                                              'aiAgents': []}, None)
    assert result['status'] == 'SKIPPED' and result['manualFollowUp'] == []


# E5: VerifyTarget --------------------------------------------------------------------------------

def guarded_listing(side, kind):
    """One check that only lists kind on side: what guarded() reports when that listing fails."""
    import verify_target

    def run():
        side.listing(kind)
        return verify_target.new_check('names', kind)
    return verify_target.guarded('names', kind, run)


@pytest.mark.parametrize('region', REGIONS)
@pytest.mark.parametrize('kind, operation', [('rules', 'list_rules'), ('evaluationForms', 'list_evaluation_forms'),
                                             ('vocabularies', 'search_vocabularies'), ('views', 'list_views')])
def test_verify_reports_a_feature_the_region_lacks_as_a_follow_up(kind, operation, region, stubbed):
    import verify_target
    connect, stub = stubbed('connect', region)
    stub.add_client_error(operation, service_error_code='UnknownOperationException')
    result = guarded_listing(verify_target.Side(connect, INSTANCE_A, instance_arn(region)), kind)
    assert result['status'] == verify_target.PASS and result['issues'] == {}
    assert result['followUps'] == {region_utils.FEATURE_NOT_AVAILABLE: 1}
    summary = verify_target.summarize([result], {kind: True})
    assert summary['verdict'] == verify_target.PASS_WITH_FOLLOW_UPS


def test_verify_keeps_other_list_errors_as_failing_issues(stubbed):
    import verify_target
    connect, stub = stubbed('connect', 'eu-west-2')
    stub.add_client_error('list_rules', service_error_code='InternalServiceException')
    result = guarded_listing(verify_target.Side(connect, INSTANCE_A, instance_arn('eu-west-2')), 'rules')
    assert result['status'] == verify_target.FAIL and result['issues'] == {'InternalServiceException': 1}


@pytest.mark.parametrize('region', ['af-south-1', 'us-gov-west-1'])
def test_verify_without_ai_agents_in_the_region_follows_up_and_has_no_assistant(region, stubbed):
    import verify_target
    connect, _ = stubbed('connect', region)
    qconnect, stub = stubbed('qconnect', region)
    stub.add_client_error('list_assistants', service_error_code='UnknownOperationException')
    side = verify_target.Side(connect, INSTANCE_A, instance_arn(region), qconnect)
    assert side.assistant_id() is None and side.ai_unavailable == 'UnknownOperationException'
    for kind in ('aiAgents', 'aiPrompts', 'aiGuardrails'):
        result = guarded_listing(side, kind)
        assert result['followUps'] == {region_utils.FEATURE_NOT_AVAILABLE: 1} and result['issues'] == {}


def test_verify_ai_listing_with_an_unreachable_qconnect_endpoint_follows_up(stubbed):
    import verify_target
    connect, _ = stubbed('connect', 'af-south-1')
    side = verify_target.Side(connect, INSTANCE_A, instance_arn('af-south-1'), UnreachableQConnect('af-south-1'))
    result = guarded_listing(side, 'aiAgents')
    assert result['followUps'] == {region_utils.FEATURE_NOT_AVAILABLE: 1}


# A7: AI prompt model IDs are kept, never rewritten by a guessed prefix ----------------------------

PROMPT = {'name': 'x', 'aiPromptId': 'p1', 'type': 'ANSWER_GENERATION', 'templateType': 'TEXT',
          'templateConfiguration': {'textFullAIPromptEditTemplateConfiguration': {'text': 't'}}}


class RecordingQConnect:
    """Assistant found, no existing prompt; records the CreateAIPrompt parameters."""

    def __init__(self):
        self.created = []

    def get_paginator(self, name):
        pages = {'list_assistants': [ASSISTANT], 'list_ai_prompts': [{'aiPromptSummaries': []}]}[name]

        class Pages:
            def paginate(self, **kwargs):
                return pages
        return Pages()

    def create_ai_prompt(self, **params):
        self.created.append(params)
        return {'aiPrompt': {'aiPromptId': 'p2'}}


@pytest.mark.parametrize('model_id', ['eu.anthropic.claude-3-haiku-20240307-v1:0',
                                      'us.anthropic.claude-3-haiku-20240307-v1:0',
                                      'apac.anthropic.claude-3-haiku-20240307-v1:0',
                                      'anthropic.claude-3-haiku-20240307-v1:0'])
@pytest.mark.parametrize('source_region, target_region', [('eu-west-2', 'ap-southeast-2'),
                                                          ('us-east-1', 'eu-central-1'),
                                                          ('ap-northeast-1', 'ca-central-1'),
                                                          ('us-gov-west-1', 'us-gov-east-1'),
                                                          ('ap-south-1', 'ap-south-1')])
def test_ai_prompt_keeps_its_model_id_and_follows_up_when_the_regions_differ(model_id, source_region,
                                                                            target_region, monkeypatch):
    import create_ai_prompts
    qconnect = RecordingQConnect()
    monkeypatch.setattr(create_ai_prompts, 'create_destination_session', lambda *a, **k: QConnectSession(qconnect))
    result = create_ai_prompts.lambda_handler({'sourceInstanceId': instance_arn(source_region),
                                               'destinationInstanceId': target_arn(target_region),
                                               'aiPrompts': [{**PROMPT, 'modelId': model_id}]}, None)
    assert [p['modelId'] for p in qconnect.created] == [model_id]
    assert result['createdPrompts'][0]['newId'] == 'p2'
    if source_region == target_region:
        assert result['manualFollowUp'] == []
    else:
        [follow_up] = result['manualFollowUp']
        assert (follow_up['type'], follow_up['source'], follow_up['modelId']) == ('aiPromptModel', 'p1', model_id)
        assert target_region in follow_up['action']


def test_no_region_prefix_guess_is_left_in_the_ai_prompt_step():
    text = (ROOT / 'lambda' / 'create_ai_prompts.py').read_text()
    assert "startswith('eu-')" not in text and "replace('us.'" not in text and "replace('eu.'" not in text


# Report and UI --------------------------------------------------------------------------------------

def test_report_shows_a_skipped_feature_as_skipped_with_its_follow_up():
    from test_migration_report import EXECUTION_ARN, FakeSfn, history, statuses
    skipped = region_utils.feature_skipped_result('aiAgents', 'af-south-1', 'source', 'UnknownOperationException',
                                                  aiAgents=[])
    report = migration_report.build_report(
        FakeSfn(history(('ListAIAgents', 'ok', skipped), ('CreateQueues', 'ok', {'queueMapping': {}}))),
        EXECUTION_ARN)
    assert report['status'] == 'SUCCEEDED' and report['failedItemCount'] == 0
    assert statuses(report) == {'ListAIAgents': 'SKIPPED', 'CreateQueues': 'SUCCEEDED'}
    assert [f['type'] for f in report['manualFollowUp']] == ['FeatureNotAvailableInRegion']


def test_ui_renders_the_follow_up_through_escape_html():
    html = (ROOT / 'frontend' / 'index.html').read_text()
    start = html.index("if (f.type === 'FeatureNotAvailableInRegion')")
    branch = html[start:html.index('</tr>`;', start)]
    for field in ('f.feature', 'f.side', 'f.region', 'f.action', 'counted'):
        assert field in branch
    assert branch.count('${escapeHtml(') == 4
