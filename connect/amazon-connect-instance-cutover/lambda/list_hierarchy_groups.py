"""
Lambda function to list user hierarchy groups from source Connect instance.
Uses ListUserHierarchyGroups and DescribeUserHierarchyGroup APIs.
"""
import json
from datetime import datetime
from region_utils import create_connect_client_from_arn
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
    Lists and describes all user hierarchy groups from source Connect instance
    """
    source_instance_arn = event['sourceInstanceId']

    # Create region-specific Connect client for source instance
    print(f"Creating Connect client from source instance ARN: {source_instance_arn}")
    connect_client, source_instance_id, source_region = create_connect_client_from_arn(source_instance_arn)

    try:
        print(f"Listing hierarchy groups from instance: {source_instance_id} in region: {source_region}")

        # First, get the hierarchy structure
        hierarchy_structure = None
        try:
            structure_response = connect_client.describe_user_hierarchy_structure(
                InstanceId=source_instance_id
            )
            hierarchy_structure = structure_response.get('HierarchyStructure')
            print(f"Retrieved hierarchy structure: {redact(hierarchy_structure)}")
        except Exception as e:
            reraise_if_fatal(e)
            print(f"Error getting hierarchy structure: {describe_error(e)}")

        # Get paginator for listing hierarchy groups
        paginator = connect_client.get_paginator('list_user_hierarchy_groups')
        hierarchy_groups = []

        # Iterate through all pages
        for page in paginator.paginate(InstanceId=source_instance_id):
            for group in page['UserHierarchyGroupSummaryList']:
                try:
                    # Get detailed information for each hierarchy group
                    group_detail = connect_client.describe_user_hierarchy_group(
                        InstanceId=source_instance_id,
                        HierarchyGroupId=group['Id']
                    )
                    hierarchy_groups.append(group_detail['HierarchyGroup'])
                    print(f"Retrieved hierarchy group: {redact(group_detail['HierarchyGroup']['Name'])}")

                except Exception as e:
                    reraise_if_fatal(e)
                    print(f"Error describing hierarchy group {group['Id']}: {describe_error(e)}")
                    continue

        # Convert to JSON serializable format
        hierarchy_groups_json = json.loads(json.dumps(hierarchy_groups, default=json_serial))
        hierarchy_structure_json = json.loads(json.dumps(hierarchy_structure, default=json_serial)) if hierarchy_structure else None

        print(f"Successfully retrieved {len(hierarchy_groups_json)} hierarchy groups")

        return {
            'statusCode': 200,
            'hierarchyGroups': hierarchy_groups_json,
            'hierarchyStructure': hierarchy_structure_json
        }

    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error listing hierarchy groups: {describe_error(e)}")
        return {
            'statusCode': 400,
            'error': describe_error(e)
        }
