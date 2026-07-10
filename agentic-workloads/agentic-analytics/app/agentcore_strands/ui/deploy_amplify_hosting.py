#!/usr/bin/env python3
"""
Deploy React UI to AWS Amplify Hosting.
Can be run standalone (workshop) or invoked by Lambda (demo mode).

Uses shared utilities from infrastructure/common/

Usage:
    python deploy_amplify_hosting.py [--app-name NAME] [--env-file PATH]
"""
import argparse
import os
import sys
import json
import boto3
from pathlib import Path

# Add infrastructure/common to path
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from common.amplify_utils import (
    create_amplify_app,
    create_branch,
    deploy_from_zip,
    wait_for_deployment,
)
from common.build_utils import (
    build_react_app,
    create_deployment_zip,
)

# Configuration
REGION = os.getenv("AWS_REGION", "us-west-2")
APP_NAME = os.getenv("AMPLIFY_APP_NAME", "agentic-analytics-ui")
BRANCH_NAME = "main"

# UI directory
UI_DIR = PROJECT_ROOT / "app" / "ui"


def load_env_vars(env_file=None):
    """Load environment variables from file."""
    env_vars = {}
    
    if env_file and Path(env_file).exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    env_vars[key.strip()] = value.strip()
    
    return env_vars


def _update_cognito_callbacks(amplify_url):
    """Add Amplify URL to Cognito user login client callback/logout URLs."""
    from dotenv import dotenv_values
    config_path = SCRIPT_DIR.parent / 'config.env'
    if not config_path.exists():
        print("  No config.env — skipping Cognito callback update")
        return
    config = dotenv_values(config_path)
    client_id = config.get('COGNITO_USER_LOGIN_CLIENT_ID')
    pool_id = config.get('COGNITO_USER_POOL_ID')
    if not client_id or not pool_id:
        print("  No COGNITO_USER_LOGIN_CLIENT_ID — skipping Cognito callback update")
        return
    cognito = boto3.client('cognito-idp', region_name=REGION)
    desc = cognito.describe_user_pool_client(UserPoolId=pool_id, ClientId=client_id)['UserPoolClient']
    callbacks = desc.get('CallbackURLs', [])
    logouts = desc.get('LogoutURLs', [])
    if amplify_url not in callbacks:
        callbacks.append(amplify_url)
        callbacks.append(amplify_url.rstrip('/') + '/')  # Add trailing-slash variant
        logouts.append(amplify_url)
        logouts.append(amplify_url.rstrip('/') + '/')
        cognito.update_user_pool_client(
            UserPoolId=pool_id, ClientId=client_id,
            ClientName=desc.get('ClientName', 'user-login'),
            CallbackURLs=callbacks, LogoutURLs=logouts,
            AllowedOAuthFlows=desc.get('AllowedOAuthFlows', ['code']),
            AllowedOAuthScopes=desc.get('AllowedOAuthScopes', ['openid', 'profile', 'email']),
            AllowedOAuthFlowsUserPoolClient=True,
            SupportedIdentityProviders=desc.get('SupportedIdentityProviders', ['COGNITO']),
            ExplicitAuthFlows=desc.get('ExplicitAuthFlows', []),
            ReadAttributes=desc.get('ReadAttributes', []),
            WriteAttributes=desc.get('WriteAttributes', []),
        )
        print(f"  [OK] Added {amplify_url} to Cognito callback URLs")
    else:
        print(f"  [OK] Amplify URL already in Cognito callbacks")


def deploy_amplify(app_name=None, env_vars=None, skip_build=False, build_dir=None):
    """
    Main deployment function.
    
    Args:
        app_name: Amplify app name
        env_vars: Environment variables for React build
        skip_build: Skip npm build (use existing build directory)
        build_dir: Path to existing build directory (if skip_build=True)
    
    Returns:
        dict with app_id, app_url
    """
    app_name = app_name or APP_NAME
    
    # Build React app
    if not skip_build:
        build_dir = build_react_app(UI_DIR, env_vars)
    elif build_dir:
        build_dir = Path(build_dir)
    else:
        build_dir = UI_DIR / "build"
    
    if not build_dir.exists():
        raise Exception(f"Build directory not found: {build_dir}")
    
    # Create ZIP
    zip_path = create_deployment_zip(build_dir)
    
    try:
        # Create Amplify app and branch
        app_id, default_domain = create_amplify_app(app_name, REGION)
        create_branch(app_id, BRANCH_NAME, REGION)
        
        # Deploy
        job_id = deploy_from_zip(app_id, BRANCH_NAME, zip_path, REGION)
        
        # Wait for completion
        wait_for_deployment(app_id, BRANCH_NAME, job_id, region=REGION)
        
        app_url = f"https://{BRANCH_NAME}.{default_domain}"
        
        # Update Cognito callback URLs to include Amplify URL
        try:
            _update_cognito_callbacks(app_url)
        except Exception as e:
            print(f"⚠️  Failed to update Cognito callbacks: {e}")
        
        print(f"""
╔══════════════════════════════════════════════════════════════════╗
║                    Deployment Complete!                          ║
╠══════════════════════════════════════════════════════════════════╣
║  App ID:  {app_id:<52} ║
║  URL:     {app_url:<52} ║
╚══════════════════════════════════════════════════════════════════╝
""")
        
        return {'app_id': app_id, 'app_url': app_url}
        
    finally:
        os.unlink(zip_path)


def main():
    parser = argparse.ArgumentParser(description='Deploy UI to Amplify Hosting')
    parser.add_argument('--app-name', default=APP_NAME, help='Amplify app name')
    parser.add_argument('--env-file', help='Path to .env file for build')
    parser.add_argument('--skip-build', action='store_true', help='Skip npm build')
    parser.add_argument('--build-dir', help='Path to existing build directory')
    
    args = parser.parse_args()
    
    env_vars = load_env_vars(args.env_file)
    
    deploy_amplify(
        app_name=args.app_name,
        env_vars=env_vars,
        skip_build=args.skip_build,
        build_dir=args.build_dir
    )


if __name__ == '__main__':
    main()
