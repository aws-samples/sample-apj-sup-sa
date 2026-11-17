from region_utils import create_destination_client
from botocore.exceptions import ClientError
from arn_mapping import ExistingIndex
from region_utils import reraise_if_fatal
from region_utils import EXISTS_ERROR_CODES, describe_error, error_code, failure_entry
from region_utils import resolve_external_id
from region_utils import step_handler

@step_handler
def lambda_handler(event, context):
    """
    Creates security profiles in destination Connect instance
    Supports cross-account migration with crossAccountRoleArn
    """
    destination_instance_arn = event['destinationInstanceId']  # Now expects ARN
    security_profiles = event['securityProfiles']
    cross_account_role_arn = event.get('crossAccountRoleArn')  # Optional for cross-account
    external_id = resolve_external_id(event)  # never logged

    # Create region-specific Connect client for destination instance (supports cross-account)
    print(f"Creating Connect client from destination instance ARN: {destination_instance_arn}")
    if cross_account_role_arn:
        print(f"Cross-account migration enabled with role: {cross_account_role_arn}")
    connect_client, destination_instance_id, destination_region = create_destination_client(
        destination_instance_arn, cross_account_role_arn, external_id
    )

    security_profile_mapping = {}
    created_profiles = []
    failed_items = []
    # Profiles already in the target, listed once before any create
    existing = ExistingIndex(connect_client, destination_instance_id, 'securityProfiles')

    print(f"Processing {len(security_profiles)} security profiles")

    for profile in security_profiles:
        original_profile_id = profile.get('SecurityProfileId')
        try:
            profile_name = profile['SecurityProfileName']
            print(f"Creating security profile: {original_profile_id}")

            if existing.error is not None:
                failed_items.append(existing.lookup_failure(original_profile_id))
                continue
            existing_id = existing.get(profile_name)
            if existing_id:
                security_profile_mapping[original_profile_id] = existing_id
                created_profiles.append({'originalId': original_profile_id, 'newId': existing_id,
                                         'status': 'existing'})
                print(f"Mapped to existing security profile: {original_profile_id} -> {existing_id}")
                continue

            # Get permissions - handle missing Permissions field
            permissions = profile.get('Permissions', [])
            if not permissions:
                print(f"Warning: No permissions found for {original_profile_id}, using empty list")

            # Prepare parameters for creating security profile
            params = {
                'InstanceId': destination_instance_id,
                'SecurityProfileName': profile_name,
                'Permissions': permissions
            }

            # Add optional parameters if they exist
            if profile.get('Description'):
                params['Description'] = profile['Description']

            if profile.get('Tags'):
                params['Tags'] = profile['Tags']

            try:
                # Create the security profile
                response = connect_client.create_security_profile(**params)
                new_profile_id = response['SecurityProfileId']

                # Store the mapping
                security_profile_mapping[original_profile_id] = new_profile_id
                created_profiles.append({
                    'originalId': original_profile_id,
                    'newId': new_profile_id,
                    'status': 'created'
                })

                print(f"Created security profile: {original_profile_id} -> {new_profile_id}")

            except ClientError as e:
                reraise_if_fatal(e)
                # Connect answers DuplicateResourceException or, for some existing
                # profiles, InvalidParameterException: look the name up once more before failing.
                if error_code(e) in EXISTS_ERROR_CODES:
                    existing_id, failure = existing.recheck(profile_name, e, original_profile_id)
                else:
                    existing_id, failure = None, failure_entry(original_profile_id, e)
                if existing_id:
                    security_profile_mapping[original_profile_id] = existing_id
                    created_profiles.append({
                        'originalId': original_profile_id,
                        'newId': existing_id,
                        'status': 'existing'
                    })
                    print(f"Mapped to existing security profile: {original_profile_id} -> {existing_id}")
                else:
                    print(f"Security profile {original_profile_id} failed: {describe_error(e)}")
                    failed_items.append(failure)

        except Exception as e:
            reraise_if_fatal(e)
            print(f"Security profile {original_profile_id} failed: {describe_error(e)}")
            failed_items.append(failure_entry(original_profile_id, e))

    print(f"Processed {len(created_profiles)} security profiles, {len(failed_items)} failed")

    return {
        'statusCode': 200,
        'securityProfileMapping': security_profile_mapping,
        'createdProfiles': created_profiles,
        'failedItems': failed_items,
    }
