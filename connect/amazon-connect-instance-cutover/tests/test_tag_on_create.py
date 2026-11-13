"""Tag on create needs TagResource: every create call that passes tags must be covered.

The IAM policy simulator does not model tag on create, so a real run hit
AccessDenied on connect:TagResource at deploy time. This test finds, by AST,
every create_* call in lambda/*.py that passes Tags/tags (as a keyword or via
a parameters dict built in the same function), works out the service from
the local botocore models, and checks the target writer role grants the
matching TagResource on the resources that call creates.
"""
import ast
import pathlib

import botocore.session
from botocore import xform_name
from cfnlint.decode import cfn_yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
_session = botocore.session.get_session()
CONNECT_OPS = {xform_name(op) for op in _session.get_service_model('connect').operation_names}
QCONNECT_OPS = {xform_name(op) for op in _session.get_service_model('qconnect').operation_names}

# Resource pattern each tagged create needs TagResource on (as written in target-role.yaml).
WISDOM_RESOURCE_TYPE = {'create_ai_agent': 'ai-agent', 'create_ai_prompt': 'ai-prompt',
                        'create_ai_guardrail': 'ai-guardrail'}


def tagged_create_calls():
    """Yield (file name, create method, service) for each create call that can pass tags."""
    for path in sorted((ROOT / 'lambda').glob('*.py')):
        tree = ast.parse(path.read_text())
        for func in ast.walk(tree):
            if not isinstance(func, ast.FunctionDef):
                continue
            tagged_dicts = set()
            for node in ast.walk(func):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if (isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name)
                                and isinstance(target.slice, ast.Constant) and target.slice.value in ('Tags', 'tags')):
                            tagged_dicts.add(target.value.id)
            for node in ast.walk(func):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr.startswith('create_')):
                    continue
                passes_tags = any(kw.arg in ('Tags', 'tags') for kw in node.keywords) or any(
                    kw.arg is None and isinstance(kw.value, ast.Name) and kw.value.id in tagged_dicts
                    for kw in node.keywords)
                if not passes_tags:
                    continue
                method = node.func.attr
                service = 'connect' if method in CONNECT_OPS else 'wisdom' if method in QCONNECT_OPS else None
                yield path.name, method, service


def granted_tag_resources(action):
    template = cfn_yaml.load(str(ROOT / 'cloudformation' / 'target-role.yaml'))
    policy = template['Resources']['TargetWriterRole']['Properties']['Policies'][0]['PolicyDocument']
    resources = []
    for statement in policy['Statement']:
        if not isinstance(statement, dict) or statement.get('Effect') != 'Allow':
            continue
        actions = statement['Action'] if isinstance(statement['Action'], list) else [statement['Action']]
        if action not in actions:
            continue
        values = statement['Resource'] if isinstance(statement['Resource'], list) else [statement['Resource']]
        for value in values:
            sub = value.get('Fn::Sub') if isinstance(value, dict) else value
            resources.append(sub[0] if isinstance(sub, list) else sub)
    return resources


def test_audit_finds_the_known_tagged_creates():
    calls = {(f, m) for f, m, _ in tagged_create_calls()}
    assert ('create_users.py', 'create_user') in calls
    assert ('create_prompts.py', 'create_prompt') in calls
    assert ('create_ai_agents.py', 'create_ai_agent') in calls
    assert len(calls) >= 15


def test_every_tagged_create_has_a_service():
    unknown = [(f, m) for f, m, s in tagged_create_calls() if s is None]
    assert unknown == []


def test_connect_tagged_creates_have_tag_resource_on_instance_children():
    if any(s == 'connect' for _, _, s in tagged_create_calls()):
        resources = granted_tag_resources('connect:TagResource')
        assert '${TargetInstanceArn}/*' in resources, resources
        assert '*' not in resources


def test_wisdom_tagged_creates_have_tag_resource_on_their_resource_type():
    resources = granted_tag_resources('wisdom:TagResource')
    for _, method, service in tagged_create_calls():
        if service != 'wisdom':
            continue
        rtype = WISDOM_RESOURCE_TYPE[method]
        assert any(r.endswith(f':{rtype}/*') and '${AWS::AccountId}' in r for r in resources), (method, resources)
    assert '*' not in resources
