"""The migration side and VerifyTarget skip exactly the same items (shared migration_filters, no AWS).

For every type with a deliberate skip, the same source items go through the
real list (or create) step and through VerifyTarget's source listing; the
test fails if the IDs they keep ever differ, or if either side stops using
the shared predicate.
"""
import ast
import json
import pathlib
import re

import botocore.session
import pytest
from botocore import xform_name
from botocore.validate import validate_parameters

import arn_mapping
import create_agent_status
import list_ai_agents
import list_ai_prompts
import list_contact_flows
import list_evaluation_forms
import list_predefined_attributes
import list_queues
import list_rules
import list_views
import list_vocabularies
import migration_filters
import verify_target
from conftest import (SOURCE_INSTANCE_ARN, SOURCE_INSTANCE_ID, SOURCE_REGION, TARGET_INSTANCE_ARN,
                      TARGET_INSTANCE_ID)
from test_state_machine import rendered_numbers, walk

ROOT = pathlib.Path(__file__).resolve().parent.parent
SESSION = botocore.session.get_session()
MODELS = {service: SESSION.get_service_model(service) for service in ('connect', 'qconnect')}


class Fake:
    """Connect or AI agents (qconnect) stand in: one page per list call, canned Describe/Get results.

    Every call is validated against the botocore input shape and recorded.
    """

    def __init__(self, service, lists, details=None):
        self.service, self.lists, self.details, self.calls = service, lists, details or {}, []
        self.operations = {xform_name(op): op for op in MODELS[service].operation_names}

    def _record(self, method, params):
        validate_parameters(params, MODELS[self.service].operation_model(self.operations[method]).input_shape)
        self.calls.append((method, params))

    def get_paginator(self, method):
        fake = self

        class Paginator:
            def paginate(self, **params):
                fake._record(method, params)
                key, items = fake.lists[method]
                return [{key: items}]
        return Paginator()

    def __getattr__(self, method):
        if method not in self.operations:
            raise AttributeError(method)

        def call(**params):
            self._record(method, params)
            if method in self.lists:  # a list call made without a paginator
                key, items = self.lists[method]
                return {key: items}
            resource_id = next(v for k, v in params.items() if k not in ('InstanceId', 'assistantId'))
            return self.details[(method, resource_id)]
        return call


# kind: (list operation, list key, summaries, {source ID: (describe method, response key, body)})
# Each case holds at least one item the migration copies and one it skips on purpose.
CASES = {
    'contactFlows': ('list_contact_flows', 'ContactFlowSummaryList', [
        {'Id': 'f-main', 'Name': 'Main flow'}, {'Id': 'f-def', 'Name': 'Default agent hold'},
        {'Id': 'f-smp', 'Name': 'Sample inbound flow'}],
        {'f-main': ('describe_contact_flow', 'ContactFlow', {'Id': 'f-main', 'Arn': 'arn:f', 'Name': 'Main flow'}),
         'f-def': ('describe_contact_flow', 'ContactFlow', {'Id': 'f-def', 'Arn': 'arn:f',
                                                            'Name': 'Default agent hold'}),
         'f-smp': ('describe_contact_flow', 'ContactFlow', {'Id': 'f-smp', 'Arn': 'arn:f',
                                                            'Name': 'Sample inbound flow'})}),
    'queues': ('list_queues', 'QueueSummaryList', [
        {'Id': 'q-std', 'Name': 'Sales', 'QueueType': 'STANDARD'},
        {'Id': 'q-agent', 'Name': 'agent queue', 'QueueType': 'AGENT'}],
        {'q-std': ('describe_queue', 'Queue', {'QueueId': 'q-std', 'Name': 'Sales'})}),
    'views': ('list_views', 'ViewsSummaryList', [
        {'Id': 'v-card', 'Name': 'Card', 'Type': 'CUSTOMER_MANAGED', 'Status': 'PUBLISHED'},
        {'Id': 'v-aws', 'Name': 'AWS Detail', 'Type': 'CUSTOMER_MANAGED', 'Status': 'PUBLISHED'},
        {'Id': 'v-draft', 'Name': 'Draft', 'Type': 'CUSTOMER_MANAGED', 'Status': 'SAVED'},
        {'Id': 'v-managed', 'Name': 'Detail', 'Type': 'AWS_MANAGED', 'Status': 'PUBLISHED'}],
        {'v-card': ('describe_view', 'View', {'Id': 'v-card', 'Name': 'Card', 'Status': 'PUBLISHED',
                                              'Content': {'Template': '{}'}})}),
    'vocabularies': ('search_vocabularies', 'VocabularySummaryList', [
        {'Id': 'vo-on', 'Name': 'Terms', 'State': 'ACTIVE', 'LanguageCode': 'en-US'},
        {'Id': 'vo-off', 'Name': 'Old', 'State': 'CREATION_FAILED', 'LanguageCode': 'en-US'}],
        {'vo-on': ('describe_vocabulary', 'Vocabulary', {'Id': 'vo-on', 'Name': 'Terms', 'State': 'ACTIVE',
                                                         'LanguageCode': 'en-US'})}),
    'evaluationForms': ('list_evaluation_forms', 'EvaluationFormSummaryList', [
        {'EvaluationFormId': 'e-active', 'Title': 'Quality'}, {'EvaluationFormId': 'e-inactive', 'Title': 'Old'}],
        {'e-active': ('describe_evaluation_form', 'EvaluationForm', {'EvaluationFormId': 'e-active',
                                                                      'Title': 'Quality', 'Status': 'ACTIVE'}),
         'e-inactive': ('describe_evaluation_form', 'EvaluationForm', {'EvaluationFormId': 'e-inactive',
                                                                        'Title': 'Old', 'Status': 'DRAFT'})}),
    'rules': ('list_rules', 'RuleSummaryList', [
        {'RuleId': 'r-pub', 'Name': 'Escalate', 'PublishStatus': 'PUBLISHED'},
        {'RuleId': 'r-draft', 'Name': 'Draft rule', 'PublishStatus': 'DRAFT'}],
        {'r-pub': ('describe_rule', 'Rule', {'RuleId': 'r-pub', 'Name': 'Escalate', 'PublishStatus': 'PUBLISHED'})}),
    'predefinedAttributes': ('list_predefined_attributes', 'PredefinedAttributeSummaryList', [
        {'Name': 'Tier'}, {'Name': 'connect:Subtype'}],
        {'Tier': ('describe_predefined_attribute', 'PredefinedAttribute', {'Name': 'Tier'})}),
    'agentStatuses': ('list_agent_statuses', 'AgentStatusSummaryList', [
        {'Id': 'a-break', 'Name': 'Break', 'Type': 'CUSTOM'}, {'Id': 'a-available', 'Name': 'Available',
                                                                 'Type': 'ROUTABLE'},
        {'Id': 'a-offline', 'Name': 'Offline', 'Type': 'OFFLINE'}], {}),
}
EXPECTED = {'contactFlows': {'f-main'}, 'queues': {'q-std'}, 'views': {'v-card'}, 'vocabularies': {'vo-on'},
            'evaluationForms': {'e-active'}, 'rules': {'r-pub'}, 'predefinedAttributes': {'Tier'},
            'agentStatuses': {'a-break'}}
LIST_STEPS = {  # kind: (module, output key, ID key)
    'contactFlows': (list_contact_flows, 'contactFlows', 'Id'),
    'queues': (list_queues, 'queues', 'QueueId'),
    'views': (list_views, 'views', 'Id'),
    'vocabularies': (list_vocabularies, 'vocabularies', 'Id'),
    'evaluationForms': (list_evaluation_forms, 'evaluationForms', 'EvaluationFormId'),
    'rules': (list_rules, 'rules', 'RuleId'),
    'predefinedAttributes': (list_predefined_attributes, 'predefinedAttributes', 'Name'),
}
# kind: the predicate both sides must use
PREDICATES = {
    'contactFlows': migration_filters.migrates_contact_flow, 'queues': migration_filters.migrates_queue,
    'views': migration_filters.migrates_view, 'vocabularies': migration_filters.migrates_vocabulary,
    'evaluationForms': migration_filters.migrates_evaluation_form, 'rules': migration_filters.migrates_rule,
    'predefinedAttributes': migration_filters.migrates_predefined_attribute,
    'agentStatuses': migration_filters.migrates_agent_status,
}
MIGRATION_SIDE_USERS = {  # kind: module whose own filter decides what is copied
    'contactFlows': list_contact_flows, 'queues': list_queues, 'views': list_views,
    'vocabularies': list_vocabularies, 'evaluationForms': list_evaluation_forms, 'rules': list_rules,
    'predefinedAttributes': list_predefined_attributes, 'agentStatuses': create_agent_status,
}


def source_fake(kind):
    operation, key, items, details = CASES[kind]
    return Fake('connect', {operation: (key, items)},
                {(method, item_id): {response_key: body} for item_id, (method, response_key, body) in details.items()})


def verify_kept(kind):
    side = verify_target.Side(source_fake(kind), SOURCE_INSTANCE_ID, SOURCE_INSTANCE_ARN, is_source=True)
    return set(side.compared(kind))


def migration_kept(kind, monkeypatch):
    if kind == 'agentStatuses':
        return agent_statuses_created(monkeypatch)
    module, output_key, id_key = LIST_STEPS[kind]
    client = source_fake(kind)
    monkeypatch.setattr(module, 'create_connect_client_from_arn',
                        lambda arn: (client, SOURCE_INSTANCE_ID, SOURCE_REGION))
    result = module.lambda_handler({'sourceInstanceId': SOURCE_INSTANCE_ARN}, None)
    assert result['statusCode'] == 200, result
    return {item[id_key] for item in result[output_key]}


def agent_statuses_created(monkeypatch):
    """The list step keeps every status; the create step skips the built-in ones."""
    _, key, items, _ = CASES['agentStatuses']

    target = Fake('connect', {'list_agent_statuses': (key, [])},
                  {('create_agent_status', item['Name']): {'AgentStatusId': f"t-{item['Id']}"} for item in items})
    monkeypatch.setattr(create_agent_status, 'create_destination_client',
                        lambda *args, **kwargs: (target, TARGET_INSTANCE_ID, SOURCE_REGION))
    monkeypatch.setattr(create_agent_status, 'resolve_external_id', lambda event: None)
    result = create_agent_status.lambda_handler({'targetInstanceId': TARGET_INSTANCE_ARN, 'agentStatuses': items},
                                                None)
    return set(result['statusMapping'])


@pytest.mark.parametrize('kind', sorted(CASES))
def test_the_migration_and_verify_target_keep_the_same_items(kind, monkeypatch):
    migrated = migration_kept(kind, monkeypatch)
    verified = verify_kept(kind)
    assert migrated == verified, f'{kind}: migration keeps {sorted(migrated)}, VerifyTarget {sorted(verified)}'
    assert migrated == EXPECTED[kind]  # and the skip still skips something


@pytest.mark.parametrize('kind', sorted(PREDICATES))
def test_both_sides_use_the_shared_predicate(kind):
    predicate = PREDICATES[kind]
    assert verify_target.KINDS[kind].migrates is predicate
    assert getattr(MIGRATION_SIDE_USERS[kind], predicate.__name__) is predicate


def test_both_sides_send_the_same_list_filters():
    for kind, params in (('queues', migration_filters.QUEUE_LIST_PARAMS),
                         ('views', migration_filters.VIEW_LIST_PARAMS),
                         ('aiAgents', migration_filters.AI_LIST_PARAMS),
                         ('aiPrompts', migration_filters.AI_LIST_PARAMS)):
        assert verify_target.KINDS[kind].extra is params, kind
    assert list_queues.QUEUE_LIST_PARAMS is migration_filters.QUEUE_LIST_PARAMS
    assert list_views.VIEW_LIST_PARAMS is migration_filters.VIEW_LIST_PARAMS
    assert list_vocabularies.VOCABULARY_SEARCH_PARAMS is migration_filters.VOCABULARY_SEARCH_PARAMS
    assert list_ai_agents.AI_LIST_PARAMS is list_ai_prompts.AI_LIST_PARAMS is migration_filters.AI_LIST_PARAMS


@pytest.mark.parametrize('kind, module, method', [('aiAgents', list_ai_agents, 'list_ai_agents'),
                                                  ('aiPrompts', list_ai_prompts, 'list_ai_prompts')])
def test_ai_lists_ask_for_the_same_origin_on_both_sides(kind, module, method, monkeypatch):
    key = verify_target.KINDS[kind].list_key
    lists = {'list_assistants': ('assistantSummaries', [{'assistantId': 'as-1', 'name': 'x'}]), method: (key, [])}
    step_client, verify_client = Fake('qconnect', lists), Fake('qconnect', lists)
    monkeypatch.setattr(module.boto3, 'client', lambda *args, **kwargs: step_client)
    monkeypatch.setattr(module, 'create_connect_client_from_arn',
                        lambda arn: (None, SOURCE_INSTANCE_ID, SOURCE_REGION))
    module.lambda_handler({'sourceInstanceId': SOURCE_INSTANCE_ARN}, None)
    verify_target.Side(None, SOURCE_INSTANCE_ID, qconnect=verify_client, is_source=True).compared(kind)
    step_params = [params for name, params in step_client.calls if name == method]
    verify_params = [params for name, params in verify_client.calls if name == method]
    assert step_params == verify_params == [{'assistantId': 'as-1', **migration_filters.AI_LIST_PARAMS}]


def test_the_evaluation_form_status_is_read_on_the_source_only():
    """The target compares every form, as the create step's lookup by title does (no target Describe)."""
    target = verify_target.Side(source_fake('evaluationForms'), TARGET_INSTANCE_ID, TARGET_INSTANCE_ARN)
    assert set(target.compared('evaluationForms')) == {'e-active', 'e-inactive'}
    assert [name for name, _ in target.connect.calls] == ['list_evaluation_forms']


def test_inactive_form_is_skipped_not_missing():
    """Regression: an inactive source form was counted as MissingInTarget and failed the run."""
    source = verify_target.Side(source_fake('evaluationForms'), SOURCE_INSTANCE_ID, SOURCE_INSTANCE_ARN,
                                is_source=True)
    target = verify_target.Side(Fake('connect', {'list_evaluation_forms': ('EvaluationFormSummaryList', [
        {'EvaluationFormId': 't-active', 'Title': 'Quality'}])}), TARGET_INSTANCE_ID, TARGET_INSTANCE_ARN)
    result = verify_target.check_names(source, target, 'evaluationForms')
    assert (result['status'], result['checked'], result['found'], result['missing'], result['skipped']) == (
        'PASS', 1, 1, 0, 1)
    assert 'Old' not in json.dumps(result) and 'Quality' not in json.dumps(result)


def test_feature_flags_mean_the_same_on_both_sides():
    """VerifyTarget checks a type only when the state machine migrates it (Choice BooleanEquals true)."""
    flags = {}
    for node in walk(json.loads(rendered_numbers())):
        match = re.fullmatch(r'\$\.features\.(\w+)', str(node.get('Variable', '')))
        if match:
            flags.setdefault(match.group(1), set()).add(json.dumps({k: v for k, v in node.items()
                                                                   if k not in ('Variable', 'Next')}))
    assert set(flags) == set(verify_target.FEATURE_KINDS) | set(verify_target.NOT_COMPARED)
    assert all(rules == {'{"BooleanEquals": true}'} for rules in flags.values()), flags
    for value, on in ((True, True), ('true', False), (1, False), (None, False)):
        assert migration_filters.feature_on({'aiAgents': value}, 'aiAgents') is on
    source = verify_target.Side(None, SOURCE_INSTANCE_ID, is_source=True)
    target = verify_target.Side(None, TARGET_INSTANCE_ID)
    ref = verify_target.SourceRef(SOURCE_INSTANCE_ID, '1', 'r', '2', 'r')
    result = verify_target.verify(source, target, {flag: 'true' for flag in flags}, ref, verify_target.Budget())
    assert result['checks'] == [] and result['notChecked'] == {}


def test_skip_rules_are_written_only_in_migration_filters():
    literals = ("'Default '", "'Sample '", "'AWS '", "'connect:'", "'ROUTABLE'", "origin='CUSTOMER'",
                "QueueTypes=['STANDARD']", "Type='CUSTOMER_MANAGED'", "State='ACTIVE'")
    offenders = {}
    for path in (ROOT / 'lambda').glob('*.py'):
        if path.name == 'migration_filters.py':
            continue
        found = [literal for literal in literals if literal in path.read_text()]
        if found:
            offenders[path.name] = found
    assert offenders == {}


# ListQueues calls that may list every queue type: the service quota counter only
# counts summaries (it never reads a Name).
UNFILTERED_LIST_QUEUES_OK = {'check_service_quotas.py'}


def _list_queues_calls_without_filter(tree):
    """(line, why) for every ListQueues call in one module that does not send QUEUE_LIST_PARAMS.

    Literal calls: each .paginate(...) belongs to the closest get_paginator(...) before it
    (assigned or chained) in the same function. Spec tuples: any tuple that names
    'list_queues' must carry QUEUE_LIST_PARAMS.
    """
    def sends_filter(call):
        return any(kw.arg is None and ast.unparse(kw.value).endswith('QUEUE_LIST_PARAMS') for kw in call.keywords)

    found = []
    for func in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
        calls = sorted((n for n in ast.walk(func) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)),
                       key=lambda n: (n.lineno, n.col_offset))
        current = None
        for call in calls:
            if call.func.attr == 'get_paginator' and call.args and isinstance(call.args[0], ast.Constant):
                current = call.args[0].value
            elif call.func.attr == 'list_queues':
                found.append((call.lineno, 'direct list_queues call'))
            elif call.func.attr == 'paginate' and current == 'list_queues' and not sends_filter(call):
                found.append((call.lineno, 'paginate without **QUEUE_LIST_PARAMS'))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Tuple) and node.elts and isinstance(node.elts[0], ast.Constant)
                and node.elts[0].value == 'list_queues'
                and not any(ast.unparse(e).endswith('QUEUE_LIST_PARAMS') for e in node.elts)):
            found.append((node.lineno, 'list_queues spec without QUEUE_LIST_PARAMS'))
    return found


def test_every_list_queues_call_sends_the_standard_queue_filter():
    """Regression: one unfiltered target ListQueues met agent queue summaries (no Name) and crashed."""
    missing = []
    for path in sorted((ROOT / 'lambda').glob('*.py')):
        if path.name in UNFILTERED_LIST_QUEUES_OK:
            continue
        missing += [f'{path.name}:{line} {why}' for line, why in
                    _list_queues_calls_without_filter(ast.parse(path.read_text()))]
    # The arn_mapping specs keep their params apart from the tuple.
    missing = [m for m in missing if not m.startswith('arn_mapping.py') or 'spec' not in m]
    assert arn_mapping.NAME_MAP_PARAMS['queues'] is migration_filters.QUEUE_LIST_PARAMS
    assert arn_mapping.EXISTING_SPECS['queues'][4] is migration_filters.QUEUE_LIST_PARAMS
    assert missing == []


def test_the_list_queues_check_sees_a_missing_filter():
    bad = ast.parse("def f(c):\n    p = c.get_paginator('list_queues')\n    return p.paginate(InstanceId='i')\n")
    good = ast.parse("def f(c):\n    return c.get_paginator('list_queues').paginate(InstanceId='i', **QUEUE_LIST_PARAMS)\n")
    assert [why for _, why in _list_queues_calls_without_filter(bad)] == ['paginate without **QUEUE_LIST_PARAMS']
    assert _list_queues_calls_without_filter(good) == []
