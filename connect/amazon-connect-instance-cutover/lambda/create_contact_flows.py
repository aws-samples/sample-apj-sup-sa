"""
Create contact flows in the destination Connect instance.

ARNs inside the flow content are rewritten for the target account, Region
and instance by arn_mapping.ArnRewriter. Flows that reference Lambda
functions or Lex bots without a match in the target are still created,
with a clear placeholder ARN, and are listed in manualFollowUp.
"""
from botocore.exceptions import ClientError

from arn_mapping import ExistingIndex, build_rewriter, merge_follow_up
from region_utils import (
    EXISTS_ERROR_CODES,
    create_connect_client_from_arn,
    create_destination_client,
    create_destination_session,
    reraise_if_fatal,
)
from region_utils import describe_error, redact
from region_utils import error_code_of
from region_utils import resolve_external_id
from region_utils import step_handler


@step_handler
def lambda_handler(event, context):
    """
    Create contact flows in destination Connect instance.
    Replaces source ARNs with destination ARNs by looking up resources by name.
    """
    source_instance_arn = event['sourceInstanceId']
    destination_instance_arn = event['destinationInstanceId']
    contact_flows = event.get('contactFlows', [])
    cross_account_role_arn = event.get('crossAccountRoleArn')
    external_id = resolve_external_id(event)  # never logged

    print(f"Source: {source_instance_arn}")
    print(f"Destination: {destination_instance_arn}")
    print(f"Contact flows to process: {len(contact_flows)}")

    dest_session = create_destination_session(destination_instance_arn, cross_account_role_arn, external_id)
    dest_client, dest_instance_id, _ = create_destination_client(destination_instance_arn, session=dest_session)
    source_client, source_instance_id, _ = create_connect_client_from_arn(source_instance_arn)

    rewriter = build_rewriter(source_instance_arn, destination_instance_arn, source_client, dest_client, dest_session)
    # Flows already in the target, listed once before any create.
    existing = ExistingIndex(dest_client, dest_instance_id, 'flows')

    created_flows = []
    skipped_flows = []
    new_flow_mapping = {}
    follow_up = {}

    for flow in contact_flows:
        original_id = flow.get('Id')
        flow_name = flow.get('Name', 'Unknown')
        report = {'rewritten': [], 'placeholders': [], 'unmapped': []}
        try:
            print(f"Processing: {redact(flow_name)}")

            if existing.error is not None:
                skipped_flows.append(existing.lookup_failure(original_id))
                continue
            existing_flow_id = existing.get(flow_name)
            if existing_flow_id:
                print(f"  Already exists: {existing_flow_id}")
                new_flow_mapping[original_id] = existing_flow_id
                skipped_flows.append({'originalId': original_id, 'newId': existing_flow_id,
                                      'status': 'existing'})
                continue

            flow_content = flow.get('Content')
            if not flow_content:
                try:
                    flow_detail = source_client.describe_contact_flow(
                        InstanceId=source_instance_id, ContactFlowId=original_id
                    )
                    flow_content = flow_detail['ContactFlow'].get('Content')
                except ClientError as e:
                    reraise_if_fatal(e, resource=f"contact flow {original_id}")
                    print(f"  Error fetching content: {describe_error(e)}")
                    skipped_flows.append({'originalId': original_id,
                                          'status': 'failed', 'error': describe_error(e), 'errorCode': error_code_of(e)})
                    continue

            if not flow_content:
                skipped_flows.append({'originalId': original_id,
                                      'status': 'failed', 'error': 'No content', 'errorCode': 'NotCreated'})
                continue

            updated_content, report = rewriter.rewrite(flow_content)
            merge_follow_up(follow_up, original_id, report)
            if report['placeholders'] or report['unmapped']:
                print(f"  {len(report['placeholders'])} placeholder(s), {len(report['unmapped'])} unmapped reference(s)")

            create_params = {
                'InstanceId': dest_instance_id,
                'Name': flow_name,
                'Type': flow['Type'],
                'Content': updated_content
            }
            if flow.get('Description'):
                create_params['Description'] = flow['Description']
            if flow.get('Tags'):
                create_params['Tags'] = flow['Tags']

            response = dest_client.create_contact_flow(**create_params)
            new_flow_id = response['ContactFlowId']
            new_flow_mapping[original_id] = new_flow_id
            created_flows.append({
                'originalId': original_id,
                'newId': new_flow_id,
                'status': 'created_with_placeholders' if report['placeholders'] else 'created',
            })
            print(f"  Created: {new_flow_id}")

        except ClientError as e:
            reraise_if_fatal(e, resource=f"contact flow {original_id}")
            code = error_code_of(e)
            if code in EXISTS_ERROR_CODES:
                existing_id, failure = existing.recheck(flow_name, e, original_id)
                if existing_id:
                    new_flow_mapping[original_id] = existing_id
                    skipped_flows.append({'originalId': original_id, 'newId': existing_id,
                                          'status': 'existing'})
                else:
                    skipped_flows.append(failure)
                continue
            print(f"  FAILED: {redact(flow_name)}: {code} - {describe_error(e)}")
            issues = [f"{i['type']}: {i['source']} -> {i['placeholder']}" for i in report['placeholders']]
            issues += [f"{i['type']}: {i['arn']} ({i['reason']})" for i in report['unmapped']]
            skipped_flows.append({
                'originalId': original_id,
                'status': 'failed',
                'error': describe_error(e),
                'issues': issues[:10], 'errorCode': code,
            })
        except Exception as e:
            reraise_if_fatal(e)
            print(f"  Error: {describe_error(e)}")
            skipped_flows.append({'originalId': original_id,
                                  'status': 'failed', 'error': describe_error(e), 'errorCode': error_code_of(e)})

    print(f"Created: {len(created_flows)}, Skipped/Failed: {len(skipped_flows)}, "
          f"Manual follow up: {len(follow_up)}")

    return {
        'statusCode': 200,
        'flowMapping': new_flow_mapping,
        'createdFlows': created_flows,
        'skippedFlows': skipped_flows,
        'manualFollowUp': list(follow_up.values()),
    }
