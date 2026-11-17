from region_utils import create_destination_client
from botocore.exceptions import ClientError
from arn_mapping import ExistingIndex
from region_utils import reraise_if_fatal
from region_utils import EXISTS_ERROR_CODES, describe_error, error_code, failure_entry, redact
from region_utils import resolve_external_id
from region_utils import step_handler

@step_handler
def lambda_handler(event, context):
    """
    Creates hours of operations in destination Connect instance
    Handles duplicates by finding existing resources and creating mappings
    Supports cross-account migration with crossAccountRoleArn
    """
    destination_instance_arn = event['destinationInstanceId']  # Now expects ARN
    hours_of_operations = event['hoursOfOperations']
    cross_account_role_arn = event.get('crossAccountRoleArn')  # Optional for cross-account
    external_id = resolve_external_id(event)  # never logged

    # Create region-specific Connect client for destination instance (supports cross-account)
    print(f"Creating Connect client from destination instance ARN: {destination_instance_arn}")
    if cross_account_role_arn:
        print(f"Cross-account migration enabled with role: {cross_account_role_arn}")
    connect_client, destination_instance_id, destination_region = create_destination_client(
        destination_instance_arn, cross_account_role_arn, external_id
    )

    hours_mapping = {}
    created_hours = []
    failed_items = []
    # Hours already in the target, listed once before any create
    existing = ExistingIndex(connect_client, destination_instance_id, 'hours')

    print(f"Processing {len(hours_of_operations)} hours of operations")

    for hours in hours_of_operations:
        try:
            hours_name = hours['Name']
            original_id = hours['HoursOfOperationId']
            print(f"Processing hours of operation: {redact(hours_name)}")

            if existing.error is not None:
                failed_items.append(existing.lookup_failure(original_id))
                continue
            existing_hours_id = existing.get(hours_name)
            if existing_hours_id:
                hours_mapping[original_id] = existing_hours_id
                created_hours.append({'originalId': original_id, 'newId': existing_hours_id, 'status': 'existing'})
                print(f"Mapped to existing hours of operation: ({original_id} -> {existing_hours_id})")
                continue

            # First, try to create the hours of operation
            try:
                # Prepare parameters for creating hours of operation
                params = {
                    'InstanceId': destination_instance_id,
                    'Name': hours_name,
                    'TimeZone': hours['TimeZone'],
                    'Config': hours['Config']
                }

                # Add optional parameters if they exist
                if hours.get('Description'):
                    params['Description'] = hours['Description']

                if hours.get('Tags'):
                    params['Tags'] = hours['Tags']

                # Create the hours of operation
                response = connect_client.create_hours_of_operation(**params)
                new_hours_id = response['HoursOfOperationId']

                # Store the mapping
                hours_mapping[original_id] = new_hours_id
                created_hours.append({
                    'originalId': original_id,
                    'newId': new_hours_id,
                    'status': 'created'
                })

                print(f"Created hours of operation: {redact(hours_name)} ({original_id} -> {new_hours_id})")

            except ClientError as e:
                reraise_if_fatal(e)
                if error_code(e) in EXISTS_ERROR_CODES:
                    print(f"Hours of operation '{redact(hours_name)}' already exists, finding existing resource...")

                    # One fresh lookup by name (created since the index was built)
                    existing_hours_id, failure = existing.recheck(hours_name, e, original_id)

                    if existing_hours_id:
                        # Create mapping to existing resource
                        hours_mapping[original_id] = existing_hours_id
                        created_hours.append({
                            'originalId': original_id,
                            'newId': existing_hours_id,
                            'status': 'existing'
                        })
                        print(f"Mapped to existing hours of operation: {redact(hours_name)} ({original_id} -> {existing_hours_id})")
                    else:
                        print(f"Could not find existing hours of operation: {original_id} ({failure['errorCode']})")
                        failed_items.append(failure)
                        continue
                else:
                    # Re-raise other client errors
                    raise e

        except Exception as e:
            reraise_if_fatal(e)
            print(f"Error: {describe_error(e)}")
            # Continue with other hours even if one fails
            failed_items.append(failure_entry(hours.get('HoursOfOperationId'), e))
            continue

    print(f"Successfully processed {len(created_hours)} hours of operations")
    print(f"Hours mapping: {redact(hours_mapping)}")

    return {
        'statusCode': 200,
        'hoursMapping': hours_mapping,
        'failedItems': failed_items,
        'createdHours': created_hours
    }
