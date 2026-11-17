"""
Create contact flow modules in the destination Connect instance.

Module content goes through the same ARN rewriter as contact flows
(arn_mapping.ArnRewriter), so Lambda, Lex, AWS managed and instance ARNs
are handled the same way. There is no blind source to target prefix
replacement: unmapped references are reported in manualFollowUp.
"""
from botocore.exceptions import ClientError

from arn_mapping import ExistingIndex, build_rewriter, merge_follow_up
from region_utils import (
    EXISTS_ERROR_CODES,
    create_connect_client_from_arn,
    create_destination_client,
    create_destination_session,
    reraise_if_fatal,
)
from region_utils import describe_error, redact
from region_utils import error_code_of
from region_utils import resolve_external_id
from region_utils import step_handler


@step_handler
def lambda_handler(event, context):
    """
    Create contact flow modules in destination Connect instance.
    Replaces source ARNs with destination ARNs by looking up resources by name.
    """
    source_instance_arn = event['sourceInstanceId']
    destination_instance_arn = event['destinationInstanceId']
    contact_flow_modules = event.get('contactFlowModules', [])
    cross_account_role_arn = event.get('crossAccountRoleArn')
    external_id = resolve_external_id(event)  # never logged

    print(f"Source: {source_instance_arn}")
    print(f"Destination: {destination_instance_arn}")
    print(f"Modules to process: {len(contact_flow_modules)}")

    dest_session = create_destination_session(destination_instance_arn, cross_account_role_arn, external_id)
    dest_client, dest_instance_id, _ = create_destination_client(destination_instance_arn, session=dest_session)
    source_client, source_instance_id, _ = create_connect_client_from_arn(source_instance_arn)

    rewriter = build_rewriter(source_instance_arn, destination_instance_arn, source_client, dest_client, dest_session)
    # Modules already in the target, listed once before any create.
    existing = ExistingIndex(dest_client, dest_instance_id, 'modules')

    created_modules = []
    skipped_modules = []
    module_mapping = {}
    follow_up = {}

    for module in contact_flow_modules:
        original_id = module.get('Id')
        module_name = module.get('Name', 'Unknown')
        try:
            print(f"Processing: {redact(module_name)}")

            if existing.error is not None:
                skipped_modules.append(existing.lookup_failure(original_id))
                continue
            existing_id = existing.get(module_name)
            if existing_id:
                print(f"  Already exists: {existing_id}")
                module_mapping[original_id] = existing_id
                skipped_modules.append({'originalId': original_id, 'newId': existing_id,
                                        'status': 'existing'})
                continue

            module_content = module.get('Content')
            if not module_content:
                try:
                    detail = source_client.describe_contact_flow_module(
                        InstanceId=source_instance_id, ContactFlowModuleId=original_id
                    )
                    module_content = detail['ContactFlowModule'].get('Content')
                except ClientError as e:
                    reraise_if_fatal(e, resource=f"flow module {original_id}")
                    print(f"  Error fetching content: {describe_error(e)}")
                    skipped_modules.append({'originalId': original_id,
                                            'status': 'failed', 'error': describe_error(e), 'errorCode': error_code_of(e)})
                    continue

            if not module_content:
                skipped_modules.append({'originalId': original_id,
                                        'status': 'failed', 'error': 'No content', 'errorCode': 'NotCreated'})
                continue

            updated_content, report = rewriter.rewrite(module_content)
            merge_follow_up(follow_up, original_id, report)

            create_params = {
                'InstanceId': dest_instance_id,
                'Name': module_name,
                'Content': updated_content
            }
            if module.get('Description'):
                create_params['Description'] = module['Description']
            if module.get('Tags'):
                create_params['Tags'] = module['Tags']

            response = dest_client.create_contact_flow_module(**create_params)
            new_id = response['Id']
            module_mapping[original_id] = new_id
            created_modules.append({
                'originalId': original_id,
                'newId': new_id,
                'status': 'created_with_placeholders' if report['placeholders'] else 'created',
            })
            print(f"  Created: {new_id}")

        except ClientError as e:
            reraise_if_fatal(e, resource=f"flow module {original_id}")
            if error_code_of(e) in EXISTS_ERROR_CODES:
                existing_id, failure = existing.recheck(module_name, e, original_id)
                if existing_id:
                    module_mapping[original_id] = existing_id
                    skipped_modules.append({'originalId': original_id, 'newId': existing_id,
                                            'status': 'existing'})
                else:
                    skipped_modules.append(failure)
            else:
                print(f"  Error: {describe_error(e)}")
                skipped_modules.append({'originalId': original_id,
                                        'status': 'failed', 'error': describe_error(e), 'errorCode': error_code_of(e)})
        except Exception as e:
            reraise_if_fatal(e)
            print(f"  Error: {describe_error(e)}")
            skipped_modules.append({'originalId': original_id,
                                    'status': 'failed', 'error': describe_error(e), 'errorCode': error_code_of(e)})

    print(f"Created: {len(created_modules)}, Skipped/Failed: {len(skipped_modules)}, "
          f"Manual follow up: {len(follow_up)}")

    return {
        'statusCode': 200,
        'moduleMapping': module_mapping,
        'createdModules': created_modules,
        'skippedModules': skipped_modules,
        'manualFollowUp': list(follow_up.values()),
    }
