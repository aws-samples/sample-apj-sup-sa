"""
Lambda function to list AI Guardrails from source Connect instance.
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
    Lists all AI Guardrails from source Connect instance's AI agents assistant
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
                'aiGuardrails': [],
                'sourceRegion': source_region,
                'message': 'No AI agents assistant found for this instance'
            }

        print(f"Listing AI Guardrails from assistant: {assistant_id}")

        ai_guardrails = []

        # List AI Guardrails
        paginator = qconnect_client.get_paginator('list_ai_guardrails')

        for page in paginator.paginate(assistantId=assistant_id):
            for guardrail_summary in page.get('aiGuardrailSummaries', []):
                try:
                    guardrail_id = guardrail_summary['aiGuardrailId']

                    # Get detailed information for each AI Guardrail
                    # Try with version qualifier first (format: id:version or id~version)
                    guardrail_detail = None
                    try:
                        guardrail_detail = qconnect_client.get_ai_guardrail(
                            assistantId=assistant_id,
                            aiGuardrailId=guardrail_id
                        )
                    except Exception as e1:
                        reraise_if_fatal(e1)
                        # If direct ID fails, try with DRAFT version
                        print(f"Direct get failed, trying with DRAFT version: {describe_error(e1)}")
                        try:
                            guardrail_detail = qconnect_client.get_ai_guardrail(
                                assistantId=assistant_id,
                                aiGuardrailId=f"{guardrail_id}:DRAFT"
                            )
                        except Exception as e2:
                            reraise_if_fatal(e2)
                            # Try listing versions and get the latest
                            print(f"DRAFT version failed: {describe_error(e2)}")
                            try:
                                versions_response = qconnect_client.list_ai_guardrail_versions(
                                    assistantId=assistant_id,
                                    aiGuardrailId=guardrail_id
                                )
                                versions = versions_response.get('aiGuardrailVersionSummaries', [])
                                if versions:
                                    # Get the latest version
                                    latest_version = versions[0].get('versionNumber')
                                    if latest_version:
                                        guardrail_detail = qconnect_client.get_ai_guardrail(
                                            assistantId=assistant_id,
                                            aiGuardrailId=f"{guardrail_id}:{latest_version}"
                                        )
                            except Exception as e3:
                                reraise_if_fatal(e3)
                                print(f"Version lookup failed: {describe_error(e3)}")
                                # Use summary data as fallback
                                guardrail_info = {
                                    'aiGuardrailId': guardrail_id,
                                    'name': guardrail_summary.get('name', 'Unknown'),
                                    'assistantId': assistant_id,
                                    'status': guardrail_summary.get('status', 'ACTIVE'),
                                    'visibilityStatus': guardrail_summary.get('visibilityStatus', 'SAVED'),
                                    'description': guardrail_summary.get('description', ''),
                                }
                                ai_guardrails.append(guardrail_info)
                                print(f"Using summary data for AI Guardrail: {redact(guardrail_info['name'])}")
                                continue

                    if guardrail_detail:
                        guardrail_info = guardrail_detail['aiGuardrail']
                        guardrail_info['assistantId'] = assistant_id
                        ai_guardrails.append(guardrail_info)
                        print(f"Retrieved AI Guardrail: {redact(guardrail_info['name'])}")

                except Exception as e:
                    reraise_if_fatal(e)
                    print(f"Error describing AI Guardrail {guardrail_summary.get('aiGuardrailId', 'Unknown')}: {describe_error(e)}")
                    continue

        # Convert to JSON serializable format
        ai_guardrails_json = json.loads(json.dumps(ai_guardrails, default=json_serial))

        print(f"Successfully retrieved {len(ai_guardrails_json)} AI Guardrails")

        return {
            'statusCode': 200,
            'aiGuardrails': ai_guardrails_json,
            'assistantId': assistant_id,
            'sourceRegion': source_region
        }

    except FeatureUnavailable as e:
        return feature_skipped_result('aiGuardrails', source_region, 'source', e.code,
                                      aiGuardrails=[], sourceRegion=source_region)
    except Exception as e:
        reraise_if_fatal(e)
        if is_feature_unavailable(e, qconnect_client):
            return feature_skipped_result('aiGuardrails', source_region, 'source', error_code_of(e),
                                          aiGuardrails=[], sourceRegion=source_region)
        print(f"Error listing AI Guardrails: {describe_error(e)}")
        return {
            'statusCode': 400,
            'error': describe_error(e)
        }
