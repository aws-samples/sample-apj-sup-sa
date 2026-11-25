#!/usr/bin/env bash
# Shared by deployment, console-user, and uninstall scripts. Source this file; do not execute it.

require_tool() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "error: $1 is required; see README.md → Installation." >&2
    return 1
  }
}

load_project_settings() {
  local root="$1" before after
  DEPLOY_ENV_FILE="${BEDROCK_METER_DEPLOY_ENV_FILE:-$root/.env.deploy.local}"
  if [ ! -f "$DEPLOY_ENV_FILE" ]; then
    if [ -n "${BEDROCK_METER_DEPLOY_ENV_FILE:-}" ]; then
      echo "error: deployment settings file not found: $DEPLOY_ENV_FILE" >&2
      return 1
    fi
    return 0
  fi
  # The file is trusted shell configuration, as before. It may set project options,
  # but must not change the terminal's AWS credentials, profile, endpoints, or region.
  # Compare privately; never print this snapshot because it can contain credentials.
  before="$(env | LC_ALL=C sort | sed -n '/^AWS_/p')"
  set -a
  # shellcheck source=/dev/null
  . "$DEPLOY_ENV_FILE"
  set +a
  after="$(env | LC_ALL=C sort | sed -n '/^AWS_/p')"
  if [ "$before" != "$after" ]; then
    echo "error: $DEPLOY_ENV_FILE changes AWS_* settings. Move them to your terminal/AWS profile; keep only project settings in this file." >&2
    return 1
  fi
}

resolve_aws_target() {
  local version
  require_tool aws
  require_tool jq
  version="$(aws --version)"
  case "$version" in
    aws-cli/2.*) ;;
    *) echo "error: AWS CLI v2 is required." >&2; return 1 ;;
  esac

  if [ -n "${CDK_DEFAULT_ACCOUNT:-}${CDK_DEFAULT_REGION:-}${REGION:-}" ]; then
    echo "Note: legacy CDK_DEFAULT_ACCOUNT/CDK_DEFAULT_REGION/REGION inputs are ignored. Use AWS credentials and AWS_REGION or your profile's region." >&2
  fi
  REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-}}"
  if [ -z "$REGION" ]; then
    # configure get reads the selected profile; it does not resolve region env vars.
    REGION="$(aws configure get region)" || {
      echo "error: cannot resolve an AWS region. Set AWS_REGION or configure a region in the selected AWS profile." >&2
      return 1
    }
  fi
  if [[ ! "$REGION" =~ ^[a-z][a-z0-9-]*-[0-9]+$ ]]; then
    echo "error: missing or invalid AWS region. Set AWS_REGION (for example ap-northeast-2) or configure your AWS profile." >&2
    return 1
  fi
  ACCOUNT="$(aws sts get-caller-identity --region "$REGION" --query Account --output text)" || {
    echo "error: cannot verify AWS credentials. Check AWS_PROFILE and refresh your login, then retry." >&2
    return 1
  }
  if [[ ! "$ACCOUNT" =~ ^[0-9]{12}$ ]]; then
    echo "error: STS did not return a valid AWS account ID." >&2
    return 1
  fi
  # Normalize for both CLI and SDK consumers. Do not select different credentials.
  export AWS_REGION="$REGION" AWS_DEFAULT_REGION="$REGION"
  export CDK_DEFAULT_ACCOUNT="$ACCOUNT" CDK_DEFAULT_REGION="$REGION"
  echo "AWS target: account=$ACCOUNT region=$REGION profile=${AWS_PROFILE:-${AWS_DEFAULT_PROFILE:-default credential chain}}"
}

check_stack_state() {
  local stack="$1" result
  if result="$(AWS_CLI_ERROR_FORMAT=legacy aws cloudformation describe-stacks \
      --stack-name "$stack" --region "$REGION" \
      --query 'Stacks[0].StackStatus' --output text 2>&1)"; then
    case "$result" in
      *_IN_PROGRESS|ROLLBACK_COMPLETE|*_FAILED)
        echo "error: $stack is $result. Inspect its events and follow docs/OperationalGuide.md → Installation failures and manual recovery before retrying." >&2
        return 1
        ;;
    esac
  elif [[ "$result" == *"(ValidationError)"* && "$result" == *"Stack with id $stack does not exist"* ]]; then
    return 0
  else
    printf 'error: cannot inspect stack %s; no deployment started.\n%s\n' "$stack" "$result" >&2
    return 1
  fi
}
