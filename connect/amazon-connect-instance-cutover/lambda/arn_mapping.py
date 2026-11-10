"""
Rewrite ARNs embedded in contact flow and flow module content so that the
content points at the target account, Region and instance.

Rules (applied to every ARN found in the content, one at a time):

* Connect resources of the source instance (queues, prompts, hours, flows,
  modules, agents, quick connects, routing and security profiles,
  hierarchy groups, views) are rewritten when a resource with the same name
  exists in the target. The source instance ARN itself maps to the target
  instance ARN.
* AWS managed resources (account field "aws", for example the managed
  views used by the Show view block) keep their resource path; only the
  Region is changed to the target Region.
* Lambda functions are rewritten to the function with the same name that
  is associated with the target instance. Otherwise a clear placeholder
  ARN in the target account and Region is written and the function is
  listed for manual follow up.
* Lex V2 bot aliases are matched by bot name and alias name against the
  bots associated with the target instance. Otherwise a placeholder alias
  ARN is written and the bot is listed for manual follow up.
* ARNs already in the target account and Region are left alone.
* Anything else that still points at the source account or Region is left
  unchanged and reported as unmapped.

Nothing here logs credentials or the ExternalId.
"""
import json
import re

import boto3
from botocore.exceptions import ClientError

from migration_filters import QUEUE_LIST_PARAMS, VIEW_LIST_PARAMS
from region_utils import BOTO_CONFIG, describe_error, error_code, failure_entry, reraise_if_fatal

LAMBDA_PLACEHOLDER_PREFIX = 'MIGRATION-PLACEHOLDER-'
# Lex V2 IDs are 10 upper case alphanumerics; these are easy to search for.
LEX_PLACEHOLDER_BOT_ID = 'MIGRATEBOT'
LEX_PLACEHOLDER_ALIAS_ID = 'PLACEHOLDR'

ARN_PATTERN = re.compile(
    r'arn:(?P<partition>aws[a-z-]*):(?P<service>[a-z0-9-]+):(?P<region>[a-z0-9-]*):'
    r'(?P<account>\d{12}|aws|):(?P<resource>[A-Za-z0-9_+=.@:/$\-]+)'
)
CONNECT_CHILD = re.compile(
    r'^instance/(?P<instance>[0-9a-f-]{36})(?:/(?P<type>[a-z-]+)/(?P<id>[A-Za-z0-9-]+)(?P<rest>[:/].*)?)?$'
)
LAMBDA_RESOURCE = re.compile(r'^function:(?P<name>[A-Za-z0-9_-]{1,64})(?::(?P<qualifier>[A-Za-z0-9_$-]+))?$')
LEX_ALIAS_RESOURCE = re.compile(r'^bot-alias/(?P<bot>[A-Z0-9]{10})/(?P<alias>[A-Z0-9]{10})$')
DYNAMIC_LAMBDA = re.compile(r'"LambdaFunctionARN"\s*:\s*"(?P<ref>\$\.[^"]+)"')

# Connect ARN path segment -> key in the id_maps dict.
CONNECT_TYPES = {
    'queue': 'queues',
    'prompt': 'prompts',
    'operating-hours': 'hours',
    'contact-flow': 'flows',
    'flow-module': 'modules',
    'agent': 'users',
    'transfer-destination': 'quickConnects',
    'routing-profile': 'routingProfiles',
    'security-profile': 'securityProfiles',
    'agent-group': 'hierarchyGroups',
    'view': 'views',
}


class InstanceRef:
    """Partition, Region, account and ID of a Connect instance."""

    def __init__(self, partition, region, account, instance_id):
        self.partition = partition
        self.region = region
        self.account = account
        self.instance_id = instance_id

    @classmethod
    def from_arn(cls, arn):
        match = ARN_PATTERN.fullmatch(arn.strip())
        child = CONNECT_CHILD.match(match.group('resource')) if match else None
        if not match or match.group('service') != 'connect' or not child:
            raise ValueError(f"Not a Connect instance ARN: {arn}")
        return cls(match.group('partition'), match.group('region'), match.group('account'),
                   child.group('instance'))

    @property
    def arn(self):
        return f"arn:{self.partition}:connect:{self.region}:{self.account}:instance/{self.instance_id}"


def lambda_placeholder_arn(target, function_name):
    name = (LAMBDA_PLACEHOLDER_PREFIX + function_name)[:64]
    return f"arn:{target.partition}:lambda:{target.region}:{target.account}:function:{name}"


def lex_placeholder_arn(target):
    return (f"arn:{target.partition}:lex:{target.region}:{target.account}:"
            f"bot-alias/{LEX_PLACEHOLDER_BOT_ID}/{LEX_PLACEHOLDER_ALIAS_ID}")


class RewriteReport:
    """What one rewrite() call changed, deduplicated per ARN."""

    def __init__(self):
        self.rewritten = []
        self.placeholders = []
        self.unmapped = []
        self._seen = set()

    def add_unmapped(self, kind, value, reason):
        key = ('unmapped', kind, value)
        if key not in self._seen:
            self._seen.add(key)
            self.unmapped.append({'type': kind, 'arn': value, 'reason': reason})

    def add_rewritten(self, kind, old, new):
        key = ('rewritten', old)
        if key not in self._seen:
            self._seen.add(key)
            self.rewritten.append({'type': kind, 'from': old, 'to': new})

    def add_placeholder(self, kind, old, new, action):
        # ARNs and IDs only: the source ARN identifies the resource, names stay out of step output.
        key = ('placeholder', old)
        if key not in self._seen:
            self._seen.add(key)
            self.placeholders.append({'type': kind, 'source': old, 'placeholder': new, 'action': action})

    def as_dict(self):
        return {'rewritten': self.rewritten, 'placeholders': self.placeholders, 'unmapped': self.unmapped}


class ArnRewriter:
    """Rewrite one piece of flow content and collect a report of what happened.

    id_maps: {'queues': {src_id: dst_id}, ...} keyed by the CONNECT_TYPES values.
    lambda_map: {function_name: target_function_arn} for functions associated
        with the target instance.
    lex_map: {source_alias_arn: target_alias_arn}.
    lex_names: {source_alias_arn: (bot_name, alias_name)} for reporting.
    """

    def __init__(self, source, target, id_maps=None, lambda_map=None, lex_map=None, lex_names=None):
        self.source = source
        self.target = target
        self.id_maps = id_maps or {}
        self.lambda_map = lambda_map or {}
        self.lex_map = lex_map or {}
        self.lex_names = lex_names or {}

    def rewrite(self, content):
        """Return (new_content_str, report) for one flow or module.

        Thread safe: all per call state lives in a RewriteReport.
        """
        rep = RewriteReport()
        content_str = content if isinstance(content, str) else json.dumps(content)
        new_content = ARN_PATTERN.sub(lambda match: self._replace(rep, match), content_str)

        for match in DYNAMIC_LAMBDA.finditer(new_content):
            rep.add_unmapped('lambdaDynamic', match.group('ref'),
                             'Lambda ARN is read from an attribute at run time; make sure the '
                             'attribute holds a target account and Region ARN')
        return new_content, rep.as_dict()

    # Internal helpers -------------------------------------------------

    def _in_target(self, region, account):
        return region == self.target.region and account == self.target.account

    def _replace(self, rep, match):
        arn = match.group(0)
        service = match.group('service')
        region = match.group('region')
        account = match.group('account')
        resource = match.group('resource')

        if service == 'connect':
            return self._replace_connect(rep, arn, region, account, resource)
        if service == 'lambda':
            return self._replace_lambda(rep, arn, region, account, resource)
        if service == 'lex':
            return self._replace_lex(rep, arn, region, account, resource)
        if account == 'aws' and region == self.source.region:
            return self._replace_aws_managed(rep, arn, match)
        if account == self.source.account and not self._in_target(region, account):
            rep.add_unmapped(service, arn, 'No mapping for this resource type; recreate or '
                               'update it in the target after migration')
        return arn

    def _replace_aws_managed(self, rep, arn, match):
        new = (f"arn:{self.target.partition}:{match.group('service')}:{self.target.region}:aws:"
               f"{match.group('resource')}")
        rep.add_rewritten('awsManaged', arn, new)
        return new

    def _replace_connect(self, rep, arn, region, account, resource):
        if account == 'aws':
            if region == self.source.region:
                new = f"arn:{self.target.partition}:connect:{self.target.region}:aws:{resource}"
                rep.add_rewritten('awsManaged', arn, new)
                return new
            return arn
        child = CONNECT_CHILD.match(resource)
        if not child or child.group('instance') != self.source.instance_id or account != self.source.account:
            if account == self.source.account and not self._in_target(region, account):
                rep.add_unmapped('connect', arn, 'Connect resource outside the source instance')
            return arn
        rtype = child.group('type')
        if rtype is None:
            rep.add_rewritten('instance', arn, self.target.arn)
            return self.target.arn
        map_key = CONNECT_TYPES.get(rtype)
        target_id = self.id_maps.get(map_key, {}).get(child.group('id')) if map_key else None
        if target_id:
            rest = child.group('rest') or ''
            new = f"{self.target.arn}/{rtype}/{target_id}{rest}"
            rep.add_rewritten(rtype, arn, new)
            return new
        rep.add_unmapped(rtype, arn, f"No {rtype} with the same name exists in the target instance")
        return arn

    def _replace_lambda(self, rep, arn, region, account, resource):
        parsed = LAMBDA_RESOURCE.match(resource)
        if not parsed:
            if not self._in_target(region, account):
                rep.add_unmapped('lambda', arn, 'Unrecognised Lambda ARN format')
            return arn
        if self._in_target(region, account):
            return arn
        name = parsed.group('name')
        mapped = self.lambda_map.get(name)
        if mapped:
            rep.add_rewritten('lambda', arn, mapped)
            return mapped
        placeholder = lambda_placeholder_arn(self.target, name)
        # The function name is part of the source ARN, so naming it here adds nothing new.
        rep.add_placeholder(
            'lambda', arn, placeholder,
            f"Deploy function '{name}' in account {self.target.account} Region {self.target.region}, "
            f"associate it with the target instance, then replace {placeholder} in the flow",
        )
        return placeholder

    def _replace_lex(self, rep, arn, region, account, resource):
        parsed = LEX_ALIAS_RESOURCE.match(resource)
        if not parsed:
            if account == self.source.account:
                rep.add_unmapped('lex', arn, 'Lex resource that is not a bot alias')
            return arn
        mapped = self.lex_map.get(arn)
        if mapped:
            if mapped != arn:
                rep.add_rewritten('lexV2', arn, mapped)
            return mapped
        if self._in_target(region, account):
            # Already in the target account and Region (for example a same account
            # migration); the Lex association step associates it with the instance.
            return arn
        # IDs from the ARN, not the bot and alias names (the names stay out of the step output).
        label = f"bot {parsed.group('bot')} alias {parsed.group('alias')}"
        placeholder = lex_placeholder_arn(self.target)
        rep.add_placeholder(
            'lexV2', arn, placeholder,
            f"Create a Lex V2 bot and alias matching source {label} ({arn}) in account "
            f"{self.target.account} Region {self.target.region}, associate it with the target "
            f"instance, then replace {placeholder} in the flow",
        )
        return placeholder


def merge_follow_up(follow_up, flow_id, report):
    """Fold one flow's report into a deduplicated manual follow up list.

    follow_up: dict keyed by (type, source) -> entry with a 'flows' list of
    source flow (or module) IDs. IDs only: flow names never enter the step output.
    """
    for item in report['placeholders']:
        key = (item['type'], item['source'])
        entry = follow_up.setdefault(key, {**item, 'flows': []})
        if flow_id not in entry['flows']:
            entry['flows'].append(flow_id)
    for item in report['unmapped']:
        key = (item['type'], item['arn'])
        entry = follow_up.setdefault(key, {'type': item['type'], 'source': item['arn'],
                                           'action': item['reason'], 'flows': []})
        if flow_id not in entry['flows']:
            entry['flows'].append(flow_id)
    return follow_up


# Mapping builders --------------------------------------------------------

def _collect(client, operation, list_key, name_key, id_key, **params):
    result = {}
    try:
        for page in client.get_paginator(operation).paginate(**params):
            for item in page.get(list_key, []):
                if item.get(name_key) and item.get(id_key):
                    result[item[name_key]] = item[id_key]
    except ClientError as err:
        reraise_if_fatal(err, resource=f"instance {params.get('InstanceId')}")
        raise
    return result


# List params per kind (the shared ones from migration_filters): agent queue
# summaries have no Name, and AWS managed views exist in every instance.
NAME_MAP_PARAMS = {'queues': QUEUE_LIST_PARAMS, 'views': VIEW_LIST_PARAMS}

NAME_MAP_SPECS = {
    'queues': ('list_queues', 'QueueSummaryList', 'Name', 'Id'),
    'prompts': ('list_prompts', 'PromptSummaryList', 'Name', 'Id'),
    'hours': ('list_hours_of_operations', 'HoursOfOperationSummaryList', 'Name', 'Id'),
    'flows': ('list_contact_flows', 'ContactFlowSummaryList', 'Name', 'Id'),
    'modules': ('list_contact_flow_modules', 'ContactFlowModulesSummaryList', 'Name', 'Id'),
    'users': ('list_users', 'UserSummaryList', 'Username', 'Id'),
    'quickConnects': ('list_quick_connects', 'QuickConnectSummaryList', 'Name', 'Id'),
    'routingProfiles': ('list_routing_profiles', 'RoutingProfileSummaryList', 'Name', 'Id'),
    'securityProfiles': ('list_security_profiles', 'SecurityProfileSummaryList', 'Name', 'Id'),
    'hierarchyGroups': ('list_user_hierarchy_groups', 'UserHierarchyGroupSummaryList', 'Name', 'Id'),
    'views': ('list_views', 'ViewsSummaryList', 'Name', 'Id'),
}


def build_name_map(source_client, dest_client, source_instance_id, dest_instance_id, kind):
    """Map source IDs to target IDs for one resource kind by name."""
    operation, list_key, name_key, id_key = NAME_MAP_SPECS[kind]
    params = dict(NAME_MAP_PARAMS.get(kind, {}))
    source_items = _collect(source_client, operation, list_key, name_key, id_key,
                            InstanceId=source_instance_id, **params)
    dest_items = _collect(dest_client, operation, list_key, name_key, id_key,
                          InstanceId=dest_instance_id, **params)
    return {src_id: dest_items[name] for name, src_id in source_items.items() if name in dest_items}


def build_id_maps(source_client, dest_client, source_instance_id, dest_instance_id, kinds=None):
    kinds = list(NAME_MAP_SPECS) if kinds is None else kinds
    return {kind: build_name_map(source_client, dest_client, source_instance_id, dest_instance_id, kind)
            for kind in kinds}


def build_lambda_map(dest_client, dest_instance_id):
    """{function_name: arn} for every Lambda associated with the target instance."""
    mapping = {}
    try:
        for page in dest_client.get_paginator('list_lambda_functions').paginate(InstanceId=dest_instance_id):
            for arn in page.get('LambdaFunctions', []):
                parsed = ARN_PATTERN.fullmatch(arn)
                resource = LAMBDA_RESOURCE.match(parsed.group('resource')) if parsed else None
                if resource:
                    # Prefer the unqualified ARN when both are associated.
                    if resource.group('name') not in mapping or not resource.group('qualifier'):
                        mapping[resource.group('name')] = arn
    except ClientError as err:
        reraise_if_fatal(err, resource=f"instance {dest_instance_id}")
        raise
    return mapping


def list_lex_v2_alias_arns(connect_client, instance_id):
    arns = []
    try:
        pages = connect_client.get_paginator('list_bots').paginate(InstanceId=instance_id, LexVersion='V2')
        for page in pages:
            for bot in page.get('LexBots', []):
                alias_arn = bot.get('LexV2Bot', {}).get('AliasArn')
                if alias_arn:
                    arns.append(alias_arn)
    except ClientError as err:
        reraise_if_fatal(err, resource=f"instance {instance_id}")
        raise
    return arns


def describe_lex_alias(lex_client_factory, alias_arn):
    """Return (bot_name, alias_name) or None when the bot or alias no longer exists."""
    parsed = ARN_PATTERN.fullmatch(alias_arn)
    resource = LEX_ALIAS_RESOURCE.match(parsed.group('resource')) if parsed else None
    if not resource:
        return None
    client = lex_client_factory(parsed.group('region'))
    try:
        bot = client.describe_bot(botId=resource.group('bot'))
        alias = client.describe_bot_alias(botId=resource.group('bot'), botAliasId=resource.group('alias'))
    except ClientError as err:
        reraise_if_fatal(err, resource=alias_arn)
        if error_code(err) == 'ResourceNotFoundException':
            print(f"Lex alias not found, cannot map by name: {alias_arn}")
            return None
        raise
    return bot['botName'], alias['botAliasName']


def build_lex_map(source_connect, dest_connect, source_instance_id, dest_instance_id,
                  source_lex_factory, dest_lex_factory):
    """Map source Lex V2 alias ARNs to target alias ARNs by (bot name, alias name).

    Only bots associated with each instance are considered. Returns
    (lex_map, lex_names) where lex_names holds the names for reporting.
    """
    target_by_name = {}
    for alias_arn in list_lex_v2_alias_arns(dest_connect, dest_instance_id):
        names = describe_lex_alias(dest_lex_factory, alias_arn)
        if names:
            target_by_name.setdefault(names, alias_arn)

    lex_map, lex_names = {}, {}
    for alias_arn in list_lex_v2_alias_arns(source_connect, source_instance_id):
        names = describe_lex_alias(source_lex_factory, alias_arn)
        if not names:
            continue
        lex_names[alias_arn] = names
        if names in target_by_name:
            lex_map[alias_arn] = target_by_name[names]
    print(f"Lex V2 mapping: {len(lex_map)} of {len(lex_names)} source aliases matched by name")
    return lex_map, lex_names


def build_rewriter(source_instance_arn, destination_instance_arn, source_client, dest_client,
                   dest_session, id_map_kinds=None):
    """Build an ArnRewriter with every mapping it needs.

    source_client uses the tool role; dest_client and dest_session use the
    assumed target role. Lex lookups go to the Region in each alias ARN.
    """
    source = InstanceRef.from_arn(source_instance_arn)
    target = InstanceRef.from_arn(destination_instance_arn)

    id_maps = build_id_maps(source_client, dest_client, source.instance_id, target.instance_id, id_map_kinds)
    lambda_map = build_lambda_map(dest_client, target.instance_id)
    lex_map, lex_names = build_lex_map(
        source_client, dest_client, source.instance_id, target.instance_id,
        source_lex_factory=lambda region: boto3.client('lexv2-models', region_name=region, config=BOTO_CONFIG),
        dest_lex_factory=lambda region: dest_session.client('lexv2-models', region_name=region, config=BOTO_CONFIG),
    )
    print("Mappings - " + ", ".join(f"{k}: {len(v)}" for k, v in id_maps.items())
          + f", lambdas: {len(lambda_map)}, lex: {len(lex_map)}")
    return ArnRewriter(source, target, id_maps, lambda_map, lex_map, lex_names)


def find_existing_by_name(connect_client, instance_id, operation, list_key, name):
    """Return the ID of the resource with this name in the instance, or None."""
    try:
        for page in connect_client.get_paginator(operation).paginate(InstanceId=instance_id):
            for item in page.get(list_key, []):
                if item.get('Name') == name:
                    return item['Id']
    except ClientError as err:
        reraise_if_fatal(err, resource=f"instance {instance_id}")
        raise
    return None


# Resources that already exist in the target ---------------------------------
#
# Every create step builds one ExistingIndex per invocation, before its first
# create call, maps the items it finds as "existing" and creates only the rest
# (otherwise a rerun creates queues and users blind and fails).
# tests/test_existing_index.py fails if a create step stops doing so.

# kind -> (list operation, summary list key, name key, ID key, list params).
# Keys checked against the botocore summary shapes (data tables use Id, rules
# RuleId, evaluation forms Title and EvaluationFormId, users Username). The list
# params are the shared ones from migration_filters, so the target is listed the
# way the source was. Predefined attributes have no ID: the name is the ID.
EXISTING_SPECS = {
    'queues': ('list_queues', 'QueueSummaryList', 'Name', 'Id', QUEUE_LIST_PARAMS),
    'users': ('list_users', 'UserSummaryList', 'Username', 'Id', {}),
    'modules': ('list_contact_flow_modules', 'ContactFlowModulesSummaryList', 'Name', 'Id', {}),
    'flows': ('list_contact_flows', 'ContactFlowSummaryList', 'Name', 'Id', {}),
    'dataTables': ('list_data_tables', 'DataTableSummaryList', 'Name', 'Id', {}),
    'evaluationForms': ('list_evaluation_forms', 'EvaluationFormSummaryList', 'Title', 'EvaluationFormId', {}),
    'prompts': ('list_prompts', 'PromptSummaryList', 'Name', 'Id', {}),
    'rules': ('list_rules', 'RuleSummaryList', 'Name', 'RuleId', {}),
    'views': ('list_views', 'ViewsSummaryList', 'Name', 'Id', VIEW_LIST_PARAMS),
    'securityProfiles': ('list_security_profiles', 'SecurityProfileSummaryList', 'Name', 'Id', {}),
    'routingProfiles': ('list_routing_profiles', 'RoutingProfileSummaryList', 'Name', 'Id', {}),
    'hours': ('list_hours_of_operations', 'HoursOfOperationSummaryList', 'Name', 'Id', {}),
    'agentStatuses': ('list_agent_statuses', 'AgentStatusSummaryList', 'Name', 'Id', {}),
    'predefinedAttributes': ('list_predefined_attributes', 'PredefinedAttributeSummaryList', 'Name', 'Name', {}),
}

LOOKUP_FAILED = 'target lookup failed'
NOT_FOUND_BY_NAME = 'exists but not found by name'


def _pages(client, list_op, params):
    """Every page of one list call: the paginator, or NextToken by hand when there is none.

    A Lambda runtime botocore can know a new operation before its paginator
    (data tables), so a missing paginator must not end the step.
    """
    if hasattr(client, 'get_paginator') and (not hasattr(client, 'can_paginate') or client.can_paginate(list_op)):
        yield from client.get_paginator(list_op).paginate(**params)
        return
    method, token = getattr(client, list_op), None
    while True:
        page = method(**params, **({'NextToken': token} if token else {}))
        yield page
        token = page.get('NextToken')
        if not token:
            return


def existing_index(client, list_op, params, list_key, name_key, id_key):
    """{name: ID} of every item a paged list call returns (exact, case sensitive names).

    Summaries without the name or the ID key are ignored (agent queue summaries
    have no Name). AccessDenied and throttling raise MigrationStepError; any other
    ClientError propagates so the caller records it with its code.
    """
    index = {}
    try:
        for page in _pages(client, list_op, params):
            for item in page.get(list_key, []):
                name, item_id = item.get(name_key), item.get(id_key)
                if name and item_id:
                    index.setdefault(name, item_id)
    except ClientError as err:
        reraise_if_fatal(err, resource=f"instance {params.get('InstanceId')}")
        raise
    return index


class ExistingIndex:
    """Name -> target ID for one resource kind, listed once per invocation.

    error holds the ClientError of a failed listing. While it is set a step must
    not create blind: it records lookup_failure(original_id), with the real code.
    Logs counts and error codes only.
    """

    def __init__(self, client, instance_id, kind):
        self.resource_type = kind  # a fixed EXISTING_SPECS key, safe to log
        self._client = client
        self._operation, self._list_key, self._name_key, self._id_key, params = EXISTING_SPECS[kind]
        self._params = {'InstanceId': instance_id, **params}
        self.last_error = None
        names = self._list()
        self.names, self.error = names or {}, self.last_error
        if self.error is None:
            print(f"Existing {self.resource_type} in target: {len(self.names)}")

    def _list(self):
        """A fresh name index, or None (and last_error set) when the listing failed."""
        try:
            names = existing_index(self._client, self._operation, self._params,
                                   self._list_key, self._name_key, self._id_key)
        except ClientError as err:
            print(f"Existing {self.resource_type} lookup failed: {describe_error(err)}")
            self.last_error = err
            return None
        self.last_error = None
        return names

    def get(self, name):
        return self.names.get(name) if name else None

    def lookup_failure(self, original_id):
        return failure_entry(original_id, self.error, LOOKUP_FAILED)

    def recheck(self, name, create_error, original_id, reason=NOT_FOUND_BY_NAME):
        """One fresh lookup after a create error that can mean "already exists".

        Returns (target_id, None) when the name is now found, otherwise
        (None, failure entry): the lookup's own code if the listing failed,
        else the create error's real code.
        """
        names = self._list()
        if names is None:
            return None, failure_entry(original_id, self.last_error, LOOKUP_FAILED)
        self.names = names
        found = self.get(name)
        if found:
            return found, None
        return None, failure_entry(original_id, create_error, reason)
