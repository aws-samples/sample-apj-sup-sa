"""
Lambda function to create Custom Vocabularies in destination Connect instance.
Uses Amazon Connect CreateVocabulary API.
"""
import time
from datetime import datetime
from region_utils import create_destination_client
from botocore.exceptions import ClientError
from region_utils import reraise_if_fatal, error_code_of
from region_utils import describe_error, failure_entry, redact
from region_utils import resolve_external_id
from region_utils import feature_skipped_result, is_feature_unavailable
from region_utils import step_handler

def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")

def find_existing_vocabulary(connect_client, instance_id, vocab_name, language_code):
    """Find an existing vocabulary by name and language code"""
    try:
        paginator = connect_client.get_paginator('search_vocabularies')

        for page in paginator.paginate(
            InstanceId=instance_id,
            NameStartsWith=vocab_name,
            LanguageCode=language_code
        ):
            for vocab in page.get('VocabularySummaryList', []):
                if vocab['Name'] == vocab_name and vocab['LanguageCode'] == language_code:
                    return vocab['Id']

        return None
    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error finding existing Vocabulary '{redact(vocab_name)}': {describe_error(e)}")
        return None

def wait_for_vocabulary_active(connect_client, instance_id, vocab_id, max_wait_seconds=120):
    """Wait for vocabulary to become ACTIVE"""
    start_time = time.time()

    while time.time() - start_time < max_wait_seconds:
        try:
            response = connect_client.describe_vocabulary(
                InstanceId=instance_id,
                VocabularyId=vocab_id
            )
            state = response['Vocabulary']['State']

            if state == 'ACTIVE':
                return True
            elif state == 'CREATION_FAILED':
                failure_reason = response['Vocabulary'].get('FailureReason', 'Unknown')
                print(f"Vocabulary creation failed: {redact(failure_reason)}")
                return False

            print(f"Vocabulary state: {state}, waiting...")
            time.sleep(5)

        except Exception as e:
            reraise_if_fatal(e)
            print(f"Error checking vocabulary state: {describe_error(e)}")
            time.sleep(5)

    print("Timeout waiting for vocabulary to become ACTIVE")
    return False

@step_handler
def lambda_handler(event, context):
    """
    Creates Custom Vocabularies in destination Connect instance
    """
    destination_instance_arn = event['destinationInstanceId']
    vocabularies = event.get('vocabularies', [])
    cross_account_role_arn = event.get('crossAccountRoleArn')
    external_id = resolve_external_id(event)  # never logged

    # Create destination Connect client
    connect_client, destination_instance_id, destination_region = create_destination_client(
        destination_instance_arn, cross_account_role_arn, external_id
    )

    print(f"Creating Custom Vocabularies in instance: {destination_instance_id}")
    if cross_account_role_arn:
        print(f"Cross-account migration enabled with role: {cross_account_role_arn}")

    vocab_mapping = {}
    created_vocabs = []
    skipped_vocabs = []
    failed_vocabs = []

    try:
        for vocab in vocabularies:
            try:
                vocab_name = vocab['Name']
                original_vocab_id = vocab['Id']
                language_code = vocab['LanguageCode']
                content = vocab.get('Content', '')

                print(f"Processing Vocabulary: {redact(vocab_name)} (Language: {language_code})")

                # Check if vocabulary already exists
                existing_vocab_id = find_existing_vocabulary(
                    connect_client, destination_instance_id, vocab_name, language_code
                )

                if existing_vocab_id:
                    print(f"Vocabulary '{redact(vocab_name)}' already exists: {existing_vocab_id}")
                    vocab_mapping[original_vocab_id] = existing_vocab_id
                    skipped_vocabs.append({
                        'originalId': original_vocab_id,
                        'existingId': existing_vocab_id,
                        'languageCode': language_code,
                        'status': 'existing'
                    })
                    continue

                # Validate content exists
                if not content:
                    print(f"Vocabulary '{redact(vocab_name)}' has no content, skipping")
                    failed_vocabs.append({
                        'originalId': original_vocab_id,
                        'languageCode': language_code,
                        'error': 'No content available', 'errorCode': 'NotCreated'
                    })
                    continue

                # Create the vocabulary
                create_params = {
                    'InstanceId': destination_instance_id,
                    'VocabularyName': vocab_name,
                    'LanguageCode': language_code,
                    'Content': content
                }

                if vocab.get('Tags'):
                    create_params['Tags'] = vocab['Tags']

                response = connect_client.create_vocabulary(**create_params)
                new_vocab_id = response['VocabularyId']

                # Wait for vocabulary to become active (vocabularies are processed asynchronously)
                print(f"Waiting for vocabulary '{redact(vocab_name)}' to become ACTIVE...")
                if wait_for_vocabulary_active(connect_client, destination_instance_id, new_vocab_id):
                    vocab_mapping[original_vocab_id] = new_vocab_id
                    created_vocabs.append({
                        'originalId': original_vocab_id,
                        'newId': new_vocab_id,
                        'languageCode': language_code,
                        'status': 'created'
                    })
                    print(f"Created Vocabulary: {redact(vocab_name)} ({original_vocab_id} -> {new_vocab_id})")
                else:
                    failed_vocabs.append({
                        'originalId': original_vocab_id,
                        'newId': new_vocab_id,
                        'languageCode': language_code,
                        'error': 'Vocabulary creation timed out or failed', 'errorCode': 'NotCreated'
                    })

            except ClientError as e:
                reraise_if_fatal(e)
                if is_feature_unavailable(e):
                    return feature_skipped_result(
                        'vocabularies', destination_region, 'target', error_code_of(e),
                        count=len(vocabularies) - len(created_vocabs) - len(skipped_vocabs) - len(failed_vocabs),
                        vocabularyMapping=vocab_mapping, createdVocabularies=created_vocabs,
                        failedVocabularies=failed_vocabs)
                error_code = e.response['Error']['Code']
                if error_code == 'ResourceInUseException':
                    print(f"Vocabulary '{redact(vocab_name)}' already exists (caught exception)")
                    existing_id = find_existing_vocabulary(
                        connect_client, destination_instance_id, vocab_name, language_code
                    )
                    if existing_id:
                        vocab_mapping[original_vocab_id] = existing_id
                        skipped_vocabs.append({
                            'originalId': original_vocab_id,
                            'newId': existing_id,
                            'languageCode': language_code,
                            'status': 'duplicate'
                        })
                    else:
                        failed_vocabs.append(failure_entry(original_vocab_id, e, 'exists but not found by name'))
                else:
                    raise e

            except Exception as e:
                reraise_if_fatal(e)
                print(f"Error: {describe_error(e)}")
                failed_vocabs.append({
                    'originalId': vocab.get('Id', 'Unknown'),
                    'languageCode': vocab.get('LanguageCode', 'Unknown'),
                    'error': describe_error(e), 'errorCode': error_code_of(e)
                })
                continue

        print(f"Successfully created {len(created_vocabs)} Custom Vocabularies")
        print(f"Skipped {len(skipped_vocabs)} existing Custom Vocabularies")
        print(f"Failed to create {len(failed_vocabs)} Custom Vocabularies")

        return {
            'statusCode': 200,
            'vocabularyMapping': vocab_mapping,
            'createdVocabularies': created_vocabs,
            'skippedVocabularies': skipped_vocabs,
            'failedVocabularies': failed_vocabs,
            'summary': {
                'totalVocabularies': len(vocabularies),
                'created': len(created_vocabs),
                'skipped': len(skipped_vocabs),
                'failed': len(failed_vocabs)
            }
        }

    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error creating Custom Vocabularies: {describe_error(e)}")
        return {
            'statusCode': 400,
            'error': describe_error(e),
            'vocabularyMapping': vocab_mapping
        }
