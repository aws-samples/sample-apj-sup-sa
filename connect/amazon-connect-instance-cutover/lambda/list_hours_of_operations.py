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
    Lists and describes all hours of operations from source Connect instance
    """
    source_instance_arn = event['sourceInstanceId']  # Now expects ARN
    # Create region-specific Connect client for source instance
    print(f"Creating Connect client from source instance ARN: {source_instance_arn}")
    connect_client, source_instance_id, source_region = create_connect_client_from_arn(source_instance_arn)

    try:
        print(f"Listing hours of operations from instance: {source_instance_id} in region: {source_region}")

        # Get paginator for listing hours of operations
        paginator = connect_client.get_paginator('list_hours_of_operations')
        hours_of_operations = []

        # Iterate through all pages
        for page in paginator.paginate(InstanceId=source_instance_id):
            for hours in page['HoursOfOperationSummaryList']:
                try:
                    # Get detailed information for each hours of operation
                    hours_detail = connect_client.describe_hours_of_operation(
                        InstanceId=source_instance_id,
                        HoursOfOperationId=hours['Id']
                    )
                    hours_of_operations.append(hours_detail['HoursOfOperation'])
                    print(f"Retrieved hours of operation: {redact(hours_detail['HoursOfOperation']['Name'])}")

                except Exception as e:
                    reraise_if_fatal(e)
                    print(f"Error describing hours of operation {hours['Id']}: {describe_error(e)}")
                    continue

        # Convert to JSON serializable format
        hours_json = json.loads(json.dumps(hours_of_operations, default=json_serial))

        print(f"Successfully retrieved {len(hours_json)} hours of operations")

        return {
            'statusCode': 200,
            'hoursOfOperations': hours_json
        }

    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error listing hours of operations: {describe_error(e)}")
        return {
            'statusCode': 400,
            'error': describe_error(e)
        }
