"""
Amplify Hosting Custom Resource Lambda
Deploys React UI to AWS Amplify Hosting.

Demo-mode only — workshop mode does not deploy this stack.

Two responsibilities beyond plain deployment:
  1. Inject runtime config into the build (config.js) so CFN-time values
     (Cognito IDs, agent runtime endpoint) reach the SPA at request time
     instead of being baked at build time.
  2. Add the Amplify app URL to the Cognito user-pool client's allowed
     callback/logout URLs so the OAuth Authorization Code flow works.
"""
import io
import json
import os
import tempfile
import urllib.request
import zipfile

import boto3

from common.amplify_utils import (
    create_amplify_app,
    create_branch,
    deploy_from_zip,
    wait_for_deployment,
    delete_amplify_app,
    update_app_env_vars,
)

REGION = os.environ.get('AWS_REGION', 'us-west-2')


def send_cfn_response(event, context, status, data=None, reason=None):
    """Send response to CloudFormation."""
    response_body = {
        'Status': status,
        'Reason': reason or f'See CloudWatch Log Stream: {context.log_stream_name}',
        'PhysicalResourceId': data.get('AppId', context.log_stream_name) if data else context.log_stream_name,
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


def _runtime_config_from_env_vars(env_vars):
    """Strip the REACT_APP_ prefix and produce the JS object the SPA reads."""
    config = {}
    for k, v in env_vars.items():
        key = k[len('REACT_APP_'):] if k.startswith('REACT_APP_') else k
        config[key] = v
    return config


def _patch_build_zip_with_config(src_path, dst_path, runtime_config):
    """Rewrite config.js inside the build ZIP with real CFN-time values.

    The placeholder shipped in the build sets `window.__APP_CONFIG__ = {}`.
    We replace it with the real values so the SPA can read them at runtime.
    """
    config_js = f"window.__APP_CONFIG__ = {json.dumps(runtime_config)};\n"
    config_bytes = config_js.encode('utf-8')

    with zipfile.ZipFile(src_path, 'r') as src, zipfile.ZipFile(dst_path, 'w', zipfile.ZIP_DEFLATED) as dst:
        replaced = False
        for item in src.infolist():
            if item.filename == 'config.js':
                dst.writestr('config.js', config_bytes)
                replaced = True
            else:
                dst.writestr(item, src.read(item.filename))
        if not replaced:
            # Build came from a UI version that didn't ship the placeholder; add one.
            dst.writestr('config.js', config_bytes)

    print(f"Injected runtime config keys: {sorted(runtime_config.keys())}")


def _add_url_to_cognito_client(user_pool_id, client_id, app_url):
    """Add the Amplify app URL to the user-pool client's callback/logout lists.

    Best-effort — logs and returns False on error rather than failing the stack,
    so the deploy still completes and the URL can be added manually if needed.
    """
    if not user_pool_id or not client_id or not app_url:
        print("Skipping Cognito patch — missing user_pool_id, client_id, or app_url")
        return False

    cognito = boto3.client('cognito-idp', region_name=REGION)
    try:
        existing = cognito.describe_user_pool_client(
            UserPoolId=user_pool_id,
            ClientId=client_id,
        )['UserPoolClient']
    except Exception as e:
        print(f"Warning: describe_user_pool_client failed: {e}")
        return False

    callback_urls = list(existing.get('CallbackURLs') or [])
    logout_urls = list(existing.get('LogoutURLs') or [])

    # Match the UI's window.location.origin + pathname format (always trailing
    # slash on the root path) — Cognito strict-matches these strings.
    callback_url = app_url if app_url.endswith('/') else app_url + '/'
    logout_url = callback_url

    changed = False
    if callback_url not in callback_urls:
        callback_urls.append(callback_url)
        changed = True
    if logout_url not in logout_urls:
        logout_urls.append(logout_url)
        changed = True

    if not changed:
        print("Cognito client already allows the Amplify URL")
        return True

    # update_user_pool_client requires re-passing fields it would otherwise clear.
    update_args = {
        'UserPoolId': user_pool_id,
        'ClientId': client_id,
        'CallbackURLs': callback_urls,
        'LogoutURLs': logout_urls,
    }
    for field in (
        'AllowedOAuthFlows',
        'AllowedOAuthScopes',
        'AllowedOAuthFlowsUserPoolClient',
        'SupportedIdentityProviders',
        'ExplicitAuthFlows',
        'ClientName',
    ):
        if field in existing:
            update_args[field] = existing[field]

    try:
        cognito.update_user_pool_client(**update_args)
        print(f"Added {app_url} to Cognito client {client_id} callback/logout URLs")
        return True
    except Exception as e:
        print(f"Warning: update_user_pool_client failed: {e}")
        return False


def lambda_handler(event, context):
    """
    CloudFormation Custom Resource handler.

    Properties:
        AppName: Amplify app name
        BranchName: Branch name (default: main)
        ArtifactsBucket: S3 bucket with UI build ZIP
        ArtifactsKey: S3 key for UI build ZIP
        EnvironmentVariables: Dict of env vars for the app (also injected as runtime config)
        CognitoUserPoolId: User pool whose client to patch with the Amplify URL
        CognitoUserClientId: Client ID to patch
    """
    print(f"Event: {json.dumps(event)}")

    request_type = event.get('RequestType', 'Create')
    props = event.get('ResourceProperties', {})

    app_name = props.get('AppName', 'agentic-analytics-ui')
    branch_name = props.get('BranchName', 'main')
    bucket = props.get('ArtifactsBucket')
    key = props.get('ArtifactsKey', 'ui/build.zip')
    env_vars = props.get('EnvironmentVariables', {}) or {}
    cognito_user_pool_id = props.get('CognitoUserPoolId', '')
    cognito_user_client_id = props.get('CognitoUserClientId', '')

    try:
        if request_type == 'Delete':
            app_id = event.get('PhysicalResourceId', '')
            if app_id and app_id.startswith('d'):  # Amplify app IDs start with 'd'
                delete_amplify_app(app_id, REGION)

            send_cfn_response(event, context, 'SUCCESS', {'AppId': app_id})
            return

        # Create or Update
        app_id, default_domain = create_amplify_app(app_name, REGION)
        create_branch(app_id, branch_name, REGION)

        # Set Amplify console env vars (informational; the SPA reads runtime config)
        if env_vars:
            update_app_env_vars(app_id, env_vars, REGION)

        # Deploy with runtime config injected into the build
        if bucket:
            s3 = boto3.client('s3', region_name=REGION)
            with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as src_tmp, \
                 tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as patched_tmp:
                try:
                    s3.download_file(bucket, key, src_tmp.name)
                    runtime_config = _runtime_config_from_env_vars(env_vars)
                    _patch_build_zip_with_config(src_tmp.name, patched_tmp.name, runtime_config)
                    job_id = deploy_from_zip(app_id, branch_name, patched_tmp.name, REGION)
                    wait_for_deployment(app_id, branch_name, job_id, region=REGION)
                finally:
                    os.unlink(src_tmp.name)
                    os.unlink(patched_tmp.name)

        app_url = f"https://{branch_name}.{default_domain}"

        # Bug fix: the Cognito user-pool client only allows localhost callbacks
        # by default, so OAuth from the Amplify URL would fail. Add it now.
        _add_url_to_cognito_client(cognito_user_pool_id, cognito_user_client_id, app_url)

        send_cfn_response(event, context, 'SUCCESS', {
            'AppId': app_id,
            'AppUrl': app_url,
            'DefaultDomain': default_domain
        })

    except Exception as e:
        print(f"Error: {e}")
        send_cfn_response(event, context, 'FAILED', reason=str(e))
        raise
