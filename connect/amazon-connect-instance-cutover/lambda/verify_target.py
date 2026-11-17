"""
State machine step VerifyTarget: read only checks of the target after a migration.

Runs as ConnectMigrationSourceReader. The source is read with the role's own
credentials; the target through the target writer role, assumed with the
ExternalId from the per run secret (externalIdSecretArn only, a raw
externalId is rejected). Only List and Describe APIs are called.

Checks, for the resource types selected in `features`:
- names: per type, source names found in the target, missing (listed by
  source ID) and extra. Things the migration skips on purpose are counted as
  skipped, never as missing, using the same predicates as the list and create
  steps (migration_filters): default and sample flows, agent queues, AWS
  managed and unpublished views, inactive vocabularies, inactive evaluation
  forms, unpublished rules, Connect owned predefined attributes and built-in
  agent statuses. AI resources whose flag is off are not checked, and phone
  numbers, Lambda functions and Lex bots are not compared by name (see
  NOT_COMPARED).
- flowContent / moduleContent: migrated flows and modules are scanned for
  ARNs and IDs of the source account, source Region or source instance.
  Each one is judged with the flow rewriter's own mapping
  (arn_mapping.build_rewriter, built only when a reference is found): a
  reference the rewriter maps now (the target has the matching resource)
  should have been rewritten and is a FAIL (MappableSourceReference); one
  the tool has no way to map is a follow up (UnmappableSourceReference), as
  are MIGRATION-PLACEHOLDER Lambda ARNs and MIGRATEBOT Lex aliases
  (Placeholder), and Set Voice ID or Check Voice ID blocks (VoiceIdEndOfSupport:
  Voice ID reached end of support on May 20, 2026 and is not migrated). Each
  follow up is listed in manualFollowUp by target flow
  ID, ARN type and reason code (FOLLOW_UP_ACTIONS says how to resolve
  each). The migration report takes the flow follow ups from here, so a
  rerun whose flows already exist still lists them.
- queueConfig: the target OutboundCallerConfig holds no source phone number
  ID; its outbound flow and the queue's hours exist in the target and have
  the source's names.
- userConfig: routing profile, security profiles and hierarchy group of each
  target user have the names of the source user's ones.
- quickConnectTargets: USER, QUEUE, PHONE_NUMBER and FLOW configs reference
  users, queues and flows that exist in the target (and match the source by
  name). The phone number itself is never read into the output.

Verdict: PASS, PASS_WITH_FOLLOW_UPS (follow ups only: placeholders, source
references the tool cannot map, a dropped caller ID number; the run ends
Succeeded and the report lists them), FAIL (a check failed or stopped
early) or ERROR (the step could not run; errorCode says why). Failing checks
are listed in `failedChecks`, which the migration report counts as failed
items, so a FAIL or ERROR ends the execution Failed.

The output and the logs hold resource IDs, ARN types, check and error codes
and counts only: never names, usernames, emails, phone numbers, flow content,
exception text or the ExternalId.
"""
import re
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import boto3
from botocore.exceptions import OperationNotPageableError

import migration_filters
from arn_mapping import ARN_PATTERN, LAMBDA_PLACEHOLDER_PREFIX, LEX_PLACEHOLDER_BOT_ID, build_rewriter
from migration_filters import feature_on
from region_utils import (
    BOTO_CONFIG,
    CONNECT_DESCRIBE_RATES,
    MigrationStepError,
    RateLimiter,
    create_connect_client_from_arn,
    create_destination_client,
    create_destination_session,
    error_code_of,
    extract_account_id_from_arn,
    is_feature_unavailable,
    reraise_if_fatal,
    resolve_external_id,
)
from region_utils import FEATURE_NOT_AVAILABLE
from region_utils import step_handler

PASS, FAIL = 'PASS', 'FAIL'
PASS_WITH_FOLLOW_UPS, ERROR = 'PASS_WITH_FOLLOW_UPS', 'ERROR'
MAX_IDS = 20                 # resource IDs listed per check; counts are always complete
MAX_FLOWS = 200              # flows listed per content check; counts are always complete
MAX_DESCRIBES = 1000         # Describe calls per check before it stops (VerificationIncomplete)
TIME_RESERVE_SECONDS = 60    # stop describing when less Lambda time than this is left
API_TIME_BUDGET_SECONDS = 20  # the UI endpoint must answer within the API Gateway timeout
API_WORKERS = 8               # UI endpoint: parallel listings (the state machine step uses 1)
CHECK_WORKERS = 4             # UI endpoint: parallel Describe items per detailed check
_NOT_STARTED = object()
LEX_PLACEHOLDER = f'bot-alias/{LEX_PLACEHOLDER_BOT_ID}/'
INCOMPLETE = 'VerificationIncomplete'


class Kind:
    """How to list one resource type, and which listed items the migration copies.

    migrates: the predicate from migration_filters that the migration side uses
    (None: everything is copied). detail: (Describe method, ID parameter,
    response key) when the predicate needs the Describe result, as for
    evaluation forms. Those are described on the source side only, to decide
    what was copied; the target side compares every listed item, as the create
    step does when it looks for an existing one by name.
    """

    def __init__(self, operation, list_key, id_key, name_key, extra=None, migrates=None, service='connect',
                 detail=None):
        self.operation, self.list_key, self.id_key, self.name_key = operation, list_key, id_key, name_key
        self.extra, self.migrates, self.service, self.detail = extra or {}, migrates, service, detail


KINDS = {
    'agentStatuses': Kind('list_agent_statuses', 'AgentStatusSummaryList', 'Id', 'Name',
                          migrates=migration_filters.migrates_agent_status),
    'hierarchyGroups': Kind('list_user_hierarchy_groups', 'UserHierarchyGroupSummaryList', 'Id', 'Name'),
    'prompts': Kind('list_prompts', 'PromptSummaryList', 'Id', 'Name'),
    # Predefined attributes have no ID: compared by name, reported by count only.
    'predefinedAttributes': Kind('list_predefined_attributes', 'PredefinedAttributeSummaryList', None, 'Name',
                                 migrates=migration_filters.migrates_predefined_attribute),
    'dataTables': Kind('list_data_tables', 'DataTableSummaryList', 'Id', 'Name'),
    'contactFlows': Kind('list_contact_flows', 'ContactFlowSummaryList', 'Id', 'Name',
                         migrates=migration_filters.migrates_contact_flow),
    'contactFlowModules': Kind('list_contact_flow_modules', 'ContactFlowModulesSummaryList', 'Id', 'Name'),
    'views': Kind('list_views', 'ViewsSummaryList', 'Id', 'Name', migration_filters.VIEW_LIST_PARAMS,
                  migration_filters.migrates_view),
    'rules': Kind('list_rules', 'RuleSummaryList', 'RuleId', 'Name', migrates=migration_filters.migrates_rule),
    'evaluationForms': Kind('list_evaluation_forms', 'EvaluationFormSummaryList', 'EvaluationFormId', 'Title',
                            migrates=migration_filters.migrates_evaluation_form,
                            detail=('describe_evaluation_form', 'EvaluationFormId', 'EvaluationForm')),
    # Listed without the State filter, so inactive ones are counted as skipped.
    'vocabularies': Kind('search_vocabularies', 'VocabularySummaryList', 'Id', 'Name',
                         migrates=migration_filters.migrates_vocabulary),
    'hoursOfOperations': Kind('list_hours_of_operations', 'HoursOfOperationSummaryList', 'Id', 'Name'),
    'queues': Kind('list_queues', 'QueueSummaryList', 'Id', 'Name', migration_filters.QUEUE_LIST_PARAMS,
                   migration_filters.migrates_queue),
    'securityProfiles': Kind('list_security_profiles', 'SecurityProfileSummaryList', 'Id', 'Name'),
    'routingProfiles': Kind('list_routing_profiles', 'RoutingProfileSummaryList', 'Id', 'Name'),
    'users': Kind('list_users', 'UserSummaryList', 'Id', 'Username'),
    'quickConnects': Kind('list_quick_connects', 'QuickConnectSummaryList', 'Id', 'Name'),
    'aiAgents': Kind('list_ai_agents', 'aiAgentSummaries', 'aiAgentId', 'name', migration_filters.AI_LIST_PARAMS,
                     service='qconnect'),
    'aiPrompts': Kind('list_ai_prompts', 'aiPromptSummaries', 'aiPromptId', 'name', migration_filters.AI_LIST_PARAMS,
                      service='qconnect'),
    'aiGuardrails': Kind('list_ai_guardrails', 'aiGuardrailSummaries', 'aiGuardrailId', 'name', service='qconnect'),
}
# feature flag -> resource types compared by name when the flag is on
FEATURE_KINDS = {
    'agentStatus': ['agentStatuses'],
    'hierarchyGroups': ['hierarchyGroups'],
    'prompts': ['prompts'],
    'predefinedAttributes': ['predefinedAttributes'],
    'dataTables': ['dataTables'],
    'contactFlows': ['contactFlows'],
    'contactFlowModules': ['contactFlowModules'],
    'views': ['views'],
    'rules': ['rules'],
    'evaluationForms': ['evaluationForms'],
    'vocabularies': ['vocabularies'],
    'queues': ['hoursOfOperations', 'queues'],
    'users': ['securityProfiles', 'routingProfiles', 'users'],
    'quickConnects': ['quickConnects'],
    'aiAgents': ['aiAgents'],
    'aiPrompts': ['aiPrompts'],
    'aiGuardrails': ['aiGuardrails'],
}
AI_FEATURES = ('aiAgents', 'aiPrompts', 'aiGuardrails')
# Selected types the migration does not copy by name: never counted as missing.
NOT_COMPARED = {
    'phoneNumbers': 'NotMigratedByDesign',     # moved by the phone number step or AWS Support, or ported
    'lambdaFunctions': 'CheckedInFlowContent',  # associations; unmatched ones become placeholders
    'lexBots': 'CheckedInFlowContent',
}
# Quick connect type -> config key and its references: (field, kind, not in target code, name mismatch code)
QUICK_CONNECT_REFERENCES = {
    'QUEUE': ('QueueConfig', (('QueueId', 'queues', 'QueueNotInTarget', 'QueueMismatch'),
                              ('ContactFlowId', 'contactFlows', 'FlowNotInTarget', 'FlowMismatch'))),
    'USER': ('UserConfig', (('UserId', 'users', 'UserNotInTarget', 'UserMismatch'),
                            ('ContactFlowId', 'contactFlows', 'FlowNotInTarget', 'FlowMismatch'))),
    # PhoneConfig holds the external number (never read here) and, if any, a flow.
    'PHONE_NUMBER': ('PhoneConfig', (('ContactFlowId', 'contactFlows', 'FlowNotInTarget', 'FlowMismatch'),)),
    'FLOW': ('FlowConfig', (('ContactFlowId', 'contactFlows', 'FlowNotInTarget', 'FlowMismatch'),)),
}


class ListUnavailable(Exception):
    """A list call failed (non fatal) or the SDK lacks the operation; the check reports the code."""

    def __init__(self, code, follow_up=False):
        super().__init__(code)
        self.code, self.follow_up = code, follow_up


_UNSET = object()


class Side:
    """One instance (source or target): Connect and optional AI agents (qconnect) clients, cached listings."""

    def __init__(self, connect, instance_id, instance_arn=None, qconnect=None, is_source=False):
        self.connect, self.instance_id, self.instance_arn, self.qconnect = connect, instance_id, instance_arn, qconnect
        self.is_source = is_source
        self._lists, self._assistant, self._phone_number_ids = {}, _UNSET, None
        self.ai_unavailable = None  # error code when the Region does not offer AI agents (qconnect)

    def rate_scope(self):
        """Account and Region of the instance (API rate quotas apply per account and Region)."""
        parts = (self.instance_arn or '').split(':')
        return (parts[4], parts[3]) if len(parts) > 5 else ('instance', self.instance_id)

    def assistant_id(self):
        """First AI agents assistant in the Region (same rule as the AI create steps), or None.

        None also when the Region does not offer AI agents; ai_unavailable then holds the error code
        and every qconnect listing reports FeatureNotAvailableInRegion as a follow up.
        """
        if self._assistant is _UNSET:
            assistant = None
            if self.qconnect is not None:
                try:
                    for page in self.qconnect.get_paginator('list_assistants').paginate():
                        summaries = page.get('assistantSummaries') or []
                        if summaries:
                            assistant = summaries[0]['assistantId']
                            break
                except Exception as err:
                    reraise_if_fatal(err, self.instance_id)
                    if not is_feature_unavailable(err, self.qconnect):
                        raise
                    self.ai_unavailable = error_code_of(err)
            self._assistant = assistant
        return self._assistant

    def _pages(self, spec):
        if spec.service == 'qconnect':
            assistant_id = self.assistant_id()
            if self.ai_unavailable:
                raise ListUnavailable(FEATURE_NOT_AVAILABLE, follow_up=True)
            if not assistant_id:
                return []
            return self.qconnect.get_paginator(spec.operation).paginate(assistantId=assistant_id, **spec.extra)
        return self.connect.get_paginator(spec.operation).paginate(InstanceId=self.instance_id, **spec.extra)

    def _migrated(self, spec, item, key):
        """The migration side's predicate; items that need a Describe are judged on the source only."""
        if spec.migrates is None:
            return True
        if spec.detail is None:
            return spec.migrates(item)
        if not self.is_source:
            return True
        method, id_param, response_key = spec.detail
        return spec.migrates(getattr(self.connect, method)(InstanceId=self.instance_id, **{id_param: key})
                             [response_key])

    def listing(self, kind):
        """(every {key: name}, compared {key: name}, skipped count); key is the ID, or the name if none."""
        if kind not in self._lists:
            spec = KINDS[kind]
            try:
                everything, compared, skipped = {}, {}, 0
                for page in self._pages(spec):
                    for item in page.get(spec.list_key, []):
                        item_name = item.get(spec.name_key)
                        key = item.get(spec.id_key) if spec.id_key else item_name
                        if item_name is None or not key:
                            continue
                        everything[key] = item_name
                        if self._migrated(spec, item, key):
                            compared[key] = item_name
                        else:
                            skipped += 1
                self._lists[kind] = (everything, compared, skipped)
            except ListUnavailable as err:
                self._lists[kind] = err
            except OperationNotPageableError:
                self._lists[kind] = ListUnavailable('SdkLacksOperation', follow_up=True)
            except Exception as err:
                reraise_if_fatal(err, self.instance_id)
                # A feature the Region does not offer is a follow up, not a failing issue.
                feature_client = self.qconnect if spec.service == 'qconnect' else None
                if is_feature_unavailable(err, feature_client):
                    self._lists[kind] = ListUnavailable(FEATURE_NOT_AVAILABLE, follow_up=True)
                else:
                    self._lists[kind] = ListUnavailable(error_code_of(err))
        cached = self._lists[kind]
        if isinstance(cached, ListUnavailable):
            raise cached
        return cached

    def names(self, kind):
        """{key: name} of every listed item, skipped ones included (for reference checks)."""
        return self.listing(kind)[0]

    def compared(self, kind):
        """{key: name} of the items the migration copies."""
        return self.listing(kind)[1]

    def phone_number_ids(self):
        """IDs of the phone numbers claimed to this instance (the numbers themselves are dropped)."""
        if self._phone_number_ids is None:
            found = set()
            paginator = self.connect.get_paginator('list_phone_numbers_v2')
            for page in paginator.paginate(TargetArn=self.instance_arn):
                found.update(p['PhoneNumberId'] for p in page.get('ListPhoneNumbersSummaryList', [])
                             if p.get('PhoneNumberId'))
            self._phone_number_ids = found
        return self._phone_number_ids


class SourceRef:
    """What counts as a reference to the source: its instance ID, and its account and Region where they differ."""

    def __init__(self, instance_id, account, region, target_account, target_region):
        self.instance_id, self.account, self.region = instance_id, account, region
        self.cross_account, self.cross_region = account != target_account, region != target_region

    @property
    def cross_account_or_region(self):
        return self.cross_account or self.cross_region


class Budget:
    """Stops Describe loops before the Lambda (or the API Gateway request) runs out of time.

    workers: 1 for the state machine step (every call in order, as it always
    ran). The UI endpoint (for_api) lists both instances with up to
    API_WORKERS calls at once, runs the detailed check groups side by side
    (verify), and up to CHECK_WORKERS Describe items at once per check, each
    Describe paced at the API's default rate (region_utils.CONNECT_DESCRIBE_RATES)
    per operation, account and Region. Each operation has its own rate, so
    the slow flow Describes (2 per second) do not hold up users or quick
    connects (10 per second).
    """

    def __init__(self, deadline=None, max_describes=MAX_DESCRIBES, workers=1):
        self.deadline, self.max_describes, self.workers = deadline, max_describes, workers
        self._limiters, self._lock = {}, threading.Lock()

    @classmethod
    def for_lambda(cls, context):
        remaining = getattr(context, 'get_remaining_time_in_millis', None)
        if not callable(remaining):
            return cls()
        return cls(time.monotonic() + remaining() / 1000 - TIME_RESERVE_SECONDS)

    @classmethod
    def for_api(cls):
        return cls(time.monotonic() + API_TIME_BUDGET_SECONDS, workers=API_WORKERS)

    @property
    def parallel(self):
        return self.workers > 1

    def past_deadline(self):
        return self.deadline is not None and time.monotonic() >= self.deadline

    def exhausted(self, describes):
        return describes >= self.max_describes or self.past_deadline()

    def pace(self, side, operation):
        """Wait for a slot of this Describe operation on this side's account and Region (parallel runs only).

        Raises _PastDeadline when the slot only comes at or after the deadline,
        so the item is left unstarted instead of running past the budget.
        """
        rate = CONNECT_DESCRIBE_RATES.get(operation)
        if not self.parallel or not rate:
            return
        key = (side.rate_scope(), operation)
        with self._lock:
            limiter = self._limiters.get(key)
            if limiter is None:
                limiter = self._limiters[key] = RateLimiter(rate)
        if not limiter.wait(self.deadline) or self.past_deadline():
            raise _PastDeadline()


class _PastDeadline(Exception):
    """A paced Describe slot came after the budget's deadline (the item is not started)."""


def fetch(budget, side, operation, key, **kwargs):
    """One Describe call for a check: (result, None), or (None, error code) for a non fatal error.

    Thread safe: it records nothing in the check result (taken() does, in order).
    """
    budget.pace(side, operation)
    try:
        return getattr(side.connect, operation)(**kwargs)[key], None
    except Exception as err:
        reraise_if_fatal(err)
        return None, error_code_of(err)


def taken(result, resource_id, fetched):
    """The result of a fetch(); a non fatal error is counted under its error code and gives None."""
    value, code = fetched
    if code is not None:
        add_issue(result, code, resource_id)
    return value


def map_in_order(call, items, workers):
    """[call(item)] in item order with up to `workers` threads; the first error cancels the rest and propagates."""
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(call, item) for item in items]
        try:
            return [future.result() for future in futures]
        except BaseException:
            for future in futures:
                future.cancel()
            raise


def within_budget(budget, result, items, load):
    """Yield (item, load(item)) in order for the items started within the budget, counting each as checked.

    The first item not started adds VerificationIncomplete and ends the loop.
    One worker: each load runs just before its item is processed, exactly the
    loop the state machine step always ran. Parallel: up to CHECK_WORKERS
    loads run at once; an item whose turn, or whose paced Describe slot, comes
    when the budget is exhausted is not started, and the results are processed
    in item order up to the first item not started, so the result has the
    same shape and order as a sequential run.
    """
    items = list(items)
    if not budget.parallel:
        for item in items:
            if budget.exhausted(result['checked']):
                add_issue(result, INCOMPLETE)
                return
            result['checked'] += 1
            yield item, load(item)
        return

    def run(indexed):
        index, item = indexed
        if budget.exhausted(index):
            return _NOT_STARTED
        try:
            return load(item)
        except _PastDeadline:
            return _NOT_STARTED

    loaded = map_in_order(run, list(enumerate(items)), min(budget.workers, CHECK_WORKERS))
    for item, value in zip(items, loaded):
        if value is _NOT_STARTED:
            add_issue(result, INCOMPLETE)
            return
        result['checked'] += 1
        yield item, value


# Listings each detailed check reads besides its own type (names of referenced resources).
REFERENCED_KINDS = {
    'queues': ('hoursOfOperations', 'contactFlows'),
    'users': ('routingProfiles', 'securityProfiles', 'hierarchyGroups'),
    'quickConnects': ('users', 'queues', 'contactFlows'),
}


def prefetch(source, target, features, ref, workers):
    """Fill both sides' listing caches in parallel (the UI endpoint); the checks then read the caches.

    A list that fails non fatally is cached as ListUnavailable, as a lazy listing
    would be, so the checks report it the same way. Fatal errors propagate.
    """
    kinds = set()
    for feature, feature_kinds in FEATURE_KINDS.items():
        if feature_on(features, feature):
            kinds.update(feature_kinds)
            kinds.update(kind for own in feature_kinds for kind in REFERENCED_KINDS.get(own, ()))
    if any(KINDS[kind].service == 'qconnect' for kind in kinds):
        # Resolved once per side before the AI listings run in parallel.
        source.assistant_id()
        target.assistant_id()
    tasks = [(side.listing, kind) for kind in sorted(kinds) for side in (source, target)]
    if feature_on(features, 'queues') and ref.cross_account_or_region:
        tasks.append((lambda _: phone_ids_or_none(source), None))

    def run(task):
        call, kind = task
        try:
            call(kind)
        except ListUnavailable:
            pass  # cached; the check reports its code

    map_in_order(run, tasks, workers)


def phone_ids_or_none(side):
    """Prefetch of the source phone number IDs; a non fatal error is left for the queue check to report."""
    try:
        return side.phone_number_ids()
    except Exception as err:
        reraise_if_fatal(err)
        return None


def new_check(check, kind):
    return {'check': check, 'type': kind, 'status': PASS, 'checked': 0, 'issues': {}, 'followUps': {},
            'ids': [], 'unlistedIssues': 0}


def add_issue(result, code, resource_id=None, follow_up=False):
    """Count an issue code (FAIL) or a follow up code (no FAIL) and list the resource ID once."""
    bucket = result['followUps'] if follow_up else result['issues']
    bucket[code] = bucket.get(code, 0) + 1
    if not follow_up:
        result['status'] = FAIL
    if resource_id and resource_id not in result['ids']:
        if len(result['ids']) < MAX_IDS:
            result['ids'].append(resource_id)
        else:
            result['unlistedIssues'] += 1


def describe(result, resource_id, call, key, **kwargs):
    """One Describe call; a non fatal error is counted under its error code and returns None."""
    try:
        return call(**kwargs)[key]
    except Exception as err:
        reraise_if_fatal(err)
        add_issue(result, error_code_of(err), resource_id)
        return None


def matched_pairs(source, target, kind):
    """[(source ID, target ID)] for compared resources with the same name in both instances."""
    target_ids = {item_name: key for key, item_name in target.compared(kind).items()}
    return [(key, target_ids[item_name]) for key, item_name in sorted(source.compared(kind).items())
            if item_name in target_ids]


def same_named(source, target, kind, source_id, target_id):
    """True if both IDs are unset, or both resolve to the same name."""
    if not source_id and not target_id:
        return True
    source_value = source.names(kind).get(source_id) if source_id else None
    return source_value is not None and source_value == target.names(kind).get(target_id)


def check_names(source, target, kind):
    """Source names found in the target, missing (source IDs listed) and extra; skipped items not counted."""
    result = new_check('names', kind)
    source_items = source.compared(kind)
    source_values, target_values = set(source_items.values()), set(target.compared(kind).values())
    found = 0
    for key, item_name in sorted(source_items.items()):
        if item_name in target_values:
            found += 1
        else:
            add_issue(result, 'MissingInTarget', key if KINDS[kind].id_key else None)
    result.update(checked=len(source_items), found=found, missing=result['issues'].get('MissingInTarget', 0),
                  extra=len(target_values - source_values), skipped=source.listing(kind)[2])
    return result


def arn_type(service, resource):
    """lambda, lex, queue, prompt, wisdom or other."""
    if service in ('lambda', 'lex', 'wisdom'):
        return service
    if service == 'connect':
        parts = resource.split('/')
        if len(parts) > 2 and parts[0] == 'instance' and parts[2] in ('queue', 'prompt'):
            return parts[2]
    return 'other'


def find_source_references(content, ref):
    """[(ARN type, service, ARN)] for each reference to the source; bare IDs outside ARNs have no ARN."""
    found = []
    outside, last = [], 0
    for match in ARN_PATTERN.finditer(content):
        outside.append(content[last:match.start()])
        last = match.end()
        account, region, resource = match.group('account'), match.group('region'), match.group('resource')
        if (ref.instance_id in resource or (ref.cross_account and account == ref.account)
                or (ref.cross_region and region == ref.region)):
            found.append((arn_type(match.group('service'), resource), match.group('service'), match.group(0)))
    outside.append(content[last:])
    text = ' '.join(outside)
    bare = text.count(ref.instance_id)
    if ref.cross_account:
        # A standalone account ID, not 12 digits inside a longer number or an ID such as a UUID.
        bare += len(re.findall(rf'(?<![\w-]){re.escape(ref.account)}(?![\w-])', text))
    found.extend([('other', None, None)] * bare)
    return found


def source_references(content, ref):
    """Counter of ARN types (and 'other' for bare IDs) in content that point at the source."""
    return Counter(kind for kind, _, _ in find_source_references(content, ref))


# A source reference left in target content is a FAIL when the flow rewriter would map it now (the
# target has the matching resource, so the flow should point at it), and a follow up when the tool has
# no way to map it. Follow up reason codes and how to resolve each (fixed text: no names, no content).
MAPPABLE = 'MappableSourceReference'
UNMAPPABLE = 'UnmappableSourceReference'
FOLLOW_UP_ACTIONS = {
    'NoTargetAssistant': 'The target has no AI agents assistant. Create one in the target Region, '
                         'integrate it with the target instance, select it in the AI agents block of '
                         'this flow and publish the flow.',
    'AssistantNotMapped': 'The tool does not map AI agents assistant ARNs. Select the target assistant '
                          'in the AI agents block of this flow and publish the flow.',
    'NoMatchingLambda': 'No function with the same name is associated with the target instance. Deploy it in the '
                        'target account and Region, add it to the target instance, select it in the Invoke AWS '
                        'Lambda function block of this flow (it holds a MIGRATION-PLACEHOLDER or source ARN) and '
                        'publish the flow.',
    'NoMatchingLexBot': 'No Lex V2 bot alias with the same bot and alias names is associated with the target '
                        'instance. Create or import the bot in the target account and Region, add it to the target '
                        'instance, select it in the Get customer input block of this flow (it holds a MIGRATEBOT '
                        'placeholder or source ARN) and publish the flow.',
    'NoMatchingTargetResource': 'No Amazon Connect Customer resource with the same name exists in the target instance. '
                                'Migrate or create it, select it in the block of this flow and publish the flow.',
    'UnsupportedArnType': 'The tool does not map this kind of ARN. Create the resource in the target account and '
                          'Region, update the block of this flow and publish the flow.',
    'SourceIdOutsideArn': 'The flow holds the source account or instance ID outside an ARN (for example in a text '
                          'or an attribute value). Replace it with the target value and publish the flow.',
    'VoiceIdEndOfSupport': 'This flow uses Voice ID (a Set Voice ID or Check Voice ID block). Amazon Connect '
                           'Customer Voice ID reached end of support on May 20, 2026, and the tool does not migrate '
                           'it. Remove those blocks or replace them with another caller authentication method, '
                           'and publish the flow.',
}
# Flow language action types of the Set Voice ID and Check Voice ID blocks (content is already read; no new call).
VOICE_ID_ACTIONS = re.compile(r'"Type"\s*:\s*"(?:StartVoiceIdStream|CheckVoiceId)"')
VOICE_ID_END_OF_SUPPORT = 'VoiceIdEndOfSupport'
SERVICE_REASONS = {'lambda': 'NoMatchingLambda', 'lex': 'NoMatchingLexBot', 'connect': 'NoMatchingTargetResource'}
MAX_FOLLOW_UPS = 200          # manual follow up entries listed; counts are always complete
FOLLOW_UP_TYPE = 'flowReference'
RESOURCE_TYPES = {'contactFlows': 'contactFlow', 'contactFlowModules': 'contactFlowModule'}


class ReferenceMapper:
    """Decides with the flow rewriter's own mapping (arn_mapping.build_rewriter) whether a reference maps.

    build: returns the ArnRewriter; called once, on the first source reference found, so a clean
    target makes no extra calls. target: the target Side (its AI agents assistant, for wisdom ARNs).
    """

    def __init__(self, build, target):
        self._build, self._target, self._rewriter, self._reasons = build, target, None, {}

    def reason(self, service, arn):
        """None if the rewriter maps the reference (a FAIL), else the follow up reason code."""
        if arn is None:
            return 'SourceIdOutsideArn'
        if arn not in self._reasons:
            if self._rewriter is None:
                self._rewriter = self._build()
            new_arn, report = self._rewriter.rewrite(arn)
            if report['rewritten'] and new_arn != arn:
                reason = None
            elif service == 'wisdom':
                reason = 'AssistantNotMapped' if self._target.assistant_id() else 'NoTargetAssistant'
            else:
                reason = SERVICE_REASONS.get(service, 'UnsupportedArnType')
            self._reasons[arn] = reason
        return self._reasons[arn]


def follow_up_entry(kind, target_id, ref_type, reason, count):
    return {'type': FOLLOW_UP_TYPE, 'resourceType': RESOURCE_TYPES[kind], 'flowId': target_id,
            'arnType': ref_type, 'reasonCode': reason, 'count': count, 'action': FOLLOW_UP_ACTIONS[reason]}


def check_content(source, target, kind, ref, budget, mapper=None):
    """Scan migrated flows or modules (same name in both instances) in the target."""
    result = new_check('flowContent' if kind == 'contactFlows' else 'moduleContent', kind)
    result.update(flows=[], unlistedFlows=0, flowsWithSourceReferences=0, sourceArnTypes={}, placeholders=0,
                  placeholderTypes={'lambda': 0, 'lex': 0}, manualFollowUp=[], unlistedFollowUps=0)
    source_values = set(source.compared(kind).values())
    totals = Counter()
    if kind == 'contactFlows':
        operation, key, id_param = 'describe_contact_flow', 'ContactFlow', 'ContactFlowId'
    else:
        operation, key, id_param = 'describe_contact_flow_module', 'ContactFlowModule', 'ContactFlowModuleId'
    migrated = [target_id for target_id, item_name in sorted(target.compared(kind).items())
                if item_name in source_values]

    def load(target_id):
        return fetch(budget, target, operation, key, InstanceId=target.instance_id, **{id_param: target_id})

    for target_id, fetched in within_budget(budget, result, migrated, load):
        flow = taken(result, target_id, fetched)
        if flow is None:
            continue
        content = flow.get('Content') or ''
        refs = find_source_references(content, ref)
        lambda_count, lex_count = content.count(LAMBDA_PLACEHOLDER_PREFIX), content.count(LEX_PLACEHOLDER)
        result['placeholderTypes']['lambda'] += lambda_count
        result['placeholderTypes']['lex'] += lex_count
        result['placeholders'] += lambda_count + lex_count
        mappable, follow_ups = classify(result, target_id, refs, mapper)
        voice_id_count = len(VOICE_ID_ACTIONS.findall(content))
        follow_ups.update({('lambda', 'NoMatchingLambda'): lambda_count, ('lex', 'NoMatchingLexBot'): lex_count,
                           ('voiceid', VOICE_ID_END_OF_SUPPORT): voice_id_count})
        follow_ups = +follow_ups  # drop zero counts
        if refs:
            totals.update(ref_type for ref_type, _, _ in refs)
            result['flowsWithSourceReferences'] += 1
        if mappable:
            add_issue(result, MAPPABLE, target_id)
        if len(refs) > mappable:
            add_issue(result, UNMAPPABLE, target_id, follow_up=True)
        if lambda_count or lex_count:
            add_issue(result, 'Placeholder', target_id, follow_up=True)
        if voice_id_count:
            add_issue(result, VOICE_ID_END_OF_SUPPORT, target_id, follow_up=True)
        for (ref_type, reason), count in sorted(follow_ups.items()):
            if len(result['manualFollowUp']) < MAX_FOLLOW_UPS:
                result['manualFollowUp'].append(follow_up_entry(kind, target_id, ref_type, reason, count))
            else:
                result['unlistedFollowUps'] += 1
        if refs or lambda_count or lex_count or voice_id_count:
            if len(result['flows']) < MAX_FLOWS:
                result['flows'].append({
                    'id': target_id, 'placeholders': lambda_count + lex_count, 'mappable': mappable,
                    'sourceArnTypes': dict(sorted(Counter(ref_type for ref_type, _, _ in refs).items())),
                    'followUps': dict(sorted(Counter({r: c for (_, r), c in follow_ups.items()}).items()))})
            else:
                result['unlistedFlows'] += 1
    result['sourceArnTypes'] = dict(sorted(totals.items()))
    return result


def classify(result, target_id, refs, mapper):
    """(mappable count, Counter of (ARN type, reason) follow ups); a mapping lookup error fails the flow."""
    mappable, follow_ups = 0, Counter()
    for ref_type, service, arn in refs:
        try:
            # Without a mapper (no mapping information) a source ARN fails closed as mappable.
            reason = mapper.reason(service, arn) if mapper else ('SourceIdOutsideArn' if arn is None else None)
        except Exception as err:
            reraise_if_fatal(err)
            add_issue(result, error_code_of(err), target_id)
            return mappable, follow_ups
        if reason is None:
            mappable += 1
        else:
            follow_ups[(ref_type, reason)] += 1
    return mappable, follow_ups


def pair_loader(budget, source, target, operation, key, id_param):
    """load() for within_budget over (source ID, target ID) pairs: both Describes, source first."""
    def load(pair):
        source_id, target_id = pair
        return (fetch(budget, source, operation, key, InstanceId=source.instance_id, **{id_param: source_id}),
                fetch(budget, target, operation, key, InstanceId=target.instance_id, **{id_param: target_id}))
    return load


def taken_pair(result, target_id, fetched):
    """(source, target) results of a pair_loader load; errors counted in order, source first."""
    return taken(result, target_id, fetched[0]), taken(result, target_id, fetched[1])


def check_queues(source, target, ref, budget, queue_options):
    """Hours, outbound flow and caller ID of each migrated queue."""
    result = new_check('queueConfig', 'queues')
    hours_migrated = queue_options.get('migrateHoursOfOperation', True)
    load = pair_loader(budget, source, target, 'describe_queue', 'Queue', 'QueueId')
    for (source_id, target_id), fetched in within_budget(budget, result, matched_pairs(source, target, 'queues'),
                                                         load):
        src, tgt = taken_pair(result, target_id, fetched)
        if src is None or tgt is None:
            continue
        hours_id = tgt.get('HoursOfOperationId')
        if hours_id not in target.names('hoursOfOperations'):
            add_issue(result, 'HoursNotInTarget', target_id)
        elif hours_migrated and not same_named(source, target, 'hoursOfOperations',
                                               src.get('HoursOfOperationId'), hours_id):
            add_issue(result, 'HoursMismatch', target_id)

        src_out, tgt_out = src.get('OutboundCallerConfig') or {}, tgt.get('OutboundCallerConfig') or {}
        source_flow_id, target_flow_id = src_out.get('OutboundFlowId'), tgt_out.get('OutboundFlowId')
        if target_flow_id and target_flow_id not in target.names('contactFlows'):
            add_issue(result, 'OutboundFlowNotInTarget', target_id)
        elif target_flow_id and source_flow_id and not same_named(source, target, 'contactFlows',
                                                                  source_flow_id, target_flow_id):
            add_issue(result, 'OutboundFlowMismatch', target_id)
        elif source_flow_id and not target_flow_id:
            add_issue(result, 'OutboundFlowNotSet', target_id, follow_up=True)  # listed by CreateQueues

        number_id = tgt_out.get('OutboundCallerIdNumberId')
        if number_id and ref.cross_account_or_region and is_source_number(result, source, target_id, number_id):
            add_issue(result, 'SourceCallerIdNumber', target_id)
        elif src_out.get('OutboundCallerIdNumberId') and not number_id:
            add_issue(result, 'OutboundCallerIdNumberNotSet', target_id, follow_up=True)  # listed by CreateQueues
        if src_out.get('OutboundCallerIdName') != tgt_out.get('OutboundCallerIdName'):
            add_issue(result, 'OutboundCallerIdNameMismatch', target_id)
    return result


def is_source_number(result, source, target_id, number_id):
    try:
        return number_id in source.phone_number_ids()
    except Exception as err:
        reraise_if_fatal(err)
        add_issue(result, error_code_of(err), target_id)
        return False


def check_users(source, target, budget, features, user_options):
    """Routing profile, security profiles and hierarchy group of each migrated user, by name."""
    result = new_check('userConfig', 'users')
    routing_migrated = user_options.get('migrateRoutingProfiles', True)
    security_migrated = user_options.get('migrateSecurityProfiles', True)
    load = pair_loader(budget, source, target, 'describe_user', 'User', 'UserId')
    for (source_id, target_id), fetched in within_budget(budget, result, matched_pairs(source, target, 'users'),
                                                         load):
        src, tgt = taken_pair(result, target_id, fetched)
        if src is None or tgt is None:
            continue
        routing_id = tgt.get('RoutingProfileId')
        if routing_id not in target.names('routingProfiles'):
            add_issue(result, 'RoutingProfileNotInTarget', target_id)
        elif routing_migrated and not same_named(source, target, 'routingProfiles', src.get('RoutingProfileId'),
                                                 routing_id):
            add_issue(result, 'RoutingProfileMismatch', target_id)

        target_profiles = [target.names('securityProfiles').get(i) for i in tgt.get('SecurityProfileIds') or []]
        source_profiles = [source.names('securityProfiles').get(i) for i in src.get('SecurityProfileIds') or []]
        if not target_profiles or None in target_profiles:
            add_issue(result, 'SecurityProfileNotInTarget', target_id)
        elif security_migrated and (None in source_profiles or sorted(source_profiles) != sorted(target_profiles)):
            add_issue(result, 'SecurityProfilesMismatch', target_id)

        group_id = tgt.get('HierarchyGroupId')
        if group_id and group_id not in target.names('hierarchyGroups'):
            add_issue(result, 'HierarchyGroupNotInTarget', target_id)
        elif feature_on(features, 'hierarchyGroups') and not same_named(source, target, 'hierarchyGroups',
                                                                src.get('HierarchyGroupId'), group_id):
            add_issue(result, 'HierarchyGroupMismatch', target_id)
    return result


def check_quick_connects(source, target, budget):
    """Each migrated quick connect references users, queues and flows that exist in the target."""
    result = new_check('quickConnectTargets', 'quickConnects')
    load = pair_loader(budget, source, target, 'describe_quick_connect', 'QuickConnect', 'QuickConnectId')
    for (source_id, target_id), fetched in within_budget(budget, result,
                                                         matched_pairs(source, target, 'quickConnects'), load):
        src, tgt = taken_pair(result, target_id, fetched)
        if src is None or tgt is None:
            continue
        src_config, tgt_config = src.get('QuickConnectConfig') or {}, tgt.get('QuickConnectConfig') or {}
        connect_type = tgt_config.get('QuickConnectType')
        if connect_type != src_config.get('QuickConnectType'):
            add_issue(result, 'TypeMismatch', target_id)
            continue
        if connect_type not in QUICK_CONNECT_REFERENCES:
            add_issue(result, 'UnknownQuickConnectType', target_id)
            continue
        config_key, references = QUICK_CONNECT_REFERENCES[connect_type]
        src_refs, tgt_refs = src_config.get(config_key) or {}, tgt_config.get(config_key) or {}
        for field, kind, missing_code, mismatch_code in references:
            source_ref_id, target_ref_id = src_refs.get(field), tgt_refs.get(field)
            if target_ref_id and target_ref_id not in target.names(kind):
                add_issue(result, missing_code, target_id)
            elif target_ref_id and source_ref_id and not same_named(source, target, kind, source_ref_id,
                                                                    target_ref_id):
                add_issue(result, mismatch_code, target_id)
            elif source_ref_id and not target_ref_id:
                add_issue(result, missing_code, target_id)
    return result


def guarded(check, kind, run):
    """Run one check; a list that cannot be read becomes that check's issue (or follow up) code."""
    try:
        return run()
    except ListUnavailable as err:
        result = new_check(check, kind)
        add_issue(result, err.code, follow_up=err.follow_up)
        return result


def summarize(checks, features):
    failed = [{'originalId': f"{c['check']}:{c['type']}", 'status': 'failed', 'errorCode': code, 'count': count}
              for c in checks for code, count in sorted(c['issues'].items())]
    follow_ups = any(c['followUps'] for c in checks)
    verdict = FAIL if failed else (PASS_WITH_FOLLOW_UPS if follow_ups else PASS)
    # Flow and module follow ups, from the scan of the target content: the migration report lists
    # them on every run, also when the flows already existed and the create step did not touch them.
    manual_follow_up = [entry for c in checks for entry in c.pop('manualFollowUp', [])]
    return {
        'verdict': verdict,
        'checkCount': len(checks),
        'failedCheckCount': sum(1 for c in checks if c['status'] == FAIL),
        'checks': checks,
        'failedChecks': failed,
        'notChecked': {feature: reason for feature, reason in NOT_COMPARED.items() if feature_on(features, feature)},
        'manualFollowUp': manual_follow_up,
    }


def verify(source, target, features, source_ref, budget, options=None, mapper=None):
    """Run every check selected by features; returns the step result (IDs, codes and counts only).

    mapper: the ReferenceMapper for source references in flow content (run_verification builds it
    from arn_mapping.build_rewriter, the flow rewriter's own mapping).
    """
    options = options or {}
    if budget.parallel:
        prefetch(source, target, features, source_ref, budget.workers)
    checks = []
    for feature, kinds in FEATURE_KINDS.items():
        if feature_on(features, feature):
            for kind in kinds:
                checks.append(guarded('names', kind, lambda k=kind: check_names(source, target, k)))
    # Detailed checks in groups. Flows and modules share the reference mapper, so they run in one
    # group, one after the other. The state machine step runs every group in order, as it always did;
    # the UI endpoint runs the groups side by side (each Describe operation has its own rate).
    groups = []
    content = []
    if feature_on(features, 'contactFlows'):
        content.append(lambda: guarded('flowContent', 'contactFlows', lambda: check_content(
            source, target, 'contactFlows', source_ref, budget, mapper)))
    if feature_on(features, 'contactFlowModules'):
        content.append(lambda: guarded('moduleContent', 'contactFlowModules', lambda: check_content(
            source, target, 'contactFlowModules', source_ref, budget, mapper)))
    if content:
        groups.append(content)
    if feature_on(features, 'queues'):
        groups.append([lambda: guarded('queueConfig', 'queues', lambda: check_queues(
            source, target, source_ref, budget, options.get('queueOptions') or {}))])
    if feature_on(features, 'users'):
        groups.append([lambda: guarded('userConfig', 'users', lambda: check_users(
            source, target, budget, features, options.get('userOptions') or {}))])
    if feature_on(features, 'quickConnects'):
        groups.append([lambda: guarded('quickConnectTargets', 'quickConnects',
                                       lambda: check_quick_connects(source, target, budget))])

    def run_group(group):
        return [run() for run in group]

    if budget.parallel and len(groups) > 1:
        results = map_in_order(run_group, groups, len(groups))
    else:
        results = [run_group(group) for group in groups]
    for group_results in results:
        checks.extend(group_results)
    return summarize(checks, features)


def error_result(err):
    """Result for a step that could not run: an error code (never the exception text) and a failed entry."""
    code = (err.code if isinstance(err, MigrationStepError) else None) or error_code_of(err)
    result = {'verdict': ERROR, 'errorCode': code, 'checkCount': 0, 'failedCheckCount': 0, 'checks': [],
              'failedChecks': [{'originalId': 'VerifyTarget', 'status': 'failed', 'errorCode': code}],
              'notChecked': {}, 'manualFollowUp': []}
    if isinstance(err, MigrationStepError) and err.action:
        result['action'] = err.action
    return result


def run_verification(source_arn, target_arn, target_session, features, options, budget):
    """Build both sides and verify. Shared by this step and the UI endpoint (verify_migration)."""
    source_connect, source_id, source_region = create_connect_client_from_arn(source_arn)
    target_connect, target_id, target_region = create_destination_client(target_arn, session=target_session)
    source_qconnect = None
    if any(feature_on(features, feature) for feature in AI_FEATURES):
        source_qconnect = boto3.client('qconnect', region_name=source_region, config=BOTO_CONFIG)
    # Also read for a wisdom ARN left in a flow (NoTargetAssistant); creating the client makes no call.
    target_qconnect = target_session.client('qconnect', region_name=target_region, config=BOTO_CONFIG)
    ref = SourceRef(source_id, extract_account_id_from_arn(source_arn), source_region,
                    extract_account_id_from_arn(target_arn), target_region)
    target = Side(target_connect, target_id, target_arn, target_qconnect)
    mapper = ReferenceMapper(lambda: build_rewriter(source_arn, target_arn, source_connect, target_connect,
                                                    target_session), target)
    return verify(Side(source_connect, source_id, source_arn, source_qconnect, is_source=True), target,
                  features, ref, budget, options, mapper)


def log_summary(result):
    for item in result['checks']:
        check_type = f"{item['check']}:{item['type']}"
        check_status, checked_count = item['status'], item['checked']
        issue_count, follow_up_count = sum(item['issues'].values()), sum(item['followUps'].values())
        print(f"Verify {check_type}: {check_status}, {checked_count} checked, {issue_count} issues, "
              f"{follow_up_count} follow ups")
    verdict_status, failed_count = result['verdict'], len(result['failedChecks'])
    verdict_code = result.get('errorCode') or 'none'
    follow_up_count = len(result.get('manualFollowUp') or [])
    print(f"Verification verdict: {verdict_status}, {failed_count} failed checks, "
          f"{follow_up_count} follow up entries, error code {verdict_code}")


@step_handler
def lambda_handler(event, context):
    try:
        external_id = resolve_external_id(event)  # never logged or returned
        target_arn = event['targetInstanceId']
        target_session = create_destination_session(target_arn, event.get('crossAccountRoleArn'), external_id)
        options = {'queueOptions': event.get('queueOptions') or {}, 'userOptions': event.get('userOptions') or {}}
        result = run_verification(event['sourceInstanceId'], target_arn, target_session,
                                  event.get('features') or {}, options, Budget.for_lambda(context))
    except Exception as err:  # the report and the final status need a result, never a crash
        result = error_result(err)
    log_summary(result)
    return result
