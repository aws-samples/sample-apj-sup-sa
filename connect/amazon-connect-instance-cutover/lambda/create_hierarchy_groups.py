"""
Lambda function to create user hierarchy groups in destination Connect instance.
Creates hierarchy structure and groups based on source instance configuration.
Supports cross-account migration with crossAccountRoleArn.
"""
from datetime import datetime
from region_utils import create_destination_client
from region_utils import reraise_if_fatal, error_code_of
from region_utils import describe_error, failure_entry, redact
from region_utils import resolve_external_id
from region_utils import step_handler

def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")

@step_handler
def lambda_handler(event, context):
    """
    Creates user hierarchy groups in destination Connect instance
    Supports cross-account migration
    """
    destination_instance_arn = event['destinationInstanceId']
    hierarchy_groups = event.get('hierarchyGroups', [])
    hierarchy_structure = event.get('hierarchyStructure')
    cross_account_role_arn = event.get('crossAccountRoleArn')  # Optional for cross-account
    external_id = resolve_external_id(event)  # never logged

    # Create region-specific Connect client for destination instance (supports cross-account)
    print(f"Creating Connect client from destination instance ARN: {destination_instance_arn}")
    if cross_account_role_arn:
        print(f"Cross-account migration enabled with role: {cross_account_role_arn}")
    connect_client, destination_instance_id, destination_region = create_destination_client(
        destination_instance_arn, cross_account_role_arn, external_id
    )

    hierarchy_group_mapping = {}
    created_groups = []
    skipped_groups = []
    failed_groups = []

    try:
        print(f"Creating hierarchy groups in instance: {destination_instance_id} in region: {destination_region}")

        # First, ensure the hierarchy structure exists in destination
        # This MUST succeed before we can create groups
        if hierarchy_structure:
            try:
                ensure_hierarchy_structure(connect_client, destination_instance_id, hierarchy_structure)
            except Exception as e:
                reraise_if_fatal(e)
                print(f"Failed to update hierarchy structure: {describe_error(e)}")
                return {
                    'statusCode': 400,
                    'error': describe_error(e),
                    'hierarchyGroupMapping': {}
                }

        # Sort groups by level to create parent groups first
        # Level 1 groups have no parent, Level 2 groups have Level 1 parent, etc.
        sorted_groups = sort_groups_by_level(hierarchy_groups)

        for group in sorted_groups:
            try:
                group_name = group['Name']
                original_group_id = group['Id']
                level_id = group.get('LevelId')

                print(f"Processing hierarchy group: {redact(group_name)} (Level: {level_id})")

                # Check if group already exists
                existing_group_id = find_existing_hierarchy_group(connect_client, destination_instance_id, group_name)

                if existing_group_id:
                    print(f"Hierarchy group '{redact(group_name)}' already exists: {existing_group_id}")
                    hierarchy_group_mapping[original_group_id] = existing_group_id
                    skipped_groups.append({
                        'originalId': original_group_id,
                        'existingId': existing_group_id,
                        'status': 'existing'
                    })
                    continue

                # Prepare create parameters
                create_params = {
                    'InstanceId': destination_instance_id,
                    'Name': group_name
                }

                # Handle parent group mapping
                hierarchy_path = group.get('HierarchyPath', {})
                parent_group_id = get_parent_group_id(hierarchy_path, hierarchy_group_mapping)

                if parent_group_id:
                    create_params['ParentGroupId'] = parent_group_id

                # Add tags if present
                if group.get('Tags'):
                    create_params['Tags'] = group['Tags']

                # Create the hierarchy group
                response = connect_client.create_user_hierarchy_group(**create_params)
                new_group_id = response['HierarchyGroupId']

                hierarchy_group_mapping[original_group_id] = new_group_id
                created_groups.append({
                    'originalId': original_group_id,
                    'newId': new_group_id,
                    'status': 'created'
                })

                print(f"Created hierarchy group: {redact(group_name)} ({original_group_id} -> {new_group_id})")

            except connect_client.exceptions.DuplicateResourceException:
                print(f"Hierarchy group '{redact(group_name)}' already exists (caught exception)")
                existing_id = find_existing_hierarchy_group(connect_client, destination_instance_id, group_name)
                if existing_id:
                    hierarchy_group_mapping[original_group_id] = existing_id
                    skipped_groups.append({
                        'originalId': original_group_id,
                        'newId': existing_id,
                        'status': 'duplicate'
                    })
                else:
                    failed_groups.append(failure_entry(original_group_id, reason='exists but not found by name'))

            except Exception as e:
                reraise_if_fatal(e)
                print(f"Error: {describe_error(e)}")
                failed_groups.append({
                    'originalId': original_group_id,
                    'error': describe_error(e), 'errorCode': error_code_of(e)
                })
                continue

        print(f"Successfully created {len(created_groups)} hierarchy groups")
        print(f"Skipped {len(skipped_groups)} existing hierarchy groups")
        print(f"Failed to create {len(failed_groups)} hierarchy groups")

        return {
            'statusCode': 200,
            'hierarchyGroupMapping': hierarchy_group_mapping,
            'createdGroups': created_groups,
            'skippedGroups': skipped_groups,
            'failedGroups': failed_groups,
            'summary': {
                'totalGroups': len(hierarchy_groups),
                'created': len(created_groups),
                'skipped': len(skipped_groups),
                'failed': len(failed_groups)
            }
        }

    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error creating hierarchy groups: {describe_error(e)}")
        return {
            'statusCode': 400,
            'error': describe_error(e),
            'hierarchyGroupMapping': hierarchy_group_mapping
        }


def ensure_hierarchy_structure(connect_client, instance_id, source_structure):
    """
    Ensure the destination instance has the same hierarchy structure as source.
    Updates the hierarchy structure if needed.
    """
    try:
        # Get current destination structure
        dest_response = connect_client.describe_user_hierarchy_structure(InstanceId=instance_id)
        dest_structure = dest_response.get('HierarchyStructure', {})

        # Build the HierarchyStructure parameter
        hierarchy_structure_param = {}
        needs_update = False

        levels = ['LevelOne', 'LevelTwo', 'LevelThree', 'LevelFour', 'LevelFive']

        for level in levels:
            if source_structure.get(level):
                source_level = source_structure[level]
                dest_level = dest_structure.get(level)

                # If source has this level but destination doesn't, or names differ
                if not dest_level or dest_level.get('Name') != source_level.get('Name'):
                    hierarchy_structure_param[level] = {'Name': source_level['Name']}
                    needs_update = True
                else:
                    # Keep existing level in the structure
                    hierarchy_structure_param[level] = {'Name': dest_level['Name']}

        if needs_update and hierarchy_structure_param:
            print(f"Updating hierarchy structure with: {redact(hierarchy_structure_param)}")
            connect_client.update_user_hierarchy_structure(
                InstanceId=instance_id,
                HierarchyStructure=hierarchy_structure_param
            )
            print("Hierarchy structure updated successfully")
        else:
            print("Hierarchy structure already matches source or no update needed")

    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error updating hierarchy structure: {describe_error(e)}")
        raise


def sort_groups_by_level(hierarchy_groups):
    """
    Sort hierarchy groups by level to ensure parent groups are created first.
    Level 1 groups (no parent) come first, then Level 2, etc.
    """
    def get_level(group):
        # Determine level from HierarchyPath
        hierarchy_path = group.get('HierarchyPath', {})
        level = 0
        for level_key in ['LevelOne', 'LevelTwo', 'LevelThree', 'LevelFour', 'LevelFive']:
            if hierarchy_path.get(level_key):
                level += 1
        return level

    return sorted(hierarchy_groups, key=get_level)


def get_parent_group_id(hierarchy_path, group_mapping):
    """
    Get the mapped parent group ID from the hierarchy path.
    Returns the ID of the immediate parent group.
    """
    # Check levels in reverse order to find the immediate parent
    levels = ['LevelFour', 'LevelThree', 'LevelTwo', 'LevelOne']
    parent_id = None

    for i, level in enumerate(levels):
        level_info = hierarchy_path.get(level)
        if level_info:
            # The next level down (if exists) is the parent
            if i + 1 < len(levels):
                next_level = levels[i + 1]
                next_level_info = hierarchy_path.get(next_level)
                if next_level_info:
                    original_parent_id = next_level_info.get('Id')
                    if original_parent_id and original_parent_id in group_mapping:
                        return group_mapping[original_parent_id]
            break

    return parent_id


def find_existing_hierarchy_group(connect_client, instance_id, group_name):
    """
    Find an existing hierarchy group by name in the destination instance.
    """
    try:
        paginator = connect_client.get_paginator('list_user_hierarchy_groups')

        for page in paginator.paginate(InstanceId=instance_id):
            for group in page['UserHierarchyGroupSummaryList']:
                if group['Name'] == group_name:
                    return group['Id']

        return None

    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error finding existing hierarchy group '{redact(group_name)}': {describe_error(e)}")
        return None
