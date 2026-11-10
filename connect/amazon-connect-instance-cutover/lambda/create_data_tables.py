"""
Lambda function to create Data Tables in destination Connect instance.
Creates tables, attributes, and values.

Note: Data Tables API requires boto3 >= 1.35.0. If not available,
this function will skip gracefully.
"""
import boto3
from arn_mapping import ExistingIndex
from region_utils import create_destination_client
from region_utils import reraise_if_fatal, error_code_of, EXISTS_ERROR_CODES
from region_utils import describe_error, failure_entry, redact
from region_utils import resolve_external_id
from region_utils import step_handler

@step_handler
def lambda_handler(event, context):
    """
    Creates data tables in the destination Connect instance.
    """
    destination_instance_arn = event['destinationInstanceId']
    data_tables = event.get('dataTables', [])
    cross_account_role_arn = event.get('crossAccountRoleArn')
    external_id = resolve_external_id(event)  # never logged

    print(f"Destination instance: {destination_instance_arn}")
    print(f"Data tables to process: {len(data_tables)}")
    print(f"Boto3 version: {boto3.__version__}")

    # If no data tables to process, return early
    if not data_tables:
        print("No data tables to process")
        return {
            'statusCode': 200,
            'tableMapping': {},
            'createdTables': [],
            'skippedTables': [],
            'failedTables': [],
            'summary': {
                'total': 0,
                'created': 0,
                'skipped': 0,
                'failed': 0
            }
        }

    connect_client, instance_id, region = create_destination_client(
        destination_instance_arn, cross_account_role_arn, external_id
    )

    created_tables = []
    skipped_tables = []
    failed_tables = []
    table_mapping = {}

    # Check if Data Tables API is available by checking if method exists
    if not hasattr(connect_client, 'list_data_tables'):
        print(f"Data Tables API not available in boto3 {boto3.__version__}")
        print("Data Tables requires boto3 >= 1.35.0")
        # Return all tables as failed with appropriate message
        for table in data_tables:
            failed_tables.append({
                'originalId': table.get('Id'),
                'status': 'failed',
                'error': f'Data Tables API not available in boto3 {boto3.__version__} - requires >= 1.35.0', 'errorCode': 'NotCreated'
            })

        return {
            'statusCode': 200,
            'tableMapping': {},
            'createdTables': [],
            'skippedTables': [],
            'failedTables': failed_tables,
            'summary': {
                'total': len(data_tables),
                'created': 0,
                'skipped': 0,
                'failed': len(data_tables)
            }
        }

    # Tables already in the target (summary key Id, not DataTableId), listed once before any create
    existing = ExistingIndex(connect_client, instance_id, 'dataTables')

    for table in data_tables:
        original_id = table.get('Id')
        table_name = table.get('Name')

        print(f"Processing table: {original_id}")
        print(f"  Source table: {len(table.get('Attributes', []) or [])} attributes, "
              f"{len(table.get('Values', []) or [])} values")

        try:
            if existing.error is not None:
                failed_tables.append(existing.lookup_failure(original_id))
                continue
            # Check if table already exists
            new_id = existing.get(table_name)
            if new_id:
                print(f"  Already exists: {new_id}")
                table_mapping[original_id] = new_id
                skipped_tables.append({
                    'originalId': original_id,
                    'newId': new_id,
                    'status': 'existing'
                })
                continue

            # Create the table with required parameters
            # API requires: Name, TimeZone, ValueLockLevel, Status
            # Status must be 'PUBLISHED' (only valid value)
            # ValueLockLevel: NONE, DATA_TABLE, PRIMARY_VALUE, ATTRIBUTE, VALUE
            # TimeZone: IANA timezone (e.g., 'UTC', 'America/New_York')
            create_params = {
                'InstanceId': instance_id,
                'Name': table_name,
                'TimeZone': table.get('TimeZone', 'UTC'),
                'ValueLockLevel': table.get('ValueLockLevel', 'NONE'),
                'Status': 'PUBLISHED'  # Only valid value per API docs
            }

            if table.get('Description'):
                create_params['Description'] = table['Description']

            if table.get('Tags'):
                create_params['Tags'] = table['Tags']

            print(f"  Creating table with {len(create_params)} parameters")

            response = connect_client.create_data_table(**create_params)
            new_table_id = response.get('Id')

            print(f"  Created table: {new_table_id}")
            table_mapping[original_id] = new_table_id

            # Create attributes if the API supports it
            attributes_created = 0
            for attr in table.get('Attributes', []):
                try:
                    # API requires: Name, ValueType
                    # Optional: Description, Primary, Validation
                    attr_params = {
                        'InstanceId': instance_id,
                        'DataTableId': new_table_id,
                        'Name': attr.get('Name'),
                        'ValueType': attr.get('ValueType', 'TEXT')
                    }

                    if attr.get('Description'):
                        attr_params['Description'] = attr['Description']

                    if attr.get('Primary'):
                        attr_params['Primary'] = attr['Primary']

                    if attr.get('Validation'):
                        attr_params['Validation'] = attr['Validation']

                    print(f"    Creating attribute: {redact(attr.get('Name'))} (Type: {attr.get('ValueType')})")
                    connect_client.create_data_table_attribute(**attr_params)
                    attributes_created += 1
                except Exception as attr_error:
                    reraise_if_fatal(attr_error)
                    print(f"  Error creating attribute {redact(attr.get('Name'))}: {describe_error(attr_error)}")
                    failed_tables.append(failure_entry(original_id, attr_error, 'table created, attribute not created'))

            print(f"  Created {attributes_created} attributes")

            # Create values (batch) if the API supports it
            values = table.get('Values', [])
            values_created = 0
            if values:
                # Batch create values (up to 25 at a time)
                batch_size = 25
                for i in range(0, len(values), batch_size):
                    batch = values[i:i+batch_size]
                    try:
                        batch_items = []
                        for val in batch:
                            item = {
                                'PrimaryValue': val.get('PrimaryValue'),
                                'AttributeValues': val.get('AttributeValues', {})
                            }
                            batch_items.append(item)

                        if batch_items:
                            connect_client.batch_create_data_table_value(
                                InstanceId=instance_id,
                                DataTableId=new_table_id,
                                Values=batch_items
                            )
                            values_created += len(batch_items)
                    except Exception as val_error:
                        reraise_if_fatal(val_error)
                        print(f"  Error creating values batch: {describe_error(val_error)}")
                        failed_tables.append(failure_entry(original_id, val_error, 'table created, values batch not created'))

            print(f"  Created {values_created} values")

            created_tables.append({
                'originalId': original_id,
                'newId': new_table_id,
                'status': 'created',
                'attributesCreated': attributes_created,
                'valuesCreated': values_created
            })

        except Exception as e:
            reraise_if_fatal(e)
            print(f"  Error: {describe_error(e)}")

            # Check if it's a duplicate (race condition), by error code only: one fresh lookup
            if error_code_of(e) in EXISTS_ERROR_CODES:
                new_id, failure = existing.recheck(table_name, e, original_id)
                if new_id:
                    table_mapping[original_id] = new_id
                    skipped_tables.append({
                        'originalId': original_id,
                        'newId': new_id,
                        'status': 'existing'
                    })
                else:
                    print(f"  Could not find existing table: {original_id} ({failure['errorCode']})")
                    failed_tables.append(failure)
            else:
                failed_tables.append({
                    'originalId': original_id,
                    'status': 'failed',
                    'error': describe_error(e), 'errorCode': error_code_of(e)
                })

    print(f"Created: {len(created_tables)}, Skipped: {len(skipped_tables)}, Failed: {len(failed_tables)}")

    return {
        'statusCode': 200,
        'tableMapping': table_mapping,
        'createdTables': created_tables,
        'skippedTables': skipped_tables,
        'failedTables': failed_tables,
        'summary': {
            'total': len(data_tables),
            'created': len(created_tables),
            'skipped': len(skipped_tables),
            'failed': len(failed_tables)
        }
    }
