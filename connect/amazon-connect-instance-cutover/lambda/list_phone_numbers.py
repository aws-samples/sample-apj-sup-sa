"""
List phone numbers claimed to the source Connect instance.

Uses the tool role in the Region taken from the source instance ARN (it
used the Lambda's default Region before, which only worked by coincidence).
"""
import json

from botocore.exceptions import ClientError

from region_utils import create_connect_client_from_arn, reraise_if_fatal
from region_utils import step_handler


@step_handler
def lambda_handler(event, context):
    source_instance_arn = event['sourceInstanceId']
    connect_client, source_instance_id, source_region = create_connect_client_from_arn(source_instance_arn)
    print(f"Listing phone numbers from instance: {source_instance_id} in {source_region}")

    phone_numbers = []
    try:
        paginator = connect_client.get_paginator('list_phone_numbers_v2')
        for page in paginator.paginate(InstanceId=source_instance_id):
            for phone_number in page['ListPhoneNumbersSummaryList']:
                # IDs, ARNs, country and type only: the number itself and its description
                # never enter the step output or the execution history.
                phone_numbers.append({
                    'PhoneNumberId': phone_number['PhoneNumberId'],
                    'PhoneNumberArn': phone_number['PhoneNumberArn'],
                    'PhoneNumberCountryCode': phone_number['PhoneNumberCountryCode'],
                    'PhoneNumberType': phone_number['PhoneNumberType'],
                    'TargetArn': phone_number['TargetArn'],
                    'InstanceId': phone_number.get('InstanceId', source_instance_id)
                })
    except ClientError as e:
        reraise_if_fatal(e, resource=source_instance_arn)
        raise

    print(f"Found {len(phone_numbers)} phone numbers in source instance")
    return {
        'statusCode': 200,
        'body': json.dumps({'phoneNumbers': phone_numbers, 'count': len(phone_numbers)})
    }
