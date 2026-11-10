import json
from region_utils import create_connect_client_from_arn
from datetime import datetime
from region_utils import reraise_if_fatal
from region_utils import describe_error, redact
from region_utils import step_handler

def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")

@step_handler
def lambda_handler(event, context):
    """
    Lists and describes all agent statuses from source Connect instance
    """
    source_instance_arn = event['sourceInstanceId']  # Now expects ARN
    # Create region-specific Connect client for source instance
    print(f"Creating Connect client from source instance ARN: {source_instance_arn}")
    connect_client, source_instance_id, source_region = create_connect_client_from_arn(source_instance_arn)

    try:
        print(f"Listing agent statuses from instance: {source_instance_id} in region: {source_region}")

        # Get paginator for listing agent statuses
        paginator = connect_client.get_paginator('list_agent_statuses')
        agent_statuses = []

        # Iterate through all pages
        for page in paginator.paginate(InstanceId=source_instance_id):
            for status in page['AgentStatusSummaryList']:
                agent_statuses.append(status)
                print(f"Retrieved agent status: {redact(status['Name'])}")

        # Convert to JSON serializable format
        statuses_json = json.loads(json.dumps(agent_statuses, default=json_serial))

        print(f"Successfully retrieved {len(statuses_json)} agent statuses")

        return {
            'statusCode': 200,
            'agentStatuses': statuses_json
        }

    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error listing agent statuses: {describe_error(e)}")
        return {
            'statusCode': 400,
            'error': describe_error(e)
        }
