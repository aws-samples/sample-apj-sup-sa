"""
Lambda function to list Custom Vocabularies from source Connect instance.
Uses Amazon Connect SearchVocabularies API.
"""
import json
from datetime import datetime
from region_utils import create_connect_client_from_arn
from region_utils import reraise_if_fatal
from region_utils import describe_error, redact
from region_utils import error_code_of, feature_skipped_result, is_feature_unavailable
from migration_filters import VOCABULARY_SEARCH_PARAMS, migrates_vocabulary
from region_utils import step_handler

def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")

@step_handler
def lambda_handler(event, context):
    """
    Lists all Custom Vocabularies from source Connect instance
    """
    source_instance_arn = event['sourceInstanceId']

    # Create region-specific Connect client for source instance
    print(f"Creating Connect client from source instance ARN: {source_instance_arn}")
    connect_client, source_instance_id, source_region = create_connect_client_from_arn(source_instance_arn)

    try:
        print(f"Listing Custom Vocabularies from instance: {source_instance_id}")

        vocabularies = []

        # Search for all ACTIVE vocabularies
        paginator = connect_client.get_paginator('search_vocabularies')

        for page in paginator.paginate(
            InstanceId=source_instance_id,
            **VOCABULARY_SEARCH_PARAMS
        ):
            for vocab_summary in page.get('VocabularySummaryList', []):
                # Active vocabularies only (shared filter, VerifyTarget uses it too).
                if not migrates_vocabulary(vocab_summary):
                    continue
                try:
                    vocab_id = vocab_summary['Id']

                    # Get detailed information including Content
                    vocab_detail = connect_client.describe_vocabulary(
                        InstanceId=source_instance_id,
                        VocabularyId=vocab_id
                    )

                    vocab_info = vocab_detail['Vocabulary']
                    vocabularies.append(vocab_info)
                    print(f"Retrieved Vocabulary: {redact(vocab_info['Name'])} (Language: {vocab_info['LanguageCode']})")

                except Exception as e:
                    reraise_if_fatal(e)
                    print(f"Error describing Vocabulary {vocab_summary.get('Id', 'Unknown')}: {describe_error(e)}")
                    continue

        # Convert to JSON serializable format
        vocabularies_json = json.loads(json.dumps(vocabularies, default=json_serial))

        print(f"Successfully retrieved {len(vocabularies_json)} Custom Vocabularies")

        return {
            'statusCode': 200,
            'vocabularies': vocabularies_json,
            'sourceRegion': source_region
        }

    except Exception as e:
        reraise_if_fatal(e)
        if is_feature_unavailable(e):
            return feature_skipped_result('vocabularies', source_region, 'source', error_code_of(e),
                                          vocabularies=[], sourceRegion=source_region)
        print(f"Error listing Custom Vocabularies: {describe_error(e)}")
        return {
            'statusCode': 400,
            'error': describe_error(e)
        }
