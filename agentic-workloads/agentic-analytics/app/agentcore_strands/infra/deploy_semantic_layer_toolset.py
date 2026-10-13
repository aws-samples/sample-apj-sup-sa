#!/usr/bin/env python3
"""
Deploy Semantic Layer (Cube Core) Lambda.

Creates a Lambda function that proxies to Cube Core's REST API (/meta and /load)
with two tools: cube_meta_tool and cube_query_tool.

The Lambda is deployed in the same VPC private subnets as the other toolset
Lambdas, using the LambdaSecurityGroup (which has egress to Cube on port 4000
via cube-stack.yaml).

CUBE_API_URL is resolved from the CubeStack CloudFormation output (private IP).

NOTE: This script only deploys the Lambda. Gateway creation and target
registration are handled by deploy_semantic_layer_stack.py, which creates
a dedicated Gateway for the semantic layer agent.
"""

import boto3
import json
import time
import zipfile
import os
from pathlib import Path
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
load_dotenv(ROOT_DIR / 'config.env')

REGION = os.getenv("AWS_REGION", "us-east-1")
LAMBDA_NAME = "semantic-layer-toolset-lambda"
SECURITY_GROUP_ID = os.getenv("SECURITY_GROUP_ID")
SUBNET_IDS = os.getenv("SUBNET_IDS", "").split(",")

def _get_cfn_output(key):
    """Read a CloudFormation output by key from main-stack, falling back to the
    nested CubeStack. Returns the value or None."""
    cfn = boto3.client('cloudformation', region_name=REGION)
    try:
        resp = cfn.describe_stacks(StackName='main-stack')
        outputs = {o['OutputKey']: o['OutputValue'] for o in resp['Stacks'][0].get('Outputs', [])}
        if key in outputs:
            return outputs[key]
    except Exception as e:
        print(f"Note: Could not read main-stack outputs: {e}")
    try:
        resources = cfn.list_stack_resources(StackName='main-stack')
        for r in resources['StackResourceSummaries']:
            if 'Cube' in r.get('LogicalResourceId', ''):
                nested = cfn.describe_stacks(StackName=r['PhysicalResourceId'])
                nested_outputs = {o['OutputKey']: o['OutputValue']
                                  for o in nested['Stacks'][0].get('Outputs', [])}
                if key in nested_outputs:
                    return nested_outputs[key]
    except Exception as e:
        print(f"Note: Could not read nested CubeStack outputs: {e}")
    return None


def get_cube_private_ip():
    """Resolve the Cube EC2 private IP from CloudFormation outputs."""
    ip = _get_cfn_output('CubePrivateIp')
    if ip:
        return ip
    raise Exception(
        "Could not resolve Cube private IP from CloudFormation. "
        "Ensure the CubeStack has been deployed (check main-stack status)."
    )


def get_cube_api_secret():
    """Resolve the shared Cube API secret. Prefer the per-deploy Secrets Manager
    secret (CubeApiSecretArn output); fall back to an env override; last resort the
    legacy workshop default (kept only so old stacks without the secret still work)."""
    env_override = os.getenv("CUBE_API_SECRET")
    if env_override:
        return env_override
    secret_arn = _get_cfn_output('CubeApiSecretArn')
    if secret_arn:
        try:
            sm = boto3.client('secretsmanager', region_name=REGION)
            return sm.get_secret_value(SecretId=secret_arn)['SecretString']
        except Exception as e:
            print(f"Warning: could not read Cube API secret from {secret_arn}: {e}")
    print("Warning: falling back to legacy default Cube API secret.")
    return "cubejs-workshop-secret-2024"  # nosec B105 - legacy fallback for pre-secret stacks


CUBE_API_SECRET = get_cube_api_secret()


def create_lambda_zip():
    zip_path = 'semantic_layer_toolset_lambda.zip'
    tools_dir = ROOT_DIR / 'tools'
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        zipf.write(
            str(tools_dir / 'semantic_layer_toolset_lambda.py'),
            'semantic_layer_toolset_lambda.py'
        )
    print(f"[OK] Created ZIP: {zip_path}")
    return zip_path


def get_or_create_role():
    iam = boto3.client('iam', region_name=REGION)
    role_name = f'{LAMBDA_NAME}-role'

    assume_policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole"
        }]
    }

    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents"
                ],
                "Resource": "arn:aws:logs:*:*:*"
            },
            {
                "Effect": "Allow",
                "Action": [
                    "ec2:CreateNetworkInterface",
                    "ec2:DescribeNetworkInterfaces",
                    "ec2:DeleteNetworkInterface",
                    "ec2:AssignPrivateIpAddresses",
                    "ec2:UnassignPrivateIpAddresses"
                ],
                "Resource": "*"
            }
        ]
    }

    try:
        iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(assume_policy)
        )
        iam.put_role_policy(
            RoleName=role_name,
            PolicyName='LambdaPolicy',
            PolicyDocument=json.dumps(policy)
        )
        print(f"[OK] Created role: {role_name}")
        time.sleep(10)
    except iam.exceptions.EntityAlreadyExistsException:
        print(f"[OK] Using existing role: {role_name}")

    return iam.get_role(RoleName=role_name)['Role']['Arn']


def deploy_lambda(role_arn, zip_path, cube_private_ip):
    """Create or update the Lambda function in the VPC private subnets."""
    client = boto3.client('lambda', region_name=REGION)

    with open(zip_path, 'rb') as f:
        zip_content = f.read()

    env = {
        'Variables': {
            'CUBE_API_URL': f'http://{cube_private_ip}:4000',
            'CUBE_API_SECRET': CUBE_API_SECRET,
        }
    }
    vpc = {
        'SubnetIds': SUBNET_IDS,
        'SecurityGroupIds': [SECURITY_GROUP_ID],
    }

    try:
        client.create_function(
            FunctionName=LAMBDA_NAME,
            Runtime='python3.12',
            Role=role_arn,
            Handler='semantic_layer_toolset_lambda.lambda_handler',
            Code={'ZipFile': zip_content},
            Timeout=60,
            MemorySize=256,
            VpcConfig=vpc,
            Environment=env,
        )
        print(f"[OK] Created Lambda: {LAMBDA_NAME}")
        client.get_waiter('function_active').wait(FunctionName=LAMBDA_NAME)
    except client.exceptions.ResourceConflictException:
        client.update_function_code(FunctionName=LAMBDA_NAME, ZipFile=zip_content)
        time.sleep(5)
        client.update_function_configuration(
            FunctionName=LAMBDA_NAME,
            Timeout=60,
            VpcConfig=vpc,
            Environment=env,
        )
        print(f"[OK] Updated Lambda: {LAMBDA_NAME}")
        client.get_waiter('function_updated').wait(FunctionName=LAMBDA_NAME)

    return client.get_function(FunctionName=LAMBDA_NAME)['Configuration']['FunctionArn']


def add_gateway_permission(lambda_arn):
    """Allow AgentCore Gateway to invoke this Lambda."""
    client = boto3.client('lambda', region_name=REGION)
    try:
        client.add_permission(
            FunctionName=LAMBDA_NAME,
            StatementId='AllowBedrockAgentCore',
            Action='lambda:InvokeFunction',
            Principal='bedrock-agentcore.amazonaws.com',
        )
        print("[OK] Added gateway invoke permission")
    except client.exceptions.ResourceConflictException:
        print("[OK] Gateway permission exists")


def test_lambda():
    """Quick smoke test — call cube_meta_tool directly."""
    print("\nTesting Lambda...")
    client = boto3.client('lambda', region_name=REGION)

    # Retry up to 3 times — VPC-attached Lambdas can take a while to become
    # invocable after creation (ENI setup).
    for attempt in range(1, 4):
        try:
            resp = client.invoke(
                FunctionName=LAMBDA_NAME,
                Payload=json.dumps({
                    'name': 'cube_meta_tool',
                    'arguments': {}
                })
            )

            result = json.loads(resp['Payload'].read())
            if result.get('statusCode') == 200:
                body = json.loads(result['body'])
                cube_names = [c['name'] for c in body.get('cubes', [])]
                print(f"[OK] Test passed! Cubes found: {cube_names}")
                return True

            error_msg = result.get('body', result.get('errorMessage', str(result)))
            print(f"  Attempt {attempt}/3 failed: {error_msg}")
        except Exception as e:
            print(f"  Attempt {attempt}/3 error: {e}")

        if attempt < 3:
            print(f"  Retrying in 15s...")
            time.sleep(15)

    print(f"❌ Test failed after 3 attempts")
    return False


def main():
    print("Deploying Semantic Layer (Cube Core) Lambda")
    print("=" * 60)

    # Resolve Cube private IP from CloudFormation
    cube_private_ip = get_cube_private_ip()
    print(f"[OK] Cube private IP: {cube_private_ip}")

    zip_path = create_lambda_zip()
    role_arn = get_or_create_role()
    lambda_arn = deploy_lambda(role_arn, zip_path, cube_private_ip)
    add_gateway_permission(lambda_arn)

    print("\n[WAIT] Waiting for Lambda VPC setup...")
    time.sleep(15)

    if not test_lambda():
        print("❌ Lambda test failed")
        return

    print("\n" + "=" * 60)
    print("[OK] Lambda deployment complete!")
    print(f"   Lambda: {lambda_arn}")
    print(f"   Tools: cube_meta_tool, cube_query_tool")
    print(f"\nNext: Run deploy_semantic_layer_stack.py to create the")
    print(f"dedicated Gateway, register the target, and deploy the UI.")


if __name__ == '__main__':
    main()
