#!/bin/bash
# ============================================================================
# DEPRECATED / LEGACY — superseded by the CDK stack.
# ============================================================================
# The AgentCore Gateway, its IAM role, the REQUEST interceptor attachment, and
# the Lambda tool target are now defined natively in AnyCompanyPayConnectAiAgentStack
# using the AWS::BedrockAgentCore::{Gateway,GatewayTarget} CloudFormation
# resources (see lib/connect-ai-agent-stack.ts). Deploy with:
#     npx cdk deploy AnyCompanyPayConnectAiAgentStack
# and tear down with `cdk destroy` — no CLI wiring or teardown needed.
#
# Running THIS script against a deployed stack would create a SECOND, unmanaged
# gateway of the same name. It is kept only for reference / the pre-CFN path, and
# refuses to run unless you set FORCE=1 explicitly.
#
# (Historical note: when this was written, AgentCore Gateway had no CloudFormation
# support. It does now — hence the migration to CDK.)
# ----------------------------------------------------------------------------
set -uo pipefail

if [ "${FORCE:-0}" != "1" ]; then
  echo "provision-gateway.sh is DEPRECATED — the gateway is now managed by" >&2
  echo "AnyCompanyPayConnectAiAgentStack (cdk deploy/destroy). Set FORCE=1 to run the" >&2
  echo "legacy CLI path anyway (this will create an unmanaged duplicate gateway)." >&2
  exit 0
fi

# Region from the environment (never hardcoded).
R="${R:-${AWS_REGION:-${CDK_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || echo us-west-2)}}}"
if [ -z "$R" ]; then
  echo "ERROR: no region. Set AWS_REGION (or R), e.g. AWS_REGION=us-west-2 $0" >&2; exit 1
fi
ENV_NAME="${ENV_NAME:-}"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
STACK="${STACK:-AnyCompanyPayConnectAiAgentStack}"
GATEWAY_NAME="${GATEWAY_NAME:-anycompany-pay-transaction-tools}"
ROLE_NAME="${ROLE_NAME:-anycompany-pay-agentcore-gateway-role}"
# --- INBOUND AUTH FOR AMAZON CONNECT (NOT Cognito) ---------------------------
# Amazon Connect is the OIDC ISSUER for the AI-agent -> gateway call. Two rules
# from the Connect docs, both required or the integration silently fails (the
# gateway namespace never appears; in the console only "None" is selectable):
#   1. The gateway's Discovery URL MUST be the CONNECT INSTANCE's OIDC endpoint
#      ([instance URL]/.well-known/openid-configuration) — NOT Cognito's.
#   2. The token Connect sends carries the GATEWAY ID in its `aud` claim, so
#      allowedAudience MUST be the gateway id (set after create — see below).
# Only "Allowed audiences" must be set; allowed clients/scopes may be empty.
CONNECT_ALIAS="${CONNECT_ALIAS:-anycompany-pay-${ACCOUNT}${ENV_NAME:+-${ENV_NAME}}}"
CONNECT_DISCOVERY_URL="${CONNECT_DISCOVERY_URL:-https://${CONNECT_ALIAS}.my.connect.aws/.well-known/openid-configuration}"
# MCP protocol version Amazon Connect speaks; must be advertised by the gateway.
MCP_VERSION="${MCP_VERSION:-2025-03-26}"

out() { aws cloudformation describe-stacks --region "$R" --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue | [0]" --output text; }
TOOL_ARN="$(out TransactionToolFnArn)"
INTERCEPTOR_ARN="$(out GatewayInterceptorFnArn)"
echo "Tool Lambda ARN        : $TOOL_ARN"
echo "Interceptor Lambda ARN : $INTERCEPTOR_ARN"
[ -z "$TOOL_ARN" ] || [ "$TOOL_ARN" = "None" ] && { echo "ERROR: deploy $STACK first."; exit 1; }

# ------------------------------------------------------------------
# 1) Gateway IAM role: assumable by AgentCore; may invoke both Lambdas.
# ------------------------------------------------------------------
TRUST=$(cat <<JSON
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"bedrock-agentcore.amazonaws.com"},"Action":"sts:AssumeRole","Condition":{"StringEquals":{"aws:SourceAccount":"${ACCOUNT}"}}}]}
JSON
)
ROLE_ARN="$(aws iam get-role --role-name "$ROLE_NAME" --query Role.Arn --output text 2>/dev/null)"
if [ -z "$ROLE_ARN" ] || [ "$ROLE_ARN" = "None" ]; then
  echo "Creating IAM role ${ROLE_NAME}..."
  ROLE_ARN="$(aws iam create-role --role-name "$ROLE_NAME" \
    --assume-role-policy-document "$TRUST" \
    --description "AnyCompanyPay AgentCore Gateway: invoke transaction tool + interceptor" \
    --query Role.Arn --output text)"
else
  echo "Reusing IAM role $ROLE_NAME"
fi
aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name invoke-lambdas \
  --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"lambda:InvokeFunction\",\"Resource\":[\"${TOOL_ARN}\",\"${INTERCEPTOR_ARN}\"]}]}"
echo "  role: $ROLE_ARN"
sleep 8  # let the new role propagate before AgentCore assumes it

# ------------------------------------------------------------------
# 2) Ensure the Gateway exists, then CONVERGE it to the canonical config.
#    The `aud` must be the gateway id, which doesn't exist until after create,
#    so: create first (discovery URL only) -> then update allowedAudience=<gwId>.
#    Running the update every time also self-corrects a previously-misconfigured
#    gateway (e.g. one created against a Cognito discovery URL).
# ------------------------------------------------------------------
GID="$(aws bedrock-agentcore-control list-gateways --region "$R" \
  --query "items[?name=='${GATEWAY_NAME}'].gatewayId | [0]" --output text 2>/dev/null)"
if [ -z "$GID" ] || [ "$GID" = "None" ]; then
  echo "Creating gateway ${GATEWAY_NAME}..."
  GID="$(aws bedrock-agentcore-control create-gateway --region "$R" \
    --name "$GATEWAY_NAME" \
    --role-arn "$ROLE_ARN" \
    --protocol-type MCP \
    --authorizer-type CUSTOM_JWT \
    --authorizer-configuration "{\"customJWTAuthorizer\":{\"discoveryUrl\":\"${CONNECT_DISCOVERY_URL}\"}}" \
    --query gatewayId --output text)" || { echo "create-gateway failed"; exit 1; }
else
  echo "Reusing gateway $GATEWAY_NAME ($GID)"
fi
echo "  gatewayId: $GID"

echo "Converging gateway config (Connect OIDC inbound, aud=<gatewayId>, MCP ${MCP_VERSION}, interceptor)..."
aws bedrock-agentcore-control update-gateway --region "$R" \
  --gateway-identifier "$GID" \
  --name "$GATEWAY_NAME" \
  --role-arn "$ROLE_ARN" \
  --protocol-type MCP \
  --protocol-configuration "{\"mcp\":{\"supportedVersions\":[\"${MCP_VERSION}\"]}}" \
  --authorizer-type CUSTOM_JWT \
  --authorizer-configuration "{\"customJWTAuthorizer\":{\"discoveryUrl\":\"${CONNECT_DISCOVERY_URL}\",\"allowedAudience\":[\"${GID}\"]}}" \
  --interceptor-configurations "[{\"interceptor\":{\"lambda\":{\"arn\":\"${INTERCEPTOR_ARN}\"}},\"interceptionPoints\":[\"REQUEST\"],\"inputConfiguration\":{\"passRequestHeaders\":true}}]" \
  --query status --output text >/dev/null || { echo "update-gateway failed"; exit 1; }
GURL="$(aws bedrock-agentcore-control get-gateway --region "$R" --gateway-identifier "$GID" --query gatewayUrl --output text 2>/dev/null)"

# ------------------------------------------------------------------
# 3) Add the Lambda tool target (MCP). merchant_id is deliberately NOT in the
#    schema — the interceptor injects it from trusted context.
# ------------------------------------------------------------------
TARGET_CFG=$(cat <<JSON
{"mcp":{"lambda":{"lambdaArn":"${TOOL_ARN}","toolSchema":{"inlinePayload":[{"name":"query_transactions","description":"Query the calling merchant's own payment transactions (read-only). Returns matching transactions, a count, and totals. The merchant is determined automatically; never ask for or accept a merchant id.","inputSchema":{"type":"object","properties":{"status":{"type":"string","description":"Optional status filter: succeeded, pending, in_progress, failed, refunded, or authorized"},"transactionId":{"type":"string","description":"Optional exact transaction id (txn_...)"},"query":{"type":"string","description":"Optional free-text search over id/method/currency/status"},"limit":{"type":"integer","description":"Max results to return (1-25)"}}}}]}}}}
JSON
)
TID="$(aws bedrock-agentcore-control list-gateway-targets --region "$R" --gateway-identifier "$GID" \
  --query "items[?name=='query-transactions'].targetId | [0]" --output text 2>/dev/null)"
if [ -n "$TID" ] && [ "$TID" != "None" ]; then
  echo "Reusing tool target 'query-transactions' ($TID)"
else
  echo "Adding Lambda tool target 'query-transactions'..."
  TID="$(aws bedrock-agentcore-control create-gateway-target --region "$R" \
    --gateway-identifier "$GID" \
    --name "query-transactions" \
    --target-configuration "$TARGET_CFG" \
    --credential-provider-configurations '[{"credentialProviderType":"GATEWAY_IAM_ROLE"}]' \
    --query targetId --output text 2>&1)" || { echo "create-gateway-target: $TID"; }
  echo "  targetId: $TID"
fi

echo
echo "=== Gateway ready ==="
echo "gatewayId       : $GID"
echo "MCP URL         : $GURL"
echo "Discovery URL   : $CONNECT_DISCOVERY_URL   (Connect instance OIDC — issuer)"
echo "Allowed audience: $GID                      (the gateway id — Connect puts this in aud)"
echo "MCP version     : $MCP_VERSION"
echo
echo "Next (Amazon Connect console — no API exists for these):"
echo "1. Third-party applications -> Add integration -> Integration type = MCP server;"
echo "   select this Gateway; under Instance association pick '$CONNECT_ALIAS'"
echo "   (only the instance matching the gateway's Discovery URL is selectable)."
echo "2. AI Agent Designer: use an ORCHESTRATION (agentic self-service) agent and add the"
echo "   'query_transactions' tool. Do NOT expose merchant_id as a tool input; the interceptor"
echo "   injects it from the trusted Connect contact (GetContactAttributes)."
echo "3. Route 'ask about my transactions' to the agent in the chat flow. No session seeding is"
echo "   required — the interceptor reads merchant_id directly from the contact attribute."
