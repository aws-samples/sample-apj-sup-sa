"""
Amplify Hosting utilities.
Shared between CFN custom resource and manual deploy scripts.
"""
import boto3
import json
import os
import tempfile
import time
import urllib.request


def get_amplify_client(region=None):
    """Get Amplify client."""
    region = region or os.environ.get('AWS_REGION', 'us-west-2')
    return boto3.client('amplify', region_name=region)


def get_s3_client(region=None):
    """Get S3 client."""
    region = region or os.environ.get('AWS_REGION', 'us-west-2')
    return boto3.client('s3', region_name=region)


def create_amplify_app(app_name, region=None):
    """Create or get existing Amplify app.
    
    Returns:
        tuple: (app_id, default_domain)
    """
    client = get_amplify_client(region)
    
    # Check if app already exists
    try:
        response = client.list_apps()
        for app in response.get('apps', []):
            if app['name'] == app_name:
                print(f"Using existing Amplify app: {app['appId']}")
                return app['appId'], app['defaultDomain']
    except Exception as e:
        print(f"Warning listing apps: {e}")
    
    # Create new app
    print(f"Creating Amplify app: {app_name}")
    response = client.create_app(
        name=app_name,
        platform='WEB',
        customRules=[{
            'source': '</^[^.]+$|\\.(?!(css|gif|ico|jpg|js|png|txt|svg|woff|woff2|ttf|map|json)$)([^.]+$)/>',
            'target': '/index.html',
            'status': '200'
        }]
    )
    
    return response['app']['appId'], response['app']['defaultDomain']


def create_branch(app_id, branch_name, region=None):
    """Create or get existing branch.
    
    Returns:
        dict: Branch details
    """
    client = get_amplify_client(region)
    
    try:
        response = client.get_branch(appId=app_id, branchName=branch_name)
        print(f"Using existing branch: {branch_name}")
        return response['branch']
    except client.exceptions.NotFoundException:
        pass
    
    print(f"Creating branch: {branch_name}")
    response = client.create_branch(
        appId=app_id,
        branchName=branch_name,
        stage='PRODUCTION',
        enableAutoBuild=False
    )
    return response['branch']


def deploy_from_zip(app_id, branch_name, zip_path, region=None):
    """Deploy to Amplify from local ZIP file.
    
    Returns:
        str: Job ID
    """
    client = get_amplify_client(region)
    
    print(f"Starting deployment from {zip_path}")
    
    # Create deployment to get presigned URL
    response = client.create_deployment(
        appId=app_id,
        branchName=branch_name
    )
    
    job_id = response['jobId']
    zip_upload_url = response['zipUploadUrl']
    
    # Upload ZIP to presigned URL
    print(f"Uploading to Amplify (job: {job_id})")
    # Amplify returns an https presigned-S3 upload URL; verify the scheme before
    # opening it (closes the B310 file://-scheme risk).
    if not zip_upload_url.lower().startswith('https://'):
        raise ValueError('Amplify zipUploadUrl must be https')
    with open(zip_path, 'rb') as f:
        req = urllib.request.Request(
            zip_upload_url,
            data=f.read(),
            method='PUT',
            headers={'Content-Type': 'application/zip'}
        )
        urllib.request.urlopen(req)  # nosec B310 - https scheme verified above
    
    # Start deployment
    client.start_deployment(
        appId=app_id,
        branchName=branch_name,
        jobId=job_id
    )
    
    return job_id


def deploy_from_s3(app_id, branch_name, bucket, key, region=None):
    """Deploy to Amplify from S3 ZIP file.
    
    Returns:
        str: Job ID
    """
    s3 = get_s3_client(region)
    
    print(f"Deploying from s3://{bucket}/{key}")
    
    # Download ZIP from S3
    with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as tmp:
        s3.download_file(bucket, key, tmp.name)
        zip_path = tmp.name
    
    try:
        return deploy_from_zip(app_id, branch_name, zip_path, region)
    finally:
        os.unlink(zip_path)


def wait_for_deployment(app_id, branch_name, job_id, timeout=300, region=None):
    """Wait for deployment to complete.
    
    Returns:
        bool: True if successful
    
    Raises:
        Exception: If deployment fails or times out
    """
    client = get_amplify_client(region)
    
    print(f"Waiting for deployment {job_id}")
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        response = client.get_job(
            appId=app_id,
            branchName=branch_name,
            jobId=job_id
        )
        
        status = response['job']['summary']['status']
        print(f"Status: {status}")
        
        if status == 'SUCCEED':
            return True
        elif status in ['FAILED', 'CANCELLED']:
            raise Exception(f"Deployment failed: {status}")
        
        time.sleep(10)
    
    raise Exception("Deployment timed out")


def delete_amplify_app(app_id, region=None):
    """Delete Amplify app."""
    client = get_amplify_client(region)
    
    print(f"Deleting Amplify app: {app_id}")
    try:
        client.delete_app(appId=app_id)
    except Exception as e:
        print(f"Warning deleting app: {e}")


def update_app_env_vars(app_id, env_vars, region=None):
    """Update Amplify app environment variables."""
    client = get_amplify_client(region)
    
    if env_vars:
        client.update_app(
            appId=app_id,
            environmentVariables=env_vars
        )
        print(f"Updated {len(env_vars)} environment variables")
