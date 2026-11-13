#!/usr/bin/env bash
# Amazon Connect Migration Tool - deploy the tool into the SOURCE account.
#
# Usage:
#   scripts/deploy.sh --source-instance-arn <arn> --target-role-arn <arn> [options]
#
# Required:
#   --source-instance-arn ARN   Source Connect instance (must be in the account you deploy to)
#   --target-role-arn ARN       ConnectMigrationTargetWriter role in the target account. It may
#                               not exist yet; create it afterwards with scripts/deploy-target-role.sh
# Options:
#   --region REGION             Region for the tool stacks (default: the source instance Region)
#   --environment NAME          Environment suffix for stack and function names (default: prod)
#   --admin-email EMAIL         Create a Cognito user for this email (invitation is emailed)
#   --cognito-domain-prefix P   Cognito sign in domain prefix (default: connect-migration-<account>-<env>)
#   --enable-phone-number-move  Opt in to UpdatePhoneNumber (same account and Region only)
#   --timeout-seconds N         Maximum run time of one migration execution (template default: 3600)
#   --cleanup-alarm-topic-arn ARN
#                               SNS topic for the cleanup failure queue alarm (template default: none)
#   --custom-domain-name NAME   Serve the UI on this domain (needs --acm-certificate-arn and a DNS record)
#   --acm-certificate-arn ARN   ACM certificate for the custom domain (must be in US East, N. Virginia)
#   --enable-api-access-logging Write API Gateway access logs to CloudWatch Logs (see README)
#   --api-gateway-cloudwatch-role-arn ARN
#                               Set this existing role as the account level API Gateway
#                               CloudWatch role (affects every REST API in the account and Region)
#   --profile NAME              AWS CLI profile for the source account
#   -h, --help                  Show this help
#
# Stacks (in order): connect-migration-frontend-<env>, connect-migration-lambdas-<env>,
# connect-migration-stepfunctions-<env>, connect-migration-backend-<env>.
# The frontend config (frontend/config.js) is generated from the stack outputs.

set -euo pipefail

usage() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; }

SOURCE_INSTANCE_ARN=""
TARGET_ROLE_ARN=""
REGION=""
ENVIRONMENT="prod"
ADMIN_EMAIL=""
COGNITO_DOMAIN_PREFIX=""
ENABLE_PHONE_NUMBER_MOVE="false"
ENABLE_API_ACCESS_LOGGING="false"
CUSTOM_DOMAIN_NAME=""
ACM_CERTIFICATE_ARN=""
API_GATEWAY_CLOUDWATCH_ROLE_ARN=""
TIMEOUT_SECONDS=""
# Same as the TimeoutSeconds default in cloudformation/4-step-functions.yaml (tests check it).
DEFAULT_TIMEOUT_SECONDS="3600"
CLEANUP_ALARM_TOPIC_ARN=""
PROFILE_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --source-instance-arn) SOURCE_INSTANCE_ARN="$2"; shift 2 ;;
        --target-role-arn) TARGET_ROLE_ARN="$2"; shift 2 ;;
        --region) REGION="$2"; shift 2 ;;
        --environment) ENVIRONMENT="$2"; shift 2 ;;
        --admin-email) ADMIN_EMAIL="$2"; shift 2 ;;
        --cognito-domain-prefix) COGNITO_DOMAIN_PREFIX="$2"; shift 2 ;;
        --enable-phone-number-move) ENABLE_PHONE_NUMBER_MOVE="true"; shift ;;
        --timeout-seconds) TIMEOUT_SECONDS="$2"; shift 2 ;;
        --cleanup-alarm-topic-arn) CLEANUP_ALARM_TOPIC_ARN="$2"; shift 2 ;;
        --custom-domain-name) CUSTOM_DOMAIN_NAME="$2"; shift 2 ;;
        --acm-certificate-arn) ACM_CERTIFICATE_ARN="$2"; shift 2 ;;
        --enable-api-access-logging) ENABLE_API_ACCESS_LOGGING="true"; shift ;;
        --api-gateway-cloudwatch-role-arn) API_GATEWAY_CLOUDWATCH_ROLE_ARN="$2"; shift 2 ;;
        --profile) PROFILE_ARGS=(--profile "$2"); shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok() { echo -e "${GREEN}[ok]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
die() { echo -e "${RED}[x]${NC} $1" >&2; exit 1; }

[[ -n "$SOURCE_INSTANCE_ARN" ]] || { usage >&2; die "--source-instance-arn is required"; }
[[ -n "$TARGET_ROLE_ARN" ]] || { usage >&2; die "--target-role-arn is required"; }
[[ "$SOURCE_INSTANCE_ARN" =~ ^arn:aws[a-z-]*:connect:([a-z0-9-]+):([0-9]{12}):instance/[0-9a-f-]{36}$ ]] \
    || die "Not a Connect instance ARN: $SOURCE_INSTANCE_ARN"
SOURCE_REGION="${BASH_REMATCH[1]}"
SOURCE_ACCOUNT="${BASH_REMATCH[2]}"
[[ "$TARGET_ROLE_ARN" =~ ^arn:aws[a-z-]*:iam::[0-9]{12}:role/.+$ ]] || die "Not an IAM role ARN: $TARGET_ROLE_ARN"
[[ "$ENVIRONMENT" =~ ^[a-z0-9-]{1,20}$ ]] || die "--environment must be lower case letters, digits or '-'"
[[ -z "$TIMEOUT_SECONDS" || "$TIMEOUT_SECONDS" =~ ^[0-9]{2,8}$ ]] || die "--timeout-seconds must be a number of seconds"
[[ -z "$CLEANUP_ALARM_TOPIC_ARN" || "$CLEANUP_ALARM_TOPIC_ARN" =~ ^arn:aws[a-z-]*:sns:[a-z0-9-]+:[0-9]{12}:[A-Za-z0-9_-]+$ ]] \
    || die "--cleanup-alarm-topic-arn must be an SNS topic ARN"
REGION="${REGION:-$SOURCE_REGION}"

for cmd in aws zip shasum; do
    command -v "$cmd" >/dev/null 2>&1 || die "'$cmd' is required"
done

cd "$(dirname "$0")/.."
BUILD_DIR=".build"
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR/lambda"

aws_cli() { aws ${PROFILE_ARGS[@]+"${PROFILE_ARGS[@]}"} --region "$REGION" "$@"; }
output() { # output <stack> <key>
    aws_cli cloudformation describe-stacks --stack-name "$1" \
        --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" --output text
}

CALLER_ACCOUNT=$(aws_cli sts get-caller-identity --query Account --output text)
[[ "$CALLER_ACCOUNT" == "$SOURCE_ACCOUNT" ]] \
    || die "Credentials are for account $CALLER_ACCOUNT but the source instance is in $SOURCE_ACCOUNT. Deploy the tool into the source account."

FRONTEND_STACK="connect-migration-frontend-${ENVIRONMENT}"
LAMBDA_STACK="connect-migration-lambdas-${ENVIRONMENT}"
SFN_STACK="connect-migration-stepfunctions-${ENVIRONMENT}"
API_STACK="connect-migration-backend-${ENVIRONMENT}"

echo "Deploying environment '${ENVIRONMENT}' to account ${CALLER_ACCOUNT}, Region ${REGION}"

# 1. Frontend: S3 + CloudFront + Cognito + private artifacts bucket --------
# ApiId lets the web UI call the API in the CSP. On a redeploy it is the current
# API; on the first deploy it is empty and is set after step 5.
EXISTING_API_ID=$(output "$API_STACK" ApiId 2>/dev/null || true)
[[ "$EXISTING_API_ID" =~ ^[a-z0-9]{10}$ ]] || EXISTING_API_ID=""
FRONTEND_PARAMS=("Environment=${ENVIRONMENT}" "CognitoDomainPrefix=${COGNITO_DOMAIN_PREFIX}"
    "CustomDomainName=${CUSTOM_DOMAIN_NAME}" "AcmCertificateArn=${ACM_CERTIFICATE_ARN}")
aws_cli cloudformation deploy \
    --template-file cloudformation/1-frontend-infrastructure.yaml \
    --stack-name "$FRONTEND_STACK" \
    --parameter-overrides "${FRONTEND_PARAMS[@]}" "ApiId=${EXISTING_API_ID}" \
    --no-fail-on-empty-changeset
WEBSITE_BUCKET=$(output "$FRONTEND_STACK" WebsiteBucket)
ARTIFACTS_BUCKET=$(output "$FRONTEND_STACK" ArtifactsBucket)
CLOUDFRONT_URL=$(output "$FRONTEND_STACK" CloudFrontURL)
DISTRIBUTION_ID=$(output "$FRONTEND_STACK" CloudFrontDistributionId)
USER_POOL_ID=$(output "$FRONTEND_STACK" UserPoolId)
USER_POOL_ARN=$(output "$FRONTEND_STACK" UserPoolArn)
USER_POOL_CLIENT_ID=$(output "$FRONTEND_STACK" UserPoolClientId)
COGNITO_DOMAIN=$(output "$FRONTEND_STACK" CognitoDomain)
ok "Frontend stack: ${CLOUDFRONT_URL}"
if [[ -n "$CUSTOM_DOMAIN_NAME" ]]; then
    warn "Point a DNS record for ${CUSTOM_DOMAIN_NAME} to $(output "$FRONTEND_STACK" CloudFrontDomainName)"
fi

# 2. Package Lambda code (each function zip includes the shared modules) --------
SHARED=(lambda/region_utils.py lambda/arn_mapping.py lambda/migration_report.py lambda/migration_filters.py)
CODE_VERSION=$(cat lambda/*.py | shasum -a 256 | cut -c1-16)
for f in lambda/*.py; do
    name=$(basename "$f" .py)
    [[ "$name" == "region_utils" || "$name" == "arn_mapping" || "$name" == "migration_report" \
        || "$name" == "migration_filters" ]] && continue
    EXTRA=()
    # The UI verification endpoint runs the VerifyTarget step's checks.
    [[ "$name" == "verify_migration" ]] && EXTRA=(lambda/verify_target.py)
    zip -q -j "$BUILD_DIR/lambda/${name}.zip" "$f" "${SHARED[@]}" ${EXTRA[@]+"${EXTRA[@]}"}
done
aws_cli s3 sync "$BUILD_DIR/lambda/" "s3://${ARTIFACTS_BUCKET}/lambda/${CODE_VERSION}/" --only-show-errors
ok "Lambda packages uploaded (version ${CODE_VERSION})"

# 3. Lambda functions and the least privilege execution roles ---------------
LAMBDA_PARAMS=(
    "Environment=${ENVIRONMENT}"
    "S3Bucket=${ARTIFACTS_BUCKET}"
    "CodeVersion=${CODE_VERSION}"
    "SourceInstanceArn=${SOURCE_INSTANCE_ARN}"
    "TargetRoleArn=${TARGET_ROLE_ARN}"
    "AllowedOrigin=${CLOUDFRONT_URL}"
    "EnablePhoneNumberMove=${ENABLE_PHONE_NUMBER_MOVE}"
)
[[ -n "$CLEANUP_ALARM_TOPIC_ARN" ]] && LAMBDA_PARAMS+=("CleanupFailureAlarmTopicArn=${CLEANUP_ALARM_TOPIC_ARN}")
aws_cli cloudformation deploy \
    --template-file cloudformation/3-lambda-functions.yaml \
    --stack-name "$LAMBDA_STACK" \
    --capabilities CAPABILITY_NAMED_IAM \
    --parameter-overrides "${LAMBDA_PARAMS[@]}" \
    --no-fail-on-empty-changeset
SOURCE_READER_ROLE_ARN=$(output "$LAMBDA_STACK" LambdaRoleArn)
ok "Lambda stack (execution role ${SOURCE_READER_ROLE_ARN})"

# 4. Step Functions (definition uploaded by 'package') ----------------------
# The definition is valid JSON. Its TimeoutSeconds must be a JSON number, which a
# CloudFormation substitution cannot produce, so the value is written into a build
# copy next to a copy of the template (same relative path), and the same value is
# passed as the TimeoutSeconds stack parameter.
SFN_TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-$DEFAULT_TIMEOUT_SECONDS}"
mkdir -p "$BUILD_DIR/cloudformation"
cp cloudformation/4-step-functions.yaml "$BUILD_DIR/cloudformation/4-step-functions.yaml"
scripts/render-state-machine.sh "$SFN_TIMEOUT_SECONDS" "$BUILD_DIR/step-functions/migration-state-machine.json"
aws_cli cloudformation package \
    --template-file "$BUILD_DIR/cloudformation/4-step-functions.yaml" \
    --s3-bucket "$ARTIFACTS_BUCKET" --s3-prefix stepfunctions \
    --output-template-file "$BUILD_DIR/4-step-functions.packaged.yaml" >/dev/null
SFN_PARAMS=("Environment=${ENVIRONMENT}" "TimeoutSeconds=${SFN_TIMEOUT_SECONDS}")
aws_cli cloudformation deploy \
    --template-file "$BUILD_DIR/4-step-functions.packaged.yaml" \
    --stack-name "$SFN_STACK" \
    --capabilities CAPABILITY_IAM \
    --parameter-overrides "${SFN_PARAMS[@]}" \
    --no-fail-on-empty-changeset
ok "Step Functions stack (with the ExternalId secret backstop rule)"

# 5. API with Cognito authorizer, CORS and throttling -----------------------
aws_cli cloudformation deploy \
    --template-file cloudformation/2-backend-api.yaml \
    --stack-name "$API_STACK" \
    --capabilities CAPABILITY_IAM \
    --parameter-overrides \
        "Environment=${ENVIRONMENT}" \
        "S3Bucket=${ARTIFACTS_BUCKET}" \
        "CodeVersion=${CODE_VERSION}" \
        "UserPoolArn=${USER_POOL_ARN}" \
        "AllowedOrigin=${CLOUDFRONT_URL}" \
        "SourceInstanceArn=${SOURCE_INSTANCE_ARN}" \
        "EnableApiAccessLogging=${ENABLE_API_ACCESS_LOGGING}" \
        "ApiGatewayCloudWatchRoleArn=${API_GATEWAY_CLOUDWATCH_ROLE_ARN}" \
    --no-fail-on-empty-changeset
API_ID=$(output "$API_STACK" ApiId)
API_BASE_URL=$(output "$API_STACK" ApiBaseUrl)
# CloudFormation does not redeploy an unchanged AWS::ApiGateway::Deployment, so
# push the current API configuration to the stage explicitly.
aws_cli apigateway create-deployment --rest-api-id "$API_ID" --stage-name "$ENVIRONMENT" \
    --description "deploy.sh ${CODE_VERSION}" >/dev/null
ok "API stack: ${API_BASE_URL}"

# Allow the web UI to call this API (Content-Security-Policy connect-src).
if [[ "$API_ID" != "$EXISTING_API_ID" ]]; then
    aws_cli cloudformation deploy \
        --template-file cloudformation/1-frontend-infrastructure.yaml \
        --stack-name "$FRONTEND_STACK" \
        --parameter-overrides "${FRONTEND_PARAMS[@]}" "ApiId=${API_ID}" \
        --no-fail-on-empty-changeset
    ok "Frontend security headers allow the API"
fi

# 6. Frontend config generated from the stack outputs -----------------------
cat > "$BUILD_DIR/config.js" <<EOF
// Generated by scripts/deploy.sh from CloudFormation outputs. Do not edit.
window.APP_CONFIG = {
  apiBaseUrl: "${API_BASE_URL}",
  cognitoDomain: "${COGNITO_DOMAIN}",
  userPoolClientId: "${USER_POOL_CLIENT_ID}",
  sourceReaderRoleArn: "${SOURCE_READER_ROLE_ARN}",
  targetRoleArn: "${TARGET_ROLE_ARN}",
  phoneNumberMoveEnabled: ${ENABLE_PHONE_NUMBER_MOVE}
};
EOF
cp "$BUILD_DIR/config.js" frontend/config.js
for file in index.html config.js; do
    content_type="text/html"
    [[ "$file" == *.js ]] && content_type="application/javascript"
    aws_cli s3 cp "frontend/${file}" "s3://${WEBSITE_BUCKET}/${file}" \
        --content-type "$content_type" --cache-control "no-cache" --only-show-errors
done
aws ${PROFILE_ARGS[@]+"${PROFILE_ARGS[@]}"} cloudfront create-invalidation --distribution-id "$DISTRIBUTION_ID" --paths "/*" >/dev/null
ok "Frontend uploaded and CloudFront invalidated"

# 7. Optional first user -------------------------------------------------------
if [[ -n "$ADMIN_EMAIL" ]]; then
    if aws_cli cognito-idp admin-get-user --user-pool-id "$USER_POOL_ID" --username "$ADMIN_EMAIL" >/dev/null 2>&1; then
        warn "Cognito user ${ADMIN_EMAIL} already exists"
    else
        aws_cli cognito-idp admin-create-user --user-pool-id "$USER_POOL_ID" --username "$ADMIN_EMAIL" \
            --user-attributes "Name=email,Value=${ADMIN_EMAIL}" "Name=email_verified,Value=true" \
            --desired-delivery-mediums EMAIL >/dev/null
        ok "Cognito user ${ADMIN_EMAIL} created; a temporary password was emailed"
    fi
fi

cat <<EOF

Deployment complete.

  Web UI:              ${CLOUDFRONT_URL}
  Source reader role:  ${SOURCE_READER_ROLE_ARN}
  Target role:         ${TARGET_ROLE_ARN}
  Phone number move:   ${ENABLE_PHONE_NUMBER_MOVE}

Next, with credentials for the TARGET account, create the target role:

  scripts/deploy-target-role.sh \\
      --target-instance-arn <target instance ARN> \\
      --trusted-source-role-arn ${SOURCE_READER_ROLE_ARN}

Then sign in to the web UI and enter the same ExternalId.
EOF
