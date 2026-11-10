from region_utils import create_destination_client
from botocore.exceptions import ClientError
from arn_mapping import ExistingIndex
from region_utils import reraise_if_fatal
from region_utils import EXISTS_ERROR_CODES, describe_error, error_code, failure_entry, redact
from region_utils import resolve_external_id
from migration_filters import QUEUE_LIST_PARAMS
from region_utils import step_handler

@step_handler
def lambda_handler(event, context):
    """
    Creates routing profiles in destination Connect instance
    Supports cross-account migration with crossAccountRoleArn
    """
    destination_instance_arn = event['destinationInstanceId']  # Now expects ARN
    routing_profiles = event['routingProfiles']
    cross_account_role_arn = event.get('crossAccountRoleArn')  # Optional for cross-account
    external_id = resolve_external_id(event)  # never logged
    # Get queue and hours mappings from previous steps if available
    queue_mapping = event.get('queueMapping', {})
    hours_mapping = event.get('hoursMapping', {})

    # Create region-specific Connect client for destination instance (supports cross-account)
    print(f"Creating Connect client from destination instance ARN: {destination_instance_arn}")
    if cross_account_role_arn:
        print(f"Cross-account migration enabled with role: {cross_account_role_arn}")
    connect_client, destination_instance_id, destination_region = create_destination_client(
        destination_instance_arn, cross_account_role_arn, external_id
    )

    routing_profile_mapping = {}
    created_profiles = []
    failed_items = []

    print(f"Processing {len(routing_profiles)} routing profiles")
    print(f"Queue mapping available: {len(queue_mapping)} mappings")
    print(f"Hours mapping available: {len(hours_mapping)} mappings")

    # Find default queue in destination for fallback
    default_queue_id = find_default_queue(connect_client, destination_instance_id)
    print(f"Default queue in destination: {default_queue_id}")
    # Profiles already in the target, listed once before any create
    existing = ExistingIndex(connect_client, destination_instance_id, 'routingProfiles')

    for profile in routing_profiles:
        try:
            profile_name = profile['Name']
            original_profile_id = profile['RoutingProfileId']
            print(f"Creating routing profile: {redact(profile_name)}")

            if existing.error is not None:
                failed_items.append(existing.lookup_failure(original_profile_id))
                continue
            existing_id = existing.get(profile_name)
            if existing_id:
                routing_profile_mapping[original_profile_id] = existing_id
                created_profiles.append({'originalId': original_profile_id, 'newId': existing_id,
                                         'status': 'existing'})
                print(f"Mapped to existing routing profile: ({original_profile_id} -> {existing_id})")
                continue

            # Map the default outbound queue ID
            original_default_queue = profile['DefaultOutboundQueueId']
            if original_default_queue in queue_mapping:
                mapped_default_queue = queue_mapping[original_default_queue]
                print(f"Mapped default outbound queue: {redact(original_default_queue)} -> {redact(mapped_default_queue)}")
            elif default_queue_id:
                mapped_default_queue = default_queue_id
                print(f"Using default queue for {redact(profile_name)}: {default_queue_id}")
            else:
                print(f"No queue mapping for default outbound queue {redact(original_default_queue)}, skipping profile")
                failed_items.append(failure_entry(original_profile_id, reason='default outbound queue not mapped'))
                continue

            # Prepare parameters for creating routing profile
            params = {
                'InstanceId': destination_instance_id,
                'Name': profile_name,
                'DefaultOutboundQueueId': mapped_default_queue,
                'MediaConcurrencies': profile['MediaConcurrencies']
            }

            # Add optional parameters if they exist
            if profile.get('Description'):
                params['Description'] = profile['Description']

            if profile.get('Tags'):
                params['Tags'] = profile['Tags']

            # Handle queue configurations if present - only include queues that have mappings
            if profile.get('QueueConfigs'):
                queue_configs = []
                for config in profile['QueueConfigs']:
                    original_queue_id = config['QueueReference']['QueueId']

                    # Only include queue config if we have a mapping for it
                    if original_queue_id in queue_mapping:
                        queue_config = {
                            'QueueReference': {
                                'QueueId': queue_mapping[original_queue_id],
                                'Channel': config['QueueReference']['Channel']
                            },
                            'Priority': config['Priority'],
                            'Delay': config['Delay']
                        }
                        queue_configs.append(queue_config)
                        print(f"Mapped queue in config: {original_queue_id} -> {redact(queue_mapping[original_queue_id])}")
                    else:
                        print(f"Skipping queue config for unmapped queue: {original_queue_id}")

                if queue_configs:
                    params['QueueConfigs'] = queue_configs

            try:
                # Create the routing profile
                response = connect_client.create_routing_profile(**params)
                new_profile_id = response['RoutingProfileId']

                # Store the mapping
                routing_profile_mapping[original_profile_id] = new_profile_id
                created_profiles.append({
                    'originalId': original_profile_id,
                    'newId': new_profile_id,
                    'status': 'created'
                })

                print(f"Created routing profile: {redact(profile_name)} ({original_profile_id} -> {new_profile_id})")

            except ClientError as e:
                reraise_if_fatal(e)
                if error_code(e) in EXISTS_ERROR_CODES:
                    print(f"Routing profile '{redact(profile_name)}' already exists, finding existing...")
                    existing_id, failure = existing.recheck(profile_name, e, original_profile_id)
                    if existing_id:
                        routing_profile_mapping[original_profile_id] = existing_id
                        created_profiles.append({
                            'originalId': original_profile_id,
                            'newId': existing_id,
                            'status': 'existing'
                        })
                        print(f"Mapped to existing routing profile: {redact(profile_name)} ({original_profile_id} -> {existing_id})")
                    else:
                        print(f"Could not find existing routing profile: {original_profile_id} ({failure['errorCode']})")
                        failed_items.append(failure)
                else:
                    raise e

        except Exception as e:
            reraise_if_fatal(e)
            print(f"Error: {describe_error(e)}")
            # Continue with other profiles even if one fails
            failed_items.append(failure_entry(profile.get('RoutingProfileId'), e))
            continue

    print(f"Successfully created {len(created_profiles)} routing profiles")
    print(f"Routing profile mapping: {redact(routing_profile_mapping)}")

    return {
        'statusCode': 200,
        'routingProfileMapping': routing_profile_mapping,
        'failedItems': failed_items,
        'createdProfiles': created_profiles
    }


def find_default_queue(connect_client, instance_id):
    """Find the default/BasicQueue in the destination instance"""
    try:
        paginator = connect_client.get_paginator('list_queues')
        for page in paginator.paginate(InstanceId=instance_id, **QUEUE_LIST_PARAMS):
            for queue in page['QueueSummaryList']:
                # Return first standard queue as default
                return queue['Id']
        return None
    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error finding default queue: {describe_error(e)}")
        return None
