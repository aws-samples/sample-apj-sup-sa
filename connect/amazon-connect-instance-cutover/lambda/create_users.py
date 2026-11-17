from region_utils import create_destination_client
import secrets
import string
from botocore.exceptions import ClientError
from arn_mapping import ExistingIndex
from region_utils import reraise_if_fatal, error_code_of, EXISTS_ERROR_CODES
from region_utils import describe_error, failure_entry, redact
from region_utils import resolve_external_id
from region_utils import step_handler

# Connect rejects an existing username with InvalidRequestException, not only
# DuplicateResourceException. The username lookup decides: a miss keeps the real code.
USER_EXISTS_ERROR_CODES = EXISTS_ERROR_CODES | {'InvalidRequestException'}
USERNAME_NOT_FOUND = 'create failed, username not found in target'

@step_handler
def lambda_handler(event, context):
    """
    Creates users in destination Connect instance with proper profile mappings
    Handles missing dependencies and password requirements
    Supports userOptions for controlling routing/security profile migration
    Supports cross-account migration with cross_account_role_arn
    Supports hierarchy group mapping for agent hierarchy
    """
    destination_instance_arn = event['destinationInstanceId']  # Now expects ARN
    users = event['users']
    security_profile_mapping = event.get('securityProfileMapping', {})
    routing_profile_mapping = event.get('routingProfileMapping', {})
    hierarchy_group_mapping = event.get('hierarchyGroupMapping', {})  # For agent hierarchy
    cross_account_role_arn = event.get('crossAccountRoleArn')  # Optional for cross-account
    external_id = resolve_external_id(event)  # never logged

    # Get user migration options (new feature)
    user_options = event.get('userOptions', {})
    migrate_routing_profiles = user_options.get('migrateRoutingProfiles', True)
    migrate_security_profiles = user_options.get('migrateSecurityProfiles', True)

    print(f"User migration options: migrateRoutingProfiles={migrate_routing_profiles}, migrateSecurityProfiles={migrate_security_profiles}")
    print(f"Hierarchy group mappings: {len(hierarchy_group_mapping)}")
    if cross_account_role_arn:
        print(f"Cross-account migration enabled with role: {cross_account_role_arn}")

    # Create region-specific Connect client for destination instance (supports cross-account)
    print(f"Creating Connect client from destination instance ARN: {destination_instance_arn}")
    connect_client, destination_instance_id, destination_region = create_destination_client(
        destination_instance_arn, cross_account_role_arn, external_id
    )

    # If not migrating profiles, find the default ones in destination
    default_routing_profile_id = None
    default_security_profile_id = None

    if not migrate_routing_profiles:
        default_routing_profile_id = find_basic_routing_profile(connect_client, destination_instance_id)
        print(f"Using default Basic Routing Profile: {default_routing_profile_id}")

    if not migrate_security_profiles:
        default_security_profile_id = find_agent_security_profile(connect_client, destination_instance_id)
        print(f"Using default Agent Security Profile: {default_security_profile_id}")

    user_mapping = {}
    created_users = []
    failed_users = []
    # Users already in the target, by exact username (Connect usernames are case
    # sensitive), listed once before any CreateUser (otherwise a rerun creates blind).
    existing = ExistingIndex(connect_client, destination_instance_id, 'users')

    print(f"Processing {len(users)} users")
    print(f"Security profile mappings: {len(security_profile_mapping)}")
    print(f"Routing profile mappings: {len(routing_profile_mapping)}")

    for user in users:
        try:
            username = user['Username']
            original_user_id = user['Id']
            print(f"Processing user: {original_user_id}")

            if existing.error is not None:
                failed_users.append(existing.lookup_failure(original_user_id))
                continue
            existing_user_id = existing.get(username)
            if existing_user_id:
                # Mapped like a created user, so quick connects and VerifyTarget can use it.
                user_mapping[original_user_id] = existing_user_id
                created_users.append({'originalId': original_user_id, 'newId': existing_user_id,
                                      'status': 'existing'})
                print(f"Mapped to existing user: ({original_user_id} -> {existing_user_id})")
                continue

            # First, try to create the user
            try:
                # Prepare parameters for creating user
                params = {
                    'InstanceId': destination_instance_id,
                    'Username': username,
                    'Password': generate_temporary_password(),  # Generate temporary password
                    'RoutingProfileId': user['RoutingProfileId'],
                    'SecurityProfileIds': user['SecurityProfileIds']
                }

                # Map routing profile ID
                original_routing_profile_id = user['RoutingProfileId']

                # Check if we should use default routing profile instead of migrating
                if not migrate_routing_profiles and default_routing_profile_id:
                    params['RoutingProfileId'] = default_routing_profile_id
                    print(f"Using default Basic Routing Profile for user {original_user_id}")
                elif original_routing_profile_id in routing_profile_mapping:
                    params['RoutingProfileId'] = routing_profile_mapping[original_routing_profile_id]
                    print(f"Mapped routing profile: {original_routing_profile_id} -> {params['RoutingProfileId']}")
                else:
                    print(f"Warning: No mapping found for routing profile {original_routing_profile_id}")
                    # Try to find existing routing profile by name or use default
                    default_routing_profile = find_default_routing_profile(connect_client, destination_instance_id)
                    if default_routing_profile:
                        params['RoutingProfileId'] = default_routing_profile
                        print(f"Using default routing profile: {redact(default_routing_profile)}")
                    else:
                        print(f"Skipping user {original_user_id} - no valid routing profile found")
                        failed_users.append({
                            'originalId': original_user_id,
                            'error': 'No valid routing profile mapping found', 'errorCode': 'NotCreated'
                        })
                        continue

                # Map security profile IDs
                mapped_security_profiles = []

                # Check if we should use default security profile instead of migrating
                if not migrate_security_profiles and default_security_profile_id:
                    mapped_security_profiles = [default_security_profile_id]
                    print(f"Using default Agent Security Profile for user {original_user_id}")
                else:
                    for security_profile_id in user['SecurityProfileIds']:
                        if security_profile_id in security_profile_mapping:
                            mapped_id = security_profile_mapping[security_profile_id]
                            mapped_security_profiles.append(mapped_id)
                            print(f"Mapped security profile: {security_profile_id} -> {mapped_id}")
                        else:
                            print(f"Warning: No mapping found for security profile {security_profile_id}")
                            # Try to find a default security profile
                            default_security_profile = find_default_security_profile(connect_client, destination_instance_id)
                            if default_security_profile:
                                mapped_security_profiles.append(default_security_profile)
                                print(f"Using default security profile: {redact(default_security_profile)}")

                if not mapped_security_profiles:
                    print(f"Skipping user {original_user_id} - no valid security profiles found")
                    failed_users.append({
                        'originalId': original_user_id,
                        'error': 'No valid security profile mappings found', 'errorCode': 'NotCreated'
                    })
                    continue

                params['SecurityProfileIds'] = mapped_security_profiles

                # Add optional parameters if they exist
                if user.get('PhoneConfig'):
                    params['PhoneConfig'] = user['PhoneConfig']

                # Only add DirectoryUserId if the destination instance supports it
                # Skip this parameter to avoid directory type conflicts
                # if user.get('DirectoryUserId'):
                #     params['DirectoryUserId'] = user['DirectoryUserId']

                # Map hierarchy group ID if present
                if user.get('HierarchyGroupId'):
                    original_hierarchy_group_id = user['HierarchyGroupId']
                    if original_hierarchy_group_id in hierarchy_group_mapping:
                        mapped_hierarchy_id = hierarchy_group_mapping[original_hierarchy_group_id]
                        params['HierarchyGroupId'] = mapped_hierarchy_id
                        print(f"Mapped hierarchy group: {original_hierarchy_group_id} -> {mapped_hierarchy_id}")
                    else:
                        print(f"Warning: No mapping found for hierarchy group {original_hierarchy_group_id}, skipping hierarchy assignment")

                if user.get('Tags'):
                    params['Tags'] = user['Tags']

                # Handle identity info
                if user.get('IdentityInfo'):
                    identity_info = {}
                    if user['IdentityInfo'].get('FirstName'):
                        identity_info['FirstName'] = user['IdentityInfo']['FirstName']
                    if user['IdentityInfo'].get('LastName'):
                        identity_info['LastName'] = user['IdentityInfo']['LastName']
                    if user['IdentityInfo'].get('Email'):
                        identity_info['Email'] = user['IdentityInfo']['Email']

                    if identity_info:
                        params['IdentityInfo'] = identity_info

                # Create the user
                response = connect_client.create_user(**params)
                new_user_id = response['UserId']

                # Associate user proficiencies if present
                if user.get('UserProficiencies') and len(user['UserProficiencies']) > 0:
                    try:
                        connect_client.associate_user_proficiencies(
                            InstanceId=destination_instance_id,
                            UserId=new_user_id,
                            UserProficiencies=user['UserProficiencies']
                        )
                        print(f"Associated {len(user['UserProficiencies'])} proficiencies for user {original_user_id}")
                    except Exception as prof_error:
                        reraise_if_fatal(prof_error)
                        print(f"Warning: Failed to associate proficiencies for user {original_user_id}: {describe_error(prof_error)}")
                        failed_users.append(failure_entry(original_user_id, prof_error, 'user created, proficiencies not associated'))

                # Store the mapping
                user_mapping[original_user_id] = new_user_id
                created_users.append({
                    'originalId': original_user_id,
                    'newId': new_user_id,
                    'status': 'created'
                })

                print(f"Created user: ({original_user_id} -> {new_user_id})")

            except ClientError as e:
                reraise_if_fatal(e)
                if error_code_of(e) in USER_EXISTS_ERROR_CODES:
                    print(f"User {original_user_id} create failed ({error_code_of(e)}), looking the username up...")

                    # One fresh lookup by username (created since the index was built)
                    existing_user_id, failure = existing.recheck(username, e, original_user_id, USERNAME_NOT_FOUND)

                    if existing_user_id:
                        # Create mapping to existing user
                        user_mapping[original_user_id] = existing_user_id
                        created_users.append({
                            'originalId': original_user_id,
                            'newId': existing_user_id,
                            'status': 'existing'
                        })
                        print(f"Mapped to existing user: ({original_user_id} -> {existing_user_id})")
                    else:
                        # Not found: a genuine bad request keeps its real code
                        print(f"Could not find existing user: {original_user_id} ({failure['errorCode']})")
                        failed_users.append(failure)
                else:
                    # Re-raise other client errors
                    raise e

        except Exception as e:
            reraise_if_fatal(e)
            print(f"Error: {describe_error(e)}")
            failed_users.append({
                'originalId': user.get('Id', 'Unknown'),
                'error': describe_error(e), 'errorCode': error_code_of(e)
            })
            # Continue with other users even if one fails
            continue

    print(f"Successfully processed {len(created_users)} users")
    print(f"Failed to process {len(failed_users)} users")
    print(f"User mapping: {redact(user_mapping)}")

    return {
        'statusCode': 200,
        'userMapping': user_mapping,
        'createdUsers': created_users,
        'failedUsers': failed_users,
        'summary': {
            'totalUsers': len(users),
            'successfullyCreated': len(created_users),
            'existing': sum(1 for u in created_users if u['status'] == 'existing'),
            'failed': len(failed_users)
        }
    }

def generate_temporary_password():
    """Generate a temporary password that meets AWS Connect requirements"""
    # AWS Connect password requirements: 8-64 chars, at least one uppercase, lowercase, number, and special char
    # Start with guaranteed required characters
    required_chars = [
        secrets.choice(string.ascii_uppercase),  # At least one uppercase
        secrets.choice(string.ascii_lowercase),  # At least one lowercase
        secrets.choice(string.digits),           # At least one digit
        secrets.choice("!@#$%^&*")               # At least one special char
    ]

    # Fill the rest with random characters
    remaining_length = 12 - len(required_chars)
    all_chars = string.ascii_letters + string.digits + "!@#$%^&*"
    remaining_chars = [secrets.choice(all_chars) for _ in range(remaining_length)]

    # Combine and shuffle
    password_chars = required_chars + remaining_chars
    secrets.SystemRandom().shuffle(password_chars)

    return ''.join(password_chars)

def find_default_routing_profile(connect_client, instance_id):
    """Find a default routing profile in the destination instance"""
    try:
        response = connect_client.list_routing_profiles(InstanceId=instance_id, MaxResults=10)
        if response['RoutingProfileSummaryList']:
            # Return the first routing profile as default
            return response['RoutingProfileSummaryList'][0]['Id']
        return None
    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error finding default routing profile: {describe_error(e)}")
        return None

def find_basic_routing_profile(connect_client, instance_id):
    """Find the 'Basic Routing Profile' in the destination instance"""
    try:
        paginator = connect_client.get_paginator('list_routing_profiles')
        for page in paginator.paginate(InstanceId=instance_id):
            for profile in page['RoutingProfileSummaryList']:
                # Look for 'Basic Routing Profile' (default Connect profile)
                if profile['Name'].lower() == 'basic routing profile':
                    print(f"Found Basic Routing Profile: {profile['Id']}")
                    return profile['Id']

        # If not found, return the first routing profile
        response = connect_client.list_routing_profiles(InstanceId=instance_id, MaxResults=1)
        if response['RoutingProfileSummaryList']:
            fallback_id = response['RoutingProfileSummaryList'][0]['Id']
            print(f"Basic Routing Profile not found, using fallback: {fallback_id}")
            return fallback_id
        return None
    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error finding Basic Routing Profile: {describe_error(e)}")
        return None

def find_agent_security_profile(connect_client, instance_id):
    """Find the 'Agent' security profile in the destination instance"""
    try:
        paginator = connect_client.get_paginator('list_security_profiles')
        for page in paginator.paginate(InstanceId=instance_id):
            for profile in page['SecurityProfileSummaryList']:
                # Look for 'Agent' security profile (default Connect profile)
                if profile['Name'].lower() == 'agent':
                    print(f"Found Agent Security Profile: {profile['Id']}")
                    return profile['Id']

        # If not found, look for any profile with 'agent' in the name
        for page in paginator.paginate(InstanceId=instance_id):
            for profile in page['SecurityProfileSummaryList']:
                if 'agent' in profile['Name'].lower():
                    print(f"Found agent-like Security Profile: {profile['Id']}")
                    return profile['Id']

        # If still not found, return the first security profile
        response = connect_client.list_security_profiles(InstanceId=instance_id, MaxResults=1)
        if response['SecurityProfileSummaryList']:
            fallback_id = response['SecurityProfileSummaryList'][0]['Id']
            print(f"Agent Security Profile not found, using fallback: {fallback_id}")
            return fallback_id
        return None
    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error finding Agent Security Profile: {describe_error(e)}")
        return None

def find_default_security_profile(connect_client, instance_id):
    """Find a default security profile in the destination instance"""
    try:
        response = connect_client.list_security_profiles(InstanceId=instance_id, MaxResults=10)
        if response['SecurityProfileSummaryList']:
            # Look for 'Agent' profile first, otherwise use the first one
            for profile in response['SecurityProfileSummaryList']:
                if 'agent' in profile['Name'].lower():
                    return profile['Id']
            # If no 'Agent' profile found, return the first one
            return response['SecurityProfileSummaryList'][0]['Id']
        return None
    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error finding default security profile: {describe_error(e)}")
        return None
