import json
from region_utils import create_connect_client_from_arn
from datetime import datetime
from region_utils import reraise_if_fatal
from region_utils import describe_error, redact
from migration_filters import migrates_contact_flow
from region_utils import step_handler

def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")

@step_handler
def lambda_handler(event, context):
    """
    Lists all contact flows from source Connect instance
    """
    source_instance_arn = event['sourceInstanceId']  # Now expects ARN
    # Create region-specific Connect client for source instance
    print(f"Creating Connect client from source instance ARN: {source_instance_arn}")
    connect_client, source_instance_id, source_region = create_connect_client_from_arn(source_instance_arn)

    try:
        print(f"Listing contact flows from instance: {source_instance_id} in region: {source_region}")

        # Get paginator for listing contact flows
        paginator = connect_client.get_paginator('list_contact_flows')
        contact_flows = []

        # Iterate through all pages
        for page in paginator.paginate(InstanceId=source_instance_id):
            for flow_summary in page['ContactFlowSummaryList']:
                # Default and sample flows exist in every instance (shared filter, VerifyTarget uses it too).
                if not migrates_contact_flow(flow_summary):
                    print(f"Skipping default or sample flow: {flow_summary['Id']}")
                    continue
                try:
                    # Get detailed contact flow information
                    flow_detail = connect_client.describe_contact_flow(
                        InstanceId=source_instance_id,
                        ContactFlowId=flow_summary['Id']
                    )
                    flow = flow_detail['ContactFlow']
                    flow_name = flow['Name']

                    if migrates_contact_flow(flow):
                        # Only store metadata, not the full Content (to avoid Step Functions data limit)
                        flow_metadata = {
                            'Id': flow['Id'],
                            'Arn': flow['Arn'],
                            'Name': flow['Name'],
                            'Type': flow.get('Type'),
                            'State': flow.get('State'),
                            'Description': flow.get('Description', ''),
                            'Tags': flow.get('Tags', {})
                        }
                        contact_flows.append(flow_metadata)
                        print(f"Retrieved contact flow: {redact(flow_name)}")
                    else:
                        print(f"Skipping AWS managed flow: {redact(flow_name)}")

                except Exception as e:
                    reraise_if_fatal(e)
                    print(f"Error describing contact flow {flow_summary['Id']}: {describe_error(e)}")
                    continue

        # Convert to JSON serializable format
        flows_json = json.loads(json.dumps(contact_flows, default=json_serial))

        print(f"Successfully retrieved {len(flows_json)} contact flows")

        return {
            'statusCode': 200,
            'contactFlows': flows_json
        }

    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error listing contact flows: {describe_error(e)}")
        return {
            'statusCode': 400,
            'error': describe_error(e)
        }
