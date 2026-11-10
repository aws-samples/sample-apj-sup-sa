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
    Lists and describes all users from source Connect instance
    """
    source_instance_arn = event['sourceInstanceId']  # Now expects ARN

    # Create region-specific Connect client from ARN
    print(f"Creating Connect client from source instance ARN: {source_instance_arn}")
    connect_client, source_instance_id, source_region = create_connect_client_from_arn(source_instance_arn)

    try:
        print(f"Listing users from instance: {source_instance_id} in region: {source_region}")

        # Get paginator for listing users
        paginator = connect_client.get_paginator('list_users')
        users = []

        # Iterate through all pages
        for page in paginator.paginate(InstanceId=source_instance_id):
            for user in page['UserSummaryList']:
                try:
                    # Get detailed information for each user
                    user_detail = connect_client.describe_user(
                        InstanceId=source_instance_id,
                        UserId=user['Id']
                    )

                    user_data = user_detail['User']

                    # Fetch routing profile name
                    try:
                        routing_profile = connect_client.describe_routing_profile(
                            InstanceId=source_instance_id,
                            RoutingProfileId=user_data['RoutingProfileId']
                        )
                        user_data['RoutingProfileName'] = routing_profile['RoutingProfile']['Name']
                    except Exception as e:
                        reraise_if_fatal(e)
                        print(f"Error fetching routing profile name for {user_data['RoutingProfileId']}: {describe_error(e)}")
                        user_data['RoutingProfileName'] = 'Unknown'

                    # Fetch security profile names
                    security_profile_names = {}
                    for sp_id in user_data['SecurityProfileIds']:
                        try:
                            security_profile = connect_client.describe_security_profile(
                                InstanceId=source_instance_id,
                                SecurityProfileId=sp_id
                            )
                            security_profile_names[sp_id] = security_profile['SecurityProfile']['SecurityProfileName']
                        except Exception as e:
                            reraise_if_fatal(e)
                            print(f"Error fetching security profile name for {sp_id}: {describe_error(e)}")
                            security_profile_names[sp_id] = 'Unknown'

                    user_data['SecurityProfileNames'] = security_profile_names

                    # Fetch user proficiencies
                    try:
                        proficiencies_response = connect_client.list_user_proficiencies(
                            InstanceId=source_instance_id,
                            UserId=user['Id'],
                            MaxResults=100
                        )
                        user_data['UserProficiencies'] = proficiencies_response.get('UserProficiencyList', [])
                        if user_data['UserProficiencies']:
                            print(f"Retrieved {len(user_data['UserProficiencies'])} proficiencies for user {redact(user_data['Username'])}")
                    except Exception as e:
                        reraise_if_fatal(e)
                        print(f"Error fetching proficiencies for user {user['Id']}: {describe_error(e)}")
                        user_data['UserProficiencies'] = []

                    users.append(user_data)
                    print(f"Retrieved user: {redact(user_detail['User']['Username'])}")

                except Exception as e:
                    reraise_if_fatal(e)
                    print(f"Error describing user {user['Id']}: {describe_error(e)}")
                    continue

        # Convert to JSON serializable format
        users_json = json.loads(json.dumps(users, default=json_serial))

        print(f"Successfully retrieved {len(users_json)} users")

        return {
            'statusCode': 200,
            'users': users_json
        }

    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error listing users: {describe_error(e)}")
        return {
            'statusCode': 400,
            'error': describe_error(e)
        }
