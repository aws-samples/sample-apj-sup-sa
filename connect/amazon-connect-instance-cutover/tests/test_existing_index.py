"""Every create step maps what already exists in the target before creating (rerun regression; no AWS).

In a rerun against a target populated by an earlier run, queues
and users were created blind and failed. These tests drive each create step against
one stateful fake target that serves every list call from
arn_mapping.EXISTING_SPECS:

* the item already exists: no create call, status "existing", ID mapped;
* the create answers an exists code that is not DuplicateResourceException and
  the item is found on the one re lookup: "existing";
* a rerun: the same step twice against the same target, the second run sees
  the first run's results, and must create 0 and fail 0;
* a static guard: every create step builds its existing index before its
  first create call.
"""
import ast
import json
import pathlib

import pytest
from botocore.exceptions import ClientError

import arn_mapping
import create_agent_status
import create_contact_flow_modules
import create_contact_flows
import create_data_tables
import create_evaluation_forms
import create_hours_of_operations
import create_predefined_attributes
import create_prompts
import create_queues
import create_routing_profiles
import create_rules
import create_security_profiles
import create_users
import create_views
from conftest import SOURCE_INSTANCE_ARN, TARGET_INSTANCE_ARN

ROOT = pathlib.Path(__file__).resolve().parent.parent

# kind -> (create method, request name key, response builder)
CREATES = {
    'queues': ('create_queue', 'Name', lambda i: {'QueueId': i}),
    'users': ('create_user', 'Username', lambda i: {'UserId': i}),
    'modules': ('create_contact_flow_module', 'Name', lambda i: {'Id': i}),
    'flows': ('create_contact_flow', 'Name', lambda i: {'ContactFlowId': i}),
    'dataTables': ('create_data_table', 'Name', lambda i: {'Id': i}),
    'evaluationForms': ('create_evaluation_form', 'Title', lambda i: {'EvaluationFormId': i}),
    'prompts': ('create_prompt', 'Name', lambda i: {'PromptId': i}),
    'rules': ('create_rule', 'Name', lambda i: {'RuleId': i}),
    'views': ('create_view', 'Name', lambda i: {'View': {'Id': i}}),
    'securityProfiles': ('create_security_profile', 'SecurityProfileName', lambda i: {'SecurityProfileId': i}),
    'routingProfiles': ('create_routing_profile', 'Name', lambda i: {'RoutingProfileId': i}),
    'hours': ('create_hours_of_operation', 'Name', lambda i: {'HoursOfOperationId': i}),
    'agentStatuses': ('create_agent_status', 'Name', lambda i: {'AgentStatusId': i}),
    'predefinedAttributes': ('create_predefined_attribute', 'Name', lambda i: {}),
}
BY_METHOD = {method: kind for kind, (method, _, _) in CREATES.items()}
BY_OPERATION = {spec[0]: kind for kind, spec in arn_mapping.EXISTING_SPECS.items()}


class FakeTarget:
    """One target instance: list calls page its state, create calls add to it.

    race: {kind: error code}. The next create of that kind raises the code while
    the item appears in the target (a concurrent writer, or Connect reporting an
    existing name with a code other than DuplicateResourceException).
    """

    def __init__(self):
        self.state = {kind: [] for kind in arn_mapping.EXISTING_SPECS}
        # Every user in a real target owns an agent queue, whose summary has no Name.
        self.state['queues'].append({'Id': 'dst-agent-q', 'QueueType': 'AGENT'})
        self.creates, self.race, self.list_params, self.counter = [], {}, [], 0

    def add(self, kind, name, target_id):
        _, _, name_key, id_key, _ = arn_mapping.EXISTING_SPECS[kind]
        # Predefined attributes: the name is the ID (both keys are Name).
        self.state[kind].append({id_key: target_id, name_key: name})

    def can_paginate(self, operation):
        return operation in BY_OPERATION

    def get_paginator(self, operation):
        kind = BY_OPERATION[operation]
        _, list_key, _, _, params = arn_mapping.EXISTING_SPECS[kind]
        fake = self

        class Paginator:
            def paginate(self, **kwargs):
                assert kwargs == {'InstanceId': 'dst', **params}, operation
                fake.list_params.append((operation, kwargs))
                return [{list_key: list(fake.state[kind])}]
        return Paginator()

    def __getattr__(self, method):
        if method not in BY_METHOD:
            # Follow up calls a create step makes (activate, associate, attributes): no output used.
            return lambda **kwargs: {}
        kind = BY_METHOD[method]
        _, name_param, response = CREATES[kind]

        def create(**kwargs):
            self.counter += 1
            name = kwargs[name_param]
            if kind in self.race:
                self.add(kind, name, f'dst-race-{self.counter}')
                raise ClientError({'Error': {'Code': self.race.pop(kind), 'Message': f'{name} exists'}}, method)
            self.creates.append(kind)
            target_id = name if kind == 'predefinedAttributes' else f'dst-new-{self.counter}'
            self.add(kind, name, target_id)
            return response(target_id)
        return create


class FakeRewriter:
    def rewrite(self, content):
        return content, {'rewritten': [], 'placeholders': [], 'unmapped': []}


class FakeS3:
    def upload_file(self, *args, **kwargs):
        pass


class FakeSession:
    def client(self, *args, **kwargs):
        return FakeS3()


def destination(target):
    return lambda *args, **kwargs: (target, 'dst', 'ap-southeast-2')


def flow_step_setup(module):
    def setup(monkeypatch, target):
        monkeypatch.setattr(module, 'create_destination_session', lambda *a, **k: FakeSession())
        monkeypatch.setattr(module, 'create_destination_client', destination(target))
        monkeypatch.setattr(module, 'create_connect_client_from_arn', lambda arn: (None, 'src', 'us-east-1'))
        monkeypatch.setattr(module, 'build_rewriter', lambda *a, **k: FakeRewriter())
    return setup


def plain_setup(module):
    def setup(monkeypatch, target):
        monkeypatch.setattr(module, 'create_destination_client', destination(target))
    return setup


def prompts_setup(monkeypatch, target):
    monkeypatch.setattr(create_prompts, 'create_destination_session', lambda *a, **k: FakeSession())
    monkeypatch.setattr(create_prompts, 'create_destination_client', destination(target))
    monkeypatch.setattr(create_prompts, 'get_or_create_temp_bucket', lambda *a: 'bucket')
    monkeypatch.setattr(create_prompts, 'download_prompt_file', lambda url, path: open(path, 'wb').write(b'RIFF'))


def rules_setup(monkeypatch, target):
    plain_setup(create_rules)(monkeypatch, target)
    monkeypatch.setattr(create_rules, 'create_connect_client_from_arn', lambda arn: (None, 'src', 'us-east-1'))
    for builder in ('build_queue_mapping', 'build_user_mapping', 'build_contact_flow_mapping'):
        monkeypatch.setattr(create_rules, builder, lambda *a: {})


def failed_with_status(*keys):
    return lambda result: [e for key in keys for e in result.get(key, []) if e.get('status') == 'failed']


class Step:
    """How to run one create step and read its result."""

    def __init__(self, kind, module, setup, event, mapping_key, entry_keys, failed, exists_code='InvalidParameterException',
                 statuses=None):
        self.kind, self.module, self.setup, self.event = kind, module, setup, event
        self.mapping_key, self.entry_keys, self.failed, self.exists_code = mapping_key, entry_keys, failed, exists_code
        self._statuses = statuses

    def run(self, monkeypatch, target):
        self.setup(monkeypatch, target)
        event = {'sourceInstanceId': SOURCE_INSTANCE_ARN, 'destinationInstanceId': TARGET_INSTANCE_ARN,
                 'targetInstanceId': TARGET_INSTANCE_ARN, **self.event}
        return self.module.lambda_handler(event, None)

    def mapping(self, result):
        return result[self.mapping_key]

    def statuses(self, result):
        if self._statuses:
            return self._statuses(result)
        return {e['originalId']: e['status'] for key in self.entry_keys for e in result.get(key, [])}

    def __repr__(self):
        return self.kind


STEPS = [
    Step('queues', create_queues, plain_setup(create_queues),
         {'hoursMapping': {'h': 'dst-h'}, 'queues': [{'Name': 'Support', 'QueueId': 'src-1', 'HoursOfOperationId': 'h'}]},
         'queueMapping', ['createdQueues'], lambda r: r['failedItems'], exists_code='ConflictException'),
    Step('users', create_users, plain_setup(create_users),
         {'routingProfileMapping': {'rp': 'dst-rp'}, 'securityProfileMapping': {'sp': 'dst-sp'},
          'users': [{'Id': 'src-1', 'Username': 'jane.doe', 'RoutingProfileId': 'rp', 'SecurityProfileIds': ['sp']}]},
         'userMapping', ['createdUsers'], lambda r: r['failedUsers'], exists_code='InvalidRequestException'),
    Step('modules', create_contact_flow_modules, flow_step_setup(create_contact_flow_modules),
         {'contactFlowModules': [{'Id': 'src-1', 'Name': 'Auth', 'Content': '{}'}]},
         'moduleMapping', ['createdModules', 'skippedModules'], failed_with_status('skippedModules')),
    Step('flows', create_contact_flows, flow_step_setup(create_contact_flows),
         {'contactFlows': [{'Id': 'src-1', 'Name': 'Inbound', 'Type': 'CONTACT_FLOW', 'Content': '{}'}]},
         'flowMapping', ['createdFlows', 'skippedFlows'], failed_with_status('skippedFlows')),
    Step('dataTables', create_data_tables, plain_setup(create_data_tables),
         {'dataTables': [{'Id': 'src-1', 'Name': 'Rates'}]},
         'tableMapping', ['createdTables', 'skippedTables'], lambda r: r['failedTables']),
    Step('evaluationForms', create_evaluation_forms, plain_setup(create_evaluation_forms),
         {'evaluationForms': [{'EvaluationFormId': 'src-1', 'Title': 'QA',
                               'Items': [{'Section': {'Title': 'Opening', 'RefId': 's1'}}]}]},
         'evaluationFormMapping', ['createdForms', 'skippedForms'], failed_with_status('skippedForms')),
    Step('prompts', create_prompts, prompts_setup,
         {'prompts': [{'Name': 'Greeting', 'PromptId': 'src-1', 'PromptPresignedUrl': 'https://example.com/p'}]},
         'promptMapping', ['createdPrompts', 'skippedPrompts'], lambda r: r['failedPrompts']),
    Step('rules', create_rules, rules_setup,
         {'rules': [{'RuleId': 'src-1', 'Name': 'Escalate', 'Function': '{}',
                     'TriggerEventSource': {'EventSourceName': 'OnPostCallAnalysisAvailable'},
                     'Actions': [{'ActionType': 'GENERATE_EVENTBRIDGE_EVENT'}]}]},
         'ruleMapping', ['createdRules', 'skippedRules'], failed_with_status('skippedRules')),
    Step('views', create_views, plain_setup(create_views),
         {'views': [{'Id': 'src-1', 'Name': 'Card', 'Content': {'Template': '{}', 'Actions': []}}]},
         'viewMapping', ['createdViews', 'skippedViews'], failed_with_status('skippedViews')),
    Step('securityProfiles', create_security_profiles, plain_setup(create_security_profiles),
         {'securityProfiles': [{'SecurityProfileId': 'src-1', 'SecurityProfileName': 'Supervisor', 'Permissions': []}]},
         'securityProfileMapping', ['createdProfiles'], lambda r: r['failedItems']),
    Step('routingProfiles', create_routing_profiles, plain_setup(create_routing_profiles),
         {'queueMapping': {'q': 'dst-q'},
          'routingProfiles': [{'Name': 'Sales', 'RoutingProfileId': 'src-1', 'DefaultOutboundQueueId': 'q',
                               'MediaConcurrencies': [{'Channel': 'VOICE', 'Concurrency': 1}]}]},
         'routingProfileMapping', ['createdProfiles'], lambda r: r['failedItems']),
    Step('hours', create_hours_of_operations, plain_setup(create_hours_of_operations),
         {'hoursOfOperations': [{'HoursOfOperationId': 'src-1', 'Name': 'Office', 'TimeZone': 'UTC', 'Config': []}]},
         'hoursMapping', ['createdHours'], lambda r: r['failedItems']),
    Step('agentStatuses', create_agent_status, plain_setup(create_agent_status),
         {'agentStatuses': [{'Id': 'src-1', 'Name': 'Break', 'Type': 'CUSTOM'}]},
         'statusMapping', [], lambda r: r['failedItems'],
         statuses=lambda r: {**{i: 'created' for i in r['migratedStatuses']},
                             **{i: 'existing' for i in r['skippedStatuses']}}),
]

ITEM_NAMES = {'queues': 'Support', 'users': 'jane.doe', 'modules': 'Auth', 'flows': 'Inbound', 'dataTables': 'Rates',
              'evaluationForms': 'QA', 'prompts': 'Greeting', 'rules': 'Escalate', 'views': 'Card',
              'securityProfiles': 'Supervisor', 'routingProfiles': 'Sales', 'hours': 'Office', 'agentStatuses': 'Break'}


@pytest.mark.parametrize('step', STEPS, ids=repr)
def test_an_existing_item_is_mapped_and_not_created(step, monkeypatch):
    target = FakeTarget()
    target.add(step.kind, ITEM_NAMES[step.kind], 'dst-existing')
    result = step.run(monkeypatch, target)
    assert target.creates == []
    assert step.mapping(result) == {'src-1': 'dst-existing'}
    assert step.statuses(result) == {'src-1': 'existing'}
    assert step.failed(result) == []
    assert ITEM_NAMES[step.kind] not in json.dumps(step.mapping(result))


@pytest.mark.parametrize('step', STEPS, ids=repr)
def test_an_exists_code_other_than_duplicate_maps_the_item_found_on_re_lookup(step, monkeypatch):
    assert step.exists_code != 'DuplicateResourceException'
    target = FakeTarget()
    target.race[step.kind] = step.exists_code
    result = step.run(monkeypatch, target)
    assert target.creates == [] and target.race == {}  # the create was tried once, and raised
    assert list(step.mapping(result).values()) == [f'dst-race-{target.counter}']
    assert step.statuses(result) == {'src-1': 'existing'}
    assert step.failed(result) == []


@pytest.mark.parametrize('step', STEPS, ids=repr)
def test_a_rerun_against_the_same_target_creates_nothing_and_fails_nothing(step, monkeypatch):
    target = FakeTarget()
    first = step.run(monkeypatch, target)
    assert target.creates == [step.kind] and step.failed(first) == []
    assert step.statuses(first) == {'src-1': 'created'}

    second = step.run(monkeypatch, target)
    assert target.creates == [step.kind], 'the second run created again'
    assert step.failed(second) == []
    assert step.statuses(second) == {'src-1': 'existing'}
    assert step.mapping(second) == step.mapping(first)


# Predefined attributes have no ID (the name is the identifier): the step reports counts only.

def run_attributes(monkeypatch, target):
    monkeypatch.setattr(create_predefined_attributes, 'create_destination_client', destination(target))
    result = create_predefined_attributes.lambda_handler(
        {'targetInstanceId': TARGET_INSTANCE_ARN,
         'predefinedAttributes': [{'Name': 'tier', 'Values': {'StringList': ['gold']}}]}, None)
    body = json.loads(result['body'])
    return body['migratedCount'], body['skippedCount'], result['failedItems']


def test_an_existing_predefined_attribute_is_skipped_not_created(monkeypatch):
    target = FakeTarget()
    target.add('predefinedAttributes', 'tier', 'tier')
    assert run_attributes(monkeypatch, target) == (0, 1, []) and target.creates == []


def test_a_predefined_attribute_exists_code_maps_it_when_found_on_re_lookup(monkeypatch):
    target = FakeTarget()
    target.race['predefinedAttributes'] = 'InvalidParameterException'
    assert run_attributes(monkeypatch, target) == (0, 1, []) and target.creates == []


def test_a_predefined_attribute_rerun_creates_nothing_and_fails_nothing(monkeypatch):
    target = FakeTarget()
    assert run_attributes(monkeypatch, target) == (1, 0, [])
    assert run_attributes(monkeypatch, target) == (0, 1, [])
    assert target.creates == ['predefinedAttributes']


# Static guard ---------------------------------------------------------------

# Calls that build clients or sessions, not resources.
NOT_A_CREATE = {'create_destination_client', 'create_destination_session', 'create_connect_client_from_arn'}
# Steps that look up before creating with their own per type lookup (not ExistingIndex).
OWN_LOOKUP = {
    'create_hierarchy_groups.py': 'find_existing_hierarchy_group',  # per group, parents first
    'create_quick_connects.py': 'list_existing_quick_connects',     # one index per invocation
    'create_ai_agents.py': 'find_existing_ai_agent',                 # AI agents, per assistant
    'create_ai_prompts.py': 'find_existing_ai_prompt',
    'create_ai_guardrails.py': 'find_existing_ai_guardrail',
    'create_vocabularies.py': 'find_existing_vocabulary',            # by name and language code
}
# Create steps that create nothing by name (phone numbers are moved, not created).
NO_NAMED_CREATE = {'create_phone_numbers.py'}


def _terminal(call):
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, 'id', None)


def lookup_before_create(source, lookup_names):
    """(first lookup line, first create line) in lambda_handler, in source order (None if absent)."""
    handler = next(n for n in ast.walk(ast.parse(source))
                   if isinstance(n, ast.FunctionDef) and n.name == 'lambda_handler')
    calls = sorted((n for n in ast.walk(handler) if isinstance(n, ast.Call)), key=lambda n: (n.lineno, n.col_offset))
    lookups = [c.lineno for c in calls if _terminal(c) in lookup_names]
    creates = [c.lineno for c in calls
               if (_terminal(c) or '').startswith('create_') and _terminal(c) not in NOT_A_CREATE]
    return (lookups[0] if lookups else None), (creates[0] if creates else None)


def test_every_create_step_builds_its_existing_index_before_its_first_create():
    problems = []
    for path in sorted((ROOT / 'lambda').glob('create_*.py')):
        if path.name in NO_NAMED_CREATE:
            continue
        lookup, create = lookup_before_create(path.read_text(), {'ExistingIndex', OWN_LOOKUP.get(path.name)})
        if create is None:
            problems.append(f'{path.name}: no create call found (update NO_NAMED_CREATE?)')
        elif lookup is None or lookup > create:
            problems.append(f'{path.name}: create at line {create} before any existing lookup ({lookup})')
    assert problems == []


def test_the_steps_this_file_drives_use_the_shared_index():
    for module in [step.module for step in STEPS] + [create_predefined_attributes]:
        assert 'ExistingIndex(' in pathlib.Path(module.__file__).read_text(), module.__name__


def test_the_guard_catches_a_create_before_the_lookup():
    blind = ("def lambda_handler(event, context):\n"
             "    c.create_queue(Name='x')\n"
             "    existing = ExistingIndex(c, 'i', 'queues')\n")
    guarded = ("def lambda_handler(event, context):\n"
               "    c, i, r = create_destination_client('arn')\n"
               "    existing = ExistingIndex(c, i, 'queues')\n"
               "    c.create_queue(Name='x')\n")
    assert lookup_before_create(blind, {'ExistingIndex'}) == (3, 2)
    assert lookup_before_create(guarded, {'ExistingIndex'}) == (3, 4)
