#!/usr/bin/env bash
# Create the ConnectMigrationTargetWriter role in the TARGET account.
#
# Usage:
#   scripts/deploy-target-role.sh --target-instance-arn <arn> --trusted-source-role-arn <arn> \
#       --external-id-file <path> [options]
#   ... | scripts/deploy-target-role.sh --target-instance-arn <arn> --trusted-source-role-arn <arn> \
#       --external-id-stdin [options]
#
# Required:
#   --target-instance-arn ARN       Target Connect instance (in the account you deploy to)
#   --trusted-source-role-arn ARN   LambdaRoleArn output of the tool's Lambda stack (source account)
#   --external-id-file PATH         File whose first line is the ExternalId (keep it mode 600), or
#   --external-id-stdin             Read the ExternalId from standard input (first line)
#                                   Without either, an interactive terminal is prompted without echo.
# Options:
#   --stack-name NAME               Default: connect-migration-target-role
#   --role-name NAME                Default: ConnectMigrationTargetWriter
#   --allow-phone-number-move       Grant connect:UpdatePhoneNumber (same account and Region only)
#   --profile NAME                  AWS CLI profile for the target account
#   -h, --help                      Show this help
#
# Generate an ExternalId with: openssl rand -hex 32 > externalid.txt; chmod 600 externalid.txt
# The value never appears on a command line or in the environment: it is passed to
# CloudFormation through a mode 600 temporary parameters file that is deleted on exit,
# as a NoEcho parameter, and it is never printed.

set -euo pipefail

usage() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; }

TARGET_INSTANCE_ARN=""
TRUSTED_SOURCE_ROLE_ARN=""
STACK_NAME="connect-migration-target-role"
ROLE_NAME="ConnectMigrationTargetWriter"
ALLOW_PHONE_NUMBER_MOVE="false"
EXTERNAL_ID_FILE=""
EXTERNAL_ID_STDIN="false"
PROFILE_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --target-instance-arn) TARGET_INSTANCE_ARN="$2"; shift 2 ;;
        --trusted-source-role-arn) TRUSTED_SOURCE_ROLE_ARN="$2"; shift 2 ;;
        --external-id-file) EXTERNAL_ID_FILE="$2"; shift 2 ;;
        --external-id-stdin) EXTERNAL_ID_STDIN="true"; shift ;;
        --stack-name) STACK_NAME="$2"; shift 2 ;;
        --role-name) ROLE_NAME="$2"; shift 2 ;;
        --allow-phone-number-move) ALLOW_PHONE_NUMBER_MOVE="true"; shift ;;
        --profile) PROFILE_ARGS=(--profile "$2"); shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

die() { echo "[x] $1" >&2; exit 1; }

[[ "$TARGET_INSTANCE_ARN" =~ ^arn:aws[a-z-]*:connect:([a-z0-9-]+):([0-9]{12}):instance/[0-9a-f-]{36}$ ]] \
    || { usage >&2; die "--target-instance-arn must be a Connect instance ARN"; }
TARGET_REGION="${BASH_REMATCH[1]}"
TARGET_ACCOUNT="${BASH_REMATCH[2]}"
[[ "$TRUSTED_SOURCE_ROLE_ARN" =~ ^arn:aws[a-z-]*:iam::[0-9]{12}:role/.+$ ]] \
    || { usage >&2; die "--trusted-source-role-arn must be an IAM role ARN"; }
[[ -n "$EXTERNAL_ID_FILE" && "$EXTERNAL_ID_STDIN" == "true" ]] \
    && die "Use either --external-id-file or --external-id-stdin, not both"

# Read the ExternalId without it ever being an argument or an environment variable.
EXTERNAL_ID=""
if [[ -n "$EXTERNAL_ID_FILE" ]]; then
    [[ -r "$EXTERNAL_ID_FILE" ]] || die "Cannot read --external-id-file"
    IFS= read -r EXTERNAL_ID < "$EXTERNAL_ID_FILE" || true
elif [[ "$EXTERNAL_ID_STDIN" == "true" ]]; then
    IFS= read -r EXTERNAL_ID || true
elif [[ -t 0 ]]; then
    IFS= read -r -s -p "ExternalId (input hidden): " EXTERNAL_ID
    echo
else
    usage >&2
    die "Pass --external-id-file or --external-id-stdin"
fi
EXTERNAL_ID="${EXTERNAL_ID%$'\r'}"
if (( ${#EXTERNAL_ID} < 2 || ${#EXTERNAL_ID} > 1224 )) || [[ ! "$EXTERNAL_ID" =~ ^[A-Za-z0-9_+=,.@:/-]+$ ]]; then
    die "ExternalId must be 2 to 1224 characters of letters, digits and _+=,.@:/-"
fi

CALLER_ACCOUNT=$(aws ${PROFILE_ARGS[@]+"${PROFILE_ARGS[@]}"} --region "$TARGET_REGION" sts get-caller-identity --query Account --output text)
[[ "$CALLER_ACCOUNT" == "$TARGET_ACCOUNT" ]] \
    || die "Credentials are for account $CALLER_ACCOUNT but the target instance is in $TARGET_ACCOUNT"

# Parameters go to the CLI through a private temporary file (JSON list of Key=Value
# strings), so the ExternalId is not visible in the process list. The value is
# validated above to a character set that needs no JSON escaping.
umask 077
PARAMS_FILE=$(mktemp "${TMPDIR:-/tmp}/target-role-params.XXXXXX")
trap 'rm -f "$PARAMS_FILE"' EXIT
chmod 600 "$PARAMS_FILE"
printf '["TargetInstanceArn=%s","TrustedSourceRoleArn=%s","ExternalId=%s","RoleName=%s","AllowPhoneNumberMove=%s"]\n' \
    "$TARGET_INSTANCE_ARN" "$TRUSTED_SOURCE_ROLE_ARN" "$EXTERNAL_ID" "$ROLE_NAME" "$ALLOW_PHONE_NUMBER_MOVE" > "$PARAMS_FILE"
EXTERNAL_ID=""

cd "$(dirname "$0")/.."
aws ${PROFILE_ARGS[@]+"${PROFILE_ARGS[@]}"} --region "$TARGET_REGION" cloudformation deploy \
    --template-file cloudformation/target-role.yaml \
    --stack-name "$STACK_NAME" \
    --capabilities CAPABILITY_NAMED_IAM \
    --parameter-overrides "file://${PARAMS_FILE}" \
    --no-fail-on-empty-changeset
rm -f "$PARAMS_FILE"

ROLE_ARN=$(aws ${PROFILE_ARGS[@]+"${PROFILE_ARGS[@]}"} --region "$TARGET_REGION" cloudformation describe-stacks --stack-name "$STACK_NAME" \
    --query "Stacks[0].Outputs[?OutputKey=='TargetRoleArn'].OutputValue" --output text)
echo "Target role: ${ROLE_ARN}"
echo "Enter this role ARN and the same ExternalId in the web UI."
