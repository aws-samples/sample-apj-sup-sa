"""
Lambda function to create AI Guardrails in destination Connect instance.
Uses the Amazon Connect Customer AI agents (qconnect) APIs.
"""
from datetime import datetime
from region_utils import BOTO_CONFIG, create_destination_session, extract_region_from_arn
from botocore.exceptions import ClientError
from region_utils import reraise_if_fatal, error_code_of
from region_utils import describe_error, failure_entry, redact
from region_utils import resolve_external_id
from region_utils import FeatureUnavailable, feature_skipped_result, is_feature_unavailable, raise_if_feature_unavailable
from region_utils import step_handler

def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")

def get_assistant_id(destination_arn, cross_account_role_arn=None, external_id=None):
    """
    Get the AI agents assistant ID in the target account and Region.
    Assumes the target role (with ExternalId) through region_utils.
    """
    region = extract_region_from_arn(destination_arn)
    session = create_destination_session(destination_arn, cross_account_role_arn, external_id)
    qconnect_client = session.client('qconnect', region_name=region, config=BOTO_CONFIG)

    try:
        paginator = qconnect_client.get_paginator('list_assistants')

        for page in paginator.paginate():
            for assistant in page.get('assistantSummaries', []):
                assistant_id = assistant['assistantId']
                print(f"Found assistant: {redact(assistant['name'])} ({assistant_id})")
                return assistant_id, qconnect_client

        print("No AI agents assistant found")
        return None, qconnect_client

    except Exception as e:
        reraise_if_fatal(e)
        raise_if_feature_unavailable(e, qconnect_client)
        print(f"Error listing assistants: {describe_error(e)}")
        return None, qconnect_client

def find_existing_ai_guardrail(qconnect_client, assistant_id, guardrail_name):
    """Find an existing AI Guardrail by name"""
    try:
        paginator = qconnect_client.get_paginator('list_ai_guardrails')

        for page in paginator.paginate(assistantId=assistant_id):
            for guardrail in page.get('aiGuardrailSummaries', []):
                if guardrail['name'] == guardrail_name:
                    return guardrail['aiGuardrailId']

        return None
    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error finding existing AI Guardrail '{redact(guardrail_name)}': {describe_error(e)}")
        return None

@step_handler
def lambda_handler(event, context):
    """
    Creates AI Guardrails in destination Connect instance's AI agents assistant
    """
    destination_instance_arn = event['destinationInstanceId']
    ai_guardrails = event.get('aiGuardrails', [])
    cross_account_role_arn = event.get('crossAccountRoleArn')
    external_id = resolve_external_id(event)  # never logged

    # Extract destination region from ARN
    destination_region = destination_instance_arn.split(':')[3]

    print(f"Creating AI Guardrails in region: {destination_region}")
    if cross_account_role_arn:
        print(f"Cross-account migration enabled with role: {cross_account_role_arn}")

    # Get the assistant ID in destination
    try:
        assistant_id, qconnect_client = get_assistant_id(destination_instance_arn, cross_account_role_arn,
                                                         external_id)
    except FeatureUnavailable as e:
        return feature_skipped_result('aiGuardrails', destination_region, 'target', e.code,
                                      count=len(ai_guardrails), aiGuardrailMapping={})

    if not assistant_id:
        return {
            'statusCode': 400,
            'error': 'No AI agents assistant found in destination instance'
        }

    guardrail_mapping = {}
    created_guardrails = []
    skipped_guardrails = []
    failed_guardrails = []

    try:
        print(f"Creating AI Guardrails in assistant: {assistant_id}")

        for guardrail in ai_guardrails:
            try:
                guardrail_name = guardrail['name']
                original_guardrail_id = guardrail['aiGuardrailId']

                print(f"Processing AI Guardrail: {redact(guardrail_name)}")

                # Check if guardrail already exists
                existing_guardrail_id = find_existing_ai_guardrail(qconnect_client, assistant_id, guardrail_name)

                if existing_guardrail_id:
                    print(f"AI Guardrail '{redact(guardrail_name)}' already exists: {existing_guardrail_id}")
                    guardrail_mapping[original_guardrail_id] = existing_guardrail_id
                    skipped_guardrails.append({
                        'originalId': original_guardrail_id,
                        'existingId': existing_guardrail_id,
                        'status': 'existing'
                    })
                    continue

                # Create the AI Guardrail
                create_params = {
                    'assistantId': assistant_id,
                    'name': guardrail_name,
                    'blockedInputMessaging': guardrail.get('blockedInputMessaging', 'Sorry, I cannot process this request.'),
                    'blockedOutputsMessaging': guardrail.get('blockedOutputsMessaging', 'Sorry, I cannot provide this response.'),
                    'visibilityStatus': guardrail.get('visibilityStatus', 'SAVED')
                }

                if guardrail.get('description'):
                    create_params['description'] = guardrail['description']

                # Copy filter configurations if present
                if guardrail.get('topicPolicyConfig'):
                    create_params['topicPolicyConfig'] = guardrail['topicPolicyConfig']

                if guardrail.get('contentPolicyConfig'):
                    create_params['contentPolicyConfig'] = guardrail['contentPolicyConfig']

                if guardrail.get('wordPolicyConfig'):
                    create_params['wordPolicyConfig'] = guardrail['wordPolicyConfig']

                if guardrail.get('sensitiveInformationPolicyConfig'):
                    create_params['sensitiveInformationPolicyConfig'] = guardrail['sensitiveInformationPolicyConfig']

                if guardrail.get('contextualGroundingPolicyConfig'):
                    create_params['contextualGroundingPolicyConfig'] = guardrail['contextualGroundingPolicyConfig']

                if guardrail.get('tags'):
                    create_params['tags'] = guardrail['tags']

                response = qconnect_client.create_ai_guardrail(**create_params)
                new_guardrail_id = response['aiGuardrail']['aiGuardrailId']

                guardrail_mapping[original_guardrail_id] = new_guardrail_id
                created_guardrails.append({
                    'originalId': original_guardrail_id,
                    'newId': new_guardrail_id,
                    'status': 'created'
                })

                print(f"Created AI Guardrail: {redact(guardrail_name)} ({original_guardrail_id} -> {new_guardrail_id})")

            except ClientError as e:
                reraise_if_fatal(e)
                if e.response['Error']['Code'] == 'ConflictException':
                    print(f"AI Guardrail '{redact(guardrail_name)}' already exists (caught exception)")
                    existing_id = find_existing_ai_guardrail(qconnect_client, assistant_id, guardrail_name)
                    if existing_id:
                        guardrail_mapping[original_guardrail_id] = existing_id
                        skipped_guardrails.append({
                            'originalId': original_guardrail_id,
                            'newId': existing_id,
                            'status': 'duplicate'
                        })
                    else:
                        failed_guardrails.append(failure_entry(original_guardrail_id, e,
                                                               'exists but not found by name'))
                else:
                    raise e

            except Exception as e:
                reraise_if_fatal(e)
                print(f"Error: {describe_error(e)}")
                failed_guardrails.append({
                    'originalId': guardrail.get('aiGuardrailId', 'Unknown'),
                    'error': describe_error(e), 'errorCode': error_code_of(e)
                })
                continue

        print(f"Successfully created {len(created_guardrails)} AI Guardrails")
        print(f"Skipped {len(skipped_guardrails)} existing AI Guardrails")
        print(f"Failed to create {len(failed_guardrails)} AI Guardrails")

        return {
            'statusCode': 200,
            'aiGuardrailMapping': guardrail_mapping,
            'createdGuardrails': created_guardrails,
            'skippedGuardrails': skipped_guardrails,
            'failedGuardrails': failed_guardrails,
            'summary': {
                'totalGuardrails': len(ai_guardrails),
                'created': len(created_guardrails),
                'skipped': len(skipped_guardrails),
                'failed': len(failed_guardrails)
            }
        }

    except Exception as e:
        reraise_if_fatal(e)
        if is_feature_unavailable(e, qconnect_client):
            done = len(created_guardrails) + len(skipped_guardrails) + len(failed_guardrails)
            return feature_skipped_result('aiGuardrails', destination_region, 'target', error_code_of(e),
                                          count=len(ai_guardrails) - done, aiGuardrailMapping=guardrail_mapping)
        print(f"Error creating AI Guardrails: {describe_error(e)}")
        return {
            'statusCode': 400,
            'error': describe_error(e),
            'aiGuardrailMapping': guardrail_mapping
        }
