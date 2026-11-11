import json
from region_utils import create_connect_client_from_arn
from datetime import datetime
from region_utils import reraise_if_fatal
from region_utils import describe_error, redact
from region_utils import error_code_of, feature_skipped_result, is_feature_unavailable
from migration_filters import migrates_predefined_attribute
from region_utils import step_handler

def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")

@step_handler
def lambda_handler(event, context):
    """
    Lists all predefined attributes from source Connect instance
    """
    source_instance_arn = event['sourceInstanceId']  # Now expects ARN
    # Create region-specific Connect client for source instance
    print(f"Creating Connect client from source instance ARN: {source_instance_arn}")
    connect_client, source_instance_id, source_region = create_connect_client_from_arn(source_instance_arn)

    try:
        print(f"Listing predefined attributes from instance: {source_instance_id} in region: {source_region}")

        # Get predefined attributes from source
        response = connect_client.list_predefined_attributes(InstanceId=source_instance_id)
        attributes = response['PredefinedAttributeSummaryList']

        # Handle pagination if needed
        while 'NextToken' in response:
            response = connect_client.list_predefined_attributes(
                InstanceId=source_instance_id,
                NextToken=response['NextToken']
            )
            attributes.extend(response['PredefinedAttributeSummaryList'])

        predefined_attributes = []
        for attr in attributes:
            # Connect owned attributes (shared filter, VerifyTarget uses it too)
            if not migrates_predefined_attribute(attr):
                print(f"Skipping predefined attribute: {redact(attr['Name'])}")
                continue

            try:
                # Get full attribute details
                full_attr = connect_client.describe_predefined_attribute(
                    InstanceId=source_instance_id,
                    Name=attr['Name']
                )
                predefined_attributes.append(full_attr['PredefinedAttribute'])
                print(f"Retrieved predefined attribute: {redact(attr['Name'])}")

            except Exception as e:
                reraise_if_fatal(e)
                print(f"Error describing predefined attribute {redact(attr['Name'])}: {describe_error(e)}")
                continue

        # Convert to JSON serializable format
        attributes_json = json.loads(json.dumps(predefined_attributes, default=json_serial))

        print(f"Successfully retrieved {len(attributes_json)} predefined attributes")

        return {
            'statusCode': 200,
            'predefinedAttributes': attributes_json
        }

    except Exception as e:
        reraise_if_fatal(e)
        if is_feature_unavailable(e):
            return feature_skipped_result('predefinedAttributes', source_region, 'source', error_code_of(e),
                                          predefinedAttributes=[])
        print(f"Error listing predefined attributes: {describe_error(e)}")
        return {
            'statusCode': 400,
            'error': describe_error(e)
        }
