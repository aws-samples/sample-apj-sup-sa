"""
Lambda function to check contact flow dependencies and identify missing resources.
Analyzes flows from source and compares with destination to find gaps.
Supports pagination to handle large numbers of flows within API Gateway timeout:
a batch stops at FLOWS_PER_BATCH flows or at the time budget, whichever comes
first, and the response says which (summary.status and summary.stopReason).
"""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from botocore.exceptions import ClientError
from arn_mapping import build_rewriter
from migration_filters import QUEUE_LIST_PARAMS
from region_utils import (
    CONNECT_DESCRIBE_RATES,
    THROTTLING_CODES,
    MigrationStepError,
    RateLimiter,
    create_connect_client_from_arn,
    create_destination_client,
    create_destination_session,
    error_code,
    error_code_of,
    extract_account_id_from_arn,
    reraise_if_fatal,
)
from region_utils import describe_error, error_text
from region_utils import external_id_from_api_request

# Flows per batch to stay within API Gateway timeout
FLOWS_PER_BATCH = 50
# Seconds from the start of the request after which no new flow is started. API
# Gateway ends the request at 29 s; in flight Describe calls still finish.
API_TIME_BUDGET_SECONDS = 20
# Concurrent DescribeContactFlow calls on the source instance. The calls are
# paced at the API's default rate (CONNECT_DESCRIBE_RATES), so a few workers
# cover the call latency; more would only queue for the limiter.
FLOW_WORKERS = 4
STOP_TIME_BUDGET = 'TimeBudget'
STOP_BATCH_SIZE = 'BatchSize'
_NOT_STARTED = object()

def lambda_handler(event, context):
    """
    Checks contact flow dependencies and identifies resources that need to be
    migrated or created in the destination before flows can be migrated.
    Supports pagination via 'offset' parameter.
    """
    deadline = time.monotonic() + API_TIME_BUDGET_SECONDS
    headers = {
        'Access-Control-Allow-Origin': os.environ['ALLOWED_ORIGIN'],
        'Access-Control-Allow-Headers': 'Content-Type,Authorization',
        'Access-Control-Allow-Methods': 'POST,OPTIONS'
    }

    if event.get('httpMethod') == 'OPTIONS':
        return {'statusCode': 200, 'headers': headers, 'body': ''}

    try:
        if 'body' in event and event['body']:
            body = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
        else:
            body = event

        source_instance_arn = body.get('sourceInstanceId')
        destination_instance_arn = body.get('destinationInstanceId')
        cross_account_role_arn = body.get('crossAccountRoleArn')
        external_id = external_id_from_api_request(body)  # UI request only, never logged
        offset = body.get('offset', 0)  # Pagination offset

        if not source_instance_arn or not destination_instance_arn:
            return {
                'statusCode': 400,
                'headers': headers,
                'body': json.dumps({'error': 'Missing sourceInstanceId or destinationInstanceId'})
            }

        print(f"Source: {source_instance_arn}, Offset: {offset}")
        print(f"Destination: {destination_instance_arn}")

        source_client, source_instance_id, source_region = create_connect_client_from_arn(source_instance_arn)
        dest_session = create_destination_session(
            destination_instance_arn, cross_account_role_arn, external_id
        )
        dest_client, dest_instance_id, dest_region = create_destination_client(
            destination_instance_arn, session=dest_session
        )
        # Same Lambda and Lex mapping the migration itself uses, so the pre check
        # predicts exactly which references will become placeholders.
        rewriter = build_rewriter(source_instance_arn, destination_instance_arn,
                                  source_client, dest_client, dest_session, id_map_kinds=[])

        source_account = extract_account_id_from_arn(source_instance_arn)
        dest_account = extract_account_id_from_arn(destination_instance_arn)
        is_cross_account = source_account != dest_account
        is_cross_region = source_region != dest_region

        print(f"Cross-account: {is_cross_account}, Cross-region: {is_cross_region}")

        # Get resources from both instances in parallel
        print("Fetching resources...")
        with ThreadPoolExecutor(max_workers=2) as executor:
            src_future = executor.submit(get_instance_resources, source_client, source_instance_id)
            dst_future = executor.submit(get_instance_resources, dest_client, dest_instance_id)
            source_resources = src_future.result()
            dest_resources = dst_future.result()

        # Get all contact flows from source
        print("Listing contact flows...")
        all_flows = []
        paginator = source_client.get_paginator('list_contact_flows')
        for page in paginator.paginate(InstanceId=source_instance_id):
            all_flows.extend(page.get('ContactFlowSummaryList', []))

        total_flows = len(all_flows)

        # Get the batch of flows to analyze based on offset
        start_idx = offset
        end_idx = min(offset + FLOWS_PER_BATCH, total_flows)
        flows_to_analyze = all_flows[start_idx:end_idx]

        print(f"Analyzing flows {start_idx} to {end_idx} of {total_flows}...")

        flow_analysis = []
        missing_summary = {
            'queues': [], 'prompts': [], 'hours': [], 'users': [],
            'lambdas': [], 'lexBots': [], 'flows': [], 'features': []
        }
        seen = {k: set() for k in missing_summary}

        def analyze_single_flow(flow_summary):
            flow_id = flow_summary['Id']
            flow_name = flow_summary['Name']
            flow_type = flow_summary.get('ContactFlowType', 'UNKNOWN')

            try:
                flow_detail = source_client.describe_contact_flow(
                    InstanceId=source_instance_id, ContactFlowId=flow_id
                )
                content = flow_detail['ContactFlow'].get('Content', '{}')
            except ClientError as err:
                reraise_if_fatal(err, resource=f"contact flow {flow_id}")
                # Not found or similar: report it instead of silently dropping the flow.
                return {
                    'name': flow_name, 'type': flow_type,
                    'existsInDestination': flow_name in dest_resources['flows_by_name'],
                    'canMigrate': False,
                    'issues': [{'category': 'flows', 'name': flow_name,
                                'reason': f"Could not read flow content: {error_code(err)}"}],
                }

            issues = analyze_flow_dependencies(rewriter, content)

            exists_in_dest = flow_name in dest_resources['flows_by_name']

            return {
                'name': flow_name, 'type': flow_type,
                'existsInDestination': exists_in_dest,
                'canMigrate': len(issues) == 0,
                'issues': issues
            }

        # Bounded parallel analysis, in flow order, paced at the DescribeContactFlow
        # default rate. Flows not started before the deadline are left for the next
        # request (the UI pages on nextOffset).
        limiter = RateLimiter(CONNECT_DESCRIBE_RATES['describe_contact_flow'])
        analyzed = analyze_within_budget(flows_to_analyze, analyze_single_flow, deadline, pace=limiter.wait)
        for result in analyzed:
            flow_analysis.append(result)
            if not result['canMigrate']:
                for issue in result['issues']:
                    cat = issue.get('category', 'other')
                    name = issue.get('name', '')
                    if cat in seen and name not in seen[cat]:
                        seen[cat].add(name)
                        missing_summary[cat].append({'name': name, 'reason': issue.get('reason', '')})

        stopped_at_budget = len(analyzed) < len(flows_to_analyze)
        end_idx = start_idx + len(analyzed)
        can_migrate = sum(1 for f in flow_analysis if f['canMigrate'])
        has_more = end_idx < total_flows
        if stopped_at_budget:
            print(f"Time budget reached: analyzed {len(analyzed)} of {len(flows_to_analyze)} flows in this batch")

        result = {
            'statusCode': 200,
            'summary': {
                'totalFlows': total_flows,
                'analyzedFlows': len(flow_analysis),
                'batchStart': start_idx,
                'batchEnd': end_idx,
                'canMigrate': can_migrate,
                'hasIssues': len(flow_analysis) - can_migrate,
                'isCrossAccount': is_cross_account,
                'isCrossRegion': is_cross_region,
                'hasMore': has_more,
                'nextOffset': end_idx if has_more else None,
                # COMPLETE: every flow up to totalFlows is analyzed. PARTIAL: call again with nextOffset.
                'status': 'PARTIAL' if has_more else 'COMPLETE',
                'stopReason': STOP_TIME_BUDGET if stopped_at_budget else (STOP_BATCH_SIZE if has_more else None),
            },
            'missingResources': missing_summary,
            'flowAnalysis': flow_analysis,
            'warnings': source_resources['warnings'] + dest_resources['warnings']
        }

        if 'httpMethod' in event:
            return {
                'statusCode': 200,
                'headers': headers,
                'body': json.dumps(result)
            }
        return result

    except MigrationStepError as e:
        # AccessDenied, throttling or missing role/ExternalId: fail the pre check loudly.
        status = 429 if e.code in THROTTLING_CODES else 400 if e.code and e.code.startswith(('Missing', 'Invalid')) else 403
        error_response = {'error': error_text(e), 'code': e.code, 'action': e.action}
        if 'httpMethod' in event:
            return {'statusCode': status, 'headers': headers, 'body': json.dumps(error_response)}
        raise
    except (ClientError, ValueError) as e:
        print(f"Error: {describe_error(e)}")
        error_response = {'error': error_text(e)}
        if 'httpMethod' in event:
            return {
                'statusCode': 500,
                'headers': headers,
                'body': json.dumps(error_response)
            }
        raise
    except Exception as e:
        # Any other failure: an error code only (never the exception text), not an
        # unhandled crash that API Gateway turns into a bare 502.
        print(f"Error: {describe_error(e)}")
        if 'httpMethod' in event:
            return {'statusCode': 500, 'headers': headers, 'body': json.dumps({'error': error_code_of(e)})}
        raise


def analyze_within_budget(flows, analyze, deadline, workers=None, pace=None):
    """Analyze flows with bounded concurrency until the deadline; returns the analyzed prefix, in order.

    The first flow is always analyzed, so every request makes progress. A flow
    whose turn comes at or after the deadline is not started, also when its
    paced slot (pace: a RateLimiter.wait) only comes after the deadline. The
    analyzed flows are returned up to the first one not started, so nextOffset
    always resumes right after them (a later flow analyzed out of order is
    simply analyzed again by the next request).
    Fatal errors (AccessDenied, throttling) propagate from the first failed flow.
    """
    workers = workers or FLOW_WORKERS

    def run(indexed):
        index, flow = indexed
        if index > 0 and time.monotonic() >= deadline:
            return _NOT_STARTED
        if pace:
            pace()
            if index > 0 and time.monotonic() >= deadline:
                return _NOT_STARTED
        return analyze(flow)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(run, enumerate(flows)))
    analyzed = []
    for result in results:
        if result is _NOT_STARTED:
            break
        analyzed.append(result)
    return analyzed


def _list_bots(client, instance_id, lex_version):
    """Yield every bot association of one Lex version (ListBots has no paginator)."""
    params = {'InstanceId': instance_id, 'LexVersion': lex_version, 'MaxResults': 25}
    while True:
        response = client.list_bots(**params)
        yield from response.get('LexBots', [])
        if not response.get('NextToken'):
            return
        params['NextToken'] = response['NextToken']


def get_instance_resources(client, instance_id):
    """List the resources a flow can depend on.

    Any AccessDenied or throttling error fails the pre check (a partial
    inventory would report false "missing" or "ok" results). Only Lex V2
    bots are listed: Lex V1 (Lex Classic) is not supported.
    """
    resources = {
        'queues_by_name': {}, 'queues_by_id': {},
        'prompts_by_name': {}, 'prompts_by_id': {},
        'hours_by_name': {}, 'hours_by_id': {},
        'users_by_name': {}, 'users_by_id': {},
        'flows_by_name': {}, 'flows_by_id': {},
        'lambdas': set(), 'lex_v2_bots': set(),
        'warnings': [],
    }
    listings = [
        ('list_queues', 'QueueSummaryList', 'Name', 'queues', QUEUE_LIST_PARAMS),
        ('list_prompts', 'PromptSummaryList', 'Name', 'prompts', {}),
        ('list_hours_of_operations', 'HoursOfOperationSummaryList', 'Name', 'hours', {}),
        ('list_users', 'UserSummaryList', 'Username', 'users', {}),
        ('list_contact_flows', 'ContactFlowSummaryList', 'Name', 'flows', {}),
    ]
    for operation, list_key, name_key, prefix, params in listings:
        try:
            for page in client.get_paginator(operation).paginate(InstanceId=instance_id, **params):
                for item in page.get(list_key, []):
                    if item.get(name_key):
                        resources[f'{prefix}_by_name'][item[name_key]] = item['Id']
                        resources[f'{prefix}_by_id'][item['Id']] = item[name_key]
        except ClientError as err:
            reraise_if_fatal(err, resource=f"instance {instance_id}")
            raise

    try:
        for page in client.get_paginator('list_lambda_functions').paginate(InstanceId=instance_id):
            resources['lambdas'].update(page.get('LambdaFunctions', []))
    except ClientError as err:
        reraise_if_fatal(err, resource=f"instance {instance_id}")
        raise

    try:
        for bot in _list_bots(client, instance_id, 'V2'):
            if 'LexV2Bot' in bot:
                resources['lex_v2_bots'].add(bot['LexV2Bot'].get('AliasArn', ''))
    except ClientError as err:
        reraise_if_fatal(err, resource=f"instance {instance_id}")
        raise

    return resources


def analyze_flow_dependencies(rewriter, content):
    """Predict which Lambda and Lex references the migration cannot map.

    Other Connect resources are migrated by the tool itself, so only Lambda
    and Lex (and dynamic Lambda references) are reported here. The result
    matches what create_contact_flows will write: mapped references are
    rewritten, everything else becomes a placeholder listed for follow up.
    """
    _, report = rewriter.rewrite(content)
    issues = []
    for item in report['placeholders']:
        # Placeholder entries carry the source ARN, not a name (names stay out of step output).
        category = 'lambdas' if item['type'] == 'lambda' else 'lexBots'
        issues.append({'category': category, 'name': item['source'],
                       'reason': f"No match in the target. {item['action']}"})
    for item in report['unmapped']:
        if item['type'] == 'lex':
            issues.append({'category': 'lexBots', 'name': item['arn'], 'reason': item['reason']})
        elif item['type'] in ('lambda', 'lambdaDynamic'):
            issues.append({'category': 'lambdas', 'name': item['arn'], 'reason': item['reason']})
    return issues
