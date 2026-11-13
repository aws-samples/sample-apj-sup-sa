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
    Lists all contact flow modules from source Connect instance
    """
    source_instance_arn = event['sourceInstanceId']  # Now expects ARN
    # Create region-specific Connect client for source instance
    print(f"Creating Connect client from source instance ARN: {source_instance_arn}")
    connect_client, source_instance_id, source_region = create_connect_client_from_arn(source_instance_arn)

    try:
        print(f"Listing contact flow modules from instance: {source_instance_id} in region: {source_region}")

        # Get paginator for listing contact flow modules
        paginator = connect_client.get_paginator('list_contact_flow_modules')
        contact_flow_modules = []

        # Iterate through all pages
        for page in paginator.paginate(InstanceId=source_instance_id):
            for module_summary in page['ContactFlowModulesSummaryList']:
                try:
                    # Get detailed contact flow module information
                    module_detail = connect_client.describe_contact_flow_module(
                        InstanceId=source_instance_id,
                        ContactFlowModuleId=module_summary['Id']
                    )

                    module = module_detail['ContactFlowModule']
                    # Only store metadata, not the full Content (to avoid Step Functions data limit)
                    module_metadata = {
                        'Id': module['Id'],
                        'Arn': module['Arn'],
                        'Name': module['Name'],
                        'State': module.get('State'),
                        'Status': module.get('Status'),
                        'Description': module.get('Description', ''),
                        'Tags': module.get('Tags', {})
                    }
                    contact_flow_modules.append(module_metadata)
                    print(f"Retrieved contact flow module: {redact(module['Name'])}")

                except Exception as e:
                    reraise_if_fatal(e)
                    print(f"Error describing contact flow module {module_summary['Id']}: {describe_error(e)}")
                    continue

        # Convert to JSON serializable format
        modules_json = json.loads(json.dumps(contact_flow_modules, default=json_serial))

        print(f"Successfully retrieved {len(modules_json)} contact flow modules")

        return {
            'statusCode': 200,
            'contactFlowModules': modules_json
        }

    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error listing contact flow modules: {describe_error(e)}")
        return {
            'statusCode': 400,
            'error': describe_error(e)
        }
