import json
from datetime import datetime
from region_utils import create_connect_client_from_arn
from region_utils import reraise_if_fatal
from region_utils import describe_error, redact
from migration_filters import QUEUE_LIST_PARAMS, migrates_queue
from region_utils import step_handler

def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")

@step_handler
def lambda_handler(event, context):
    """
    Lists and describes all queues from source Connect instance
    """
    source_instance_arn = event['sourceInstanceId']  # Now expects ARN

    # Create region-specific Connect client for source instance
    print(f"Creating Connect client from source instance ARN: {source_instance_arn}")
    connect_client, source_instance_id, source_region = create_connect_client_from_arn(source_instance_arn)

    try:
        print(f"Listing queues from instance: {source_instance_id} in region: {source_region}")

        # Only standard queues. Agent queues (QueueType AGENT) belong to users and
        # cannot be described or created, so they are filtered before DescribeQueue.
        paginator = connect_client.get_paginator('list_queues')
        queues = []

        # Iterate through all pages
        for page in paginator.paginate(InstanceId=source_instance_id, **QUEUE_LIST_PARAMS):
            for queue in page['QueueSummaryList']:
                if not migrates_queue(queue):
                    continue
                try:
                    # Get detailed information for each queue
                    queue_detail = connect_client.describe_queue(
                        InstanceId=source_instance_id,
                        QueueId=queue['Id']
                    )
                    queues.append(queue_detail['Queue'])
                    print(f"Retrieved queue: {redact(queue_detail['Queue']['Name'])}")

                except Exception as e:
                    reraise_if_fatal(e)
                    print(f"Error describing queue {queue['Id']}: {describe_error(e)}")
                    continue

        # Convert to JSON serializable format
        queues_json = json.loads(json.dumps(queues, default=json_serial))

        print(f"Successfully retrieved {len(queues_json)} queues")

        return {
            'statusCode': 200,
            'queues': queues_json
        }

    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error listing queues: {describe_error(e)}")
        return {
            'statusCode': 400,
            'error': describe_error(e)
        }
