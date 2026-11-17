"""
Lambda function to create AI Prompts in destination Connect instance.
Uses the Amazon Connect Customer AI agents (qconnect) APIs.
"""
from datetime import datetime
from region_utils import BOTO_CONFIG, create_destination_session, extract_region_from_arn
from botocore.exceptions import ClientError
from region_utils import reraise_if_fatal, error_code_of
from region_utils import describe_error, failure_entry, redact
from region_utils import resolve_external_id
from region_utils import FeatureUnavailable, feature_skipped_result, is_feature_unavailable, raise_if_feature_unavailable
from migration_filters import AI_LIST_PARAMS
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

def find_existing_ai_prompt(qconnect_client, assistant_id, prompt_name):
    """Find an existing AI Prompt by name"""
    try:
        paginator = qconnect_client.get_paginator('list_ai_prompts')

        for page in paginator.paginate(assistantId=assistant_id, **AI_LIST_PARAMS):
            for prompt in page.get('aiPromptSummaries', []):
                if prompt['name'] == prompt_name:
                    return prompt['aiPromptId']

        return None
    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error finding existing AI Prompt '{redact(prompt_name)}': {describe_error(e)}")
        return None

@step_handler
def lambda_handler(event, context):
    """
    Creates AI Prompts in destination Connect instance's AI agents assistant
    """
    destination_instance_arn = event['destinationInstanceId']
    ai_prompts = event.get('aiPrompts', [])
    cross_account_role_arn = event.get('crossAccountRoleArn')
    external_id = resolve_external_id(event)  # never logged

    # Extract destination region from ARN
    destination_region = destination_instance_arn.split(':')[3]
    source_instance_arn = event.get('sourceInstanceId')
    source_region = extract_region_from_arn(source_instance_arn) if source_instance_arn else None

    print(f"Creating AI Prompts in region: {destination_region}")
    if cross_account_role_arn:
        print(f"Cross-account migration enabled with role: {cross_account_role_arn}")

    # Get the assistant ID in destination
    try:
        assistant_id, qconnect_client = get_assistant_id(destination_instance_arn, cross_account_role_arn,
                                                         external_id)
    except FeatureUnavailable as e:
        return feature_skipped_result('aiPrompts', destination_region, 'target', e.code, count=len(ai_prompts),
                                      aiPromptMapping={})

    if not assistant_id:
        return {
            'statusCode': 400,
            'error': 'No AI agents assistant found in destination instance'
        }

    prompt_mapping = {}
    created_prompts = []
    skipped_prompts = []
    failed_prompts = []
    model_follow_up = []

    try:
        print(f"Creating AI Prompts in assistant: {assistant_id}")

        for prompt in ai_prompts:
            try:
                prompt_name = prompt['name']
                original_prompt_id = prompt['aiPromptId']

                print(f"Processing AI Prompt: {redact(prompt_name)}")

                # Check if prompt already exists
                existing_prompt_id = find_existing_ai_prompt(qconnect_client, assistant_id, prompt_name)

                if existing_prompt_id:
                    print(f"AI Prompt '{redact(prompt_name)}' already exists: {existing_prompt_id}")
                    prompt_mapping[original_prompt_id] = existing_prompt_id
                    skipped_prompts.append({
                        'originalId': original_prompt_id,
                        'existingId': existing_prompt_id,
                        'status': 'existing'
                    })
                    continue

                # Create the AI Prompt
                create_params = {
                    'assistantId': assistant_id,
                    'name': prompt_name,
                    'type': prompt['type'],
                    'templateType': prompt['templateType'],
                    'templateConfiguration': prompt['templateConfiguration'],
                    'visibilityStatus': prompt.get('visibilityStatus', 'SAVED'),
                    'apiFormat': prompt.get('apiFormat', 'ANTHROPIC_CLAUDE_MESSAGES')
                }

                if prompt.get('description'):
                    create_params['description'] = prompt['description']

                # Keep the source modelId. A model or inference profile ID can be Region specific (for
                # example a us. or eu. cross Region inference profile), and which one the target Region
                # offers is not derivable from the Region code, so nothing is guessed: when the Regions
                # differ the prompt is listed for a manual check.
                model_note = None
                if prompt.get('modelId'):
                    model_id = prompt['modelId']
                    create_params['modelId'] = model_id
                    print(f"Using modelId as is: {model_id}")
                    if source_region and source_region != destination_region:
                        model_note = {
                            'type': 'aiPromptModel', 'source': original_prompt_id, 'modelId': model_id,
                            'action': (f"The AI prompt keeps model ID {model_id} from source Region {source_region}. "
                                       f"Check that target Region {destination_region} offers this model or "
                                       "inference profile; if not, edit the AI prompt in the target to one it "
                                       "offers."),
                        }

                if prompt.get('tags'):
                    create_params['tags'] = prompt['tags']

                response = qconnect_client.create_ai_prompt(**create_params)
                new_prompt_id = response['aiPrompt']['aiPromptId']

                prompt_mapping[original_prompt_id] = new_prompt_id
                created_prompts.append({
                    'originalId': original_prompt_id,
                    'newId': new_prompt_id,
                    'type': prompt['type'],
                    'status': 'created'
                })
                if model_note:
                    model_follow_up.append(model_note)

                print(f"Created AI Prompt: {redact(prompt_name)} ({original_prompt_id} -> {new_prompt_id})")

            except ClientError as e:
                reraise_if_fatal(e)
                if e.response['Error']['Code'] == 'ConflictException':
                    print(f"AI Prompt '{redact(prompt_name)}' already exists (caught exception)")
                    existing_id = find_existing_ai_prompt(qconnect_client, assistant_id, prompt_name)
                    if existing_id:
                        prompt_mapping[original_prompt_id] = existing_id
                        skipped_prompts.append({
                            'originalId': original_prompt_id,
                            'newId': existing_id,
                            'status': 'duplicate'
                        })
                    else:
                        failed_prompts.append(failure_entry(original_prompt_id, e, 'exists but not found by name'))
                else:
                    raise e

            except Exception as e:
                reraise_if_fatal(e)
                print(f"Error: {describe_error(e)}")
                failed_prompts.append({
                    'originalId': prompt.get('aiPromptId', 'Unknown'),
                    'error': describe_error(e), 'errorCode': error_code_of(e)
                })
                continue

        print(f"Successfully created {len(created_prompts)} AI Prompts")
        print(f"Skipped {len(skipped_prompts)} existing AI Prompts")
        print(f"Failed to create {len(failed_prompts)} AI Prompts")

        return {
            'statusCode': 200,
            'aiPromptMapping': prompt_mapping,
            'createdPrompts': created_prompts,
            'skippedPrompts': skipped_prompts,
            'failedPrompts': failed_prompts,
            'manualFollowUp': model_follow_up,
            'summary': {
                'totalPrompts': len(ai_prompts),
                'created': len(created_prompts),
                'skipped': len(skipped_prompts),
                'failed': len(failed_prompts)
            }
        }

    except Exception as e:
        reraise_if_fatal(e)
        if is_feature_unavailable(e, qconnect_client):
            done = len(created_prompts) + len(skipped_prompts) + len(failed_prompts)
            return feature_skipped_result('aiPrompts', destination_region, 'target', error_code_of(e),
                                          count=len(ai_prompts) - done, aiPromptMapping=prompt_mapping)
        print(f"Error creating AI Prompts: {describe_error(e)}")
        return {
            'statusCode': 400,
            'error': describe_error(e),
            'aiPromptMapping': prompt_mapping
        }
