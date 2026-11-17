"""
Associate Lambda functions with the destination Connect instance.

This function never writes to the source account. Earlier versions called
lambda:RemovePermission and lambda:AddPermission on the source functions so
that a target instance in another account or Region could invoke them. That
was removed on purpose:

* it changes resource policies in the source account with the tool's role,
  which would need write access there (the tool role is read only);
* it grants a foreign account invoke rights on source code without review;
* a target flow calling a function in another Region adds latency on every
  contact and can break data residency requirements.

The supported pattern is to deploy each function in the target account and
Region and associate it with the target instance. For each function
associated with the source instance this step:

1. reports a match when a function with the same name is already associated
   with the target instance (contact flows are rewritten to it);
2. associates the function when it is already in the target account and
   Region (same account, same Region migration);
3. otherwise lists it in manualFollowUp.
"""
from botocore.exceptions import ClientError

from arn_mapping import ARN_PATTERN, LAMBDA_RESOURCE, build_lambda_map
from region_utils import (
    create_destination_client,
    extract_account_id_from_arn,
    reraise_if_fatal,
)
from region_utils import redact
from region_utils import describe_error, error_code_of
from region_utils import resolve_external_id
from region_utils import step_handler


def parse_lambda_arn(lambda_arn):
    """Return (region, account, function_name) or None."""
    match = ARN_PATTERN.fullmatch(lambda_arn or '')
    resource = LAMBDA_RESOURCE.match(match.group('resource')) if match and match.group('service') == 'lambda' else None
    if not resource:
        return None
    return match.group('region'), match.group('account'), resource.group('name')


@step_handler
def lambda_handler(event, context):
    destination_instance_arn = event['destinationInstanceId']
    lambda_functions = event.get('lambdaFunctions', [])
    cross_account_role_arn = event.get('crossAccountRoleArn')
    external_id = resolve_external_id(event)  # never logged

    print(f"Destination instance: {destination_instance_arn}")
    print(f"Lambda functions to process: {len(lambda_functions)}")

    connect_client, destination_instance_id, destination_region = create_destination_client(
        destination_instance_arn, cross_account_role_arn, external_id
    )
    destination_account = extract_account_id_from_arn(destination_instance_arn)
    target_by_name = build_lambda_map(connect_client, destination_instance_id)

    associated, matched, skipped, failed, follow_up = [], [], [], [], []

    for lambda_arn in lambda_functions:
        parsed = parse_lambda_arn(lambda_arn)
        if not parsed:
            failed.append({'arn': lambda_arn, 'error': 'Unrecognised Lambda ARN', 'errorCode': 'NotCreated'})
            continue
        region, account, name = parsed

        if name in target_by_name:
            matched.append({'arn': lambda_arn, 'targetArn': target_by_name[name]})
            print(f"  {name}: already associated with the target as {redact(target_by_name[name])}")
            continue

        if region == destination_region and account == destination_account:
            try:
                connect_client.associate_lambda_function(InstanceId=destination_instance_id,
                                                         FunctionArn=lambda_arn)
                associated.append(lambda_arn)
                print(f"  Associated: {lambda_arn}")
            except ClientError as e:
                reraise_if_fatal(e, resource=lambda_arn)
                if e.response['Error']['Code'] == 'ResourceConflictException':
                    skipped.append({'arn': lambda_arn, 'reason': 'Already associated'})
                else:
                    failed.append({'arn': lambda_arn, 'error': describe_error(e), 'errorCode': error_code_of(e)})
            continue

        # The ARN already carries the function name; no separate name key.
        follow_up.append({
            'type': 'lambda',
            'source': lambda_arn,
            'action': (f"Deploy function '{name}' in account {destination_account} Region "
                       f"{destination_region} and associate it with the target instance. Flows that "
                       "use it reference a MIGRATION-PLACEHOLDER function until then."),
        })

    print(f"Associated: {len(associated)}, matched: {len(matched)}, manual follow up: {len(follow_up)}, "
          f"failed: {len(failed)}")

    return {
        'statusCode': 200,
        'associated': associated,
        'matched': matched,
        'skipped': skipped,
        'failed': failed,
        'manualFollowUp': follow_up,
        'summary': {
            'total': len(lambda_functions),
            'associated': len(associated),
            'matched': len(matched),
            'manualFollowUp': len(follow_up),
            'skipped': len(skipped),
            'failed': len(failed),
        },
    }
