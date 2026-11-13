"""
Lambda function to create AI Agents in destination Connect instance.
Uses the Amazon Connect Customer AI agents (qconnect) APIs.
"""
import json
import boto3
from datetime import datetime
from botocore.exceptions import ClientError
from region_utils import BOTO_CONFIG, create_destination_session, extract_region_from_arn
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

def get_qconnect_client(destination_arn, cross_account_role_arn=None, external_id=None):
    """Get a qconnect client for the target account and Region (assumes the target role with ExternalId)."""
    region = extract_region_from_arn(destination_arn)
    session = create_destination_session(destination_arn, cross_account_role_arn, external_id)
    return session.client('qconnect', region_name=region, config=BOTO_CONFIG)

def get_assistant_id(qconnect_client):
    """Get the AI agents assistant ID"""
    try:
        paginator = qconnect_client.get_paginator('list_assistants')

        for page in paginator.paginate():
            for assistant in page.get('assistantSummaries', []):
                assistant_id = assistant['assistantId']
                print(f"Found assistant: {redact(assistant['name'])} ({assistant_id})")
                return assistant_id

        print("No AI agents assistant found")
        return None

    except Exception as e:
        reraise_if_fatal(e)
        raise_if_feature_unavailable(e, qconnect_client)
        print(f"Error listing assistants: {describe_error(e)}")
        return None

def build_ai_prompt_mapping(source_client, dest_client, source_assistant_id, dest_assistant_id):
    """Build mapping of source AI Prompt IDs to destination AI Prompt IDs by name"""
    mapping = {}
    try:
        # Get source prompts
        source_prompts = {}
        paginator = source_client.get_paginator('list_ai_prompts')
        for page in paginator.paginate(assistantId=source_assistant_id, **AI_LIST_PARAMS):
            for prompt in page.get('aiPromptSummaries', []):
                source_prompts[prompt['name']] = prompt['aiPromptId']

        # Get destination prompts
        dest_prompts = {}
        paginator = dest_client.get_paginator('list_ai_prompts')
        for page in paginator.paginate(assistantId=dest_assistant_id, **AI_LIST_PARAMS):
            for prompt in page.get('aiPromptSummaries', []):
                dest_prompts[prompt['name']] = prompt['aiPromptId']

        # Build mapping by name
        for name, source_id in source_prompts.items():
            if name in dest_prompts:
                mapping[source_id] = dest_prompts[name]
                print(f"Mapped AI Prompt: {name} ({source_id} -> {redact(dest_prompts[name])})")

        print(f"Built AI Prompt mapping: {len(mapping)} prompts mapped")

    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error building AI Prompt mapping: {describe_error(e)}")

    return mapping

def build_ai_guardrail_mapping(source_client, dest_client, source_assistant_id, dest_assistant_id):
    """Build mapping of source AI Guardrail IDs to destination AI Guardrail IDs by name"""
    mapping = {}
    try:
        # Get source guardrails
        source_guardrails = {}
        paginator = source_client.get_paginator('list_ai_guardrails')
        for page in paginator.paginate(assistantId=source_assistant_id):
            for guardrail in page.get('aiGuardrailSummaries', []):
                source_guardrails[guardrail['name']] = guardrail['aiGuardrailId']

        # Get destination guardrails
        dest_guardrails = {}
        paginator = dest_client.get_paginator('list_ai_guardrails')
        for page in paginator.paginate(assistantId=dest_assistant_id):
            for guardrail in page.get('aiGuardrailSummaries', []):
                dest_guardrails[guardrail['name']] = guardrail['aiGuardrailId']

        # Build mapping by name
        for name, source_id in source_guardrails.items():
            if name in dest_guardrails:
                mapping[source_id] = dest_guardrails[name]
                print(f"Mapped AI Guardrail: {name} ({source_id} -> {redact(dest_guardrails[name])})")

        print(f"Built AI Guardrail mapping: {len(mapping)} guardrails mapped")

    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error building AI Guardrail mapping: {describe_error(e)}")

    return mapping

def find_existing_ai_agent(qconnect_client, assistant_id, agent_name):
    """Find an existing AI Agent by name"""
    try:
        paginator = qconnect_client.get_paginator('list_ai_agents')

        for page in paginator.paginate(assistantId=assistant_id, **AI_LIST_PARAMS):
            for agent in page.get('aiAgentSummaries', []):
                if agent['name'] == agent_name:
                    return agent['aiAgentId']

        return None
    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error finding existing AI Agent '{redact(agent_name)}': {describe_error(e)}")
        return None

@step_handler
def lambda_handler(event, context):
    """
    Creates AI Agents in destination Connect instance's AI agents assistant
    """
    source_instance_arn = event.get('sourceInstanceId')
    destination_instance_arn = event['destinationInstanceId']
    ai_agents = event.get('aiAgents', [])
    cross_account_role_arn = event.get('crossAccountRoleArn')
    external_id = resolve_external_id(event)  # never logged

    # Use provided mappings if available, otherwise build them
    ai_prompt_mapping = event.get('aiPromptMapping', {})
    ai_guardrail_mapping = event.get('aiGuardrailMapping', {})

    # Extract regions from ARNs
    source_region = source_instance_arn.split(':')[3] if source_instance_arn else None
    destination_region = destination_instance_arn.split(':')[3]

    print(f"Creating AI Agents in region: {destination_region}")
    if cross_account_role_arn:
        print(f"Cross-account migration enabled with role: {cross_account_role_arn}")

    # Create qconnect clients
    source_client = boto3.client('qconnect', region_name=source_region, config=BOTO_CONFIG) if source_region else None
    dest_client = get_qconnect_client(destination_instance_arn, cross_account_role_arn, external_id)

    # Get assistant IDs
    try:
        source_assistant_id = get_assistant_id(source_client) if source_client else None
    except FeatureUnavailable:
        source_assistant_id = None  # no source mapping; ListAIAgents already reported the source Region
    try:
        dest_assistant_id = get_assistant_id(dest_client)
    except FeatureUnavailable as e:
        return feature_skipped_result('aiAgents', destination_region, 'target', e.code, count=len(ai_agents),
                                      aiAgentMapping={})

    if not dest_assistant_id:
        return {
            'statusCode': 400,
            'error': 'No AI agents assistant found in destination instance'
        }

    # Build mappings if not provided and we have source info
    if not ai_prompt_mapping and source_client and source_assistant_id:
        print("Building AI Prompt mapping from source and destination...")
        ai_prompt_mapping = build_ai_prompt_mapping(source_client, dest_client, source_assistant_id, dest_assistant_id)

    if not ai_guardrail_mapping and source_client and source_assistant_id:
        print("Building AI Guardrail mapping from source and destination...")
        ai_guardrail_mapping = build_ai_guardrail_mapping(source_client, dest_client, source_assistant_id, dest_assistant_id)

    print(f"AI Prompt mapping: {len(ai_prompt_mapping)} entries")
    print(f"AI Guardrail mapping: {len(ai_guardrail_mapping)} entries")

    agent_mapping = {}
    created_agents = []
    skipped_agents = []
    failed_agents = []

    try:
        print(f"Creating AI Agents in assistant: {dest_assistant_id}")

        for agent in ai_agents:
            try:
                agent_name = agent['name']
                original_agent_id = agent['aiAgentId']
                agent_type = agent['type']

                print(f"Processing AI Agent: {redact(agent_name)} (type: {agent_type})")

                # Check if agent already exists
                existing_agent_id = find_existing_ai_agent(dest_client, dest_assistant_id, agent_name)

                if existing_agent_id:
                    print(f"AI Agent '{redact(agent_name)}' already exists: {existing_agent_id}")
                    agent_mapping[original_agent_id] = existing_agent_id
                    skipped_agents.append({
                        'originalId': original_agent_id,
                        'existingId': existing_agent_id,
                        'status': 'existing'
                    })
                    continue

                # Prepare configuration - update prompt and guardrail IDs
                raw_config = agent.get('configuration', {})

                # Check for SDK_UNKNOWN_MEMBER which indicates unsupported agent type
                if 'SDK_UNKNOWN_MEMBER' in raw_config:
                    print(f"Skipping AI Agent '{redact(agent_name)}' - agent type '{agent_type}' is not supported for migration")
                    failed_agents.append({
                        'originalId': original_agent_id,
                        'type': agent_type,
                        'error': f"Agent type '{agent_type}' is not supported for migration. Please create this agent manually in the destination.", 'errorCode': 'NotCreated'
                    })
                    continue

                configuration = update_configuration_ids(
                    raw_config,
                    ai_prompt_mapping,
                    ai_guardrail_mapping
                )

                # Create the AI Agent
                create_params = {
                    'assistantId': dest_assistant_id,
                    'name': agent_name,
                    'type': agent_type,
                    'configuration': configuration,
                    'visibilityStatus': agent.get('visibilityStatus', 'SAVED')
                }

                if agent.get('description'):
                    create_params['description'] = agent['description']

                if agent.get('tags'):
                    create_params['tags'] = agent['tags']

                response = dest_client.create_ai_agent(**create_params)
                new_agent_id = response['aiAgent']['aiAgentId']

                agent_mapping[original_agent_id] = new_agent_id
                created_agents.append({
                    'originalId': original_agent_id,
                    'newId': new_agent_id,
                    'type': agent_type,
                    'status': 'created'
                })

                print(f"Created AI Agent: {redact(agent_name)} ({original_agent_id} -> {new_agent_id})")

            except ClientError as e:
                reraise_if_fatal(e)
                if e.response['Error']['Code'] == 'ConflictException':
                    print(f"AI Agent '{redact(agent_name)}' already exists (caught exception)")
                    existing_id = find_existing_ai_agent(dest_client, dest_assistant_id, agent_name)
                    if existing_id:
                        agent_mapping[original_agent_id] = existing_id
                        skipped_agents.append({
                            'originalId': original_agent_id,
                            'newId': existing_id,
                            'status': 'duplicate'
                        })
                    else:
                        failed_agents.append(failure_entry(original_agent_id, e, 'exists but not found by name'))
                else:
                    raise e

            except Exception as e:
                reraise_if_fatal(e)
                print(f"Error: {describe_error(e)}")
                failed_agents.append({
                    'originalId': agent.get('aiAgentId', 'Unknown'),
                    'error': describe_error(e), 'errorCode': error_code_of(e)
                })
                continue

        print(f"Successfully created {len(created_agents)} AI Agents")
        print(f"Skipped {len(skipped_agents)} existing AI Agents")
        print(f"Failed to create {len(failed_agents)} AI Agents")

        return {
            'statusCode': 200,
            'aiAgentMapping': agent_mapping,
            'createdAgents': created_agents,
            'skippedAgents': skipped_agents,
            'failedAgents': failed_agents,
            'summary': {
                'totalAgents': len(ai_agents),
                'created': len(created_agents),
                'skipped': len(skipped_agents),
                'failed': len(failed_agents)
            }
        }

    except Exception as e:
        reraise_if_fatal(e)
        if is_feature_unavailable(e, dest_client):
            done = len(created_agents) + len(skipped_agents) + len(failed_agents)
            return feature_skipped_result('aiAgents', destination_region, 'target', error_code_of(e),
                                          count=len(ai_agents) - done, aiAgentMapping=agent_mapping)
        print(f"Error creating AI Agents: {describe_error(e)}")
        return {
            'statusCode': 400,
            'error': describe_error(e),
            'aiAgentMapping': agent_mapping
        }


def update_configuration_ids(configuration, prompt_mapping, guardrail_mapping):
    """
    Update AI Prompt and AI Guardrail IDs in the configuration to use destination IDs
    """
    if not configuration:
        return configuration

    # Deep copy to avoid modifying original
    config = json.loads(json.dumps(configuration))

    # List of configuration types and their prompt/guardrail fields
    config_fields = {
        'manualSearchAIAgentConfiguration': {
            'prompts': ['answerGenerationAIPromptId'],
            'guardrails': ['answerGenerationAIGuardrailId']
        },
        'answerRecommendationAIAgentConfiguration': {
            'prompts': ['intentLabelingGenerationAIPromptId', 'queryReformulationAIPromptId', 'answerGenerationAIPromptId'],
            'guardrails': ['answerGenerationAIGuardrailId']
        },
        'selfServiceAIAgentConfiguration': {
            'prompts': ['selfServicePreProcessingAIPromptId', 'selfServiceAnswerGenerationAIPromptId'],
            'guardrails': ['selfServiceAIGuardrailId']
        },
        'orchestrationAIAgentConfiguration': {
            'prompts': ['orchestrationAIPromptId'],
            'guardrails': ['orchestrationAIGuardrailId']
        },
        'noteTakingAIAgentConfiguration': {
            'prompts': ['noteTakingAIPromptId'],
            'guardrails': ['noteTakingAIGuardrailId']
        },
        'caseSummarizationAIAgentConfiguration': {
            'prompts': ['caseSummarizationAIPromptId'],
            'guardrails': ['caseSummarizationAIGuardrailId']
        }
    }

    for config_type, fields in config_fields.items():
        if config_type in config:
            sub_config = config[config_type]

            # Update prompt IDs
            for prompt_field in fields.get('prompts', []):
                if prompt_field in sub_config and sub_config[prompt_field]:
                    old_id = sub_config[prompt_field]
                    if old_id in prompt_mapping:
                        sub_config[prompt_field] = prompt_mapping[old_id]
                        print(f"Updated {prompt_field}: {old_id} -> {redact(prompt_mapping[old_id])}")

            # Update guardrail IDs
            for guardrail_field in fields.get('guardrails', []):
                if guardrail_field in sub_config and sub_config[guardrail_field]:
                    old_id = sub_config[guardrail_field]
                    if old_id in guardrail_mapping:
                        sub_config[guardrail_field] = guardrail_mapping[old_id]
                        print(f"Updated {guardrail_field}: {old_id} -> {redact(guardrail_mapping[old_id])}")

    return config
