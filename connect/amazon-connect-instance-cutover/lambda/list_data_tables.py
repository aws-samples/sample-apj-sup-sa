"""
Lambda function to list Data Tables from source Connect instance.
Lists tables, their attributes, and optionally their values.

Note: Data Tables API requires boto3 >= 1.35.0. If not available,
this function will return empty results.
"""
import boto3
from region_utils import create_connect_client_from_arn
from region_utils import reraise_if_fatal
from region_utils import describe_error, error_code_of, redact
from region_utils import step_handler

@step_handler
def lambda_handler(event, context):
    """
    Lists all data tables from the source Connect instance.
    Returns table metadata, attributes, and primary values.
    """
    source_instance_arn = event.get('sourceInstanceId')
    include_values = event.get('includeValues', True)

    print(f"Source instance: {source_instance_arn}")
    print(f"Include values: {include_values}")
    print(f"Boto3 version: {boto3.__version__}")

    connect_client, instance_id, region = create_connect_client_from_arn(source_instance_arn)

    data_tables = []

    # Check if Data Tables API is available
    if not hasattr(connect_client, 'list_data_tables'):
        print(f"Data Tables API not available in boto3 {boto3.__version__}")
        print("Data Tables requires boto3 >= 1.35.0")
        return {
            'statusCode': 200,
            'dataTables': [],
            'count': 0,
            'message': f'Data Tables API not available in boto3 {boto3.__version__}'
        }

    try:
        # List all data tables using manual pagination
        next_token = None
        while True:
            if next_token:
                response = connect_client.list_data_tables(InstanceId=instance_id, NextToken=next_token)
            else:
                response = connect_client.list_data_tables(InstanceId=instance_id)

            for table_summary in response.get('DataTableSummaryList', []):
                # API returns 'Id' not 'DataTableId' in the summary
                table_id = table_summary.get('Id')
                table_name = table_summary.get('Name')

                print(f"Processing table: {redact(table_name)} (Id: {table_id})")

                # Get full table details using describe_data_table
                table_info = {}
                try:
                    table_detail = connect_client.describe_data_table(
                        InstanceId=instance_id,
                        DataTableId=table_id
                    )
                    table_info = table_detail.get('DataTable', {})
                    print(f"  Table details: TimeZone={table_info.get('TimeZone')}, Status={table_info.get('Status')}, ValueLockLevel={table_info.get('ValueLockLevel')}")
                except Exception as e:
                    reraise_if_fatal(e)
                    print(f"  Error describing table: {describe_error(e)}")
                    table_info = table_summary

                # Get table attributes
                attributes = []
                try:
                    attr_response = connect_client.list_data_table_attributes(
                        InstanceId=instance_id,
                        DataTableId=table_id
                    )
                    # API returns 'Attributes' not 'DataTableAttributeSummaryList'
                    attr_list = attr_response.get('Attributes', [])
                    print(f"  Found {len(attr_list)} attributes from API")
                    for attr in attr_list:
                        # Each attribute has: AttributeId, Name, ValueType, Description, Primary, etc.
                        attributes.append({
                            'AttributeId': attr.get('AttributeId'),
                            'Name': attr.get('Name'),
                            'ValueType': attr.get('ValueType', 'TEXT'),
                            'Description': attr.get('Description', ''),
                            'Primary': attr.get('Primary', False),
                            'Validation': attr.get('Validation', {})
                        })
                        print(f"    Attribute: {redact(attr.get('Name'))} (Type: {attr.get('ValueType')}, Primary: {attr.get('Primary')})")
                except Exception as e:
                    reraise_if_fatal(e)
                    print(f"  Error listing attributes: {describe_error(e)}")

                # Get table values (primary keys and their data)
                values = []
                if include_values:
                    try:
                        # List primary values first
                        primary_response = connect_client.list_data_table_primary_values(
                            InstanceId=instance_id,
                            DataTableId=table_id
                        )
                        primary_values = primary_response.get('PrimaryValues', [])

                        if primary_values:
                            # Batch describe values
                            try:
                                values_response = connect_client.batch_describe_data_table_value(
                                    InstanceId=instance_id,
                                    DataTableId=table_id,
                                    PrimaryValues=primary_values[:100]  # Limit to 100
                                )
                                values = values_response.get('DataTableValues', [])
                            except Exception as e:
                                reraise_if_fatal(e)
                                print(f"  Error batch describing values: {describe_error(e)}")
                                # Fallback: list values
                                try:
                                    list_values_response = connect_client.list_data_table_values(
                                        InstanceId=instance_id,
                                        DataTableId=table_id
                                    )
                                    values = list_values_response.get('DataTableValueSummaryList', [])
                                except Exception as e2:
                                    reraise_if_fatal(e2)
                                    print(f"  Error listing values: {describe_error(e2)}")
                    except Exception as e:
                        reraise_if_fatal(e)
                        print(f"  Error getting values: {describe_error(e)}")

                # Build table object with all required fields for create
                data_tables.append({
                    'Id': table_id,
                    'Name': table_name,
                    'Description': table_info.get('Description', ''),
                    'TimeZone': table_info.get('TimeZone', 'UTC'),
                    'ValueLockLevel': table_info.get('ValueLockLevel', 'NONE'),
                    'Status': table_info.get('Status', 'ACTIVE'),
                    'Attributes': attributes,
                    'Values': values,
                    'Tags': table_info.get('Tags', {})
                })

                print(f"  Found {len(attributes)} attributes, {len(values)} values")

            next_token = response.get('NextToken')
            if not next_token:
                break

        print(f"Total data tables found: {len(data_tables)}")

    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error listing data tables: {describe_error(e)}")
        # Data tables might not be available in this region/instance (matched on the error code only)
        if error_code_of(e) == 'UnknownOperationException':
            print("Data tables feature may not be available for this instance")

    return {
        'statusCode': 200,
        'dataTables': data_tables,
        'count': len(data_tables)
    }
