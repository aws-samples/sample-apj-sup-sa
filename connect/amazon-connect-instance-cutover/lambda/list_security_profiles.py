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
    Lists and describes all security profiles from source Connect instance
    """
    source_instance_arn = event['sourceInstanceId']  # Now expects ARN
    # Create region-specific Connect client for source instance
    print(f"Creating Connect client from source instance ARN: {source_instance_arn}")
    connect_client, source_instance_id, source_region = create_connect_client_from_arn(source_instance_arn)

    try:
        print(f"Listing security profiles from instance: {source_instance_id} in region: {source_region}")

        # Get paginator for listing security profiles
        paginator = connect_client.get_paginator('list_security_profiles')
        security_profiles = []

        # Iterate through all pages
        for page in paginator.paginate(InstanceId=source_instance_id):
            for profile in page['SecurityProfileSummaryList']:
                try:
                    # Get detailed information for each security profile
                    profile_detail = connect_client.describe_security_profile(
                        InstanceId=source_instance_id,
                        SecurityProfileId=profile['Id']
                    )
                    profile_data = profile_detail['SecurityProfile']

                    # Ensure SecurityProfileId is set (describe returns 'Id')
                    if 'Id' in profile_data and 'SecurityProfileId' not in profile_data:
                        profile_data['SecurityProfileId'] = profile_data['Id']

                    # Get permissions separately (describe_security_profile doesn't return them)
                    try:
                        permissions_response = connect_client.list_security_profile_permissions(
                            InstanceId=source_instance_id,
                            SecurityProfileId=profile['Id'],
                            MaxResults=100
                        )
                        profile_data['Permissions'] = permissions_response.get('Permissions', [])
                        print(f"Retrieved {len(profile_data['Permissions'])} permissions for {redact(profile_data['SecurityProfileName'])}")
                    except Exception as perm_error:
                        reraise_if_fatal(perm_error)
                        print(f"Error getting permissions for {profile['Id']}: {describe_error(perm_error)}")
                        profile_data['Permissions'] = []

                    security_profiles.append(profile_data)
                    print(f"Retrieved security profile: {redact(profile_data['SecurityProfileName'])} (ID: {profile_data.get('SecurityProfileId', profile_data.get('Id'))})")

                except Exception as e:
                    reraise_if_fatal(e)
                    print(f"Error describing security profile {profile['Id']}: {describe_error(e)}")
                    continue

        # Convert to JSON serializable format
        profiles_json = json.loads(json.dumps(security_profiles, default=json_serial))

        print(f"Successfully retrieved {len(profiles_json)} security profiles")

        return {
            'statusCode': 200,
            'securityProfiles': profiles_json
        }

    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error listing security profiles: {describe_error(e)}")
        return {
            'statusCode': 400,
            'error': describe_error(e)
        }
