from region_utils import create_destination_client
import json
from botocore.exceptions import ClientError
from arn_mapping import ExistingIndex
from region_utils import reraise_if_fatal
from region_utils import EXISTS_ERROR_CODES, describe_error, error_code, failure_entry
from region_utils import resolve_external_id
from region_utils import error_code_of, feature_skipped_result, is_feature_unavailable
from region_utils import step_handler

@step_handler
def lambda_handler(event, context):
    """Lambda function to create Connect predefined attributes from ListPredefinedAttributes response
    Supports cross-account migration with crossAccountRoleArn
    """

    target_instance_arn = event['targetInstanceId']  # Now expects ARN
    cross_account_role_arn = event.get('crossAccountRoleArn')  # Optional for cross-account
    external_id = resolve_external_id(event)  # never logged

    # Create region-specific Connect client for target instance (supports cross-account)
    print(f"Creating Connect client from target instance ARN: {target_instance_arn}")
    if cross_account_role_arn:
        print(f"Cross-account migration enabled with role: {cross_account_role_arn}")
    connect, target_instance_id, target_region = create_destination_client(
        target_instance_arn, cross_account_role_arn, external_id
    )

    try:
        # Get predefined attributes from ListPredefinedAttributes response
        attributes = event['predefinedAttributes']

        # Create predefined attributes in target instance
        migrated = 0
        skipped = 0
        failed_items = []
        # Attributes already in the target, listed once before any create
        existing = ExistingIndex(connect, target_instance_id, 'predefinedAttributes')
        if existing.error is not None and is_feature_unavailable(existing.error):
            return feature_skipped_result('predefinedAttributes', target_region, 'target',
                                          error_code_of(existing.error), count=len(attributes))

        for attr in attributes:
            print(f"Processing predefined attribute ({len(attr.get('Values', {}) or {})} value sets)")
            if existing.error is not None:
                failed_items.append(existing.lookup_failure(attr.get('Name')))
                continue
            if existing.get(attr.get('Name')):
                print("Predefined attribute already exists in the target, skipping")
                skipped += 1
                continue
            try:
                # Build create parameters
                create_params = {
                    'InstanceId': target_instance_id,
                    'Name': attr['Name'],
                    'Values': attr['Values']
                }

                # Add Purposes if present
                if attr.get('Purposes'):
                    create_params['Purposes'] = attr['Purposes']

                # Add AttributeConfiguration if present
                if attr.get('AttributeConfiguration'):
                    # Only include EnableValueValidationOnAssociation (IsReadOnly is read-only)
                    attr_config = {}
                    if 'EnableValueValidationOnAssociation' in attr['AttributeConfiguration']:
                        attr_config['EnableValueValidationOnAssociation'] = attr['AttributeConfiguration']['EnableValueValidationOnAssociation']
                    if attr_config:
                        create_params['AttributeConfiguration'] = attr_config

                connect.create_predefined_attribute(**create_params)
                print("Created predefined attribute")
                migrated += 1
            except ClientError as e:
                reraise_if_fatal(e)
                # Predefined attributes have no ID: the name is their only identifier, so a
                # failed one is reported by name (tests/test_log_hygiene.py allows exactly this).
                found, failure = None, failure_entry(attr.get('Name'), e)
                if error_code(e) in EXISTS_ERROR_CODES:
                    found, failure = existing.recheck(attr.get('Name'), e, attr.get('Name'))
                if found:
                    print("Predefined attribute already exists in the target, skipping")
                    skipped += 1
                else:
                    print(f"Error creating predefined attribute: {describe_error(e)}")
                    failed_items.append(failure)
            except Exception as e:
                reraise_if_fatal(e)
                print(f"Error creating predefined attribute: {describe_error(e)}")
                # Predefined attributes have no ID: the name is their only identifier, so a
                # failed one is reported by name (tests/test_log_hygiene.py allows exactly this).
                failed_items.append(failure_entry(attr.get('Name'), e))

        # Counts only for created and existing attributes: no names in the step output.
        return {
            'statusCode': 200,
            'body': json.dumps({
                'message': f'Migrated {migrated} predefined attributes',
                'migratedCount': migrated,
                'skippedCount': skipped,
                'failedItems': failed_items
            }),
            'failedItems': failed_items
        }

    except Exception as e:
        reraise_if_fatal(e)
        return {
            'statusCode': 500,
            'body': json.dumps({'error': describe_error(e)})
        }
