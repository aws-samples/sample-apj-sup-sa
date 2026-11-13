"""ARN rewriting across account and Region, plus Lex and Lambda mapping."""
import json

import pytest
from botocore.stub import ANY

import arn_mapping
from arn_mapping import ArnRewriter, InstanceRef
from conftest import (
    SOURCE_ACCOUNT,
    SOURCE_INSTANCE_ARN,
    SOURCE_INSTANCE_ID,
    SOURCE_REGION,
    TARGET_ACCOUNT,
    TARGET_INSTANCE_ARN,
    TARGET_INSTANCE_ID,
    TARGET_REGION,
)
from region_utils import MigrationStepError

SRC_Q = 'aaaaaaaa-0000-4000-8000-000000000001'
DST_Q = 'bbbbbbbb-0000-4000-8000-000000000001'
SRC_SP = 'aaaaaaaa-0000-4000-8000-000000000009'
SRC_LEX_ALIAS = f'arn:aws:lex:{SOURCE_REGION}:{SOURCE_ACCOUNT}:bot-alias/SRCBOT0001/SRCALIAS01'
DST_LEX_ALIAS = f'arn:aws:lex:{TARGET_REGION}:{TARGET_ACCOUNT}:bot-alias/DSTBOT0001/DSTALIAS01'
SRC_LEX_OTHER = f'arn:aws:lex:{SOURCE_REGION}:{SOURCE_ACCOUNT}:bot-alias/SRCBOT0002/SRCALIAS02'
SRC_LAMBDA = f'arn:aws:lambda:{SOURCE_REGION}:{SOURCE_ACCOUNT}:function:lookup-customer'
DST_LAMBDA = f'arn:aws:lambda:{TARGET_REGION}:{TARGET_ACCOUNT}:function:lookup-customer'
SRC_LAMBDA_UNMATCHED = f'arn:aws:lambda:{SOURCE_REGION}:{SOURCE_ACCOUNT}:function:legacy-dip:prod'


def flow(*arns):
    actions = [{'Identifier': str(i), 'Parameters': {'Ref': arn}} for i, arn in enumerate(arns)]
    return json.dumps({'Version': '2019-10-30', 'Actions': actions})


@pytest.fixture
def rewriter():
    return ArnRewriter(
        InstanceRef.from_arn(SOURCE_INSTANCE_ARN),
        InstanceRef.from_arn(TARGET_INSTANCE_ARN),
        id_maps={'queues': {SRC_Q: DST_Q}},
        lambda_map={'lookup-customer': DST_LAMBDA},
        lex_map={SRC_LEX_ALIAS: DST_LEX_ALIAS},
        lex_names={SRC_LEX_ALIAS: ('OrderBot', 'live'), SRC_LEX_OTHER: ('SurveyBot', 'prod')},
    )


def test_instance_ref_parses_arn():
    ref = InstanceRef.from_arn(TARGET_INSTANCE_ARN)
    assert (ref.partition, ref.region, ref.account, ref.instance_id) == (
        'aws', TARGET_REGION, TARGET_ACCOUNT, TARGET_INSTANCE_ID)
    assert ref.arn == TARGET_INSTANCE_ARN


def test_mapped_queue_rewrites_account_region_and_id(rewriter):
    src = f'{SOURCE_INSTANCE_ARN}/queue/{SRC_Q}'
    content, report = rewriter.rewrite(flow(src))
    assert f'{TARGET_INSTANCE_ARN}/queue/{DST_Q}' in content
    assert SOURCE_ACCOUNT not in content and SOURCE_REGION not in content
    assert report['rewritten'][0]['type'] == 'queue'
    assert report['unmapped'] == []


def test_instance_arn_maps_to_target_instance(rewriter):
    content, _ = rewriter.rewrite(flow(SOURCE_INSTANCE_ARN))
    assert TARGET_INSTANCE_ARN in content and SOURCE_INSTANCE_ID not in content


def test_aws_managed_view_gets_target_region(rewriter):
    managed = f'arn:aws:connect:{SOURCE_REGION}:aws:view/standard-detail:$LATEST'
    content, report = rewriter.rewrite(flow(managed))
    assert f'arn:aws:connect:{TARGET_REGION}:aws:view/standard-detail:$LATEST' in content
    assert report['rewritten'][0]['type'] == 'awsManaged'


def test_unmapped_instance_child_is_kept_and_reported(rewriter):
    src = f'{SOURCE_INSTANCE_ARN}/security-profile/{SRC_SP}'
    content, report = rewriter.rewrite(flow(src))
    assert src in content
    assert report['unmapped'][0]['type'] == 'security-profile'
    assert report['unmapped'][0]['arn'] == src


def test_other_service_in_source_account_is_reported(rewriter):
    other = f'arn:aws:wisdom:{SOURCE_REGION}:{SOURCE_ACCOUNT}:assistant/abc'
    content, report = rewriter.rewrite(flow(other))
    assert other in content
    assert report['unmapped'][0]['type'] == 'wisdom'


def test_lambda_mapped_by_name_to_target(rewriter):
    content, report = rewriter.rewrite(flow(SRC_LAMBDA))
    assert DST_LAMBDA in content and SRC_LAMBDA not in content
    assert report['placeholders'] == []


def test_unmatched_lambda_gets_placeholder_in_target_account_and_region(rewriter):
    content, report = rewriter.rewrite(flow(SRC_LAMBDA_UNMATCHED))
    expected = f'arn:aws:lambda:{TARGET_REGION}:{TARGET_ACCOUNT}:function:MIGRATION-PLACEHOLDER-legacy-dip'
    assert expected in content
    assert SRC_LAMBDA_UNMATCHED not in content
    item = report['placeholders'][0]
    assert item['type'] == 'lambda' and item['source'] == SRC_LAMBDA_UNMATCHED and 'name' not in item


def test_placeholder_function_name_is_truncated_to_lambda_limit():
    target = InstanceRef.from_arn(TARGET_INSTANCE_ARN)
    arn = arn_mapping.lambda_placeholder_arn(target, 'x' * 64)
    assert len(arn.split(':function:')[1]) == 64


def test_lambda_already_in_target_is_untouched(rewriter):
    content, report = rewriter.rewrite(flow(DST_LAMBDA))
    assert DST_LAMBDA in content
    assert report == {'rewritten': [], 'placeholders': [], 'unmapped': []}


def test_lex_mapped_by_name_to_target_alias(rewriter):
    content, report = rewriter.rewrite(flow(SRC_LEX_ALIAS))
    assert DST_LEX_ALIAS in content and SRC_LEX_ALIAS not in content
    assert report['rewritten'][0]['type'] == 'lexV2'


def test_unmatched_lex_keeps_flow_with_placeholder_and_reports_ids_not_names(rewriter):
    content, report = rewriter.rewrite(flow(SRC_LEX_OTHER))
    assert arn_mapping.lex_placeholder_arn(InstanceRef.from_arn(TARGET_INSTANCE_ARN)) in content
    item = report['placeholders'][0]
    assert item['type'] == 'lexV2' and item['source'] == SRC_LEX_OTHER and 'name' not in item
    assert 'SurveyBot' not in json.dumps(report)


def test_dynamic_lambda_is_reported_and_lex_v1_blocks_are_not_handled(rewriter):
    content = json.dumps({'Actions': [
        {'Parameters': {'LexBot': {'Name': 'OldBot', 'Region': SOURCE_REGION}}},
        {'Parameters': {'LambdaFunctionARN': '$.Attributes.fn'}},
    ]})
    new_content, report = rewriter.rewrite(content)
    kinds = {i['type'] for i in report['unmapped']}
    assert kinds == {'lambdaDynamic'}  # Lex V1 (Lex Classic) is not supported, so it is not detected
    assert new_content == content


def test_rewrite_reports_are_independent_per_call(rewriter):
    _, first = rewriter.rewrite(flow(SRC_LAMBDA_UNMATCHED))
    _, second = rewriter.rewrite(flow(SRC_LAMBDA))
    assert first['placeholders'] and not second['placeholders']


def test_merge_follow_up_deduplicates_across_flows_by_flow_id(rewriter):
    follow_up = {}
    for flow_id in ('flow-a', 'flow-b'):
        _, report = rewriter.rewrite(flow(SRC_LAMBDA_UNMATCHED))
        arn_mapping.merge_follow_up(follow_up, flow_id, report)
    entries = list(follow_up.values())
    assert len(entries) == 1 and entries[0]['flows'] == ['flow-a', 'flow-b']


def test_build_lex_map_matches_bot_and_alias_names(stubbed):
    src_connect, src_stub = stubbed('connect', SOURCE_REGION)
    dst_connect, dst_stub = stubbed('connect', TARGET_REGION)
    src_lex, src_lex_stub = stubbed('lexv2-models', SOURCE_REGION)
    dst_lex, dst_lex_stub = stubbed('lexv2-models', TARGET_REGION)

    dst_stub.add_response('list_bots', {'LexBots': [{'LexV2Bot': {'AliasArn': DST_LEX_ALIAS}}]},
                          {'InstanceId': TARGET_INSTANCE_ID, 'LexVersion': 'V2'})
    dst_lex_stub.add_response('describe_bot', {'botId': 'DSTBOT0001', 'botName': 'OrderBot'},
                              {'botId': 'DSTBOT0001'})
    dst_lex_stub.add_response('describe_bot_alias', {'botAliasId': 'DSTALIAS01', 'botAliasName': 'live'},
                              {'botId': 'DSTBOT0001', 'botAliasId': 'DSTALIAS01'})
    src_stub.add_response('list_bots', {'LexBots': [{'LexV2Bot': {'AliasArn': SRC_LEX_ALIAS}},
                                                    {'LexV2Bot': {'AliasArn': SRC_LEX_OTHER}}]},
                          {'InstanceId': SOURCE_INSTANCE_ID, 'LexVersion': 'V2'})
    src_lex_stub.add_response('describe_bot', {'botId': 'SRCBOT0001', 'botName': 'OrderBot'},
                              {'botId': 'SRCBOT0001'})
    src_lex_stub.add_response('describe_bot_alias', {'botAliasId': 'SRCALIAS01', 'botAliasName': 'live'},
                              {'botId': 'SRCBOT0001', 'botAliasId': 'SRCALIAS01'})
    src_lex_stub.add_response('describe_bot', {'botId': 'SRCBOT0002', 'botName': 'SurveyBot'},
                              {'botId': 'SRCBOT0002'})
    src_lex_stub.add_response('describe_bot_alias', {'botAliasId': 'SRCALIAS02', 'botAliasName': 'prod'},
                              {'botId': 'SRCBOT0002', 'botAliasId': 'SRCALIAS02'})

    regions = []

    def src_factory(region):
        regions.append(('src', region))
        return src_lex

    def dst_factory(region):
        regions.append(('dst', region))
        return dst_lex

    lex_map, lex_names = arn_mapping.build_lex_map(
        src_connect, dst_connect, SOURCE_INSTANCE_ID, TARGET_INSTANCE_ID, src_factory, dst_factory)

    assert lex_map == {SRC_LEX_ALIAS: DST_LEX_ALIAS}
    assert lex_names[SRC_LEX_OTHER] == ('SurveyBot', 'prod')
    # Lex calls go to the Region in each alias ARN, with the matching side's credentials.
    assert ('dst', TARGET_REGION) in regions and ('src', SOURCE_REGION) in regions


def test_build_lambda_map_prefers_unqualified_arn(stubbed):
    dst_connect, dst_stub = stubbed('connect', TARGET_REGION)
    dst_stub.add_response('list_lambda_functions',
                          {'LambdaFunctions': [DST_LAMBDA + ':live', DST_LAMBDA]},
                          {'InstanceId': TARGET_INSTANCE_ID})
    assert arn_mapping.build_lambda_map(dst_connect, TARGET_INSTANCE_ID) == {'lookup-customer': DST_LAMBDA}


def test_build_name_map_access_denied_fails_the_step(stubbed):
    src_connect, src_stub = stubbed('connect', SOURCE_REGION)
    dst_connect, _ = stubbed('connect', TARGET_REGION)
    src_stub.add_client_error('list_queues', service_error_code='AccessDeniedException',
                              service_message='not authorized to perform: connect:ListQueues',
                              http_status_code=403, expected_params={'InstanceId': ANY, 'QueueTypes': ['STANDARD']})
    with pytest.raises(MigrationStepError) as info:
        arn_mapping.build_name_map(src_connect, dst_connect, SOURCE_INSTANCE_ID, TARGET_INSTANCE_ID, 'queues')
    assert info.value.action == 'ListQueues'
    assert SOURCE_INSTANCE_ID in info.value.resource


def test_build_name_map_matches_by_name(stubbed):
    src_connect, src_stub = stubbed('connect', SOURCE_REGION)
    dst_connect, dst_stub = stubbed('connect', TARGET_REGION)
    src_stub.add_response('list_queues', {'QueueSummaryList': [{'Id': SRC_Q, 'Name': 'Sales'},
                                                               {'Id': 'x', 'Name': 'OnlyInSource'}]},
                          {'InstanceId': SOURCE_INSTANCE_ID, 'QueueTypes': ['STANDARD']})
    dst_stub.add_response('list_queues', {'QueueSummaryList': [{'Id': DST_Q, 'Name': 'Sales'}]},
                          {'InstanceId': TARGET_INSTANCE_ID, 'QueueTypes': ['STANDARD']})
    assert arn_mapping.build_name_map(
        src_connect, dst_connect, SOURCE_INSTANCE_ID, TARGET_INSTANCE_ID, 'queues') == {SRC_Q: DST_Q}
