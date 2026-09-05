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

# Keys the Gateway accepts in a tool's inputSchema. This is not a style choice:
# the bedrock-agentcore-control SchemaDefinition shape has exactly these five
# members, and anything else is rejected outright.
#
# "enum" is therefore impossible to pass through, which used to mean the allowed
# values simply vanished from the remote contract - the model saw a bare string
# where the local tool advertises a closed set. _sanitize_schema folds them into
# the description instead, which the API does accept, so both modes tell the model
# the same thing.
_ALLOWED_SCHEMA_KEYS = {"type", "properties", "required", "items", "description"}


def _send(event, context, status, data=None, physical_id=None, reason=None, no_echo=True):
    """Send a response to the CloudFormation pre-signed URL.

    NoEcho defaults to true. CloudFormation masks a custom resource's returned
    values only when asked to, and this resource's Data carries Cognito endpoints
    and a client id, so there is nothing to gain from having them echoed back in
    stack events and Fn::GetAtt output.
    """
    body = json.dumps(
        {
            "Status": status,
            "Reason": reason or f"See CloudWatch log stream: {context.log_stream_name}",
            "PhysicalResourceId": physical_id or context.log_stream_name,
            "StackId": event["StackId"],
            "RequestId": event["RequestId"],
            "LogicalResourceId": event["LogicalResourceId"],
            "NoEcho": no_echo,
            "Data": data or {},
        }
    ).encode()
    req = urllib.request.Request(
        event["ResponseURL"],
        data=body,
        method="PUT",
        headers={"content-type": "", "content-length": str(len(body))},
    )
    # CloudFormation hands us a presigned HTTPS callback URL. Assert the
    # scheme before opening it, so a malformed event cannot turn this into
    # a file:// or custom-scheme fetch.
    if not event["ResponseURL"].startswith("https://"):
        raise ValueError("ResponseURL is not https")
    urllib.request.urlopen(req, timeout=30)  # nosec B310 - scheme asserted above


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
        # Carry a dropped enum over as prose, so the closed set still reaches the
        # model through the one field the Gateway will take.
        allowed = node.get("enum")
        if allowed:
            values = ", ".join(str(a) for a in allowed)
            desc = out.get("description", "").rstrip()
            sep = " " if desc and not desc.endswith(".") else ("" if not desc else " ")
            out["description"] = f"{desc}{'.' if desc and not desc.endswith('.') else ''}{sep}One of: {values}.".strip()
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
        created = True
    except iam.exceptions.EntityAlreadyExistsException:
        # Re-run against an account that already has the role, e.g. a stack that
        # rolled back mid-create. Reuse it, but still attach the policy below:
        # doing that inside the create try meant the already-exists path returned a
        # role with no guarantee it could invoke anything.
        arn = iam.get_role(RoleName=role_name)["Role"]["Arn"]
        created = False

    # put_role_policy is idempotent - it overwrites an inline policy of the same
    # name - so this is safe on both paths and converges on the right document.
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
    if created:
        time.sleep(10)  # let a brand-new role propagate
    return arn, role_name


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


def enable_transaction_search():
    """Turn on CloudWatch Transaction Search so AgentCore traces are ingested.

    Done here, at provisioning time, rather than by the participant in Part 1.
    Enabling it reaches well past X-Ray - UpdateTraceSegmentDestination calls
    application-signals:StartDiscovery, which in turn needs
    cloudtrail:CreateServiceLinkedChannel - and handing a participant that chain
    of account-level enablement permissions to run a documented one-liner is a
    poor trade. It is a per-account, one-time setting, so it belongs with the
    rest of the provisioning.

    Best effort on purpose: if it fails, Parts 1 and 2 still run and only the
    trace views are empty, which is not worth failing a stack over.
    """
    xray = boto3.client("xray", region_name=REGION)
    logs = boto3.client("logs", region_name=REGION)
    account = boto3.client("sts").get_caller_identity()["Account"]

    # X-Ray writes indexed spans into aws/spans, and it needs a CloudWatch Logs
    # RESOURCE policy to do it - an identity policy on us is not enough.
    # UpdateTraceSegmentDestination fails with "XRay does not have permission to
    # call PutLogEvents on the aws/spans Log Group" until this exists.
    try:
        logs.create_log_group(logGroupName="aws/spans")
    except logs.exceptions.ResourceAlreadyExistsException:
        pass
    except Exception as exc:  # noqa: BLE001
        print(f"transaction search: aws/spans not created ({exc})")
    try:
        logs.put_resource_policy(
            policyName="TransactionSearchXRayAccess",
            policyDocument=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Sid": "TransactionSearchXRayAccess",
                            "Effect": "Allow",
                            "Principal": {"Service": "xray.amazonaws.com"},
                            "Action": ["logs:PutLogEvents", "logs:CreateLogStream"],
                            "Resource": (
                                f"arn:aws:logs:{REGION}:{account}:"
                                "log-group:aws/spans:*"
                            ),
                            "Condition": {
                                "StringEquals": {"aws:SourceAccount": account},
                                "ArnLike": {
                                    "aws:SourceArn":
                                        f"arn:aws:xray:{REGION}:{account}:*"
                                },
                            },
                        }
                    ],
                }
            ),
        )
        print("transaction search: aws/spans resource policy in place")
    except Exception as exc:  # noqa: BLE001
        print(f"transaction search: resource policy not set ({exc})")

    try:
        xray.update_trace_segment_destination(Destination="CloudWatchLogs")
        print("transaction search: trace segment destination -> CloudWatchLogs")
    except Exception as exc:  # noqa: BLE001 - never fail provisioning for this
        print(f"transaction search: destination not set ({exc})")
    try:
        xray.update_indexing_rule(
            Name="Default",
            Rule={"Probabilistic": {"DesiredSamplingPercentage": 100}},
        )
        print("transaction search: indexing 100% of traces")
    except Exception as exc:  # noqa: BLE001
        print(f"transaction search: indexing rule not set ({exc})")


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

def _try(label, fn):
    """Run a teardown step, and SAY SO when it fails.

    Teardown must not fail the stack delete - a half-deleted stack is worse than
    a leftover resource. But the previous version swallowed every error with a
    bare `except: pass`, so when the gateway delete failed the stack still
    reported DELETE_COMPLETE and left an AgentCore Gateway running with no trace
    of why. Log it, so a leftover is findable in CloudWatch.
    """
    try:
        fn()
        print(f"teardown: {label} ok")
        return True
    except Exception as exc:  # noqa: BLE001 - never fail the stack delete
        print(f"teardown: {label} FAILED - {exc}")
        return False


def teardown(project, ssm_name, meta_name):
    ssm = boto3.client("ssm", region_name=REGION)
    meta = {}
    _try("read meta parameter", lambda: meta.update(json.loads(
        ssm.get_parameter(Name=meta_name)["Parameter"]["Value"])))

    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    gw = meta.get("gateway_id")

    if gw and meta.get("target_id"):
        _try("delete gateway target", lambda: control.delete_gateway_target(
            gatewayIdentifier=gw, targetId=meta["target_id"]))
        # The gateway cannot be deleted while a target is still attached or
        # still deleting, and delete_gateway fails with a conflict rather than
        # waiting. Poll until the targets are actually gone.
        for _ in range(30):
            try:
                left = control.list_gateway_targets(
                    gatewayIdentifier=gw).get("items", [])
            except Exception as exc:  # noqa: BLE001
                print(f"teardown: list gateway targets failed - {exc}")
                break
            if not left:
                break
            time.sleep(5)
        else:
            print("teardown: gateway targets still present after 150s")

    if gw:
        # Retry the gateway delete too: even with no targets listed the service
        # can briefly report a conflict.
        for attempt in range(1, 7):
            if _try(f"delete gateway (attempt {attempt})",
                    lambda: control.delete_gateway(gatewayIdentifier=gw)):
                break
            time.sleep(10)

    idp = boto3.client("cognito-idp", region_name=REGION)
    if meta.get("domain") and meta.get("user_pool_id"):
        _try("delete cognito domain", lambda: idp.delete_user_pool_domain(
            Domain=meta["domain"], UserPoolId=meta["user_pool_id"]))
    if meta.get("user_pool_id"):
        _try("delete cognito user pool", lambda: idp.delete_user_pool(
            UserPoolId=meta["user_pool_id"]))

    iam = boto3.client("iam")
    role = meta.get("role_name") or f"{project}-gateway-role"
    _try("delete gateway role policy", lambda: iam.delete_role_policy(
        RoleName=role, PolicyName="invoke-tools"))
    _try("delete gateway role", lambda: iam.delete_role(RoleName=role))

    for n in (ssm_name, meta_name):
        _try(f"delete parameter {n}", lambda n=n: ssm.delete_parameter(Name=n))


# --- Handler ----------------------------------------------------------------

def handler(event, context):
    rt = event.get("RequestType", "")
    p = event.get("ResourceProperties", {})
    project = p.get("ProjectName", "biodiversity-anomaly")
    ssm_name = p.get("SsmParamName", "/touch-grass/gateway-env")
    meta_name = ssm_name + "-meta"
    # Everything that can raise belongs inside the try, including reading required
    # properties: a KeyError out here would mean _send is never called, and
    # CloudFormation then waits out the full custom-resource timeout - an hour of
    # a participant's event - before rolling back with no reason recorded.
    try:
        tools_arn = p["ToolsFunctionArn"]
        if rt == "Create":
            enable_transaction_search()
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
                    # Endpoints and the client id only. The client SECRET is
                    # deliberately NOT returned: a custom resource's Data becomes
                    # readable through Fn::GetAtt and through DescribeStackEvents,
                    # and nothing needs it from here - the full gateway.env,
                    # secret included, is in the SSM SecureString written above.
                    "CognitoTokenUrl": cognito["token_url"],
                    "CognitoClientId": cognito["client_id"],
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
