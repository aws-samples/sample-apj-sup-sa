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
    Lists and describes all routing profiles from source Connect instance
    """
    source_instance_arn = event['sourceInstanceId']  # Now expects ARN
    # Create region-specific Connect client for source instance
    print(f"Creating Connect client from source instance ARN: {source_instance_arn}")
    connect_client, source_instance_id, source_region = create_connect_client_from_arn(source_instance_arn)

    try:
        print(f"Listing routing profiles from instance: {source_instance_id} in region: {source_region}")

        # Get paginator for listing routing profiles
        paginator = connect_client.get_paginator('list_routing_profiles')
        routing_profiles = []

        # Iterate through all pages
        for page in paginator.paginate(InstanceId=source_instance_id):
            for profile in page['RoutingProfileSummaryList']:
                try:
                    # Get detailed information for each routing profile
                    profile_detail = connect_client.describe_routing_profile(
                        InstanceId=source_instance_id,
                        RoutingProfileId=profile['Id']
                    )
                    routing_profiles.append(profile_detail['RoutingProfile'])
                    print(f"Retrieved routing profile: {redact(profile_detail['RoutingProfile']['Name'])}")

                except Exception as e:
                    reraise_if_fatal(e)
                    print(f"Error describing routing profile {profile['Id']}: {describe_error(e)}")
                    continue

        # Convert to JSON serializable format
        profiles_json = json.loads(json.dumps(routing_profiles, default=json_serial))

        print(f"Successfully retrieved {len(profiles_json)} routing profiles")

        return {
            'statusCode': 200,
            'routingProfiles': profiles_json
        }

    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error listing routing profiles: {describe_error(e)}")
        return {
            'statusCode': 400,
            'error': describe_error(e)
        }
