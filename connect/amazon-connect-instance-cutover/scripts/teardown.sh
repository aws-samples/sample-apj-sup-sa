#!/usr/bin/env bash
# Amazon Connect Customer Migration Tool: remove everything the tool deployed.
#
# Usage:
#   scripts/teardown.sh --region REGION [options]
#
# Source (tool) account, in this order:
#   1. delete connect-migration-backend-<env>, connect-migration-stepfunctions-<env> and
#      connect-migration-lambdas-<env> (this deletes the tool's IAM roles in those stacks);
#   2. empty the website, artifacts and access log buckets (every object version and
#      delete marker; the stack outputs WebsiteBucket, ArtifactsBucket, AccessLogsBucket);
#   3. delete connect-migration-frontend-<env> (CloudFront, Cognito user pool and its users,
#      the now empty buckets).
# Target account, only with --target-profile (or --target-same-credentials):
#   4. delete the target role stack (ConnectMigrationTargetWriter);
#   5. empty and delete the temporary prompt bucket
#      connect-migration-prompts-temp-<target account>-<target Region>.
#
# It never calls the Amazon Connect Customer API: both Connect instances, their
# configuration and their phone numbers are left as they are.
#
# Options:
#   --region REGION              Region of the tool stacks (required)
#   --environment NAME           Environment suffix used at deploy time (default: prod)
#   --profile NAME               AWS CLI profile for the source (tool) account
#   --target-profile NAME        AWS CLI profile for the target account (enables steps 4 and 5)
#   --target-same-credentials    Steps 4 and 5 with the source credentials (same account runs)
#   --target-region REGION       Target Region, for the prompt bucket name (default: --region)
#   --target-stack-name NAME     Target role stack (default: connect-migration-target-role)
#   --dry-run                    Print what would be deleted; change nothing
#   --yes                        Do not ask for confirmation
#   -h, --help                   Show this help
#
# Not removed: CloudWatch log groups the Lambda functions created, the account level
# API Gateway CloudWatch role setting (if --api-gateway-cloudwatch-role-arn was used),
# and any ExternalId secret a failed cleanup left behind (see Cleanup failure queue).

set -euo pipefail

usage() { awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; }

REGION=""
ENVIRONMENT="prod"
PROFILE_ARGS=()
TARGET_PROFILE_ARGS=()
TARGET_ENABLED="false"
TARGET_REGION=""
TARGET_STACK="connect-migration-target-role"
DRY_RUN="false"
ASSUME_YES="false"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --region) REGION="${2:-}"; shift 2 ;;
        --environment) ENVIRONMENT="${2:-}"; shift 2 ;;
        --profile) PROFILE_ARGS=(--profile "${2:-}"); shift 2 ;;
        --target-profile) TARGET_PROFILE_ARGS=(--profile "${2:-}"); TARGET_ENABLED="true"; shift 2 ;;
        --target-same-credentials) TARGET_ENABLED="true"; shift ;;
        --target-region) TARGET_REGION="${2:-}"; shift 2 ;;
        --target-stack-name) TARGET_STACK="${2:-}"; shift 2 ;;
        --dry-run) DRY_RUN="true"; shift ;;
        --yes) ASSUME_YES="true"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

die() { echo "[x] $1" >&2; exit 1; }
# Format only (any Region code, including ones launched later); never a Region list.
REGION_PATTERN='^[a-z]{2,}(-[a-z]+)+-[0-9]+$'
[[ "$REGION" =~ $REGION_PATTERN ]] || die "--region is required (for example the Region you deployed to)"
TARGET_REGION="${TARGET_REGION:-$REGION}"
[[ "$TARGET_REGION" =~ $REGION_PATTERN ]] || die "--target-region is not a Region name"
[[ "$ENVIRONMENT" =~ ^[a-z0-9-]{1,20}$ ]] || die "--environment must be lower case letters, digits or '-'"
[[ "$TARGET_STACK" =~ ^[A-Za-z][A-Za-z0-9-]{0,127}$ ]] || die "--target-stack-name is not a stack name"
if [[ "$TARGET_ENABLED" == "true" && ${#TARGET_PROFILE_ARGS[@]} -eq 0 && ${#PROFILE_ARGS[@]} -gt 0 ]]; then
    TARGET_PROFILE_ARGS=("${PROFILE_ARGS[@]}")
fi
command -v aws >/dev/null 2>&1 || die "'aws' is required"

src() { aws ${PROFILE_ARGS[@]+"${PROFILE_ARGS[@]}"} --region "$REGION" "$@"; }
tgt() { aws ${TARGET_PROFILE_ARGS[@]+"${TARGET_PROFILE_ARGS[@]}"} --region "$TARGET_REGION" "$@"; }

FRONTEND_STACK="connect-migration-frontend-${ENVIRONMENT}"
LAMBDA_STACK="connect-migration-lambdas-${ENVIRONMENT}"
SFN_STACK="connect-migration-stepfunctions-${ENVIRONMENT}"
API_STACK="connect-migration-backend-${ENVIRONMENT}"

stack_exists() { # stack_exists <runner> <stack>
    "$1" cloudformation describe-stacks --stack-name "$2" --query 'Stacks[0].StackStatus' --output text >/dev/null 2>&1
}
output() { # output <stack> <key>
    src cloudformation describe-stacks --stack-name "$1" \
        --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" --output text 2>/dev/null || true
}
bucket_exists() { "$1" s3api head-bucket --bucket "$2" >/dev/null 2>&1; }

SOURCE_ACCOUNT=$(src sts get-caller-identity --query Account --output text)
[[ "$SOURCE_ACCOUNT" =~ ^[0-9]{12}$ ]] || die "Could not read the source account from the credentials"

BUCKETS=()
if stack_exists src "$FRONTEND_STACK"; then
    for key in WebsiteBucket ArtifactsBucket AccessLogsBucket; do
        name=$(output "$FRONTEND_STACK" "$key")
        [[ "$name" =~ ^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$ ]] && BUCKETS+=("$name")
    done
fi

TARGET_ACCOUNT=""
PROMPT_BUCKET=""
if [[ "$TARGET_ENABLED" == "true" ]]; then
    TARGET_ACCOUNT=$(tgt sts get-caller-identity --query Account --output text)
    [[ "$TARGET_ACCOUNT" =~ ^[0-9]{12}$ ]] || die "Could not read the target account from the credentials"
    PROMPT_BUCKET="connect-migration-prompts-temp-${TARGET_ACCOUNT}-${TARGET_REGION}"
fi

echo "Teardown plan for environment '${ENVIRONMENT}' (tool account ${SOURCE_ACCOUNT}, Region ${REGION}):"
for stack in "$API_STACK" "$SFN_STACK" "$LAMBDA_STACK"; do
    if stack_exists src "$stack"; then echo "  delete stack $stack"; else echo "  skip stack $stack (not found)"; fi
done
for bucket in ${BUCKETS[@]+"${BUCKETS[@]}"}; do echo "  empty bucket $bucket (all versions)"; done
if stack_exists src "$FRONTEND_STACK"; then echo "  delete stack $FRONTEND_STACK"; else echo "  skip stack $FRONTEND_STACK (not found)"; fi
if [[ "$TARGET_ENABLED" == "true" ]]; then
    echo "Target account ${TARGET_ACCOUNT}, Region ${TARGET_REGION}:"
    if stack_exists tgt "$TARGET_STACK"; then echo "  delete stack $TARGET_STACK"; else echo "  skip stack $TARGET_STACK (not found)"; fi
    if bucket_exists tgt "$PROMPT_BUCKET"; then echo "  empty and delete bucket $PROMPT_BUCKET"; else echo "  skip bucket $PROMPT_BUCKET (not found)"; fi
else
    echo "Target account: skipped (pass --target-profile to delete the target role and the prompt bucket)"
fi
echo "Amazon Connect Customer instances are not touched."

if [[ "$DRY_RUN" == "true" ]]; then
    echo "Dry run: nothing was changed."
    exit 0
fi

if [[ "$ASSUME_YES" != "true" ]]; then
    [[ -t 0 ]] || die "Not a terminal: pass --yes to confirm, or --dry-run to only print the plan"
    read -r -p "Type the environment name ('${ENVIRONMENT}') to delete all of the above: " answer
    [[ "$answer" == "$ENVIRONMENT" ]] || die "Not confirmed; nothing was changed"
fi

delete_stack() { # delete_stack <runner> <stack>
    if stack_exists "$1" "$2"; then
        echo "Deleting stack $2..."
        "$1" cloudformation delete-stack --stack-name "$2"
        "$1" cloudformation wait stack-delete-complete --stack-name "$2"
        echo "[ok] $2 deleted"
    fi
}

empty_bucket() { # empty_bucket <runner> <bucket>: every object version and delete marker, 1000 at a time
    local page
    bucket_exists "$1" "$2" || return 0
    echo "Emptying bucket $2..."
    while :; do
        page=$("$1" s3api list-object-versions --bucket "$2" --no-paginate --max-keys 1000 \
            --query '{Objects: [Versions, DeleteMarkers][][].{Key: Key, VersionId: VersionId}, Quiet: `true`}' \
            --output json)
        if [[ "$page" != *'"Key"'* ]]; then break; fi
        "$1" s3api delete-objects --bucket "$2" --delete "$page" >/dev/null
    done
    echo "[ok] $2 is empty"
}

for stack in "$API_STACK" "$SFN_STACK" "$LAMBDA_STACK"; do delete_stack src "$stack"; done
for bucket in ${BUCKETS[@]+"${BUCKETS[@]}"}; do empty_bucket src "$bucket"; done
delete_stack src "$FRONTEND_STACK"

if [[ "$TARGET_ENABLED" == "true" ]]; then
    delete_stack tgt "$TARGET_STACK"
    if bucket_exists tgt "$PROMPT_BUCKET"; then
        empty_bucket tgt "$PROMPT_BUCKET"
        tgt s3api delete-bucket --bucket "$PROMPT_BUCKET" --expected-bucket-owner "$TARGET_ACCOUNT"
        echo "[ok] $PROMPT_BUCKET deleted"
    fi
fi
echo "Teardown complete. Amazon Connect Customer instances were not touched."
