"""
Lambda function to create prompts in destination Connect instance.
Supports both same-region and cross-region migration.
Downloads prompt audio from source and uploads to destination.
Supports cross-account migration by creating S3 bucket in destination account.
"""
import json
import urllib.parse
import urllib.request
import os
import shutil
import tempfile
import uuid
from datetime import datetime
from region_utils import (
    BOTO_CONFIG,
    create_destination_client,
    create_destination_session,
    extract_account_id_from_arn,
)
from botocore.exceptions import ClientError
from arn_mapping import ExistingIndex
from region_utils import reraise_if_fatal, error_code_of, EXISTS_ERROR_CODES
from region_utils import describe_error, mask_account_ids, redact
from region_utils import resolve_external_id
from region_utils import step_handler

# S3 bucket for temporary prompt storage
TEMP_BUCKET_PREFIX = 'connect-migration-prompts-temp'

DOWNLOAD_TIMEOUT_SECONDS = 30
MAX_PROMPT_BYTES = 50 * 1024 * 1024


class PromptDownloadError(Exception):
    """The prompt URL is not an https URL, or the download failed or was too large."""


class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != 'https':
            raise PromptDownloadError('Refusing a redirect to a non https URL')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_prompt_file(url, destination_path):
    """Download a GetPromptFile pre signed URL to a local file.

    Only https URLs (and https redirects) are accepted, with a timeout and a
    size limit, so the URL can never be file://, ftp:// or plain http.
    """
    parts = urllib.parse.urlsplit(url or '')
    if parts.scheme != 'https' or not parts.netloc:
        raise PromptDownloadError('Prompt URL must be an https URL')
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(), _HttpsOnlyRedirects())
    # Scheme validated above and redirects restricted to https.
    with opener.open(url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:  # nosec B310
        with open(destination_path, 'wb') as out:
            total = 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_PROMPT_BYTES:
                    raise PromptDownloadError('Prompt file is larger than the limit')
                out.write(chunk)

def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")

@step_handler
def lambda_handler(event, context):
    """
    Creates prompts in destination Connect instance
    Supports cross-account migration with crossAccountRoleArn
    """
    destination_instance_arn = event['destinationInstanceId']
    prompts = event.get('prompts', [])
    cross_account_role_arn = event.get('crossAccountRoleArn')  # Optional for cross-account
    external_id = resolve_external_id(event)  # never logged

    # Create region-specific Connect client for destination instance (supports cross-account)
    print(f"Creating Connect client from destination instance ARN: {destination_instance_arn}")
    if cross_account_role_arn:
        print(f"Cross-account migration enabled with role: {cross_account_role_arn}")
    # One target session (one AssumeRole) for both the Connect and the S3 client.
    # The bucket lives in the destination account so the destination instance can read it.
    dest_session = create_destination_session(
        destination_instance_arn, cross_account_role_arn, external_id
    )
    connect_client, destination_instance_id, destination_region = create_destination_client(
        destination_instance_arn, session=dest_session
    )
    s3_client = dest_session.client('s3', region_name=destination_region, config=BOTO_CONFIG)
    dest_account_id = extract_account_id_from_arn(destination_instance_arn)
    # Prompts already in the target, listed once before any create
    existing = ExistingIndex(connect_client, destination_instance_id, 'prompts')

    # Get or create temporary S3 bucket for prompt files in destination account
    temp_bucket = get_or_create_temp_bucket(s3_client, destination_region, dest_account_id)

    prompt_mapping = {}
    created_prompts = []
    skipped_prompts = []
    failed_prompts = []

    try:
        print(f"Creating prompts in instance: {destination_instance_id} in region: {destination_region}")
        print(f"Using temporary S3 bucket: {mask_account_ids(temp_bucket)}")

        for prompt in prompts:
            try:
                prompt_name = prompt['Name']
                original_prompt_id = prompt['PromptId']
                presigned_url = prompt.get('PromptPresignedUrl')

                print(f"Processing prompt: {redact(prompt_name)}")

                # Check if prompt already exists
                if existing.error is not None:
                    failed_prompts.append(existing.lookup_failure(original_prompt_id))
                    continue
                existing_prompt_id = existing.get(prompt_name)

                if existing_prompt_id:
                    print(f"Prompt '{redact(prompt_name)}' already exists: {existing_prompt_id}")
                    prompt_mapping[original_prompt_id] = existing_prompt_id
                    skipped_prompts.append({
                        'originalId': original_prompt_id,
                        'existingId': existing_prompt_id,
                        'status': 'existing'
                    })
                    continue

                if not presigned_url:
                    print(f"No presigned URL for prompt {redact(prompt_name)}, skipping")
                    failed_prompts.append({
                        'originalId': original_prompt_id,
                        'error': 'No presigned URL available', 'errorCode': 'NotCreated'
                    })
                    continue

                # Download the prompt file from the source into a private temporary
                # directory (tempfile: unique name, mode 700, in the platform temp
                # directory), and remove it on every path.
                print("Downloading prompt file from presigned URL...")
                temp_dir = tempfile.mkdtemp(prefix='connect-migration-prompt-')
                temp_file_path = os.path.join(temp_dir, 'prompt.wav')
                try:
                    try:
                        download_prompt_file(presigned_url, temp_file_path)
                        print("Downloaded prompt file")
                    except (PromptDownloadError, OSError) as e:
                        reraise_if_fatal(e)
                        print(f"Error downloading prompt file: {describe_error(e)}")
                        failed_prompts.append({
                            'originalId': original_prompt_id,
                            'error': describe_error(e), 'errorCode': error_code_of(e)
                        })
                        continue

                    # Upload to temporary S3 bucket (in destination account for cross-account).
                    # The key uses the source prompt ID and a random suffix, never the prompt
                    # name, so names do not reach S3 keys or the logs that print them.
                    s3_key = f'prompts/{destination_instance_id}/{original_prompt_id}/{uuid.uuid4().hex}.wav'

                    try:
                        s3_client.upload_file(temp_file_path, temp_bucket, s3_key,
                                              ExtraArgs={'ExpectedBucketOwner': dest_account_id})
                        s3_uri = f's3://{temp_bucket}/{s3_key}'
                        # Mask the bucket only: the key holds IDs that the logs keep.
                        print(f"Uploaded prompt to S3: s3://{mask_account_ids(temp_bucket)}/{s3_key}")
                    except Exception as e:
                        reraise_if_fatal(e)
                        print(f"Error uploading to S3: {describe_error(e)}")
                        failed_prompts.append({
                            'originalId': original_prompt_id,
                            'error': describe_error(e), 'errorCode': error_code_of(e)
                        })
                        continue
                finally:
                    shutil.rmtree(temp_dir, ignore_errors=True)

                # Create prompt in destination
                create_params = {
                    'InstanceId': destination_instance_id,
                    'Name': prompt_name,
                    'S3Uri': s3_uri
                }

                if prompt.get('Description'):
                    create_params['Description'] = prompt['Description']

                if prompt.get('Tags'):
                    create_params['Tags'] = prompt['Tags']

                try:
                    response = connect_client.create_prompt(**create_params)
                    new_prompt_id = response['PromptId']

                    prompt_mapping[original_prompt_id] = new_prompt_id
                    created_prompts.append({
                        'originalId': original_prompt_id,
                        'newId': new_prompt_id,
                        'status': 'created'
                    })

                    print(f"Created prompt: {redact(prompt_name)} ({original_prompt_id} -> {new_prompt_id})")

                except ClientError as e:
                    reraise_if_fatal(e)
                    if error_code_of(e) in EXISTS_ERROR_CODES:
                        print(f"Prompt {original_prompt_id} create failed ({error_code_of(e)}), looking the name up...")
                        existing_id, failure = existing.recheck(prompt_name, e, original_prompt_id)
                        if existing_id:
                            prompt_mapping[original_prompt_id] = existing_id
                            skipped_prompts.append({
                                'originalId': original_prompt_id,
                                'existingId': existing_id,
                                'status': 'existing'
                            })
                        else:
                            failed_prompts.append(failure)
                    else:
                        raise e

            except Exception as e:
                reraise_if_fatal(e)
                print(f"Error: {describe_error(e)}")
                failed_prompts.append({
                    'originalId': prompt.get('PromptId', 'Unknown'),
                    'error': describe_error(e), 'errorCode': error_code_of(e)
                })
                continue

        print(f"Successfully created {len(created_prompts)} prompts")
        print(f"Skipped {len(skipped_prompts)} existing prompts")
        print(f"Failed to create {len(failed_prompts)} prompts")

        return {
            'statusCode': 200,
            'promptMapping': prompt_mapping,
            'createdPrompts': created_prompts,
            'skippedPrompts': skipped_prompts,
            'failedPrompts': failed_prompts,
            'summary': {
                'totalPrompts': len(prompts),
                'created': len(created_prompts),
                'skipped': len(skipped_prompts),
                'failed': len(failed_prompts)
            }
        }

    except Exception as e:
        reraise_if_fatal(e)
        print(f"Error creating prompts: {describe_error(e)}")
        return {
            'statusCode': 400,
            'error': describe_error(e),
            'promptMapping': prompt_mapping
        }


def harden_temp_bucket(s3_client, bucket_name, account_id):
    """Block public access, default encryption (SSE-S3) and a TLS only bucket policy.

    Applied every run, so a bucket created by an older version is fixed too.
    """
    s3_client.put_public_access_block(
        Bucket=bucket_name,
        ExpectedBucketOwner=account_id,
        PublicAccessBlockConfiguration={
            'BlockPublicAcls': True, 'IgnorePublicAcls': True,
            'BlockPublicPolicy': True, 'RestrictPublicBuckets': True,
        },
    )
    s3_client.put_bucket_encryption(
        Bucket=bucket_name,
        ExpectedBucketOwner=account_id,
        ServerSideEncryptionConfiguration={
            'Rules': [{'ApplyServerSideEncryptionByDefault': {'SSEAlgorithm': 'AES256'}}]
        },
    )
    partition = s3_client.meta.partition
    s3_client.put_bucket_policy(
        Bucket=bucket_name,
        ExpectedBucketOwner=account_id,
        Policy=json.dumps({
            'Version': '2012-10-17',
            'Statement': [{
                'Sid': 'DenyInsecureTransport',
                'Effect': 'Deny',
                'Principal': '*',
                'Action': 's3:*',
                'Resource': [f'arn:{partition}:s3:::{bucket_name}', f'arn:{partition}:s3:::{bucket_name}/*'],
                'Condition': {'Bool': {'aws:SecureTransport': 'false'}},
            }],
        }),
    )


def get_or_create_temp_bucket(s3_client, region, account_id):
    """
    Get or create a temporary S3 bucket for prompt files
    For cross-account, this creates the bucket in the destination account.
    The bucket must belong to the destination account (ExpectedBucketOwner), and
    is hardened on every run.
    """
    bucket_name = f'{TEMP_BUCKET_PREFIX}-{account_id}-{region}'

    try:
        # Check if bucket exists and is ours
        s3_client.head_bucket(Bucket=bucket_name, ExpectedBucketOwner=account_id)
        print(f"Using existing bucket: {mask_account_ids(bucket_name)}")
        harden_temp_bucket(s3_client, bucket_name, account_id)
        return bucket_name
    except ClientError as e:
        reraise_if_fatal(e)
        error_code = e.response['Error']['Code']
        if error_code == '404' or error_code == '403':
            # Bucket doesn't exist or we don't have access, try to create it
            print(f"Creating bucket: {mask_account_ids(bucket_name)}")
            try:
                if region == 'us-east-1':
                    s3_client.create_bucket(Bucket=bucket_name)
                else:
                    s3_client.create_bucket(
                        Bucket=bucket_name,
                        CreateBucketConfiguration={'LocationConstraint': region}
                    )

                # Set lifecycle policy to auto-delete files after 1 day
                s3_client.put_bucket_lifecycle_configuration(
                    Bucket=bucket_name,
                    LifecycleConfiguration={
                        'Rules': [
                            {
                                'ID': 'DeleteTempPrompts',
                                'Status': 'Enabled',
                                'Filter': {'Prefix': 'prompts/'},
                                'Expiration': {'Days': 1}
                            }
                        ]
                    }
                )
                harden_temp_bucket(s3_client, bucket_name, account_id)

                print(f"Created bucket with lifecycle policy: {mask_account_ids(bucket_name)}")
                return bucket_name
            except Exception as create_error:
                reraise_if_fatal(create_error)
                print(f"Error creating bucket: {describe_error(create_error)}")
                raise
        else:
            print(f"Error checking bucket: {describe_error(e)}")
            raise
