"""
Bedrock Knowledge Base Ingestion Custom Resource Lambda
Creates KB with Aurora pgvector, S3 data source, and ingests business-context.md.

Can be invoked:
1. Via CloudFormation Custom Resource (automatic deployment)
2. Via direct Lambda invoke (workshop mode)
"""
import boto3
import json
import os
import time
import urllib.request

REGION = os.environ.get('AWS_REGION', 'us-west-2')
EMBEDDING_DIMENSION = 1024  # Titan v2

bedrock_agent = boto3.client('bedrock-agent', region_name=REGION)
s3_client = boto3.client('s3', region_name=REGION)
iam_client = boto3.client('iam', region_name=REGION)
rds_client = boto3.client('rds-data', region_name=REGION)


def execute_sql(resource_arn, secret_arn, database, sql):
    """Execute SQL via RDS Data API."""
    return rds_client.execute_statement(
        resourceArn=resource_arn,
        secretArn=secret_arn,
        database=database,
        sql=sql
    )


def create_embeddings_table(aurora_cluster_arn, aurora_secret_arn, database_name):
    """Create pgvector table for Bedrock Knowledge Base."""
    print("Creating bedrock_kb_embeddings table...")
    
    # Ensure pgvector extension exists
    try:
        execute_sql(aurora_cluster_arn, aurora_secret_arn, database_name,
                   "CREATE EXTENSION IF NOT EXISTS vector")
        print("  [OK] pgvector extension enabled")
    except Exception as e:
        print(f"  Extension may already exist: {e}")
    
    # Create Bedrock KB compatible table
    create_sql = f"""
    CREATE TABLE IF NOT EXISTS bedrock_kb_embeddings (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        chunks TEXT NOT NULL,
        embedding vector({EMBEDDING_DIMENSION}),
        metadata JSON
    )
    """
    execute_sql(aurora_cluster_arn, aurora_secret_arn, database_name, create_sql)
    
    # Create GIN index on chunks for full-text search (required by Bedrock KB)
    execute_sql(aurora_cluster_arn, aurora_secret_arn, database_name,
               "CREATE INDEX IF NOT EXISTS idx_chunks_gin ON bedrock_kb_embeddings USING gin (to_tsvector('simple', chunks))")
    
    # Create HNSW index on embedding for vector search (required by Bedrock KB)
    execute_sql(aurora_cluster_arn, aurora_secret_arn, database_name,
               "CREATE INDEX IF NOT EXISTS idx_embedding_hnsw ON bedrock_kb_embeddings USING hnsw (embedding vector_cosine_ops)")
    print("  [OK] Table and indexes created")


def create_kb_role(kb_name, aurora_secret_arn, kb_docs_bucket=None):
    """Create IAM role for Bedrock Knowledge Base."""
    role_name = f"{kb_name}-role"
    
    assume_policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "bedrock.amazonaws.com"},
            "Action": "sts:AssumeRole"
        }]
    }
    
    statements = [
        {
            "Effect": "Allow",
            "Action": ["bedrock:InvokeModel"],
            "Resource": f"arn:aws:bedrock:{REGION}::foundation-model/amazon.titan-embed-text-v2:0"
        },
        {
            "Effect": "Allow",
            "Action": ["secretsmanager:GetSecretValue"],
            "Resource": aurora_secret_arn
        },
        {
            "Effect": "Allow",
            "Action": [
                "rds-data:ExecuteStatement",
                "rds-data:BatchExecuteStatement"
            ],
            "Resource": "*"
        },
        {
            "Effect": "Allow",
            "Action": ["rds:DescribeDBClusters"],
            "Resource": "*"
        }
    ]
    
    # Add S3 read permission for KB docs bucket
    if kb_docs_bucket:
        statements.append({
            "Effect": "Allow",
            # GetBucketLocation is REQUIRED: without it Bedrock's ingestion S3 client
            # can't resolve the bucket region, falls back to the global endpoint, and
            # gets 301 PermanentRedirect ("must be addressed using the specified endpoint")
            # on every ingestion in us-east-1.
            "Action": ["s3:GetObject", "s3:ListBucket", "s3:GetBucketLocation"],
            "Resource": [
                f"arn:aws:s3:::{kb_docs_bucket}",
                f"arn:aws:s3:::{kb_docs_bucket}/*"
            ]
        })
    
    policy = {
        "Version": "2012-10-17",
        "Statement": statements
    }
    
    try:
        response = iam_client.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(assume_policy),
            Description='Role for Bedrock Knowledge Base'
        )
        role_arn = response['Role']['Arn']
        print(f"[OK] Created IAM role: {role_name}")
        time.sleep(10)  # Wait for role propagation
    except iam_client.exceptions.EntityAlreadyExistsException:
        response = iam_client.get_role(RoleName=role_name)
        role_arn = response['Role']['Arn']
        print(f"[OK] Using existing IAM role: {role_name}")
    
    # Always update the policy to ensure correct permissions
    iam_client.put_role_policy(
        RoleName=role_name,
        PolicyName='BedrockKBPolicy',
        PolicyDocument=json.dumps(policy)
    )
    print(f"[OK] Updated IAM role policy with current secret ARN")
    # Initial wait for policy propagation; create_knowledge_base also retries on the
    # rds:DescribeDBClusters propagation race, so this is a best-effort head start.
    time.sleep(20)
    return role_arn


def _find_kb_by_name(name):
    """Paginate all KBs and return the id of the one matching `name` (or None)."""
    _next = None
    while True:
        kwargs = {'maxResults': 100}
        if _next:
            kwargs['nextToken'] = _next
        resp = bedrock_agent.list_knowledge_bases(**kwargs)
        for kb in resp.get('knowledgeBaseSummaries', []):
            if kb['name'] == name:
                return kb['knowledgeBaseId']
        _next = resp.get('nextToken')
        if not _next:
            return None


def create_knowledge_base(kb_name, role_arn, aurora_cluster_arn, aurora_secret_arn, database_name):
    """Create Bedrock Knowledge Base with Aurora pgvector."""
    print(f"Creating Knowledge Base: {kb_name}")
    
    # Check if KB already exists (including alternate names).
    # IMPORTANT: paginate — list_knowledge_bases() returns only the first page
    # (~10 KBs). In a shared/sandbox account with many KBs, a single page can miss
    # our leftover KB, and we'd then try to create it again -> ConflictException.
    try:
        kb_map = {}
        _next = None
        while True:
            kwargs = {'maxResults': 100}
            if _next:
                kwargs['nextToken'] = _next
            response = bedrock_agent.list_knowledge_bases(**kwargs)
            for kb in response.get('knowledgeBaseSummaries', []):
                kb_map[kb['name']] = kb
            _next = response.get('nextToken')
            if not _next:
                break

        # Check primary name first, then alternate
        for name in [kb_name, f"{kb_name}-v2"]:
            if name in kb_map:
                kb = kb_map[name]
                kb_id = kb['knowledgeBaseId']
                status = kb['status']
                print(f"Found KB '{name}': {kb_id} with status: {status}")
                
                if status == 'ACTIVE':
                    print(f"[OK] Using existing active KB: {kb_id}")
                    return kb_id
                elif status == 'CREATING':
                    wait_for_kb(kb_id)
                    return kb_id
                elif status in ['DELETE_UNSUCCESSFUL', 'FAILED']:
                    print(f"KB '{name}' stuck in {status}, trying next...")
                    continue
                elif status == 'DELETING':
                    print(f"KB is deleting, waiting...")
                    for _ in range(30):
                        time.sleep(5)
                        try:
                            bedrock_agent.get_knowledge_base(knowledgeBaseId=kb_id)
                        except:
                            print(f"KB deleted")
                            break
        
        # Determine which name to use for new KB
        if kb_name in kb_map and kb_map[kb_name]['status'] in ['DELETE_UNSUCCESSFUL', 'FAILED']:
            kb_name = f"{kb_name}-v2"
            if kb_name in kb_map:
                kb_name = f"{kb_name}-v3"
        print(f"Creating new KB with name: {kb_name}")
    except Exception as e:
        print(f"Error checking KBs: {e}")
    
    kb_kwargs = dict(
        name=kb_name,
        description='Agentic Analytics business context knowledge base',
        roleArn=role_arn,
        knowledgeBaseConfiguration={
            'type': 'VECTOR',
            'vectorKnowledgeBaseConfiguration': {
                'embeddingModelArn': f'arn:aws:bedrock:{REGION}::foundation-model/amazon.titan-embed-text-v2:0'
            }
        },
        storageConfiguration={
            'type': 'RDS',
            'rdsConfiguration': {
                'resourceArn': aurora_cluster_arn,
                'credentialsSecretArn': aurora_secret_arn,
                'databaseName': database_name,
                'tableName': 'bedrock_kb_embeddings',
                'fieldMapping': {
                    'primaryKeyField': 'id',
                    'vectorField': 'embedding',
                    'textField': 'chunks',
                    'metadataField': 'metadata'
                }
            }
        }
    )

    # Bedrock validates the RDS storage config by assuming the KB role and calling
    # rds:DescribeDBClusters. On a FRESH account the role + its inline policy were
    # just created, so IAM may not have propagated yet — CreateKnowledgeBase then
    # fails with a ValidationException wrapping a 403 "not authorized to perform:
    # rds:DescribeDBClusters". The permission IS granted (see create_kb_role); it's
    # purely an eventual-consistency race. Retry with backoff until IAM catches up.
    response = None
    for attempt in range(1, 13):  # up to ~12 tries / ~5 min
        try:
            response = bedrock_agent.create_knowledge_base(**kb_kwargs)
            break
        except bedrock_agent.exceptions.ConflictException:
            # A KB with this name already exists (leftover from a prior rolled-back
            # attempt that the paginated pre-check somehow still missed, or a
            # concurrent create). Reuse it instead of failing the whole deploy.
            print(f"[conflict] KB '{kb_kwargs['name']}' already exists — looking it up to reuse")
            existing = _find_kb_by_name(kb_kwargs['name'])
            if existing:
                kb_id = existing
                print(f"[OK] Reusing existing KB: {kb_id}")
                wait_for_kb_active(kb_id)
                return kb_id
            print("[conflict] could not resolve existing KB id; retrying after 15s")
            time.sleep(15)
        except bedrock_agent.exceptions.ValidationException as e:
            msg = str(e)
            transient = ('rds:DescribeDBClusters' in msg or 'not authorized' in msg
                         or 'storage configuration provided is invalid' in msg)
            if not transient:
                raise
            print(f"[retry {attempt}] KB role perms not propagated yet ({msg[:140]}); sleeping 25s")
            time.sleep(25)
    if response is None:
        raise RuntimeError("create_knowledge_base failed after retries (propagation race or unresolved name conflict)")

    kb_id = response['knowledgeBase']['knowledgeBaseId']
    print(f"[OK] Created Knowledge Base: {kb_id}")
    
    # Wait for KB to be active
    wait_for_kb_active(kb_id)
    return kb_id


def wait_for_kb_active(kb_id, timeout=300):
    """Wait for Knowledge Base to become active."""
    print("Waiting for Knowledge Base to be active...")
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        response = bedrock_agent.get_knowledge_base(knowledgeBaseId=kb_id)
        status = response['knowledgeBase']['status']
        
        if status == 'ACTIVE':
            print(f"[OK] Knowledge Base is active")
            return True
        elif status == 'FAILED':
            raise Exception(f"Knowledge Base creation failed")
        
        print(f"  Status: {status}")
        time.sleep(10)
    
    raise Exception(f"Knowledge Base timed out after {timeout}s")


def create_s3_data_source(kb_id, ds_name, bucket_arn):
    """Create S3 data source with hierarchical chunking."""
    print(f"Creating S3 data source: {ds_name}")
    
    # Check if data source already exists
    try:
        response = bedrock_agent.list_data_sources(knowledgeBaseId=kb_id)
        for ds in response.get('dataSourceSummaries', []):
            if ds['name'] == ds_name:
                print(f"[OK] Data source already exists: {ds['dataSourceId']}")
                wait_for_data_source(kb_id, ds['dataSourceId'])
                return ds['dataSourceId']
    except Exception as e:
        print(f"Error listing data sources: {e}")
    
    response = bedrock_agent.create_data_source(
        knowledgeBaseId=kb_id,
        name=ds_name,
        description='Business context documentation',
        dataSourceConfiguration={
            'type': 'S3',
            's3Configuration': {
                'bucketArn': bucket_arn
            }
        },
        vectorIngestionConfiguration={
            'chunkingConfiguration': {
                'chunkingStrategy': 'HIERARCHICAL',
                'hierarchicalChunkingConfiguration': {
                    'levelConfigurations': [
                        {'maxTokens': 1000},
                        {'maxTokens': 300}
                    ],
                    'overlapTokens': 50
                }
            }
        }
    )
    
    ds_id = response['dataSource']['dataSourceId']
    print(f"[OK] Created S3 data source: {ds_id}")
    wait_for_data_source(kb_id, ds_id)
    return ds_id


def wait_for_data_source(kb_id, ds_id, timeout=120):
    """Wait for data source to become available."""
    print(f"Waiting for data source to be available...")
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        response = bedrock_agent.get_data_source(
            knowledgeBaseId=kb_id,
            dataSourceId=ds_id
        )
        status = response['dataSource']['status']
        
        if status == 'AVAILABLE':
            print(f"[OK] Data source is available")
            return True
        elif status == 'DELETE_UNSUCCESSFUL':
            raise Exception(f"Data source creation failed: {status}")
        
        print(f"  Data source status: {status}")
        time.sleep(5)
    
    raise Exception(f"Data source timed out after {timeout}s")


def start_ingestion(kb_id, ds_id):
    """Start ingestion job for S3 data source.

    Wraps the start + wait in an OUTER retry: a freshly-created S3 docs bucket can
    return 301 PermanentRedirect to Bedrock's data-source reader for a minute or two
    until the bucket's regional endpoint fully propagates, which surfaces as a FAILED
    ingestion job (not a start_ingestion_job exception). Retrying the whole job clears it.
    """
    for outer in range(6):  # up to ~6 tries; bucket endpoint propagates within ~1-2 min
        result = _run_one_ingestion(kb_id, ds_id)
        if result == 'COMPLETE':
            return True
        # result is the failure-reason string; retry only the transient S3-endpoint race
        if ('must be addressed using the specified endpoint' in result
                or 'PermanentRedirect' in result or 'Status Code: 301' in result) and outer < 5:
            print(f"  Ingestion hit S3 endpoint-propagation race (try {outer + 1}/6): {result[:160]} — retrying in 20s")
            time.sleep(20)
            continue
        raise Exception(f"Ingestion failed: {result}")
    raise Exception("Ingestion did not complete after retries")


def _run_one_ingestion(kb_id, ds_id):
    """Start one ingestion job and wait for it. Returns 'COMPLETE' or the failure-reason string."""
    print(f"Starting ingestion job...")

    # The Aurora RDS Data API (HttpEndpoint) can take a short while to become
    # usable after the cluster reports 'available'. StartIngestionJob then fails
    # with ValidationException ("HttpEndpoint is not enabled for resource ...").
    # Retry with backoff so this transient race doesn't fail the whole deploy.
    response = None
    for attempt in range(12):  # ~12 * 15s = 3 min
        try:
            response = bedrock_agent.start_ingestion_job(
                knowledgeBaseId=kb_id,
                dataSourceId=ds_id
            )
            break
        except Exception as e:
            msg = str(e)
            transient = ('HttpEndpoint' in msg or 'is not enabled' in msg
                         or 'ValidationException' in msg or 'storage configuration' in msg)
            if transient and attempt < 11:
                print(f"  Ingestion not ready (attempt {attempt + 1}/12): {msg[:160]} — retrying in 15s")
                time.sleep(15)
                continue
            raise
    if response is None:
        raise RuntimeError("start_ingestion_job did not succeed after retries")

    job_id = response['ingestionJob']['ingestionJobId']
    status = response['ingestionJob']['status']
    print(f"  Ingestion job started: {job_id} ({status})")

    # Wait for completion
    for _ in range(60):
        time.sleep(10)
        check = bedrock_agent.get_ingestion_job(
            knowledgeBaseId=kb_id,
            dataSourceId=ds_id,
            ingestionJobId=job_id
        )
        status = check['ingestionJob']['status']
        stats = check['ingestionJob'].get('statistics', {})
        print(f"  Status: {status} | Scanned: {stats.get('numberOfDocumentsScanned', 0)} | Indexed: {stats.get('numberOfNewDocumentsIndexed', 0)} | Failed: {stats.get('numberOfDocumentsFailed', 0)}")

        if status == 'COMPLETE':
            print(f"[OK] Ingestion complete")
            return 'COMPLETE'
        elif status == 'FAILED':
            reason = check['ingestionJob'].get('failureReasons', ['Unknown'])
            return str(reason)

    return 'Ingestion timed out'


def load_content_from_s3(bucket, key):
    """Load document content from S3."""
    print(f"Loading content from s3://{bucket}/{key}")
    response = s3_client.get_object(Bucket=bucket, Key=key)
    content = response['Body'].read().decode('utf-8')
    print(f"[OK] Loaded {len(content)} characters")
    return content


def send_cfn_response(event, context, status, data=None, reason=None):
    """Send response to CloudFormation."""
    response_body = {
        'Status': status,
        'Reason': reason or f'See CloudWatch Log Stream: {context.log_stream_name}',
        'PhysicalResourceId': event.get('PhysicalResourceId', context.log_stream_name),
        'StackId': event['StackId'],
        'RequestId': event['RequestId'],
        'LogicalResourceId': event['LogicalResourceId'],
        'Data': data or {}
    }
    
    # CFN always supplies an https presigned-S3 ResponseURL; verify the scheme
    # before opening it (closes the B310 file://-scheme risk).
    if not event['ResponseURL'].lower().startswith('https://'):
        raise ValueError('ResponseURL must be https')
    req = urllib.request.Request(
        event['ResponseURL'],
        data=json.dumps(response_body).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='PUT'
    )
    urllib.request.urlopen(req)  # nosec B310 - https scheme verified above


def delete_knowledge_base(kb_name):
    """Delete Knowledge Base and associated resources."""
    print(f"Deleting Knowledge Base: {kb_name}")
    
    try:
        response = bedrock_agent.list_knowledge_bases()
        for kb in response.get('knowledgeBaseSummaries', []):
            if kb['name'] == kb_name:
                kb_id = kb['knowledgeBaseId']
                
                # Delete data sources first
                ds_response = bedrock_agent.list_data_sources(knowledgeBaseId=kb_id)
                for ds in ds_response.get('dataSourceSummaries', []):
                    bedrock_agent.delete_data_source(
                        knowledgeBaseId=kb_id,
                        dataSourceId=ds['dataSourceId']
                    )
                    print(f"  Deleted data source: {ds['dataSourceId']}")
                
                # Delete KB
                bedrock_agent.delete_knowledge_base(knowledgeBaseId=kb_id)
                print(f"[OK] Deleted Knowledge Base: {kb_id}")
                return True
    except Exception as e:
        print(f"Error deleting KB: {e}")
    
    return False


def lambda_handler(event, context):
    """Lambda handler for both CFN Custom Resource and direct invocation."""
    print(f"Event: {json.dumps(event)}")
    
    is_cfn = 'RequestType' in event and 'ResponseURL' in event
    
    try:
        if is_cfn:
            request_type = event['RequestType']
            props = event['ResourceProperties']
            kb_name = props.get('KnowledgeBaseName', 'agentic-analytics-kb')
            
            if request_type == 'Delete':
                delete_knowledge_base(kb_name)
                send_cfn_response(event, context, 'SUCCESS')
                return {'status': 'success', 'message': 'Knowledge Base deleted'}
            
            aurora_cluster_arn = props['AuroraClusterArn']
            aurora_secret_arn = props['DatabaseSecretArn']
            database_name = props.get('DatabaseName', 'timely_unicorn')
            kb_docs_bucket = props.get('KBDocsBucket', props.get('ArtifactsBucket', ''))
            content_key = props.get('BusinessContextKey', 'docs/business-context.md')
        else:
            kb_name = event.get('KnowledgeBaseName', 'agentic-analytics-kb')
            aurora_cluster_arn = event.get('AuroraClusterArn') or os.environ.get('AURORA_CLUSTER_ARN')
            aurora_secret_arn = event.get('DatabaseSecretArn') or os.environ.get('DATABASE_SECRET_ARN')
            database_name = event.get('DatabaseName', 'timely_unicorn')
            kb_docs_bucket = event.get('KBDocsBucket') or event.get('ArtifactsBucket') or os.environ.get('ARTIFACTS_BUCKET')
            content_key = event.get('BusinessContextKey', 'docs/business-context.md')
        
        # Create IAM role (with S3 access for KB docs bucket)
        role_arn = create_kb_role(kb_name, aurora_secret_arn, kb_docs_bucket)
        
        # Create pgvector table (must exist before KB creation)
        create_embeddings_table(aurora_cluster_arn, aurora_secret_arn, database_name)
        
        # Create Knowledge Base
        kb_id = create_knowledge_base(kb_name, role_arn, aurora_cluster_arn, aurora_secret_arn, database_name)
        
        # Create S3 data source pointing to KB docs bucket
        bucket_arn = f"arn:aws:s3:::{kb_docs_bucket}"
        ds_id = create_s3_data_source(kb_id, 'business-context-source', bucket_arn)
        
        # Start ingestion job
        start_ingestion(kb_id, ds_id)
        
        if is_cfn:
            send_cfn_response(event, context, 'SUCCESS', {
                'KnowledgeBaseId': kb_id,
                'DataSourceId': ds_id
            })
        
        return {
            'status': 'success',
            'KnowledgeBaseId': kb_id,
            'DataSourceId': ds_id
        }
        
    except Exception as e:
        print(f"Error: {str(e)}")
        if is_cfn:
            send_cfn_response(event, context, 'FAILED', reason=str(e))
        raise
