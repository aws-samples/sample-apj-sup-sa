"""
Check Amazon Connect service quotas for the source and destination instances.

Deployed by cloudformation/3-lambda-functions.yaml and exposed as
POST /check-quotas. Source quotas and counts use the tool role in the source
Region. Destination quotas and counts use the target writer role (AssumeRole
with ExternalId) in the destination Region, so the numbers describe the
target account and not the tool account.

Every quota checked here is a per instance Amazon Connect quota (Service
Quotas context scope AWS::Connect::Instance). Values are looked up in this
order, and the result says which one was used in sourceQuotaSource and
destinationQuotaSource:

1. 'applied-instance': GetServiceQuota with ContextId set to the instance ARN
   (an increase granted for that instance);
2. 'applied-account': GetServiceQuota without ContextId (the applied value
   for the account and Region);
3. 'aws-default': only when GetServiceQuota returns NoSuchResourceException
   (no applied value exists), GetAWSDefaultServiceQuota with the same code.

There is no built in fallback number. If even the default is unknown the
quota is reported with status 'unknown' instead of a guessed value.

Most quotas count items (target count plus source count against the target
quota). Concurrent active calls per instance is a capacity quota with nothing
to copy: it is checked on every run and warns when the target quota is lower
than the source quota, because the target carries the source's traffic after
the cutover. Amazon Lex bots per instance counts Lex V2 bot associations
(ListBots with LexVersion V2). Only Agent status per instance is not
adjustable in Service Quotas.
"""
import json
import os

import boto3
from botocore.exceptions import ClientError

from region_utils import (
    BOTO_CONFIG,
    THROTTLING_CODES,
    MigrationStepError,
    create_connect_client_from_arn,
    create_destination_session,
    error_code,
    extract_instance_id_from_arn,
    extract_region_from_arn,
    reraise_if_fatal,
)
from region_utils import describe_error, error_text
from region_utils import external_id_from_api_request

# Real Amazon Connect quota codes (service code 'connect'), matched by QuotaName to
# list-aws-default-service-quotas output for two Regions on 2026-11-08.
# Codes, names and defaults were identical in both Regions. 'name' is the Service Quotas
# QuotaName.
CONNECT_QUOTAS = {
    'users': {'quota_code': 'L-9A46857E', 'name': 'Users per instance', 'adjustable': True},
    'queues': {'quota_code': 'L-19A87C94', 'name': 'Queues per instance', 'adjustable': True},
    'quickConnects': {'quota_code': 'L-68BBE2E8', 'name': 'Quick connects per instance', 'adjustable': True},
    'contactFlows': {'quota_code': 'L-22922690', 'name': 'Contact flows per instance', 'adjustable': True},
    'contactFlowModules': {'quota_code': 'L-19755C7E', 'name': 'Flow modules per instance', 'adjustable': True},
    'hoursOfOperation': {'quota_code': 'L-20CD02F7', 'name': 'Hours of operation per instance', 'adjustable': True},
    'routingProfiles': {'quota_code': 'L-D3E7BE26', 'name': 'Routing profiles per instance', 'adjustable': True},
    'securityProfiles': {'quota_code': 'L-F325A715', 'name': 'Security profiles per instance', 'adjustable': True},
    'phoneNumbers': {'quota_code': 'L-8F812903', 'name': 'Phone numbers per instance', 'adjustable': True},
    'prompts': {'quota_code': 'L-0865B754', 'name': 'Prompts per instance', 'adjustable': True},
    'agentStatus': {'quota_code': 'L-D945C9A8', 'name': 'Agent status per instance', 'adjustable': False},
    # Service Quotas names this one "Predefined Attributes"; its description says per instance.
    'predefinedAttributes': {'quota_code': 'L-3828FBF0', 'name': 'Predefined Attributes', 'adjustable': True},
    # Capacity, not a resource count: the target has to carry the source's traffic after the cutover.
    'concurrentActiveCalls': {'quota_code': 'L-12AB7C57', 'name': 'Concurrent active calls per instance',
                              'adjustable': True},
    'lexBots': {'quota_code': 'L-B93A6612', 'name': 'Amazon Lex bots per instance', 'adjustable': True},
}

# (operation, list key, extra list params); None for a capacity quota with nothing to count.
COUNTERS = {
    'users': ('list_users', 'UserSummaryList', {}),
    'queues': ('list_queues', 'QueueSummaryList', {}),
    'quickConnects': ('list_quick_connects', 'QuickConnectSummaryList', {}),
    'contactFlows': ('list_contact_flows', 'ContactFlowSummaryList', {}),
    'contactFlowModules': ('list_contact_flow_modules', 'ContactFlowModulesSummaryList', {}),
    'hoursOfOperation': ('list_hours_of_operations', 'HoursOfOperationSummaryList', {}),
    'routingProfiles': ('list_routing_profiles', 'RoutingProfileSummaryList', {}),
    'securityProfiles': ('list_security_profiles', 'SecurityProfileSummaryList', {}),
    'phoneNumbers': ('list_phone_numbers_v2', 'ListPhoneNumbersSummaryList', {}),
    'prompts': ('list_prompts', 'PromptSummaryList', {}),
    'agentStatus': ('list_agent_statuses', 'AgentStatusSummaryList', {}),
    'predefinedAttributes': ('list_predefined_attributes', 'PredefinedAttributeSummaryList', {}),
    # Lex V2 bot associations only (Lex V1 is not supported).
    'lexBots': ('list_bots', 'LexBots', {'LexVersion': 'V2'}),
    'concurrentActiveCalls': None,
}
CAPACITY_QUOTAS = frozenset(key for key, counter in COUNTERS.items() if counter is None)
# Checked on every run, whatever is selected: a cutover moves the source's traffic to the target.
ALWAYS_CHECKED = frozenset({'concurrentActiveCalls'})

FEATURE_TO_QUOTAS = {
    'users': ['users', 'routingProfiles', 'securityProfiles'],
    'queues': ['queues', 'hoursOfOperation'],
    'quickConnects': ['quickConnects'],
    'contactFlows': ['contactFlows'],
    'contactFlowModules': ['contactFlowModules'],
    'agentStatus': ['agentStatus'],
    'predefinedAttributes': ['predefinedAttributes'],
    'prompts': ['prompts'],
    'phoneNumbers': ['phoneNumbers'],
    'lexBots': ['lexBots'],
}


def cors_headers():
    return {
        'Access-Control-Allow-Origin': os.environ['ALLOWED_ORIGIN'],
        'Access-Control-Allow-Headers': 'Content-Type,Authorization',
        'Access-Control-Allow-Methods': 'POST,OPTIONS',
    }


def _no_such_resource(err, quota_code):
    """True for NoSuchResourceException; fatal errors raise, anything else re raises."""
    reraise_if_fatal(err, resource=f"connect/{quota_code}")
    return error_code(err) == 'NoSuchResourceException'


def get_quota_value(sq_client, quota_code, instance_arn):
    """Return (value, source) for one per instance quota; (None, 'unknown') if no value exists.

    AccessDenied and throttling fail the request.
    """
    for kwargs, source in (({'ContextId': instance_arn}, 'applied-instance'), ({}, 'applied-account')):
        try:
            quota = sq_client.get_service_quota(ServiceCode='connect', QuotaCode=quota_code, **kwargs)
            return quota['Quota']['Value'], source
        except ClientError as err:
            if not _no_such_resource(err, quota_code):
                raise
    try:
        quota = sq_client.get_aws_default_service_quota(ServiceCode='connect', QuotaCode=quota_code)
        return quota['Quota']['Value'], 'aws-default'
    except ClientError as err:
        if not _no_such_resource(err, quota_code):
            raise
    print(f"No applied or default value for connect/{quota_code}")
    return None, 'unknown'


def get_resource_count(connect_client, instance_id, resource_type):
    """Number of items counted against the quota, or None for a capacity quota (nothing to count)."""
    if COUNTERS[resource_type] is None:
        return None
    operation, list_key, extra = COUNTERS[resource_type]
    try:
        pages = connect_client.get_paginator(operation).paginate(InstanceId=instance_id, **extra)
        return sum(len(page.get(list_key, [])) for page in pages)
    except ClientError as err:
        reraise_if_fatal(err, resource=f"instance {instance_id}")
        raise


def quota_row(quota_key, info, source, destination):
    """(result row, warning or None). source and destination: (quota, quota source, count).

    A count quota fails when the target count plus the source count exceeds the target quota. A
    capacity quota (concurrent active calls) has nothing to copy: it fails when the target quota is
    lower than the source quota, because the target carries the source's traffic after the cutover.
    """
    (source_quota, source_quota_source, source_count) = source
    (dest_quota, dest_quota_source, dest_count) = destination
    capacity = quota_key in CAPACITY_QUOTAS
    if capacity:
        projected = None
        would_exceed = dest_quota is not None and source_quota is not None and dest_quota < source_quota
        needed = source_quota
    else:
        projected = dest_count + source_count
        would_exceed = dest_quota is not None and projected > dest_quota
        needed = projected
    row = {
        'resource': info['name'], 'quotaCode': info['quota_code'], 'adjustable': info['adjustable'],
        'kind': 'capacity' if capacity else 'count',
        'sourceQuota': None if source_quota is None else int(source_quota),
        'sourceQuotaSource': source_quota_source,
        'sourceCount': source_count,
        'destinationQuota': None if dest_quota is None else int(dest_quota),
        'destinationQuotaSource': dest_quota_source,
        'destinationCount': dest_count,
        'projectedCount': projected, 'wouldExceed': would_exceed,
        'status': 'unknown' if dest_quota is None else 'warning' if would_exceed else 'ok',
    }
    if not would_exceed:
        return row, None
    shortage = needed - dest_quota
    action = ("Request a quota increase for the target instance before migration."
              if info['adjustable'] else
              "This quota cannot be increased; migrate fewer items or remove unused ones in the target.")
    if capacity:
        message = (f"Destination quota {int(dest_quota)} ({dest_quota_source}) is lower than the source quota "
                   f"{int(source_quota)} ({source_quota_source}). After the cutover the target carries the "
                   f"source's traffic. {action}")
    else:
        message = (f"Migration would exceed destination quota by {int(shortage)}. "
                   f"Current: {dest_count}, Adding: {source_count}, Quota: {int(dest_quota)} "
                   f"({dest_quota_source}). {action}")
    warning = {
        'resource': info['name'], 'message': message, 'requiredIncrease': shortage,
        'recommendedQuota': (int(source_quota) if capacity else projected + 10) if info['adjustable'] else None,
    }
    return row, warning


def lambda_handler(event, context):
    headers = cors_headers()
    if event.get('httpMethod') == 'OPTIONS':
        return {'statusCode': 200, 'headers': headers, 'body': ''}

    body = event
    if 'body' in event:
        try:
            body = json.loads(event['body']) if event['body'] else {}
        except (json.JSONDecodeError, TypeError):
            body = {}

    source_instance_arn = (body.get('sourceInstanceId') or '').strip()
    dest_instance_arn = (body.get('destinationInstanceId') or '').strip()
    selected_features = body.get('features', {}) or {}
    cross_account_role_arn = body.get('crossAccountRoleArn')
    external_id = external_id_from_api_request(body)  # UI request only, never logged

    if not source_instance_arn or not dest_instance_arn:
        return {'statusCode': 400, 'headers': headers,
                'body': json.dumps({'error': 'Both source and destination instance ARNs are required'})}

    try:
        source_region = extract_region_from_arn(source_instance_arn)
        dest_region = extract_region_from_arn(dest_instance_arn)
        dest_instance_id = extract_instance_id_from_arn(dest_instance_arn)

        source_sq = boto3.client('service-quotas', region_name=source_region, config=BOTO_CONFIG)
        source_connect, source_instance_id, _ = create_connect_client_from_arn(source_instance_arn)

        dest_session = create_destination_session(dest_instance_arn, cross_account_role_arn, external_id)
        dest_sq = dest_session.client('service-quotas', region_name=dest_region, config=BOTO_CONFIG)
        dest_connect = dest_session.client('connect', region_name=dest_region, config=BOTO_CONFIG)

        quotas_to_check = set()
        for feature, enabled in selected_features.items():
            if enabled and feature in FEATURE_TO_QUOTAS:
                quotas_to_check.update(FEATURE_TO_QUOTAS[feature])
        if not quotas_to_check:
            quotas_to_check = set(CONNECT_QUOTAS)
        quotas_to_check |= ALWAYS_CHECKED

        results, warnings, defaults_used = [], [], []
        for quota_key in sorted(quotas_to_check):
            info = CONNECT_QUOTAS[quota_key]
            source_quota, source_quota_source = get_quota_value(source_sq, info['quota_code'], source_instance_arn)
            dest_quota, dest_quota_source = get_quota_value(dest_sq, info['quota_code'], dest_instance_arn)
            source_count = get_resource_count(source_connect, source_instance_id, quota_key)
            dest_count = get_resource_count(dest_connect, dest_instance_id, quota_key)
            for side, value_source in (('source', source_quota_source), ('destination', dest_quota_source)):
                if value_source == 'aws-default':
                    defaults_used.append({'resource': info['name'], 'quotaCode': info['quota_code'], 'side': side})
            row, warning = quota_row(quota_key, info, (source_quota, source_quota_source, source_count),
                                     (dest_quota, dest_quota_source, dest_count))
            results.append(row)
            if warning:
                warnings.append(warning)

        return {'statusCode': 200, 'headers': headers, 'body': json.dumps({
            'quotas': results, 'warnings': warnings, 'hasWarnings': bool(warnings),
            'sourceRegion': source_region, 'destinationRegion': dest_region,
            'summary': {'totalChecked': len(results), 'totalWarnings': len(warnings),
                        'canProceed': not warnings,
                        'appliedValues': sum(r[f'{s}QuotaSource'].startswith('applied')
                                             for r in results for s in ('source', 'destination')),
                        'defaultValues': len(defaults_used),
                        'unknownValues': sum(r[f'{s}QuotaSource'] == 'unknown'
                                             for r in results for s in ('source', 'destination'))},
            'defaultsUsed': defaults_used,
        })}

    except MigrationStepError as e:
        status = 429 if e.code in THROTTLING_CODES else 400 if e.code and e.code.startswith(('Missing', 'Invalid')) else 403
        return {'statusCode': status, 'headers': headers,
                'body': json.dumps({'error': error_text(e), 'code': e.code, 'action': e.action})}
    except (ClientError, ValueError) as e:
        print(f"Error checking quotas: {describe_error(e)}")
        return {'statusCode': 500, 'headers': headers, 'body': json.dumps({'error': error_text(e)})}
