"""
Lambda function to list prompts from source Connect instance.
Uses ListPrompts and DescribePrompt APIs.
"""
import json
from datetime import datetime
from region_utils import create_connect_client_from_arn
from region_utils import reraise_if_fatal
from region_utils import describe_error, redact
from region_utils import step_handler

def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")

@step_handler
def lambda_handler(event, context):
    """
    Lists and describes all prompts from source Connect instance
    """
    source_instance_arn = event['sourceInstanceId']

    # Create region-specific Connect client for source instance
    print(f"Creating Connect client from source instance ARN: {source_instance_arn}")
    connect_client, source_instance_id, source_region = create_connect_client_from_arn(source_instance_arn)

    try:
        print(f"Listing prompts from instance: {source_instance_id} in region: {source_region}")

        # Get paginator for listing prompts
        paginator = connect_client.get_paginator('list_prompts')
        prompts = []

        # Iterate through all pages
        for page in paginator.paginate(InstanceId=source_instance_id):
            for prompt_summary in page.get('PromptSummaryList', []):
                try:
                    prompt_id = prompt_summary['Id']

                    # Get detailed information for each prompt
                    prompt_detail = connect_client.describe_prompt(
                        InstanceId=source_instance_id,
                        PromptId=prompt_id
                    )

                    prompt_info = prompt_detail['Prompt']

                    # Get the presigned URL for the prompt file
                    try:
                        file_response = connect_client.get_prompt_file(
                            InstanceId=source_instance_id,
                            PromptId=prompt_id
                        )
                        prompt_info['PromptPresignedUrl'] = file_response.get('PromptPresignedUrl')
                        prompt_info['SourceRegion'] = source_region
                        print(f"Retrieved prompt with file URL: {redact(prompt_info['Name'])}")
                    except Exception as e:
                        reraise_if_fatal(e)
                        print(f"Warning: Could not get prompt file for {prompt_id}: {describe_error(e)}")
                        prompt_info['PromptPresignedUrl'] = None

                    prompts.append(prompt_info)
                    print(f"Retrieved prompt: {redact(prompt_info['Name'])}")

                except Exception as e:
                    reraise_if_fatal(e)
                    print(f"Error describing prompt {prompt_summary.get('Id', 'Unknown')}: {describe_error(e)}")
                    continue

        # Convert to JSON serializable format
        prompts_json = json.loads(json.dumps(prompts, default=json_serial))

        print(f"Successfully retrieved {len(prompts_json)} prompts")

        return {
            'statusCode': 200,
            'prompts': prompts_json,
            'sourceRegion': source_region
        }

    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error listing prompts: {describe_error(e)}")
        return {
            'statusCode': 400,
            'error': describe_error(e)
        }
