"""VerifyTarget: read only target verification (fake clients validated against the botocore model, no AWS)."""
import json
import pathlib
import time

import botocore.session
import pytest
from botocore import xform_name
from botocore.exceptions import ClientError, OperationNotPageableError
from botocore.validate import validate_parameters
from cfnlint.decode import cfn_yaml

import api_migration_status
import arn_mapping
import migration_report
import region_utils
import verify_migration
import verify_target
from conftest import (EXTERNAL_ID, SOURCE_ACCOUNT, SOURCE_INSTANCE_ARN, SOURCE_INSTANCE_ID, SOURCE_REGION,
                      TARGET_ACCOUNT, TARGET_INSTANCE_ARN, TARGET_INSTANCE_ID, TARGET_REGION, TARGET_ROLE_ARN)
from test_migration_report import EXECUTION_ARN, FakeSfn, history
from test_state_machine import final_outcome

ROOT = pathlib.Path(__file__).resolve().parent.parent
SESSION = botocore.session.get_session()
MODELS = {service: SESSION.get_service_model(service) for service in ('connect', 'qconnect')}
LIST_KEYS = {spec.operation: spec.list_key for spec in verify_target.KINDS.values()}
LIST_KEYS.update(list_assistants='assistantSummaries', list_phone_numbers_v2='ListPhoneNumbersSummaryList',
                 list_lambda_functions='LambdaFunctions', list_bots='LexBots')
CROSS = verify_target.SourceRef(SOURCE_INSTANCE_ID, SOURCE_ACCOUNT, SOURCE_REGION, TARGET_ACCOUNT, TARGET_REGION)

# Personal data and names that must never reach the output or the logs.
SECRETS_IN_DATA = ('jdoe', 'Jane', 'Doe', 'jane.doe@example.com', '+15550100', '+15550123', '+15550199',
                   'Main flow', 'Sales queue', 'Weekdays', 'To Sales', 'Hotline')


class FakeClient:
    """Connect or AI agents (qconnect) stand in with canned list pages and Describe results.

    Every call is validated against the botocore input shape and recorded as
    an IAM action ('connect:DescribeQueue', 'wisdom:ListAIAgents', ...).
    """

    def __init__(self, service, data, errors=None):
        self.service, self.data, self.errors, self.actions = service, data, errors or {}, []
        self.operations = {xform_name(op): op for op in MODELS[service].operation_names}

    def _record(self, method, params):
        operation = self.operations[method]  # KeyError: not an operation of this service
        validate_parameters(params, MODELS[self.service].operation_model(operation).input_shape)
        self.actions.append(f"{'wisdom' if self.service == 'qconnect' else 'connect'}:{operation}")
        if method in self.errors:
            code = self.errors[method]
            if code == 'NotPageable':
                raise OperationNotPageableError(operation_name=method)
            raise ClientError({'Error': {'Code': code, 'Message': 'details with jdoe'}}, operation)

    def get_paginator(self, method):
        client = self

        class Paginator:
            def paginate(self, **params):
                client._record(method, params)
                return [{LIST_KEYS[method]: client.data.lists.get(method, [])}]
        return Paginator()

    def __getattr__(self, method):
        if not method.startswith('describe_'):
            raise AttributeError(method)

        def call(**params):
            self._record(method, params)
            resource_id = next(v for k, v in params.items() if k != 'InstanceId')
            if (method, resource_id) not in self.data.describes:
                raise ClientError({'Error': {'Code': 'ResourceNotFoundException', 'Message': resource_id}},
                                  self.operations[method])
            return self.data.describes[(method, resource_id)]
        return call


class Data:
    """Canned listings and Describe results for one instance."""

    def __init__(self):
        self.lists, self.describes = {}, {}

    def add(self, kind, **item):
        self.lists.setdefault(verify_target.KINDS[kind].operation, []).append(item)
        return self

    def raw(self, method, *items):
        self.lists.setdefault(method, []).extend(items)
        return self

    def describe(self, method, resource_id, key, body):
        self.describes[(method, resource_id)] = {key: body}
        return self


def make_sides(source_data, target_data, ai=False, source_errors=None, target_errors=None):
    source = verify_target.Side(FakeClient('connect', source_data, source_errors), SOURCE_INSTANCE_ID,
                                SOURCE_INSTANCE_ARN, FakeClient('qconnect', source_data) if ai else None,
                                is_source=True)
    target = verify_target.Side(FakeClient('connect', target_data, target_errors), TARGET_INSTANCE_ID,
                                TARGET_INSTANCE_ARN, FakeClient('qconnect', target_data) if ai else None)
    return source, target


def actions_of(side):
    return set(side.connect.actions) | set(side.qconnect.actions if side.qconnect else [])


# --- names ----------------------------------------------------------------------------------------

def test_names_report_found_missing_and_extra_and_never_count_skipped_items():
    src = (Data().add('contactFlows', Id='s-main', Name='Main flow').add('contactFlows', Id='s-gone', Name='Gone')
           .add('contactFlows', Id='s-def', Name='Default agent hold').add('contactFlows', Id='s-smp', Name='Sample IVR')
           .add('queues', Id='s-q1', Name='Sales queue', QueueType='STANDARD')
           .add('queues', Id='s-aq', Name='agent-queue', QueueType='AGENT')
           .add('views', Id='s-v1', Name='Card', Type='CUSTOMER_MANAGED', Status='PUBLISHED')
           .add('views', Id='s-v2', Name='AWS Detail', Type='CUSTOMER_MANAGED', Status='PUBLISHED')
           .add('views', Id='s-v3', Name='Draft', Type='CUSTOMER_MANAGED', Status='SAVED')
           .add('predefinedAttributes', Name='Tier').add('predefinedAttributes', Name='Language'))
    tgt = (Data().add('contactFlows', Id='t-main', Name='Main flow').add('contactFlows', Id='t-extra', Name='Extra')
           .add('contactFlows', Id='t-def', Name='Default agent hold')
           .add('queues', Id='t-q1', Name='Sales queue', QueueType='STANDARD')
           .add('views', Id='t-v1', Name='Card', Type='CUSTOMER_MANAGED', Status='PUBLISHED')
           .add('predefinedAttributes', Name='Tier'))
    source, target = make_sides(src, tgt)

    flows = verify_target.check_names(source, target, 'contactFlows')
    assert {k: flows[k] for k in ('status', 'checked', 'found', 'missing', 'extra', 'skipped', 'ids')} == {
        'status': 'FAIL', 'checked': 2, 'found': 1, 'missing': 1, 'extra': 1, 'skipped': 2, 'ids': ['s-gone']}
    assert flows['issues'] == {'MissingInTarget': 1}

    queues = verify_target.check_names(source, target, 'queues')
    assert (queues['status'], queues['checked'], queues['missing'], queues['skipped']) == ('PASS', 1, 0, 1)
    views = verify_target.check_names(source, target, 'views')
    assert (views['status'], views['checked'], views['missing'], views['skipped']) == ('PASS', 1, 0, 2)
    # No ID exists for predefined attributes: counted, never listed (the name is the only key).
    attributes = verify_target.check_names(source, target, 'predefinedAttributes')
    assert (attributes['missing'], attributes['ids']) == (1, [])
    assert 'Language' not in json.dumps([flows, queues, views, attributes])


def test_skipped_types_and_ai_with_the_flag_off_are_not_compared():
    source, target = make_sides(Data(), Data())
    features = {'phoneNumbers': True, 'lambdaFunctions': True, 'lexBots': True,
                'aiAgents': False, 'aiPrompts': False, 'aiGuardrails': False}
    result = verify_target.verify(source, target, features, CROSS, verify_target.Budget())
    assert result['verdict'] == 'PASS' and result['checks'] == []
    assert result['notChecked'] == {'phoneNumbers': 'NotMigratedByDesign', 'lambdaFunctions': 'CheckedInFlowContent',
                                    'lexBots': 'CheckedInFlowContent'}
    assert actions_of(source) == actions_of(target) == set()


def test_ai_resources_are_compared_by_name_when_the_flag_is_on():
    src = (Data().raw('list_assistants', {'assistantId': 'a-src', 'name': 'x'})
           .add('aiAgents', aiAgentId='ag-1', name='Helper').add('aiAgents', aiAgentId='ag-2', name='Gone'))
    tgt = Data().raw('list_assistants', {'assistantId': 'a-tgt', 'name': 'y'}).add('aiAgents', aiAgentId='ag-9',
                                                                                     name='Helper')
    source, target = make_sides(src, tgt, ai=True)
    result = verify_target.verify(source, target, {'aiAgents': True}, CROSS, verify_target.Budget())
    (check,) = result['checks']
    assert (check['type'], check['found'], check['missing'], check['ids']) == ('aiAgents', 1, 1, ['ag-2'])
    assert result['verdict'] == 'FAIL'
    assert actions_of(target) == {'wisdom:ListAssistants', 'wisdom:ListAIAgents'}


def test_a_list_the_sdk_does_not_have_is_a_follow_up_and_other_list_errors_fail_the_check():
    source, target = make_sides(Data(), Data(), target_errors={'list_data_tables': 'NotPageable',
                                                               'list_rules': 'InvalidRequestException'})
    result = verify_target.verify(source, target, {'dataTables': True, 'rules': True}, CROSS, verify_target.Budget())
    tables, rules = result['checks']
    assert tables['status'] == 'PASS' and tables['followUps'] == {'SdkLacksOperation': 1}
    assert rules['status'] == 'FAIL' and rules['issues'] == {'InvalidRequestException': 1}
    assert 'jdoe' not in json.dumps(result)


# --- flow and module content ----------------------------------------------------------------------

SOURCE_FLOW = json.dumps({'Actions': [
    {'Parameters': {'LambdaFunctionARN': f'arn:aws:lambda:{SOURCE_REGION}:{SOURCE_ACCOUNT}:function:lookup'}},
    {'Parameters': {'QueueId': f'arn:aws:connect:{SOURCE_REGION}:{SOURCE_ACCOUNT}:instance/{SOURCE_INSTANCE_ID}'
                               '/queue/q-1'}},
    {'Parameters': {'PromptId': f'arn:aws:connect:{SOURCE_REGION}:{TARGET_ACCOUNT}:instance/{TARGET_INSTANCE_ID}'
                                '/prompt/p-1'}},
    {'Parameters': {'LexV2Bot': {'AliasArn': f'arn:aws:lex:{SOURCE_REGION}:{SOURCE_ACCOUNT}:bot-alias/'
                                             'AAAAAAAAAA/BBBBBBBBBB'}}},
    {'Parameters': {'WisdomAssistantArn': f'arn:aws:wisdom:{SOURCE_REGION}:{SOURCE_ACCOUNT}:assistant/as-1'}},
    {'Parameters': {'ViewResource': {'Id': f'arn:aws:connect:{SOURCE_REGION}:aws:view/detail'}}},
    {'Parameters': {'Text': f'instance {SOURCE_INSTANCE_ID}'}},
]})
CLEAN_FLOW = json.dumps({'Actions': [
    {'Parameters': {'LambdaFunctionARN': f'arn:aws:lambda:{TARGET_REGION}:{TARGET_ACCOUNT}:function:lookup'}},
    {'Parameters': {'QueueId': f'arn:aws:connect:{TARGET_REGION}:{TARGET_ACCOUNT}:instance/{TARGET_INSTANCE_ID}'
                               '/queue/q-9'}},
    {'Parameters': {'ViewResource': {'Id': f'arn:aws:connect:{TARGET_REGION}:aws:view/detail'}}},
]})


def flow_pair(target_content, module=False):
    kind = 'contactFlowModules' if module else 'contactFlows'
    method, key = (('describe_contact_flow_module', 'ContactFlowModule') if module
                   else ('describe_contact_flow', 'ContactFlow'))
    src = Data().add(kind, Id='s-1', Name='Main flow').add(kind, Id='s-2', Name='Clean flow')
    tgt = (Data().add(kind, Id='t-1', Name='Main flow').add(kind, Id='t-2', Name='Clean flow')
           .describe(method, 't-1', key, {'Id': 't-1', 'Name': 'Main flow', 'Content': target_content})
           .describe(method, 't-2', key, {'Id': 't-2', 'Name': 'Clean flow', 'Content': CLEAN_FLOW}))
    return make_sides(src, tgt)


def rewriter_mapper(target, id_maps=None, lambda_map=None, lex_map=None):
    """A ReferenceMapper over the flow rewriter itself (arn_mapping.ArnRewriter) with the given mappings."""
    rewriter = arn_mapping.ArnRewriter(arn_mapping.InstanceRef.from_arn(SOURCE_INSTANCE_ARN),
                                       arn_mapping.InstanceRef.from_arn(TARGET_INSTANCE_ARN),
                                       id_maps, lambda_map, lex_map)
    return verify_target.ReferenceMapper(lambda: rewriter, target)


@pytest.mark.parametrize('module', [False, True])
def test_a_flow_with_source_references_is_flagged_by_target_id_and_a_clean_flow_passes(module):
    source, target = flow_pair(SOURCE_FLOW, module)
    kind = 'contactFlowModules' if module else 'contactFlows'
    # The target has the source queue (by name) and no assistant, function or bot.
    mapper = rewriter_mapper(target, id_maps={'queues': {'q-1': 'q-9'}})
    result = verify_target.check_content(source, target, kind, CROSS, verify_target.Budget(), mapper)
    assert result['check'] == ('moduleContent' if module else 'flowContent')
    assert result['status'] == 'FAIL' and result['checked'] == 2
    # The queue and the AWS managed view map, so the flow should point at the target: FAIL.
    assert result['issues'] == {'MappableSourceReference': 1} and result['ids'] == ['t-1']
    assert result['followUps'] == {'UnmappableSourceReference': 1}
    reasons = {'NoMatchingLambda': 1, 'NoMatchingLexBot': 1, 'NoMatchingTargetResource': 1,
               'NoTargetAssistant': 1, 'SourceIdOutsideArn': 1}
    assert result['flows'] == [{'id': 't-1', 'placeholders': 0, 'mappable': 2, 'followUps': reasons,
                                'sourceArnTypes': {'lambda': 1, 'lex': 1, 'other': 2, 'prompt': 1, 'queue': 1,
                                                   'wisdom': 1}}]
    resource_type = 'contactFlowModule' if module else 'contactFlow'
    assert [(f['resourceType'], f['flowId'], f['arnType'], f['reasonCode'], f['count'])
            for f in result['manualFollowUp']] == [
        (resource_type, 't-1', 'lambda', 'NoMatchingLambda', 1), (resource_type, 't-1', 'lex', 'NoMatchingLexBot', 1),
        (resource_type, 't-1', 'other', 'SourceIdOutsideArn', 1),
        (resource_type, 't-1', 'prompt', 'NoMatchingTargetResource', 1),
        (resource_type, 't-1', 'wisdom', 'NoTargetAssistant', 1)]
    assert result['flowsWithSourceReferences'] == 1
    text = json.dumps(result)
    assert 'Main flow' not in text and 'Clean flow' not in text and 'lookup' not in text and 'Actions' not in text
    assert 'arn:' not in text and SOURCE_ACCOUNT not in text


def test_without_mapping_information_a_source_arn_fails_closed():
    source, target = flow_pair(SOURCE_FLOW)
    result = verify_target.check_content(source, target, 'contactFlows', CROSS, verify_target.Budget())
    assert result['issues'] == {'MappableSourceReference': 1}
    assert result['flows'][0]['mappable'] == 6 and result['flows'][0]['followUps'] == {'SourceIdOutsideArn': 1}


def test_a_mapping_lookup_error_fails_the_flow_by_error_code():
    source, target = flow_pair(SOURCE_FLOW)

    def broken():
        raise ClientError({'Error': {'Code': 'InternalServiceException', 'Message': 'jdoe'}}, 'ListQueues')

    result = verify_target.check_content(source, target, 'contactFlows', CROSS, verify_target.Budget(),
                                         verify_target.ReferenceMapper(broken, target))
    assert result['status'] == 'FAIL' and result['issues'] == {'InternalServiceException': 1}
    assert 'jdoe' not in json.dumps(result)


def test_clean_flows_pass():
    source, target = flow_pair(CLEAN_FLOW)
    result = verify_target.check_content(source, target, 'contactFlows', CROSS, verify_target.Budget())
    assert result['status'] == 'PASS' and result['flows'] == [] and result['checked'] == 2


@pytest.mark.parametrize('ref_args, content, expected', [
    # Cross account: the source account is flagged even in the target Region.
    ((SOURCE_ACCOUNT, SOURCE_REGION, TARGET_ACCOUNT, TARGET_REGION),
     f'arn:aws:lambda:{TARGET_REGION}:{SOURCE_ACCOUNT}:function:f', {'lambda': 1}),
    # Same account, other Region: the source Region is flagged, the target Region is not.
    ((SOURCE_ACCOUNT, SOURCE_REGION, SOURCE_ACCOUNT, TARGET_REGION),
     f'arn:aws:lambda:{SOURCE_REGION}:{SOURCE_ACCOUNT}:function:f', {'lambda': 1}),
    ((SOURCE_ACCOUNT, SOURCE_REGION, SOURCE_ACCOUNT, TARGET_REGION),
     f'arn:aws:lambda:{TARGET_REGION}:{SOURCE_ACCOUNT}:function:f', {}),
    # Same account and Region: only the source instance ID counts.
    ((SOURCE_ACCOUNT, SOURCE_REGION, SOURCE_ACCOUNT, SOURCE_REGION),
     f'arn:aws:connect:{SOURCE_REGION}:{SOURCE_ACCOUNT}:instance/{SOURCE_INSTANCE_ID}/queue/q', {'queue': 1}),
    ((SOURCE_ACCOUNT, SOURCE_REGION, SOURCE_ACCOUNT, SOURCE_REGION),
     f'arn:aws:lambda:{SOURCE_REGION}:{SOURCE_ACCOUNT}:function:f', {}),
    # Bare IDs outside ARNs: the source instance ID, and the source account ID across accounts.
    ((SOURCE_ACCOUNT, SOURCE_REGION, TARGET_ACCOUNT, TARGET_REGION),
     f'"x": "{SOURCE_ACCOUNT}", "y": "{SOURCE_INSTANCE_ID}", "z": "9{SOURCE_ACCOUNT}"', {'other': 2}),
])
def test_source_account_region_and_instance_references(ref_args, content, expected):
    ref = verify_target.SourceRef(SOURCE_INSTANCE_ID, *ref_args)
    assert dict(verify_target.source_references(content, ref)) == expected


def test_placeholders_are_counted_as_follow_ups_not_failures():
    placeholder = (f'arn:aws:lambda:{TARGET_REGION}:{TARGET_ACCOUNT}:function:MIGRATION-PLACEHOLDER-lookup '
                   f'arn:aws:lambda:{TARGET_REGION}:{TARGET_ACCOUNT}:function:MIGRATION-PLACEHOLDER-other '
                   f'arn:aws:lex:{TARGET_REGION}:{TARGET_ACCOUNT}:bot-alias/MIGRATEBOT/PLACEHOLDR')
    source, target = flow_pair(placeholder)
    result = verify_target.check_content(source, target, 'contactFlows', CROSS, verify_target.Budget())
    assert result['status'] == 'PASS' and result['issues'] == {}
    assert result['placeholders'] == 3 and result['placeholderTypes'] == {'lambda': 2, 'lex': 1}
    assert result['followUps'] == {'Placeholder': 1}
    assert result['flows'] == [{'id': 't-1', 'sourceArnTypes': {}, 'placeholders': 3, 'mappable': 0,
                                'followUps': {'NoMatchingLambda': 2, 'NoMatchingLexBot': 1}}]
    assert [(f['flowId'], f['arnType'], f['reasonCode'], f['count']) for f in result['manualFollowUp']] == [
        ('t-1', 'lambda', 'NoMatchingLambda', 2), ('t-1', 'lex', 'NoMatchingLexBot', 1)]
    assert verify_target.summarize([result], {})['verdict'] == 'PASS_WITH_FOLLOW_UPS'


VOICE_ID_FLOW = json.dumps({'Version': '2019-10-30', 'StartAction': 'a', 'Actions': [
    {'Identifier': 'a', 'Type': 'StartVoiceIdStream', 'Parameters': {}, 'Transitions': {'NextAction': 'b'}},
    {'Identifier': 'b', 'Type': 'CheckVoiceId', 'Parameters': {'CheckVoiceIdOption': 'voiceAuthentication'},
     'Transitions': {}}]})


@pytest.mark.parametrize('module', [False, True])
def test_voice_id_blocks_are_an_end_of_support_follow_up(module):
    source, target = flow_pair(VOICE_ID_FLOW, module)
    kind = 'contactFlowModules' if module else 'contactFlows'
    result = verify_target.check_content(source, target, kind, CROSS, verify_target.Budget())
    assert result['status'] == 'PASS' and result['issues'] == {}
    assert result['followUps'] == {'VoiceIdEndOfSupport': 1}
    assert [(f['flowId'], f['arnType'], f['reasonCode'], f['count']) for f in result['manualFollowUp']] == [
        ('t-1', 'voiceid', 'VoiceIdEndOfSupport', 2)]
    assert 'May 20, 2026' in result['manualFollowUp'][0]['action']
    assert result['flows'] == [{'id': 't-1', 'sourceArnTypes': {}, 'placeholders': 0, 'mappable': 0,
                                'followUps': {'VoiceIdEndOfSupport': 2}}]
    assert verify_target.summarize([result], {})['verdict'] == 'PASS_WITH_FOLLOW_UPS'


def test_a_flow_without_voice_id_has_no_voice_id_follow_up():
    lookalike = json.dumps({'Actions': [{'Type': 'UpdateContactTextToSpeechVoice', 'Parameters': {
        'TextToSpeechVoice': 'Joanna', 'Note': 'CheckVoiceId'}}]})
    source, target = flow_pair(lookalike)
    result = verify_target.check_content(source, target, 'contactFlows', CROSS, verify_target.Budget())
    assert result['followUps'] == {} and result['manualFollowUp'] == []

def queue_data(target_config, target_hours='t-h', number_listed=True):
    src = (Data().add('queues', Id='s-q', Name='Sales queue', QueueType='STANDARD')
           .add('hoursOfOperations', Id='s-h', Name='Weekdays')
           .add('contactFlows', Id='s-out', Name='Outbound whisper')
           .describe('describe_queue', 's-q', 'Queue', {
               'QueueId': 's-q', 'Name': 'Sales queue', 'HoursOfOperationId': 's-h',
               'OutboundCallerConfig': {'OutboundCallerIdName': 'Sales', 'OutboundCallerIdNumberId': 'pn-src',
                                        'OutboundFlowId': 's-out'}}))
    if number_listed:
        src.raw('list_phone_numbers_v2', {'PhoneNumberId': 'pn-src', 'PhoneNumber': '+15550100'})
    tgt = (Data().add('queues', Id='t-q', Name='Sales queue', QueueType='STANDARD')
           .add('hoursOfOperations', Id='t-h', Name='Weekdays').add('contactFlows', Id='t-out', Name='Outbound whisper')
           .describe('describe_queue', 't-q', 'Queue', {'QueueId': 't-q', 'Name': 'Sales queue',
                                                        'HoursOfOperationId': target_hours,
                                                        'OutboundCallerConfig': target_config}))
    return make_sides(src, tgt)


def test_a_queue_with_the_source_caller_id_number_fails():
    source, target = queue_data({'OutboundCallerIdName': 'Sales', 'OutboundCallerIdNumberId': 'pn-src',
                                 'OutboundFlowId': 't-out'})
    result = verify_target.check_queues(source, target, CROSS, verify_target.Budget(), {})
    assert result['status'] == 'FAIL' and result['issues'] == {'SourceCallerIdNumber': 1}
    assert result['ids'] == ['t-q']
    assert '+15550100' not in json.dumps(result)
    assert 'connect:ListPhoneNumbersV2' in source.connect.actions


def test_a_clean_queue_passes_and_a_dropped_caller_id_number_is_a_follow_up():
    source, target = queue_data({'OutboundCallerIdName': 'Sales', 'OutboundFlowId': 't-out'})
    result = verify_target.check_queues(source, target, CROSS, verify_target.Budget(), {})
    assert result['status'] == 'PASS' and result['issues'] == {}
    assert result['followUps'] == {'OutboundCallerIdNumberNotSet': 1} and result['checked'] == 1


def test_a_queue_with_hours_or_outbound_flow_missing_in_the_target_fails():
    source, target = queue_data({'OutboundCallerIdName': 'Sales', 'OutboundFlowId': 's-out'}, target_hours='s-h')
    result = verify_target.check_queues(source, target, CROSS, verify_target.Budget(), {})
    assert result['issues'] == {'HoursNotInTarget': 1, 'OutboundFlowNotInTarget': 1}


# --- users ------------------------------------------------------------------------------------------

def user(user_id, routing, security, group=None):
    body = {'Id': user_id, 'Username': 'jdoe', 'RoutingProfileId': routing, 'SecurityProfileIds': security,
            'IdentityInfo': {'FirstName': 'Jane', 'LastName': 'Doe', 'Email': 'jane.doe@example.com'},
            'PhoneConfig': {'PhoneType': 'DESK_PHONE', 'DeskPhoneNumber': '+15550199'}}
    if group:
        body['HierarchyGroupId'] = group
    return body


def user_data():
    src = (Data().add('routingProfiles', Id='s-rp', Name='Agents').add('securityProfiles', Id='s-sp', Name='Agent')
           .add('securityProfiles', Id='s-sp2', Name='Supervisor').add('hierarchyGroups', Id='s-hg', Name='Team A'))
    tgt = (Data().add('routingProfiles', Id='t-rp', Name='Agents')
           .add('routingProfiles', Id='t-basic', Name='Basic Routing Profile')
           .add('securityProfiles', Id='t-sp', Name='Agent').add('securityProfiles', Id='t-sp2', Name='Supervisor')
           .add('hierarchyGroups', Id='t-hg', Name='Team A'))
    for n, (routing, security) in enumerate((('t-basic', ['t-sp']), ('t-rp', ['t-sp2']), ('t-rp', ['t-sp'])), 1):
        src.add('users', Id=f's-u{n}', Username=f'user{n}@example.com')
        src.describe('describe_user', f's-u{n}', 'User', user(f's-u{n}', 's-rp', ['s-sp'], 's-hg'))
        tgt.add('users', Id=f't-u{n}', Username=f'user{n}@example.com')
        tgt.describe('describe_user', f't-u{n}', 'User', user(f't-u{n}', routing, security, 't-hg'))
    return make_sides(src, tgt)


def test_users_mapped_to_the_wrong_routing_or_security_profile_fail_by_target_id():
    source, target = user_data()
    result = verify_target.check_users(source, target, verify_target.Budget(), {'hierarchyGroups': True}, {})
    assert result['checked'] == 3 and result['status'] == 'FAIL'
    assert result['issues'] == {'RoutingProfileMismatch': 1, 'SecurityProfilesMismatch': 1}
    assert result['ids'] == ['t-u1', 't-u2']
    text = json.dumps(result)
    assert all(value not in text for value in ('jdoe', 'Jane', 'Doe', 'example.com', '+15550199'))


def test_user_options_that_keep_the_default_profiles_are_not_mismatches():
    source, target = user_data()
    options = {'migrateRoutingProfiles': False, 'migrateSecurityProfiles': False}
    result = verify_target.check_users(source, target, verify_target.Budget(), {}, options)
    assert result['status'] == 'PASS' and result['checked'] == 3


# --- quick connects ---------------------------------------------------------------------------------

def quick_connect(qc_id, name, config):
    return {'QuickConnectId': qc_id, 'Name': name, 'QuickConnectConfig': config}


def test_user_queue_and_phone_number_quick_connects_resolve_to_target_resources():
    cases = {  # name: (source config, target config)
        'To Sales': ({'QuickConnectType': 'QUEUE', 'QueueConfig': {'QueueId': 's-q', 'ContactFlowId': 's-qf'}},
                     {'QuickConnectType': 'QUEUE', 'QueueConfig': {'QueueId': 't-q', 'ContactFlowId': 't-qf'}}),
        'To Jo': ({'QuickConnectType': 'USER', 'UserConfig': {'UserId': 's-u', 'ContactFlowId': 's-af'}},
                  {'QuickConnectType': 'USER', 'UserConfig': {'UserId': 't-u', 'ContactFlowId': 't-af'}}),
        'Hotline': ({'QuickConnectType': 'PHONE_NUMBER', 'PhoneConfig': {'PhoneNumber': '+15550123'}},
                    {'QuickConnectType': 'PHONE_NUMBER', 'PhoneConfig': {'PhoneNumber': '+15550123'}}),
        'Broken queue': ({'QuickConnectType': 'QUEUE', 'QueueConfig': {'QueueId': 's-q', 'ContactFlowId': 's-qf'}},
                         {'QuickConnectType': 'QUEUE', 'QueueConfig': {'QueueId': 's-q', 'ContactFlowId': 't-qf'}}),
        'To Max': ({'QuickConnectType': 'USER', 'UserConfig': {'UserId': 's-u', 'ContactFlowId': 's-af'}},
                   {'QuickConnectType': 'USER', 'UserConfig': {'UserId': 'ghost', 'ContactFlowId': 't-qf'}}),
        'Hotline flow': ({'QuickConnectType': 'PHONE_NUMBER',
                          'PhoneConfig': {'PhoneNumber': '+15550100', 'ContactFlowId': 's-qf'}},
                         {'QuickConnectType': 'PHONE_NUMBER',
                          'PhoneConfig': {'PhoneNumber': '+15550100', 'ContactFlowId': 'gone-flow'}}),
    }
    src = (Data().add('queues', Id='s-q', Name='Sales queue').add('users', Id='s-u', Username='jdoe')
           .add('contactFlows', Id='s-qf', Name='Default queue transfer')
           .add('contactFlows', Id='s-af', Name='Default agent transfer'))
    tgt = (Data().add('queues', Id='t-q', Name='Sales queue').add('users', Id='t-u', Username='jdoe')
           .add('contactFlows', Id='t-qf', Name='Default queue transfer')
           .add('contactFlows', Id='t-af', Name='Default agent transfer'))
    for n, (qc_name, (source_config, target_config)) in enumerate(cases.items()):
        src.add('quickConnects', Id=f's-qc{n}', Name=qc_name)
        src.describe('describe_quick_connect', f's-qc{n}', 'QuickConnect',
                     quick_connect(f's-qc{n}', qc_name, source_config))
        tgt.add('quickConnects', Id=f't-qc{n}', Name=qc_name)
        tgt.describe('describe_quick_connect', f't-qc{n}', 'QuickConnect',
                     quick_connect(f't-qc{n}', qc_name, target_config))
    source, target = make_sides(src, tgt)
    result = verify_target.check_quick_connects(source, target, verify_target.Budget())
    assert result['checked'] == 6
    assert result['issues'] == {'QueueNotInTarget': 1, 'UserNotInTarget': 1, 'FlowMismatch': 1,
                                'FlowNotInTarget': 1}
    # Broken queue (3), To Max (4: unknown user and the queue flow instead of the agent flow), Hotline flow (5).
    assert result['ids'] == ['t-qc3', 't-qc4', 't-qc5']
    text = json.dumps(result)
    assert '+1555' not in text and 'Hotline' not in text and 'jdoe' not in text


# --- verdict ----------------------------------------------------------------------------------------

def test_verdict_logic():
    passing, follow_up, failing = (verify_target.new_check('names', k) for k in ('queues', 'users', 'prompts'))
    verify_target.add_issue(follow_up, 'Placeholder', 'f-1', follow_up=True)
    verify_target.add_issue(failing, 'MissingInTarget', 'p-1')
    verify_target.add_issue(failing, 'MissingInTarget', 'p-2')
    assert verify_target.summarize([], {})['verdict'] == 'PASS'
    assert verify_target.summarize([passing], {})['verdict'] == 'PASS'
    assert verify_target.summarize([passing, follow_up], {})['verdict'] == 'PASS_WITH_FOLLOW_UPS'
    result = verify_target.summarize([passing, follow_up, failing], {})
    assert result['verdict'] == 'FAIL' and result['failedCheckCount'] == 1
    assert result['failedChecks'] == [{'originalId': 'names:prompts', 'status': 'failed',
                                       'errorCode': 'MissingInTarget', 'count': 2}]
    assert [c['status'] for c in result['checks']] == ['PASS', 'PASS', 'FAIL']
    error = verify_target.error_result(ValueError('secret text jdoe'))
    assert error['verdict'] == 'ERROR' and error['errorCode'] == 'ValueError'
    assert error['failedChecks'] == [{'originalId': 'VerifyTarget', 'status': 'failed', 'errorCode': 'ValueError'}]
    assert 'jdoe' not in json.dumps(error)


def test_a_check_that_runs_out_of_budget_fails_as_incomplete():
    source, target = flow_pair(CLEAN_FLOW)
    result = verify_target.check_content(source, target, 'contactFlows', CROSS, verify_target.Budget(max_describes=1))
    assert result['checked'] == 1 and result['issues'] == {'VerificationIncomplete': 1}
    assert verify_target.summarize([result], {})['verdict'] == 'FAIL'


# --- handler: ExternalId, errors, output and log hygiene --------------------------------------------

def full_data():
    """Source and target with every checked type, names, emails and phone numbers in the data."""
    src, tgt = Data(), Data()
    for data, prefix, instance in ((src, 's', SOURCE_INSTANCE_ID), (tgt, 't', TARGET_INSTANCE_ID)):
        (data.add('contactFlows', Id=f'{prefix}-f', Name='Main flow').add('hoursOfOperations', Id=f'{prefix}-h',
                                                                          Name='Weekdays')
         .add('queues', Id=f'{prefix}-q', Name='Sales queue', QueueType='STANDARD')
         .add('routingProfiles', Id=f'{prefix}-rp', Name='Agents').add('securityProfiles', Id=f'{prefix}-sp',
                                                                       Name='Agent')
         .add('users', Id=f'{prefix}-u', Username='jdoe').add('quickConnects', Id=f'{prefix}-qc', Name='Hotline')
         .add('contactFlowModules', Id=f'{prefix}-m', Name='Main flow')
         .raw('list_assistants', {'assistantId': f'{prefix}-a', 'name': 'Assistant'})
         .add('aiAgents', aiAgentId=f'{prefix}-ag', name='Helper')
         .add('aiPrompts', aiPromptId=f'{prefix}-ap', name='Prompt')
         .add('aiGuardrails', aiGuardrailId=f'{prefix}-gr', name='Guard'))
        # Fields the shared migration filters read, set to values the migration copies.
        copied = {'agentStatuses': {'Type': 'CUSTOM'}, 'views': {'Type': 'CUSTOMER_MANAGED', 'Status': 'PUBLISHED'},
                  'rules': {'PublishStatus': 'PUBLISHED'}, 'vocabularies': {'State': 'ACTIVE'}}
        for kind in ('agentStatuses', 'hierarchyGroups', 'prompts', 'dataTables', 'views', 'rules', 'vocabularies'):
            spec = verify_target.KINDS[kind]
            data.add(kind, **{spec.id_key: f'{prefix}-{kind}', spec.name_key: 'Sales queue'}, **copied.get(kind, {}))
        data.add('predefinedAttributes', Name='Tier').add('evaluationForms', EvaluationFormId=f'{prefix}-ef',
                                                          Title='Weekdays')
        data.describe('describe_evaluation_form', f'{prefix}-ef', 'EvaluationForm', {'Status': 'ACTIVE'})
        content = f'arn:aws:connect:{TARGET_REGION}:{TARGET_ACCOUNT}:instance/{instance}/queue/{prefix}-q'
        data.describe('describe_contact_flow', f'{prefix}-f', 'ContactFlow', {'Content': content, 'Name': 'Main flow'})
        data.describe('describe_contact_flow_module', f'{prefix}-m', 'ContactFlowModule', {'Content': '{}'})
        data.describe('describe_queue', f'{prefix}-q', 'Queue', {
            'Name': 'Sales queue', 'HoursOfOperationId': f'{prefix}-h',
            'OutboundCallerConfig': {'OutboundCallerIdName': 'Sales queue', 'OutboundFlowId': f'{prefix}-f'}})
        data.describe('describe_user', f'{prefix}-u', 'User', user(f'{prefix}-u', f'{prefix}-rp', [f'{prefix}-sp']))
        data.describe('describe_quick_connect', f'{prefix}-qc', 'QuickConnect', quick_connect(
            f'{prefix}-qc', 'Hotline', {'QuickConnectType': 'PHONE_NUMBER', 'PhoneConfig': {'PhoneNumber': '+15550123'}}))
    return src, tgt


ALL_FEATURES = {feature: True for feature in verify_target.FEATURE_KINDS} | {
    'phoneNumbers': True, 'lambdaFunctions': True, 'lexBots': True}


class FakeSession:
    def __init__(self, clients):
        self.clients = clients

    def client(self, service, **kwargs):
        return self.clients[service]


@pytest.fixture
def wired(monkeypatch, external_id_secret):
    """Wire lambda_handler to fake clients; returns (source clients, target clients, sessions created)."""
    src, tgt = full_data()
    source = {'connect': FakeClient('connect', src), 'qconnect': FakeClient('qconnect', src)}
    target = {'connect': FakeClient('connect', tgt), 'qconnect': FakeClient('qconnect', tgt)}
    sessions = []

    def fake_session(arn, role, external_id):
        sessions.append((arn, role, external_id))
        return FakeSession(target)

    monkeypatch.setattr(verify_target, 'create_destination_session', fake_session)
    monkeypatch.setattr(verify_target, 'create_connect_client_from_arn',
                        lambda arn: (source['connect'], SOURCE_INSTANCE_ID, SOURCE_REGION))
    monkeypatch.setattr(verify_target, 'create_destination_client',
                        lambda arn, session: (session.client('connect'), TARGET_INSTANCE_ID, TARGET_REGION))
    monkeypatch.setattr(verify_target.boto3, 'client', lambda service, **kwargs: source[service])
    return source, target, sessions


def step_event(**extra):
    return {'sourceInstanceId': SOURCE_INSTANCE_ARN, 'targetInstanceId': TARGET_INSTANCE_ARN,
            'crossAccountRoleArn': TARGET_ROLE_ARN, 'features': ALL_FEATURES, 'queueOptions': {}, 'userOptions': {},
            **extra}


def test_step_output_and_logs_hold_ids_codes_and_counts_only(wired, external_id_secret, capsys):
    source, target, sessions = wired
    result = verify_target.lambda_handler(step_event(externalIdSecretArn=external_id_secret), None)
    assert sessions == [(TARGET_INSTANCE_ARN, TARGET_ROLE_ARN, EXTERNAL_ID)]
    assert result['verdict'] == 'PASS', result['failedChecks']
    assert {c['check'] for c in result['checks']} == {'names', 'flowContent', 'moduleContent', 'queueConfig',
                                                      'userConfig', 'quickConnectTargets'}
    assert result['checkCount'] == 25 and result['failedCheckCount'] == 0
    out = capsys.readouterr().out
    assert 'Verification verdict: PASS, 0 failed checks' in out
    for text in (json.dumps(result), out):
        assert EXTERNAL_ID not in text
        for value in SECRETS_IN_DATA:
            assert value not in text, value


def test_a_raw_external_id_is_rejected_before_any_aws_call(wired, capsys):
    source, target, sessions = wired
    result = verify_target.lambda_handler(step_event(externalId=EXTERNAL_ID), None)
    assert result['verdict'] == 'ERROR' and result['errorCode'] == 'RawExternalIdRejected'
    assert sessions == [] and source['connect'].actions == [] and target['connect'].actions == []
    assert result['failedChecks'] == [{'originalId': 'VerifyTarget', 'status': 'failed',
                                       'errorCode': 'RawExternalIdRejected'}]
    assert EXTERNAL_ID not in json.dumps(result) + capsys.readouterr().out


def test_access_denied_is_an_error_code_never_a_partial_pass(wired, external_id_secret, capsys):
    source, target, _ = wired
    target['connect'].errors['list_queues'] = 'AccessDeniedException'
    result = verify_target.lambda_handler(step_event(externalIdSecretArn=external_id_secret), None)
    assert result['verdict'] == 'ERROR' and result['errorCode'] == 'AccessDeniedException'
    assert result['action'] == 'ListQueues' and result['checks'] == []
    assert 'jdoe' not in json.dumps(result)


# --- IAM: every call on each side is granted to the role used on that side --------------------------

def policy_actions(template, logical_id, policy_name):
    resource = cfn_yaml.load(str(ROOT / 'cloudformation' / template))['Resources'][logical_id]
    policy = next(p for p in resource['Properties']['Policies'] if p['PolicyName'] == policy_name)
    statements = {}
    for statement in policy['PolicyDocument']['Statement']:
        if 'Fn::If' in statement:
            continue
        actions = statement['Action'] if isinstance(statement['Action'], list) else [statement['Action']]
        statements[statement['Sid']] = set(actions)
    return statements


def test_every_call_is_granted_on_its_side_and_the_target_describes_are_exactly_the_new_grant(wired,
                                                                                              external_id_secret):
    source, target, _ = wired
    # A source caller ID number on the target queue makes the step read the source phone numbers too.
    target['connect'].data.describes[('describe_queue', 't-q')]['Queue']['OutboundCallerConfig'][
        'OutboundCallerIdNumberId'] = 'pn-1'
    # A source reference in a flow makes the step build the flow rewriter's mapping on both sides.
    target_flow_holds(target, WISDOM_ARN, SOURCE_QUEUE_ARN)
    verify_target.lambda_handler(step_event(externalIdSecretArn=external_id_secret), None)
    source_calls = set(source['connect'].actions) | set(source['qconnect'].actions)
    target_calls = set(target['connect'].actions) | set(target['qconnect'].actions)
    assert {'connect:ListLambdaFunctions', 'connect:ListBots', 'wisdom:ListAssistants'} <= target_calls
    assert 'connect:ListBots' in source_calls
    reader = policy_actions('3-lambda-functions.yaml', 'LambdaExecutionRole', 'SourceReader')
    writer = policy_actions('target-role.yaml', 'TargetWriterRole', 'TargetWriter')
    assert source_calls <= set().union(*reader.values()), source_calls - set().union(*reader.values())
    assert target_calls <= set().union(*writer.values()), target_calls - set().union(*writer.values())
    assert 'connect:ListPhoneNumbersV2' in source_calls and 'connect:SearchVocabularies' in source_calls
    describes = {a for a in target_calls if ':Describe' in a}
    assert writer['ConnectDescribeTargetForVerify'] == describes == {
        'connect:DescribeContactFlow', 'connect:DescribeContactFlowModule', 'connect:DescribeQueue',
        'connect:DescribeQuickConnect', 'connect:DescribeUser'}
    assert all(':Describe' in a or ':List' in a or ':Search' in a for a in source_calls | target_calls)


# --- migration report and status API ----------------------------------------------------------------

CLEANUP = ('CleanupExternalIdSecret', 'ok', {'status': 'DELETED'})


def verify_result(verdict, failed=()):
    return {'verdict': verdict, 'checkCount': 1, 'failedCheckCount': len(failed), 'notChecked': {},
            'checks': [{'check': 'names', 'type': 'queues', 'status': 'FAIL' if failed else 'PASS', 'checked': 1,
                        'issues': {}, 'followUps': {}, 'ids': [], 'unlistedIssues': 0}],
            'failedChecks': [{'originalId': 'names:queues', 'status': 'failed', 'errorCode': code, 'count': 1}
                             for code in failed]}


def test_a_failing_verification_fails_the_report_and_ends_the_run_failed():
    events = history(('CreateQueues', 'ok', {}), ('VerifyTarget', 'ok', verify_result('FAIL', ['MissingInTarget'])),
                     CLEANUP)
    report = migration_report.build_report(FakeSfn(events), EXECUTION_ARN, cleanup_status='DELETED')
    assert report['status'] == 'FAILED'
    assert report['verification']['verdict'] == 'FAIL' and report['verification']['failedCheckCount'] == 1
    step = next(s for s in report['steps'] if s['state'] == 'VerifyTarget')
    assert step['status'] == 'COMPLETED_WITH_FAILURES' and step['errorCodes'] == {'MissingInTarget': 1}
    assert final_outcome(report['status'], 'DELETED', 'FAIL') == 'TargetVerificationFailed'


def test_a_verification_with_follow_ups_only_passes():
    events = history(('VerifyTarget', 'ok', verify_result('PASS_WITH_FOLLOW_UPS')), CLEANUP)
    report = migration_report.build_report(FakeSfn(events), EXECUTION_ARN, cleanup_status='DELETED')
    assert report['status'] == 'SUCCEEDED' and report['verification']['verdict'] == 'PASS_WITH_FOLLOW_UPS'
    assert final_outcome(report['status'], 'DELETED', 'PASS_WITH_FOLLOW_UPS') == 'MigrationSucceeded'


def test_a_verify_step_that_crashed_reaches_the_report_as_error_and_fails_the_run():
    events = history(('VerifyTarget', 'fail', 'States.Timeout'))
    events.append({'id': 3, 'previousEventId': 2, 'type': 'PassStateEntered',
                   'stateEnteredEventDetails': {'name': 'VerifyTargetFailed'}})
    events += [{**e, 'id': e['id'] + 3, 'previousEventId': e.get('previousEventId') and e['previousEventId'] + 3}
               for e in history(CLEANUP)]
    report = migration_report.build_report(FakeSfn(events), EXECUTION_ARN, cleanup_status='DELETED')
    assert report['status'] == 'FAILED'
    assert report['verification'] == {'verdict': 'ERROR', 'errorCode': 'States.Timeout'}
    assert report['failedSteps'] == [{'state': 'VerifyTarget', 'error': 'States.Timeout'}]
    assert final_outcome(report['status'], 'DELETED', 'ERROR') == 'TargetVerificationErrored'


def test_a_verify_error_result_fails_the_report_and_a_run_without_verify_says_not_run():
    error = verify_target.error_result(ValueError('x'))
    report = migration_report.build_report(FakeSfn(history(('VerifyTarget', 'ok', error), CLEANUP)), EXECUTION_ARN,
                                           cleanup_status='DELETED')
    assert report['status'] == 'FAILED' and report['verification']['verdict'] == 'ERROR'
    assert report['verification']['errorCode'] == 'ValueError'
    report = migration_report.build_report(FakeSfn(history(('CreateQueues', 'ok', {}), CLEANUP)), EXECUTION_ARN,
                                           cleanup_status='DELETED')
    assert report['verification'] == {'verdict': 'NOT_RUN'} and report['status'] == 'SUCCEEDED'


def test_status_api_returns_the_verification(monkeypatch):
    events = history(('VerifyTarget', 'ok', verify_result('PASS')), CLEANUP)

    class Sfn(FakeSfn):
        def describe_execution(self, executionArn):
            return {'status': 'SUCCEEDED'}

    monkeypatch.setattr(api_migration_status.boto3, 'client', lambda *a, **k: Sfn(events))
    monkeypatch.setenv('ALLOWED_ORIGIN', 'https://d111111abcdef8.cloudfront.net')
    monkeypatch.setenv('EXECUTION_ARN_PREFIX', EXECUTION_ARN.rsplit(':', 1)[0] + ':')
    response = api_migration_status.lambda_handler({'body': json.dumps({'executionArn': EXECUTION_ARN})}, None)
    body = json.loads(response['body'])
    assert body['verification']['verdict'] == 'PASS' and body['verification']['checks'][0]['type'] == 'queues'


# --- the UI endpoint runs the same verifier ---------------------------------------------------------

def test_verify_migration_api_calls_the_shared_verifier(monkeypatch):
    monkeypatch.setenv('ALLOWED_ORIGIN', 'https://d111111abcdef8.cloudfront.net')
    calls, sessions = [], []
    session = object()
    monkeypatch.setattr(verify_migration, 'create_destination_session',
                        lambda *args: sessions.append(args) or session)

    def fake_run(source_arn, target_arn, target_session, features, options, budget):
        calls.append((source_arn, target_arn, target_session, features, options))
        assert isinstance(budget, verify_target.Budget) and budget.deadline is not None
        return verify_result('PASS')

    monkeypatch.setattr(verify_target, 'run_verification', fake_run)
    body = {'sourceInstanceId': SOURCE_INSTANCE_ARN, 'destinationInstanceId': TARGET_INSTANCE_ARN,
            'crossAccountRoleArn': TARGET_ROLE_ARN, 'externalId': EXTERNAL_ID, 'features': {'queues': True},
            'queueOptions': {'migrateHoursOfOperation': False}}
    response = verify_migration.lambda_handler({'httpMethod': 'POST', 'body': json.dumps(body)}, None)
    assert response['statusCode'] == 200 and json.loads(response['body'])['verdict'] == 'PASS'
    assert sessions == [(TARGET_INSTANCE_ARN, TARGET_ROLE_ARN, EXTERNAL_ID)]
    assert calls == [(SOURCE_INSTANCE_ARN, TARGET_INSTANCE_ARN, session, {'queues': True},
                      {'queueOptions': {'migrateHoursOfOperation': False}, 'userOptions': {}})]
    assert EXTERNAL_ID not in response['body']


# --- source references the tool cannot map: follow ups, not failures ------------------

NO_AI = {feature: on for feature, on in ALL_FEATURES.items() if feature not in verify_target.AI_FEATURES}
WISDOM_ARN = f'arn:aws:wisdom:{SOURCE_REGION}:{SOURCE_ACCOUNT}:assistant/as-src-0001'
SOURCE_QUEUE_ARN = f'arn:aws:connect:{SOURCE_REGION}:{SOURCE_ACCOUNT}:instance/{SOURCE_INSTANCE_ID}/queue/s-q'
SOURCE_LAMBDA_ARN = f'arn:aws:lambda:{SOURCE_REGION}:{SOURCE_ACCOUNT}:function:lookup'
TARGET_LAMBDA_ARN = f'arn:aws:lambda:{TARGET_REGION}:{TARGET_ACCOUNT}:function:lookup'


def target_flow_holds(target, *arns):
    content = json.dumps({'Actions': [{'Parameters': {'Ref': arn}} for arn in arns]})
    target['connect'].data.describes[('describe_contact_flow', 't-f')]['ContactFlow']['Content'] = content


def run_wisdom_case(wired, secret_arn, target_has_assistant=False):
    _, target, _ = wired
    if not target_has_assistant:
        target['qconnect'].data.lists['list_assistants'] = []
    target_flow_holds(target, WISDOM_ARN)
    return verify_target.lambda_handler(step_event(features=NO_AI, externalIdSecretArn=secret_arn), None)


@pytest.mark.parametrize('target_has_assistant, reason', [(False, 'NoTargetAssistant'),
                                                          (True, 'AssistantNotMapped')])
def test_an_unmappable_wisdom_arn_gives_pass_with_follow_ups(wired, external_id_secret, capsys,
                                                             target_has_assistant, reason):
    result = run_wisdom_case(wired, external_id_secret, target_has_assistant)
    assert result['verdict'] == 'PASS_WITH_FOLLOW_UPS' and result['failedChecks'] == []
    flow_check = next(c for c in result['checks'] if c['check'] == 'flowContent')
    assert flow_check['status'] == 'PASS' and flow_check['followUps'] == {'UnmappableSourceReference': 1}
    assert result['manualFollowUp'] == [{
        'type': 'flowReference', 'resourceType': 'contactFlow', 'flowId': 't-f', 'arnType': 'wisdom',
        'reasonCode': reason, 'count': 1, 'action': verify_target.FOLLOW_UP_ACTIONS[reason]}]
    out = capsys.readouterr().out
    assert 'Verification verdict: PASS_WITH_FOLLOW_UPS, 0 failed checks, 1 follow up entries' in out
    for text in (json.dumps(result), out):
        assert WISDOM_ARN not in text and 'as-src-0001' not in text and 'Actions' not in text
        for value in SECRETS_IN_DATA:
            assert value not in text, value


@pytest.mark.parametrize('arns, target_lambdas, verdict, issues, reasons', [
    # The target has a queue with the source queue's name: the rewriter maps it, so the flow should
    # point at the target queue. Mappable but unmapped: FAIL.
    ((SOURCE_QUEUE_ARN,), [], 'FAIL', {'MappableSourceReference': 1}, []),
    # A function with the same name is associated with the target: mappable, FAIL.
    ((SOURCE_LAMBDA_ARN,), [TARGET_LAMBDA_ARN], 'FAIL', {'MappableSourceReference': 1}, []),
    # No such function in the target: the tool cannot map it, a follow up.
    ((SOURCE_LAMBDA_ARN,), [], 'PASS_WITH_FOLLOW_UPS', {}, ['NoMatchingLambda']),
    # Both in one flow: the mappable one still fails the run, the other is listed.
    ((SOURCE_QUEUE_ARN, SOURCE_LAMBDA_ARN), [], 'FAIL', {'MappableSourceReference': 1}, ['NoMatchingLambda']),
])
def test_a_mappable_but_unmapped_reference_fails(wired, external_id_secret, arns, target_lambdas, verdict, issues,
                                                 reasons):
    source, target, _ = wired
    target['connect'].data.raw('list_lambda_functions', *target_lambdas)
    target_flow_holds(target, *arns)
    result = verify_target.lambda_handler(step_event(features=NO_AI, externalIdSecretArn=external_id_secret), None)
    flow_check = next(c for c in result['checks'] if c['check'] == 'flowContent')
    assert result['verdict'] == verdict and flow_check['issues'] == issues
    assert [f['reasonCode'] for f in result['manualFollowUp']] == reasons
    assert all(f['flowId'] == 't-f' for f in result['manualFollowUp'])
    # The decision came from the flow rewriter's own mapping lookups.
    assert 'connect:ListLambdaFunctions' in target['connect'].actions
    assert 'connect:ListQueues' in source['connect'].actions
    assert 'arn:' not in json.dumps(result)


def flows_step(rerun):
    """CreateContactFlows result: the first run creates the flow and lists the reference; a rerun finds it existing."""
    if rerun:
        return {'statusCode': 200, 'flowMapping': {'s-f': 't-f'}, 'createdFlows': [],
                'skippedFlows': [{'originalId': 's-f', 'newId': 't-f', 'status': 'existing'}], 'manualFollowUp': []}
    return {'statusCode': 200, 'flowMapping': {'s-f': 't-f'}, 'skippedFlows': [],
            'createdFlows': [{'originalId': 's-f', 'newId': 't-f', 'status': 'created'}],
            'manualFollowUp': [{'type': 'wisdom', 'source': WISDOM_ARN, 'flows': ['s-f'],
                                'action': 'No mapping for this resource type'}]}


def test_a_rerun_with_existing_flows_still_lists_the_follow_up(wired, external_id_secret):
    verify = run_wisdom_case(wired, external_id_secret)
    reports = {}
    for rerun in (False, True):
        events = history(('CreateContactFlows', 'ok', flows_step(rerun)), ('VerifyTarget', 'ok', verify), CLEANUP)
        reports[rerun] = migration_report.build_report(FakeSfn(events), EXECUTION_ARN, cleanup_status='DELETED')
    assert reports[True]['manualFollowUp'] == reports[False]['manualFollowUp'] == verify['manualFollowUp']
    (item,) = reports[True]['manualFollowUp']
    assert (item['flowId'], item['arnType'], item['reasonCode']) == ('t-f', 'wisdom', 'NoTargetAssistant')
    # Without a completed VerifyTarget scan the create step's own list is kept.
    events = history(('CreateContactFlows', 'ok', flows_step(False)), CLEANUP)
    report = migration_report.build_report(FakeSfn(events), EXECUTION_ARN, cleanup_status='DELETED')
    assert report['manualFollowUp'] == flows_step(False)['manualFollowUp']


def test_pass_with_follow_ups_ends_succeeded_with_the_follow_ups_in_the_report(wired, external_id_secret,
                                                                               monkeypatch):
    verify = run_wisdom_case(wired, external_id_secret)
    events = history(('CreateContactFlows', 'ok', flows_step(True)), ('VerifyTarget', 'ok', verify), CLEANUP)
    report = migration_report.build_report(FakeSfn(events), EXECUTION_ARN, cleanup_status='DELETED')
    assert report['status'] == 'SUCCEEDED' and report['failedItemCount'] == 0
    assert report['verification']['verdict'] == 'PASS_WITH_FOLLOW_UPS'
    assert [f['reasonCode'] for f in report['manualFollowUp']] == ['NoTargetAssistant']
    assert final_outcome(report['status'], 'DELETED', verify['verdict']) == 'MigrationSucceeded'

    class Sfn(FakeSfn):
        def describe_execution(self, executionArn):
            return {'status': 'SUCCEEDED'}

    monkeypatch.setattr(api_migration_status.boto3, 'client', lambda *a, **k: Sfn(events))
    monkeypatch.setenv('ALLOWED_ORIGIN', 'https://d111111abcdef8.cloudfront.net')
    monkeypatch.setenv('EXECUTION_ARN_PREFIX', EXECUTION_ARN.rsplit(':', 1)[0] + ':')
    body = json.loads(api_migration_status.lambda_handler({'body': json.dumps({'executionArn': EXECUTION_ARN})},
                                                          None)['body'])
    assert body['migrationStatus'] == 'SUCCEEDED' and body['manualFollowUp'] == report['manualFollowUp']
    assert WISDOM_ARN not in json.dumps(report['verification'])


# --- the UI endpoint budget: parallel, paced Describes ----------------------------------------------

# Time scale for the budget tests: rates are multiplied and the budget and the call latency divided
# by it, so the measured shape (20 s budget, about 0.25 s per Describe) runs in well under a second.
SCALE = 50
LATENCY_SECONDS = 0.25 / SCALE


class SlowClient(FakeClient):
    """A FakeClient whose Describe calls take LATENCY_SECONDS (as a real API call takes time)."""

    def __getattr__(self, method):
        call = super().__getattr__(method)

        def slow(**params):
            time.sleep(LATENCY_SECONDS)
            return call(**params)
        return slow


def measured_sides(flows, users=10, quick_connects=2):
    """Instances shaped like the measured run (10 users, 2 quick connects) with `flows` migrated flows."""
    src, tgt = Data(), Data()
    for data, p in ((src, 's'), (tgt, 't')):
        data.add('routingProfiles', Id=f'{p}-rp', Name='Agents').add('securityProfiles', Id=f'{p}-sp', Name='Agent')
        for n in range(flows):
            data.add('contactFlows', Id=f'{p}-f{n}', Name=f'Flow {n}')
            data.describe('describe_contact_flow', f'{p}-f{n}', 'ContactFlow', {'Content': CLEAN_FLOW})
        for n in range(users):
            data.add('users', Id=f'{p}-u{n}', Username=f'user{n}')
            data.describe('describe_user', f'{p}-u{n}', 'User', user(f'{p}-u{n}', f'{p}-rp', [f'{p}-sp']))
        for n in range(quick_connects):
            data.add('quickConnects', Id=f'{p}-qc{n}', Name=f'QC {n}')
            data.describe('describe_quick_connect', f'{p}-qc{n}', 'QuickConnect', quick_connect(
                f'{p}-qc{n}', f'QC {n}', {'QuickConnectType': 'FLOW', 'FlowConfig': {'ContactFlowId': f'{p}-f0'}}))
    source = verify_target.Side(SlowClient('connect', src), SOURCE_INSTANCE_ID, SOURCE_INSTANCE_ARN, is_source=True)
    target = verify_target.Side(SlowClient('connect', tgt), TARGET_INSTANCE_ID, TARGET_INSTANCE_ARN)
    return source, target


@pytest.fixture
def scaled_rates(monkeypatch):
    rates = {op: rate * SCALE for op, rate in region_utils.CONNECT_DESCRIBE_RATES.items()}
    monkeypatch.setattr(verify_target, 'CONNECT_DESCRIBE_RATES', rates)
    return rates


def api_budget():
    budget = verify_target.Budget.for_api()
    budget.deadline = time.monotonic() + verify_target.API_TIME_BUDGET_SECONDS / SCALE
    return budget


MEASURED_FEATURES = {'contactFlows': True, 'users': True, 'quickConnects': True}


def checks_by_name(result):
    return {c['check']: c for c in result['checks'] if c['check'] != 'names'}


def test_the_api_verify_finishes_users_and_quick_connects_within_the_budget(scaled_rates):
    """Regression: userConfig stopped at 5 of 10 and quickConnectTargets at 0 of 2 behind the flow Describes."""
    source, target = measured_sides(flows=22)
    started = time.monotonic()
    result = verify_target.verify(source, target, MEASURED_FEATURES, CROSS, api_budget())
    elapsed = time.monotonic() - started
    checks = checks_by_name(result)
    assert checks['userConfig']['checked'] == 10 and checks['userConfig']['status'] == 'PASS', checks['userConfig']
    assert checks['quickConnectTargets']['checked'] == 2 and checks['quickConnectTargets']['status'] == 'PASS'
    assert checks['flowContent']['checked'] == 22 and checks['flowContent']['status'] == 'PASS'
    assert result['verdict'] == 'PASS'
    assert elapsed < verify_target.API_TIME_BUDGET_SECONDS / SCALE + 2 * LATENCY_SECONDS


def test_more_flows_than_the_budget_leaves_only_the_flow_check_incomplete(scaled_rates):
    """60 flows at 2 per second need 30 s: the flow check stops, users and quick connects still finish."""
    source, target = measured_sides(flows=60)
    result = verify_target.verify(source, target, MEASURED_FEATURES, CROSS, api_budget())
    checks = checks_by_name(result)
    assert checks['flowContent']['issues'] == {'VerificationIncomplete': 1}
    assert 0 < checks['flowContent']['checked'] < 60
    assert checks['userConfig']['checked'] == 10 and checks['userConfig']['status'] == 'PASS'
    assert checks['quickConnectTargets']['checked'] == 2 and checks['quickConnectTargets']['status'] == 'PASS'
    # The flows checked are the first ones, in order (a prefix), so the count is meaningful.
    assert result['failedChecks'] == [{'originalId': 'flowContent:contactFlows', 'status': 'failed',
                                       'errorCode': 'VerificationIncomplete', 'count': 1}]


def test_the_state_machine_verify_is_unchanged_sequential_and_unpaced(monkeypatch):
    """Budget() (the VerifyTarget step): one worker, no rate limiter, every call in the old order."""
    monkeypatch.setattr(verify_target, 'RateLimiter', lambda rate: pytest.fail('the step must not pace'))
    monkeypatch.setattr(verify_target, 'map_in_order', lambda *a: pytest.fail('the step must not run in parallel'))
    source, target = measured_sides(flows=3, users=2, quick_connects=1)
    result = verify_target.verify(source, target, MEASURED_FEATURES, CROSS, verify_target.Budget())
    assert result['verdict'] == 'PASS'
    assert [(c['check'], c['checked']) for c in result['checks'] if c['check'] != 'names'] == [
        ('flowContent', 3), ('userConfig', 2), ('quickConnectTargets', 1)]
    target_describes = [a for a in target.connect.actions if ':Describe' in a]
    # Groups in the old order: flows, then users, then quick connects.
    assert target_describes == (['connect:DescribeContactFlow'] * 3 + ['connect:DescribeUser'] * 2
                                + ['connect:DescribeQuickConnect'])
    assert [a for a in source.connect.actions if ':Describe' in a] == (['connect:DescribeUser'] * 2
                                                                       + ['connect:DescribeQuickConnect'])


def test_every_paced_describe_uses_one_limiter_per_operation_account_and_region(monkeypatch):
    limiters = []

    class Recording:
        def __init__(self, rate):
            self.rate, self.calls = rate, 0
            limiters.append(self)

        def wait(self, deadline=None):
            self.calls += 1
            return True

    monkeypatch.setattr(verify_target, 'RateLimiter', Recording)
    source, target = measured_sides(flows=4, users=3, quick_connects=2)
    budget = verify_target.Budget.for_api()
    result = verify_target.verify(source, target, MEASURED_FEATURES, CROSS, budget)
    assert result['verdict'] == 'PASS'
    by_key = {key: limiter for key, limiter in budget._limiters.items()}
    assert {(scope, op): lim.calls for (scope, op), lim in by_key.items()} == {
        ((TARGET_ACCOUNT, TARGET_REGION), 'describe_contact_flow'): 4,
        ((SOURCE_ACCOUNT, SOURCE_REGION), 'describe_user'): 3, ((TARGET_ACCOUNT, TARGET_REGION), 'describe_user'): 3,
        ((SOURCE_ACCOUNT, SOURCE_REGION), 'describe_quick_connect'): 2,
        ((TARGET_ACCOUNT, TARGET_REGION), 'describe_quick_connect'): 2}
    assert {lim.rate for lim in limiters} == {2, 10}
    assert len(limiters) == len(by_key)  # one limiter per key, shared by the threads


def test_a_paced_slot_after_the_deadline_is_not_started():
    """DescribeUser at 10 per second: slots at +0, +0.1, ... s; a 0.35 s budget leaves slots from +0.4 s unstarted."""
    source, target = measured_sides(flows=0, users=10, quick_connects=0)
    budget = verify_target.Budget.for_api()
    budget.deadline = time.monotonic() + 0.35
    started = time.monotonic()
    result = verify_target.check_users(source, target, budget, {}, {})
    assert result['issues'] == {'VerificationIncomplete': 1} and 0 < result['checked'] < 10
    # Items waiting for a slot past the deadline returned at once instead of running on.
    assert time.monotonic() - started < 0.35 + 0.15


def test_a_fatal_error_in_a_parallel_check_is_an_error_never_a_partial_pass():
    source, target = measured_sides(flows=2, users=4, quick_connects=1)
    target.connect.errors['describe_user'] = 'AccessDeniedException'
    with pytest.raises(region_utils.MigrationStepError):
        verify_target.verify(source, target, MEASURED_FEATURES, CROSS, verify_target.Budget.for_api())


def test_the_rate_limiter_declines_a_slot_past_the_deadline_without_reserving_it():
    now, slept = [100.0], []
    limiter = region_utils.RateLimiter(2, clock=lambda: now[0], sleep=slept.append)
    assert limiter.wait(deadline=101.0) and limiter.wait(deadline=101.0)  # slots +0 and +0.5
    assert limiter.wait(deadline=101.0) is False  # slot +1.0 is at the deadline: declined, no sleep
    assert slept == [0.5]
    assert limiter.wait(deadline=102.0) and slept == [0.5, 1.0]  # the declined slot was not reserved
