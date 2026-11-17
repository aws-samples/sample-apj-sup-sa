"""
POST /list-phone-numbers: phone numbers claimed to the source instance, for
the (opt in) phone number step in the UI. Source Region from the ARN.
"""
import json
import os

from botocore.exceptions import ClientError

from region_utils import THROTTLING_CODES, MigrationStepError, create_connect_client_from_arn, reraise_if_fatal
from region_utils import describe_error, error_text


def cors_headers():
    return {
        'Access-Control-Allow-Origin': os.environ['ALLOWED_ORIGIN'],
        'Access-Control-Allow-Headers': 'Content-Type,Authorization',
        'Access-Control-Allow-Methods': 'POST,OPTIONS',
    }


def response(status, body):
    return {'statusCode': status, 'headers': cors_headers(), 'body': json.dumps(body)}


def lambda_handler(event, context):
    if event.get('httpMethod') == 'OPTIONS':
        return response(200, {})
    try:
        body = json.loads(event['body']) if event.get('body') else {}
    except (json.JSONDecodeError, TypeError):
        return response(400, {'error': 'Request body must be JSON'})
    source_arn = (body.get('sourceInstanceId') or '').strip()
    if not source_arn:
        return response(400, {'error': 'Missing sourceInstanceId'})

    try:
        client, instance_id, _ = create_connect_client_from_arn(source_arn)
        numbers = []
        try:
            for page in client.get_paginator('list_phone_numbers_v2').paginate(InstanceId=instance_id):
                for pn in page['ListPhoneNumbersSummaryList']:
                    numbers.append({
                        'PhoneNumberId': pn['PhoneNumberId'],
                        'PhoneNumberArn': pn['PhoneNumberArn'],
                        'PhoneNumber': pn['PhoneNumber'],
                        'PhoneNumberCountryCode': pn['PhoneNumberCountryCode'],
                        'PhoneNumberType': pn['PhoneNumberType'],
                        'PhoneNumberDescription': pn.get('PhoneNumberDescription', ''),
                        'TargetArn': pn['TargetArn'],
                        'InstanceId': pn.get('InstanceId', instance_id),
                    })
        except ClientError as err:
            reraise_if_fatal(err, resource=source_arn)
            raise
        return response(200, {'phoneNumbers': numbers, 'count': len(numbers)})
    except MigrationStepError as err:
        return response(429 if err.code in THROTTLING_CODES else 403, {'error': error_text(err), 'action': err.action})
    except (ClientError, ValueError) as err:
        print(f"Error listing phone numbers: {describe_error(err)}")
        return response(500, {'error': error_text(err)})
