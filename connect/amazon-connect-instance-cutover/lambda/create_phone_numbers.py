"""
Phone number step. Moving is disabled by default.

How a number gets from the source to the target instance depends on the
accounts and Regions (Administrator Guide, "Move a phone number across
instances"):

* same account and same Region: a self move with ListPhoneNumbersV2 and
  UpdatePhoneNumber (PhoneNumberSelfMove). This is the only path the tool
  performs, and only with the explicit opt in below;
* different account in the same Region, or different Region in the same
  account: AWS Support moves the numbers (PhoneNumberSupportMove);
* different account and different Region: porting (PhoneNumberPorting).

Moving a number is destructive for the source instance (the number stops
ringing there), so the self move is behind an explicit opt in:

* the Lambda environment variable ENABLE_PHONE_NUMBER_MOVE must be "true"
  (CloudFormation parameter EnablePhoneNumberMove on the Lambda stack), and
* the target writer role must allow connect:UpdatePhoneNumber (parameter
  AllowPhoneNumberMove on cloudformation/target-role.yaml).

Every number the step does not move is listed in one manualFollowUp entry
per path, with the reason code of the path that applies, the count and the
phone number IDs. The numbers themselves never enter the step output or the
execution history. When enabled, the move uses the target role credentials
(AssumeRole with ExternalId) in the target Region.
"""
import os

from botocore.exceptions import ClientError

from region_utils import (
    create_connect_client_from_arn,
    create_destination_client,
    extract_account_id_from_arn,
    extract_region_from_arn,
    reraise_if_fatal,
)
from region_utils import error_code_of
from region_utils import resolve_external_id
from region_utils import step_handler

SELF_MOVE, SUPPORT_MOVE, PORTING = 'PhoneNumberSelfMove', 'PhoneNumberSupportMove', 'PhoneNumberPorting'
OUTBOUND_CALLER_ID = ('The outbound caller ID on the target queues has to be set again after the move '
                      '(the migration report lists queues whose caller ID number was dropped).')
PATH_ACTIONS = {
    SELF_MOVE: ('Same account and Region: move the numbers yourself with ListPhoneNumbersV2 and UpdatePhoneNumber. '
                'The phone number step of this tool does that when the deployment opts in '
                '(--enable-phone-number-move and --allow-phone-number-move). A moved number stops working on the '
                'source instance, and calls to it can fail while it moves. ' + OUTBOUND_CALLER_ID),
    SUPPORT_MOVE: ('Different account or different Region: AWS Support moves the numbers. Open one phone number '
                   'migration case from each account involved (Service: Connect (Number Management), Category: Phone '
                   'Number Migration). Moves are scheduled Monday to Friday and take about 20 to 30 minutes per '
                   'number; calls to a number can fail while it moves. At migration time a number must not be the '
                   'outbound caller ID on any queue. ' + OUTBOUND_CALLER_ID),
    PORTING: ('Different account and different Region: the numbers cannot be moved between the instances; port them '
              'to the target instance with a porting request from the target account. Calls to a number can fail '
              'while it ports. ' + OUTBOUND_CALLER_ID),
}
MOVE_FAILED = 'UpdatePhoneNumber failed for these numbers; the error codes are in notMigrated. '


def move_enabled():
    return os.environ.get('ENABLE_PHONE_NUMBER_MOVE', 'false').strip().lower() == 'true'


def phone_number_path(source_instance_arn, destination_instance_arn):
    """Reason code of the path that applies, from the source and target account and Region."""
    same_account = extract_account_id_from_arn(source_instance_arn) == extract_account_id_from_arn(destination_instance_arn)
    same_region = extract_region_from_arn(source_instance_arn) == extract_region_from_arn(destination_instance_arn)
    if same_account and same_region:
        return SELF_MOVE
    if same_account or same_region:
        return SUPPORT_MOVE
    return PORTING


def follow_up_for(numbers, reason_code, prefix=''):
    """One entry for the numbers not moved: reason code, count and IDs only, never the numbers."""
    if not numbers:
        return []
    return [{
        'type': 'phoneNumber',
        'reasonCode': reason_code,
        'count': len(numbers),
        'phoneNumberIds': [n.get('PhoneNumberId') for n in numbers],
        'action': prefix + PATH_ACTIONS[reason_code],
    }]


def ids_only(numbers):
    """Keep only the ID and ARN of each selected number (the execution input may hold more)."""
    return [{'PhoneNumberId': n.get('PhoneNumberId'), 'PhoneNumberArn': n.get('PhoneNumberArn')}
            for n in numbers if isinstance(n, dict) and n.get('PhoneNumberId')]


def get_all_phone_numbers_from_source(source_instance_arn):
    """List the numbers claimed to the source instance (source credentials, source Region): IDs and ARNs only."""
    client, source_instance_id, _ = create_connect_client_from_arn(source_instance_arn)
    numbers = []
    try:
        for page in client.get_paginator('list_phone_numbers_v2').paginate(InstanceId=source_instance_id):
            for pn in page['ListPhoneNumbersSummaryList']:
                numbers.append({'PhoneNumberId': pn['PhoneNumberId'], 'PhoneNumberArn': pn['PhoneNumberArn']})
    except ClientError as e:
        reraise_if_fatal(e, resource=source_instance_arn)
        raise
    return numbers


@step_handler
def lambda_handler(event, context):
    source_instance_arn = event['sourceInstanceId']
    destination_instance_arn = event['destinationInstanceId']
    cross_account_role_arn = event.get('crossAccountRoleArn')
    external_id = resolve_external_id(event)  # never logged

    phone_numbers = ids_only(event.get('selectedPhoneNumbers') or event.get('phoneNumbers') or [])
    if not phone_numbers:
        phone_numbers = get_all_phone_numbers_from_source(source_instance_arn)
    path_code = phone_number_path(source_instance_arn, destination_instance_arn)
    print(f"Phone numbers in scope: {len(phone_numbers)}, path {path_code}")

    base = {'statusCode': 200, 'migrated': [], 'notMigrated': [], 'totalProcessed': len(phone_numbers),
            'path': path_code}

    if path_code != SELF_MOVE:
        print("Source and target differ in account or Region; phone numbers are not moved by the tool")
        return {**base, 'status': 'NOT_SUPPORTED', 'manualFollowUp': follow_up_for(phone_numbers, path_code)}

    if not move_enabled():
        print("Phone number move is disabled (ENABLE_PHONE_NUMBER_MOVE is not 'true'); nothing was moved")
        return {**base, 'status': 'DISABLED', 'manualFollowUp': follow_up_for(phone_numbers, path_code)}

    if not cross_account_role_arn:
        raise ValueError('Moving phone numbers requires the target role (crossAccountRoleArn and externalId).')

    # Target credentials, target Region.
    connect_client, _, _ = create_destination_client(destination_instance_arn, cross_account_role_arn, external_id)

    migrated, not_migrated = [], []
    for pn in phone_numbers:
        try:
            response = connect_client.update_phone_number(
                PhoneNumberId=pn['PhoneNumberId'],
                TargetArn=destination_instance_arn,
            )
            migrated.append({'phoneNumberId': pn['PhoneNumberId'], 'newArn': response['PhoneNumberArn']})
            print(f"Moved phone number {pn['PhoneNumberId']} to the target instance")
        except ClientError as e:
            reraise_if_fatal(e, resource=pn.get('PhoneNumberArn') or pn['PhoneNumberId'])
            not_migrated.append({'phoneNumberId': pn['PhoneNumberId'], 'reason': 'UpdatePhoneNumber failed',
                                 'errorCode': error_code_of(e)})

    failed_ids = {x['phoneNumberId'] for x in not_migrated}
    return {**base, 'status': 'COMPLETED', 'migrated': migrated, 'notMigrated': not_migrated,
            'manualFollowUp': follow_up_for([n for n in phone_numbers if n['PhoneNumberId'] in failed_ids],
                                            path_code, MOVE_FAILED)}
