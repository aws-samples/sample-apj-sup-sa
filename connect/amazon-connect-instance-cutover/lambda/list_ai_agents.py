"""
Lambda function to list AI Agents from source Connect instance.
Uses the Amazon Connect Customer AI agents (qconnect) APIs.
"""
import json
import boto3
from datetime import datetime
from region_utils import create_connect_client_from_arn
from region_utils import reraise_if_fatal
from region_utils import describe_error, redact
from migration_filters import AI_LIST_PARAMS
from region_utils import step_handler

def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")

def get_assistant_id_from_connect(connect_client, instance_id, region):
    """
    Get the AI agents assistant ID associated with a Connect instance.
    The assistant is linked via the Connect instance integration.
    """
    qconnect_client = boto3.client('qconnect', region_name=region)

    try:
        # List all assistants and find one that matches our Connect instance
        paginator = qconnect_client.get_paginator('list_assistants')

        for page in paginator.paginate():
            for assistant in page.get('assistantSummaries', []):
                assistant_id = assistant['assistantId']
                # Return the first assistant found in this region
                # In production, you might want to match by name or tags
                print(f"Found assistant: {redact(assistant['name'])} ({assistant_id})")
                return assistant_id, qconnect_client

        print("No AI agents assistant found")
        return None, qconnect_client

    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error listing assistants: {describe_error(e)}")
        return None, qconnect_client

@step_handler
def lambda_handler(event, context):
    """
    Lists all AI Agents from source Connect instance's AI agents assistant
    """
    source_instance_arn = event['sourceInstanceId']

    # Create region-specific Connect client for source instance
    print(f"Creating Connect client from source instance ARN: {source_instance_arn}")
    connect_client, source_instance_id, source_region = create_connect_client_from_arn(source_instance_arn)

    try:
        print(f"Looking for an AI agents assistant in region: {source_region}")

        # Get the assistant ID associated with this Connect instance
        assistant_id, qconnect_client = get_assistant_id_from_connect(
            connect_client, source_instance_id, source_region
        )

        if not assistant_id:
            return {
                'statusCode': 200,
                'aiAgents': [],
                'sourceRegion': source_region,
                'message': 'No AI agents assistant found for this instance'
            }

        print(f"Listing AI Agents from assistant: {assistant_id}")

        ai_agents = []

        # List AI Agents (only CUSTOMER origin, not SYSTEM defaults)
        paginator = qconnect_client.get_paginator('list_ai_agents')

        for page in paginator.paginate(assistantId=assistant_id, **AI_LIST_PARAMS):
            for agent_summary in page.get('aiAgentSummaries', []):
                try:
                    agent_id = agent_summary['aiAgentId']

                    # Get detailed information for each AI Agent
                    agent_detail = qconnect_client.get_ai_agent(
                        assistantId=assistant_id,
                        aiAgentId=agent_id
                    )

                    agent_info = agent_detail['aiAgent']
                    agent_info['assistantId'] = assistant_id

                    ai_agents.append(agent_info)
                    print(f"Retrieved AI Agent: {redact(agent_info['name'])} (type: {agent_info['type']})")

                except Exception as e:
                    reraise_if_fatal(e)
                    print(f"Error describing AI Agent {agent_summary.get('aiAgentId', 'Unknown')}: {describe_error(e)}")
                    continue

        # Convert to JSON serializable format
        ai_agents_json = json.loads(json.dumps(ai_agents, default=json_serial))

        print(f"Successfully retrieved {len(ai_agents_json)} AI Agents")

        return {
            'statusCode': 200,
            'aiAgents': ai_agents_json,
            'assistantId': assistant_id,
            'sourceRegion': source_region
        }

    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error listing AI Agents: {describe_error(e)}")
        return {
            'statusCode': 400,
            'error': describe_error(e)
        }
