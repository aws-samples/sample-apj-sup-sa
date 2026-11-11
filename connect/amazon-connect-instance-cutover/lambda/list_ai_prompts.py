"""
Lambda function to list AI Prompts from source Connect instance.
Uses the Amazon Connect Customer AI agents (qconnect) APIs.
"""
import json
import boto3
from datetime import datetime
from region_utils import create_connect_client_from_arn
from region_utils import reraise_if_fatal
from region_utils import describe_error, redact
from region_utils import (FeatureUnavailable, error_code_of, feature_skipped_result, is_feature_unavailable,
                          raise_if_feature_unavailable)
from migration_filters import AI_LIST_PARAMS
from region_utils import step_handler

def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")

def get_assistant_id(region):
    """
    Get the AI agents assistant ID in the specified region.
    """
    qconnect_client = boto3.client('qconnect', region_name=region)

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

@step_handler
def lambda_handler(event, context):
    """
    Lists all AI Prompts from source Connect instance's AI agents assistant
    """
    source_instance_arn = event['sourceInstanceId']

    # Create region-specific Connect client for source instance
    print(f"Creating Connect client from source instance ARN: {source_instance_arn}")
    connect_client, source_instance_id, source_region = create_connect_client_from_arn(source_instance_arn)
    qconnect_client = None

    try:
        print(f"Looking for an AI agents assistant in region: {source_region}")

        # Get the assistant ID
        assistant_id, qconnect_client = get_assistant_id(source_region)

        if not assistant_id:
            return {
                'statusCode': 200,
                'aiPrompts': [],
                'sourceRegion': source_region,
                'message': 'No AI agents assistant found for this instance'
            }

        print(f"Listing AI Prompts from assistant: {assistant_id}")

        ai_prompts = []

        # List AI Prompts (only CUSTOMER origin, not SYSTEM defaults)
        paginator = qconnect_client.get_paginator('list_ai_prompts')

        for page in paginator.paginate(assistantId=assistant_id, **AI_LIST_PARAMS):
            for prompt_summary in page.get('aiPromptSummaries', []):
                try:
                    prompt_id = prompt_summary['aiPromptId']

                    # Get detailed information for each AI Prompt
                    prompt_detail = qconnect_client.get_ai_prompt(
                        assistantId=assistant_id,
                        aiPromptId=prompt_id
                    )

                    prompt_info = prompt_detail['aiPrompt']
                    prompt_info['assistantId'] = assistant_id

                    ai_prompts.append(prompt_info)
                    print(f"Retrieved AI Prompt: {redact(prompt_info['name'])} (type: {prompt_info.get('type', 'N/A')})")

                except Exception as e:
                    reraise_if_fatal(e)
                    print(f"Error describing AI Prompt {prompt_summary.get('aiPromptId', 'Unknown')}: {describe_error(e)}")
                    continue

        # Convert to JSON serializable format
        ai_prompts_json = json.loads(json.dumps(ai_prompts, default=json_serial))

        print(f"Successfully retrieved {len(ai_prompts_json)} AI Prompts")

        return {
            'statusCode': 200,
            'aiPrompts': ai_prompts_json,
            'assistantId': assistant_id,
            'sourceRegion': source_region
        }

    except FeatureUnavailable as e:
        return feature_skipped_result('aiPrompts', source_region, 'source', e.code,
                                      aiPrompts=[], sourceRegion=source_region)
    except Exception as e:
        reraise_if_fatal(e)
        if is_feature_unavailable(e, qconnect_client):
            return feature_skipped_result('aiPrompts', source_region, 'source', error_code_of(e),
                                          aiPrompts=[], sourceRegion=source_region)
        print(f"Error listing AI Prompts: {describe_error(e)}")
        return {
            'statusCode': 400,
            'error': describe_error(e)
        }
