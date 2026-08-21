"""CloudFormation custom-resource handler that provisions the AgentCore Gateway.

This is the CFN-native version of infra/create_gateway.py. It runs as a Lambda
backing the `GatewaySetup` custom resource in infra/template.yaml. On stack
create it:

  1. creates a Cognito user pool + resource server + M2M (client-credentials)
     app client for inbound auth,
  2. creates an AgentCore Gateway (MCP protocol, CUSTOM_JWT authorizer) and a
     Lambda target using the schemas in tool_definition.json (bundled alongside
     this file by the provisioner),
  3. writes the full `gateway.env` contents to an SSM SecureString parameter so
     participants can pull it with a single `aws ssm get-parameter` command.

On stack delete it tears the Gateway, Cognito pool, gateway IAM role, and the SSM
parameters down again.

Notes / validation caveats (verify in a Workshop Studio test event):
  - Requires the Lambda runtime boto3 to include the `bedrock-agentcore-control`
    client. If the bundled boto3 is too old, attach a newer boto3 layer.
  - Gateway creation is asynchronous; this function polls for READY, so its CFN
    timeout must be generous (the template sets 600s).

This module deliberately has NO third-party imports and does not rely on the
`cfnresponse` helper (that is only auto-injected for inline ZipFile functions),
so it sends the CloudFormation response itself.

It is also the single source of truth for gateway creation: the manual CLI script
`infra/create_gateway.py` imports these functions (`setup_cognito`,
`ensure_gateway_role`, `create_gateway`, `_gateway_env`) so the own-account path
and the CloudFormation path cannot drift.
"""

import json
import os
import time
import urllib.request

import boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")

_ALLOWED_SCHEMA_KEYS = {"type", "properties", "required", "items", "description"}


def _send(event, context, status, data=None, physical_id=None, reason=None):
    """Send a response to the CloudFormation pre-signed URL."""
    body = json.dumps(
        {
            "Status": status,
            "Reason": reason or f"See CloudWatch log stream: {context.log_stream_name}",
            "PhysicalResourceId": physical_id or context.log_stream_name,
            "StackId": event["StackId"],
            "RequestId": event["RequestId"],
            "LogicalResourceId": event["LogicalResourceId"],
            "NoEcho": False,
            "Data": data or {},
        }
    ).encode()
    req = urllib.request.Request(
        event["ResponseURL"],
        data=body,
        method="PUT",
        headers={"content-type": "", "content-length": str(len(body))},
    )
    urllib.request.urlopen(req, timeout=30)


def _sanitize_schema(node):
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k not in _ALLOWED_SCHEMA_KEYS:
                continue
            if k == "properties" and isinstance(v, dict):
                out[k] = {pk: _sanitize_schema(pv) for pk, pv in v.items()}
            elif k == "items":
                out[k] = _sanitize_schema(v)
            else:
                out[k] = v
        return out
    return node


def _load_tool_def():
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "tool_definition.json")) as f:
        return json.load(f)


# --- Cognito ---------------------------------------------------------------

def setup_cognito(project):
    idp = boto3.client("cognito-idp", region_name=REGION)
    pool_id = idp.create_user_pool(PoolName=f"{project}-gateway-pool")["UserPool"]["Id"]
    domain = f"{project}-{pool_id.split('_')[-1].lower()}"
    idp.create_user_pool_domain(Domain=domain, UserPoolId=pool_id)
    scope = "gateway.invoke"
    idp.create_resource_server(
        UserPoolId=pool_id,
        Identifier=f"{project}-gateway",
        Name=f"{project}-gateway",
        Scopes=[{"ScopeName": scope, "ScopeDescription": "Invoke gateway tools"}],
    )
    full_scope = f"{project}-gateway/{scope}"
    client = idp.create_user_pool_client(
        UserPoolId=pool_id,
        ClientName=f"{project}-m2m",
        GenerateSecret=True,
        AllowedOAuthFlows=["client_credentials"],
        AllowedOAuthScopes=[full_scope],
        AllowedOAuthFlowsUserPoolClient=True,
        SupportedIdentityProviders=["COGNITO"],
    )["UserPoolClient"]
    return {
        "user_pool_id": pool_id,
        "domain": domain,
        "client_id": client["ClientId"],
        "client_secret": client["ClientSecret"],
        "scope": full_scope,
        "discovery_url": f"https://cognito-idp.{REGION}.amazonaws.com/{pool_id}/.well-known/openid-configuration",
        "token_url": f"https://{domain}.auth.{REGION}.amazoncognito.com/oauth2/token",
    }


# --- Gateway IAM role ------------------------------------------------------

def ensure_gateway_role(project, tools_arn):
    iam = boto3.client("iam")
    role_name = f"{project}-gateway-role"
    assume = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    try:
        arn = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(assume),
            Description="AgentCore Gateway -> tool Lambda invocation",
        )["Role"]["Arn"]
        iam.put_role_policy(
            RoleName=role_name,
            PolicyName="invoke-tools",
            PolicyDocument=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Action": "lambda:InvokeFunction",
                            "Resource": tools_arn,
                        }
                    ],
                }
            ),
        )
        time.sleep(10)  # let the role propagate
        return arn, role_name
    except iam.exceptions.EntityAlreadyExistsException:
        return iam.get_role(RoleName=role_name)["Role"]["Arn"], role_name


# --- Gateway ---------------------------------------------------------------

def create_gateway(project, cognito, role_arn, tools_arn, tool_def=None):
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    if tool_def is None:
        tool_def = _load_tool_def()
    gw = control.create_gateway(
        name=f"{project}-gateway",
        roleArn=role_arn,
        protocolType="MCP",
        authorizerType="CUSTOM_JWT",
        authorizerConfiguration={
            "customJWTAuthorizer": {
                "discoveryUrl": cognito["discovery_url"],
                "allowedClients": [cognito["client_id"]],
            }
        },
    )
    gateway_id = gw["gatewayId"]
    gateway_url = gw["gatewayUrl"]
    for _ in range(90):
        status = control.get_gateway(gatewayIdentifier=gateway_id)["status"]
        if status == "READY":
            break
        if status in ("FAILED", "DELETING"):
            raise RuntimeError(f"Gateway entered {status}")
        time.sleep(5)
    else:
        raise TimeoutError("Gateway did not reach READY in time")

    target = control.create_gateway_target(
        gatewayIdentifier=gateway_id,
        name=tool_def["gatewayTargetName"],
        targetConfiguration={
            "mcp": {
                "lambda": {
                    "lambdaArn": tools_arn,
                    "toolSchema": {
                        "inlinePayload": [
                            {
                                "name": t["name"],
                                "description": t["description"],
                                "inputSchema": _sanitize_schema(t["inputSchema"]),
                            }
                            for t in tool_def["tools"]
                        ]
                    },
                }
            }
        },
        credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
    )
    return gateway_id, gateway_url, target.get("targetId")


# --- SSM --------------------------------------------------------------------

def _put_ssm(name, value, secure):
    ssm = boto3.client("ssm", region_name=REGION)
    ssm.put_parameter(
        Name=name,
        Value=value,
        Type="SecureString" if secure else "String",
        Overwrite=True,
    )


def _gateway_env(gateway_url, cognito):
    return "\n".join(
        [
            f"export AGENTCORE_GATEWAY_URL={gateway_url}",
            f"export COGNITO_TOKEN_URL={cognito['token_url']}",
            f"export COGNITO_CLIENT_ID={cognito['client_id']}",
            f"export COGNITO_CLIENT_SECRET={cognito['client_secret']}",
            f"export COGNITO_SCOPE={cognito['scope']}",
            "export TOOL_MODE=gateway",
            "",
        ]
    )


# --- Teardown ---------------------------------------------------------------

def teardown(project, ssm_name, meta_name):
    ssm = boto3.client("ssm", region_name=REGION)
    meta = {}
    try:
        meta = json.loads(
            ssm.get_parameter(Name=meta_name)["Parameter"]["Value"]
        )
    except Exception:
        pass
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    try:
        if meta.get("gateway_id") and meta.get("target_id"):
            control.delete_gateway_target(
                gatewayIdentifier=meta["gateway_id"], targetId=meta["target_id"]
            )
        if meta.get("gateway_id"):
            control.delete_gateway(gatewayIdentifier=meta["gateway_id"])
    except Exception:
        pass
    try:
        idp = boto3.client("cognito-idp", region_name=REGION)
        if meta.get("domain") and meta.get("user_pool_id"):
            idp.delete_user_pool_domain(
                Domain=meta["domain"], UserPoolId=meta["user_pool_id"]
            )
        if meta.get("user_pool_id"):
            idp.delete_user_pool(UserPoolId=meta["user_pool_id"])
    except Exception:
        pass
    try:
        iam = boto3.client("iam")
        role = meta.get("role_name") or f"{project}-gateway-role"
        iam.delete_role_policy(RoleName=role, PolicyName="invoke-tools")
        iam.delete_role(RoleName=role)
    except Exception:
        pass
    for n in (ssm_name, meta_name):
        try:
            ssm.delete_parameter(Name=n)
        except Exception:
            pass


# --- Handler ----------------------------------------------------------------

def handler(event, context):
    rt = event.get("RequestType", "")
    p = event.get("ResourceProperties", {})
    project = p.get("ProjectName", "biodiversity-anomaly")
    tools_arn = p["ToolsFunctionArn"]
    ssm_name = p.get("SsmParamName", "/touch-grass/gateway-env")
    meta_name = ssm_name + "-meta"
    try:
        if rt == "Create":
            cognito = setup_cognito(project)
            role_arn, role_name = ensure_gateway_role(project, tools_arn)
            gateway_id, gateway_url, target_id = create_gateway(
                project, cognito, role_arn, tools_arn
            )
            _put_ssm(ssm_name, _gateway_env(gateway_url, cognito), secure=True)
            _put_ssm(
                meta_name,
                json.dumps(
                    {
                        "user_pool_id": cognito["user_pool_id"],
                        "domain": cognito["domain"],
                        "gateway_id": gateway_id,
                        "target_id": target_id,
                        "role_name": role_name,
                    }
                ),
                secure=False,
            )
            _send(
                event,
                context,
                "SUCCESS",
                {
                    "GatewayUrl": gateway_url,
                    "SsmParamName": ssm_name,
                    # Exposed as resource attributes so the pre-deployed sample
                    # AgentCore Runtime (see infra/template.yaml) can consume them
                    # as environment variables and run in gateway mode.
                    "CognitoTokenUrl": cognito["token_url"],
                    "CognitoClientId": cognito["client_id"],
                    "CognitoClientSecret": cognito["client_secret"],
                    "CognitoScope": cognito["scope"],
                },
                physical_id=gateway_id,
            )
        elif rt == "Delete":
            teardown(project, ssm_name, meta_name)
            _send(event, context, "SUCCESS", physical_id=event.get("PhysicalResourceId"))
        else:  # Update: no-op (workshop stacks are create-once)
            _send(event, context, "SUCCESS", physical_id=event.get("PhysicalResourceId"))
    except Exception as e:
        _send(event, context, "FAILED", reason=str(e),
              physical_id=event.get("PhysicalResourceId"))
