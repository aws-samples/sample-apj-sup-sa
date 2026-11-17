from botocore.exceptions import ClientError
from arn_mapping import ExistingIndex, build_name_map
from region_utils import create_connect_client_from_arn, create_destination_client, reraise_if_fatal
from region_utils import EXISTS_ERROR_CODES, describe_error, error_code, failure_entry, redact
from region_utils import resolve_external_id
from region_utils import step_handler


PHONE_NUMBER_FOLLOW_UP = (
    "Phone numbers do not move across accounts or Regions, so the source outbound caller ID "
    "number was dropped. Claim a number in the target instance and set it as this queue's "
    "outbound caller ID number (console, or UpdateQueueOutboundCallerConfig)."
)


def map_outbound_caller_config(config, flow_mapping, queue_name):
    """Rewrite a source OutboundCallerConfig for the target instance.

    * OutboundCallerIdName is kept.
    * OutboundFlowId is mapped to the target flow with the same name, or
      dropped and reported when there is no such flow.
    * OutboundCallerIdNumberId is always dropped and reported, because the
      number belongs to the source instance (account and Region).

    Returns (config_for_create_queue_or_None, follow_up_items).
    """
    if not config:
        return None, []
    mapped = {}
    follow_up = []
    if config.get('OutboundCallerIdName'):
        mapped['OutboundCallerIdName'] = config['OutboundCallerIdName']

    source_flow_id = config.get('OutboundFlowId')
    if source_flow_id:
        if source_flow_id in flow_mapping:
            mapped['OutboundFlowId'] = flow_mapping[source_flow_id]
        else:
            follow_up.append({
                'type': 'queueOutboundFlow', 'queue': queue_name, 'source': source_flow_id,
                'action': (f"No flow with the same name as source outbound whisper flow {source_flow_id} "
                           f"exists in the target. Migrate it, then set it as the outbound flow of queue "
                           f"'{queue_name}'."),
            })

    source_number_id = config.get('OutboundCallerIdNumberId')
    if source_number_id:
        follow_up.append({'type': 'queueOutboundCallerIdNumber', 'queue': queue_name,
                          'source': source_number_id, 'action': PHONE_NUMBER_FOLLOW_UP})

    return (mapped or None), follow_up


def build_flow_mapping(event, dest_client, destination_instance_id, queues):
    """Source flow ID -> target flow ID by name, only when a queue needs it."""
    if not any((q.get('OutboundCallerConfig') or {}).get('OutboundFlowId') for q in queues):
        return {}
    source_instance_arn = event.get('sourceInstanceId')
    if not source_instance_arn:
        print("No sourceInstanceId in the event; outbound flows cannot be mapped and will be reported")
        return {}
    source_client, source_instance_id, _ = create_connect_client_from_arn(source_instance_arn)
    return build_name_map(source_client, dest_client, source_instance_id, destination_instance_id, 'flows')


@step_handler
def lambda_handler(event, context):
    """
    Creates queues in destination Connect instance with proper hours mapping
    Handles duplicates by finding existing resources and creating mappings
    Supports queueOptions for controlling hours of operation migration
    Supports cross-account migration with crossAccountRoleArn
    """
    destination_instance_arn = event['destinationInstanceId']  # Now expects ARN
    queues = event['queues']
    hours_mapping = event.get('hoursMapping', {})
    cross_account_role_arn = event.get('crossAccountRoleArn')  # Optional for cross-account
    external_id = resolve_external_id(event)  # never logged

    # Get queue migration options
    queue_options = event.get('queueOptions', {})
    migrate_hours_of_operation = queue_options.get('migrateHoursOfOperation', True)

    print(f"Queue migration options: migrateHoursOfOperation={migrate_hours_of_operation}")
    if cross_account_role_arn:
        print(f"Cross-account migration enabled with role: {cross_account_role_arn}")

    # Create region-specific Connect client for destination instance (supports cross-account)
    print(f"Creating Connect client from destination instance ARN: {destination_instance_arn}")
    connect_client, destination_instance_id, destination_region = create_destination_client(
        destination_instance_arn, cross_account_role_arn, external_id
    )

    # If not migrating hours of operation, find the default one in destination
    default_hours_id = None
    if not migrate_hours_of_operation:
        default_hours_id = find_basic_hours_of_operation(connect_client, destination_instance_id)
        print(f"Using default Basic Hours of Operation: {default_hours_id}")

    queue_mapping = {}
    created_queues = []
    failed_items = []
    follow_up = []
    flow_mapping = build_flow_mapping(event, connect_client, destination_instance_id, queues)
    # Standard queues already in the target, listed once before any CreateQueue (otherwise
    # a rerun creates blind, and a duplicate lookup crashes on agent queue summaries).
    existing = ExistingIndex(connect_client, destination_instance_id, 'queues')

    print(f"Processing {len(queues)} queues")
    print(f"Hours mapping received: {redact(hours_mapping)}")

    for queue in queues:
        try:
            queue_name = queue['Name']
            original_queue_id = queue['QueueId']
            print(f"Processing queue: {redact(queue_name)}")

            if existing.error is not None:
                failed_items.append(existing.lookup_failure(original_queue_id))
                continue
            existing_queue_id = existing.get(queue_name)
            if existing_queue_id:
                queue_mapping[original_queue_id] = existing_queue_id
                created_queues.append({'originalId': original_queue_id, 'newId': existing_queue_id,
                                       'status': 'existing'})
                print(f"Mapped to existing queue: ({original_queue_id} -> {existing_queue_id})")
                continue

            # Determine which hours of operation ID to use
            original_hours_id = queue['HoursOfOperationId']

            if not migrate_hours_of_operation and default_hours_id:
                # Use default hours of operation
                mapped_hours_id = default_hours_id
                print(f"Using default Basic Hours for queue {redact(queue_name)}")
            elif original_hours_id in hours_mapping:
                # Use mapped hours from migration
                mapped_hours_id = hours_mapping[original_hours_id]
                print(f"Mapped hours of operation: {original_hours_id} -> {mapped_hours_id}")
            else:
                # Fallback to default if no mapping found
                if default_hours_id:
                    mapped_hours_id = default_hours_id
                    print(f"No mapping found for hours {original_hours_id}, using default")
                else:
                    # Try to find default hours as last resort
                    default_hours_id = find_basic_hours_of_operation(connect_client, destination_instance_id)
                    if default_hours_id:
                        mapped_hours_id = default_hours_id
                        print(f"Using fallback default hours: {default_hours_id}")
                    else:
                        print(f"No hours of operation available for queue {redact(queue_name)}, skipping")
                        failed_items.append(failure_entry(original_queue_id, reason='no hours of operation in target'))
                        continue

            # First, try to create the queue
            try:
                # Prepare parameters for creating queue
                params = {
                    'InstanceId': destination_instance_id,
                    'Name': queue_name,
                    'HoursOfOperationId': mapped_hours_id
                }

                # Add optional parameters if they exist
                if queue.get('Description'):
                    params['Description'] = queue['Description']

                outbound_config, queue_follow_up = map_outbound_caller_config(
                    queue.get('OutboundCallerConfig'), flow_mapping, queue_name
                )
                if outbound_config:
                    params['OutboundCallerConfig'] = outbound_config
                follow_up.extend(queue_follow_up)

                if queue.get('Tags'):
                    params['Tags'] = queue['Tags']

                # Create the queue
                response = connect_client.create_queue(**params)
                new_queue_id = response['QueueId']

                # Store the mapping
                queue_mapping[original_queue_id] = new_queue_id
                created_queues.append({
                    'originalId': original_queue_id,
                    'newId': new_queue_id,
                    'status': 'created'
                })

                print(f"Created queue: {redact(queue_name)} ({original_queue_id} -> {new_queue_id})")

            except ClientError as e:
                reraise_if_fatal(e)
                if error_code(e) in EXISTS_ERROR_CODES:
                    print(f"Queue '{redact(queue_name)}' already exists, finding existing resource...")

                    # One fresh lookup by name (created since the index was built)
                    existing_queue_id, failure = existing.recheck(queue_name, e, original_queue_id)

                    if existing_queue_id:
                        # Create mapping to existing resource
                        queue_mapping[original_queue_id] = existing_queue_id
                        created_queues.append({
                            'originalId': original_queue_id,
                            'newId': existing_queue_id,
                            'status': 'existing'
                        })
                        print(f"Mapped to existing queue: {redact(queue_name)} ({original_queue_id} -> {existing_queue_id})")
                    else:
                        print(f"Could not find existing queue: {original_queue_id} ({failure['errorCode']})")
                        failed_items.append(failure)
                        continue
                elif e.response['Error']['Code'] == 'ResourceNotFoundException':
                    print(f"Hours of operation {mapped_hours_id} not found in destination instance")
                    print(f"Skipping queue {redact(queue_name)} - hours of operation dependency missing")
                    failed_items.append(failure_entry(original_queue_id, e, 'hours of operation missing in target'))
                    continue
                else:
                    # Re-raise other client errors
                    raise e

        except Exception as e:
            reraise_if_fatal(e)
            print(f"Error: {describe_error(e)}")
            # Continue with other queues even if one fails
            failed_items.append(failure_entry(queue.get('QueueId'), e))
            continue

    print(f"Successfully processed {len(created_queues)} queues")
    print(f"Queue mapping: {redact(queue_mapping)}")

    return {
        'statusCode': 200,
        'queueMapping': queue_mapping,
        'failedItems': failed_items,
        'createdQueues': created_queues,
        'manualFollowUp': follow_up
    }


def find_basic_hours_of_operation(connect_client, instance_id):
    """Find the 'Basic Hours' or default hours of operation in the destination instance"""
    try:
        paginator = connect_client.get_paginator('list_hours_of_operations')
        for page in paginator.paginate(InstanceId=instance_id):
            for hours in page['HoursOfOperationSummaryList']:
                # Look for 'Basic Hours' (default Connect hours)
                if hours['Name'].lower() == 'basic hours':
                    print(f"Found Basic Hours: {hours['Id']}")
                    return hours['Id']

        # If 'Basic Hours' not found, return the first hours of operation
        response = connect_client.list_hours_of_operations(InstanceId=instance_id, MaxResults=1)
        if response['HoursOfOperationSummaryList']:
            fallback_id = response['HoursOfOperationSummaryList'][0]['Id']
            print(f"Basic Hours not found, using fallback: {fallback_id}")
            return fallback_id
        return None
    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error finding Basic Hours of Operation: {describe_error(e)}")
        return None
