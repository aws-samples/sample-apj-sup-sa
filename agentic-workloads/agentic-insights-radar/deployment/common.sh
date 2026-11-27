# shellcheck shell=bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# Shared by deploy.sh, destroy.sh and publish-site.sh. Sourced, never run on its own.
# Expects ROOT (the repository root) to be set by the caller.

air_die() { echo "error: $*" >&2; exit 1; }

# The Region the same way in every script: AWS_REGION, then AWS_DEFAULT_REGION, then the CLI profile.
air_region() {
  local region="${AWS_REGION:-${AWS_DEFAULT_REGION:-}}"
  [[ -n "$region" ]] || region="$(aws configure get region 2>/dev/null || true)"
  [[ -n "$region" ]] || air_die "set AWS_REGION (or AWS_DEFAULT_REGION, or a Region in your AWS CLI profile)"
  printf '%s' "$region"
}

# The stack name for a prefix: the default prefix keeps the plain name.
air_stack() {
  local prefix="${1:-air}"
  if [[ "$prefix" == air ]]; then printf 'AgenticInsightsRadar'; else printf 'AgenticInsightsRadar-%s' "$prefix"; fi
}

# The prefix of a stack name (the reverse of air_stack).
air_prefix_of() {
  local stack="$1"
  case "$stack" in
    AgenticInsightsRadar) printf 'air' ;;
    AgenticInsightsRadar-*) printf '%s' "${stack#AgenticInsightsRadar-}" ;;
    *) air_die "$stack is not an Agentic Insights Radar stack name (AgenticInsightsRadar or AgenticInsightsRadar-<prefix>)" ;;
  esac
}

# Tools, their minimum versions, and an AWS CLI new enough for the services the scripts call.
air_check_tools() {
  local tool
  for tool in aws uv node npm python3; do command -v "$tool" >/dev/null || air_die "$tool is not installed"; done
  python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))' || air_die "Python 3.12 or later is needed ($(python3 --version 2>&1))"
  node -e 'process.exit(Number(process.versions.node.split(".")[0]) < 20 ? 1 : 0)' \
    || air_die "Node.js 20 or later is needed ($(node --version 2>&1))"
  local cmd
  for cmd in "s3vectors list-vector-buckets" "bedrock-agentcore-control list-agent-runtimes"; do
    # --generate-cli-skeleton runs nothing: it only fails when this AWS CLI does not know the command.
    # shellcheck disable=SC2086  # the service and operation are two words on purpose
    AWS_PAGER="" aws $cmd --generate-cli-skeleton >/dev/null 2>&1 \
      || air_die "this AWS CLI has no 'aws ${cmd%% *}' command: install a current AWS CLI v2 ($(aws --version 2>&1 | cut -d' ' -f1))"
  done
}

# The global cross-Region inference profile of each model-catalog key (the deploy-time preflight
# checks the chosen chat model, not only the default). Kept in step with
# backend/src/insights_radar/modules/catalog.py by infra/tests/test_scripts.py.
air_model_profile() {
  case "$1" in
    haiku-4-5)   printf 'global.anthropic.claude-haiku-4-5-20251001-v1:0' ;;
    nova-2-lite) printf 'global.amazon.nova-2-lite-v1:0' ;;
    sonnet-5-5)  printf 'global.anthropic.claude-sonnet-5-5' ;;
    sonnet-4-6)  printf 'global.anthropic.claude-sonnet-4-6' ;;
    opus-5-5)    printf 'global.anthropic.claude-opus-5-5' ;;
    fable-5-1)   printf 'global.anthropic.claude-fable-5-1' ;;
    *) air_die "unknown model key '$1' (see the implementation guide, Deployment parameters)" ;;
  esac
}

# The pinned AWS CDK CLI from infra/package-lock.json, installed without running package scripts.
air_cdk() {
  npm ci --ignore-scripts --no-audit --no-fund --silent --prefix "$ROOT/infra" >/dev/null \
    || air_die "could not install the AWS CDK CLI from infra/package-lock.json"
  CDK="$ROOT/infra/node_modules/.bin/cdk"
  [[ -x "$CDK" ]] || air_die "the AWS CDK CLI is missing from infra/node_modules"
  # The sample sends no telemetry: the CLI's usage reporting is off here, the template's
  # AWS::CDK::Metadata resource by "versionReporting": false in infra/cdk.json.
  export CDK_DISABLE_CLI_TELEMETRY=true
}

# The stack's status, or NONE when it does not exist. Any other error stops the script.
air_stack_status() {
  local stack="$1" status
  if ! status="$(aws cloudformation describe-stacks --stack-name "$stack" --query 'Stacks[0].StackStatus' \
                 --output text 2>&1)"; then
    [[ "$status" == *"does not exist"* ]] || air_die "cannot read stack $stack: $status"
    status=NONE
  fi
  printf '%s' "$status"
}
