"""
POST /list-quick-connects: country calling prefixes used by PHONE_NUMBER
quick connects in the source instance, so the UI can warn about outbound
calling allow lists in the target before migrating quick connects.
"""
import json
import os

from botocore.exceptions import ClientError

from region_utils import THROTTLING_CODES, MigrationStepError, create_connect_client_from_arn, reraise_if_fatal
from region_utils import describe_error, error_text

COUNTRY_CODES = {
    '1', '7', '20', '27', '30', '31', '32', '33', '34', '36', '39', '40', '41', '43', '44', '45', '46', '47',
    '48', '49', '51', '52', '53', '54', '55', '56', '57', '58', '60', '61', '62', '63', '64', '65', '66', '81',
    '82', '84', '86', '90', '91', '92', '93', '94', '95', '98', '212', '213', '234', '351', '352', '353', '354',
    '358', '370', '371', '372', '380', '420', '421', '852', '886', '966', '971', '972', '974',
}


def extract_phone_prefix(phone_number):
    if not phone_number or not phone_number.startswith('+'):
        return None
    digits = phone_number[1:]
    for length in (3, 2, 1):
        if len(digits) >= length and digits[:length] in COUNTRY_CODES:
            return '+' + digits[:length]
    return '+' + digits[:3] if digits else None


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
        prefixes = set()
        try:
            for page in client.get_paginator('list_quick_connects').paginate(
                    InstanceId=instance_id, QuickConnectTypes=['PHONE_NUMBER']):
                for qc in page['QuickConnectSummaryList']:
                    detail = client.describe_quick_connect(InstanceId=instance_id, QuickConnectId=qc['Id'])
                    config = detail['QuickConnect'].get('QuickConnectConfig', {})
                    prefix = extract_phone_prefix(config.get('PhoneConfig', {}).get('PhoneNumber', ''))
                    if prefix:
                        prefixes.add(prefix)
        except ClientError as err:
            reraise_if_fatal(err, resource=source_arn)
            raise
        return response(200, {'phonePrefixes': sorted(prefixes)})
    except MigrationStepError as err:
        return response(429 if err.code in THROTTLING_CODES else 403, {'error': error_text(err), 'action': err.action})
    except (ClientError, ValueError) as err:
        print(f"Error listing quick connects: {describe_error(err)}")
        return response(500, {'error': error_text(err)})
