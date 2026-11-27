#!/usr/bin/env bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# One-click validation for Agentic Insights Radar on AWS.
#
# Deploys the repository the way the README describes (./deployment/deploy.sh), checks the
# deployment, and removes it again with ./deployment/destroy.sh --purge. It takes no input: every
# parameter is a variable below, and each can be set from the environment. Tested target: Linux
# x86_64, AWS CodeBuild image aws/codebuild/amazonlinux-x86_64-standard:5.0. Needs AWS credentials
# for the target account.
#
#   AIR_VALIDATE_REGION=us-west-2 ./deployment/validate.sh   # the two-stack (edge) path
#   AIR_VALIDATE_SOURCE=. ./deployment/validate.sh            # validate this checkout, not GitHub main
#   AIR_VALIDATE_E2E=true ./deployment/validate.sh            # also run the e2e smoke tests (no browser, no AI)
#   AIR_VALIDATE_RETAIN_DATA=true ./deployment/validate.sh    # the default customer path: deletion
#                                                             # protection, retained data, then --purge
#   AIR_VALIDATE_E2E_MARKERS='not browser' ./deployment/validate.sh   # include the AI tests
#   AIR_VALIDATE_UPGRADE_FROM=v1.0.0 ./deployment/validate.sh # deploy that release first, check it, then
#                                                             # upgrade it to the validated code and check again
#   AIR_VALIDATE_CLEANUP=false ./deployment/validate.sh       # keep the deployment; remove it later with:
#   ./deployment/validate.sh --cleanup-only                   # destroy what the last run deployed
#
# Each check prints PASS or FAIL. The script exits 0 only when every check passes. By default the
# stack is destroyed on exit, also after a failure; the two steps above split that in two so each
# can run with fresh credentials (a deploy, the e2e tests and a destroy together can outlast a
# one-hour role session, and credentials that expire during cleanup leave a whole stack billing).
set -euo pipefail

MODE=run
while [[ $# -gt 0 ]]; do
  case "$1" in
    --cleanup-only) MODE=cleanup ;;
    *) echo "usage: validate.sh [--cleanup-only]" >&2; exit 2 ;;
  esac
  shift
done

# ---- Parameters ---------------------------------------------------------------------------------
REPO_URL="${AIR_VALIDATE_REPO:-https://github.com/aws-samples/sample-apj-sup-sa.git}"
# The sample's folder inside REPO_URL (empty when REPO_URL holds the sample at its root).
SUBDIR="${AIR_VALIDATE_SUBDIR-agentic-workloads/agentic-insights-radar}"
BRANCH="${AIR_VALIDATE_BRANCH:-main}"
SOURCE="${AIR_VALIDATE_SOURCE:-}"  # a local checkout to validate instead of cloning REPO_URL (its tracked files)
AWS_REGION="${AIR_VALIDATE_REGION:-us-east-1}"  # us-west-2 (or any Region but us-east-1) exercises the edge stack
ADMIN_EMAIL="${AIR_ADMIN_EMAIL:-admin@example.com}"  # the first admin; its invitation is suppressed (no email is sent)
RUN_E2E="${AIR_VALIDATE_E2E:-false}"  # true: run the e2e suite against the stack
# Which e2e tests: the default leaves out the ones that need a browser or spend model tokens.
E2E_MARKERS="${AIR_VALIDATE_E2E_MARKERS:-not browser and not ai}"
# false is the quick path. true is the default customer path (P2-040): the stack is created
# unprotected and protected in a second deploy, keeps its data on delete, and --purge then has to
# remove the retained tables, buckets, user pool, keys and log groups.
RETAIN_DATA="${AIR_VALIDATE_RETAIN_DATA:-false}"
CLEANUP="${AIR_VALIDATE_CLEANUP:-true}"   # true: destroy the stack when the script exits
# A git tag or branch of the previous release. Set, the run deploys that release, checks it, and then
# upgrades the same stack to the code under validation: service-side refusals of updated properties,
# delete ordering and name collisions show only on an upgrade, never on a fresh install (P3-007).
UPGRADE_FROM="${AIR_VALIDATE_UPGRADE_FROM:-}"
STATE="${AIR_VALIDATE_STATE:-$PWD/.validate-state}"  # what the last run deployed, for --cleanup-only
APP_WAIT_SECONDS="${AIR_VALIDATE_APP_WAIT_SECONDS:-900}"  # how long to wait for the CloudFront URL to serve the app
# -------------------------------------------------------------------------------------------------

export AWS_REGION AWS_DEFAULT_REGION="$AWS_REGION"
FAILURES=0
PASSES=0

log() { printf '[validate] %s\n' "$*"; }
pass() { PASSES=$((PASSES + 1)); printf 'PASS: %s\n' "$*"; }
fail() { FAILURES=$((FAILURES + 1)); printf 'FAIL: %s\n' "$*"; }

stack_exists() {
  aws cloudformation describe-stacks --stack-name "$1" --region "$2" >/dev/null 2>&1
}

# The us-east-1 edge stack of a deployment outside us-east-1: it keeps a WAF log group and a KMS key
# on delete, and the next deploy with the same prefix fails on the log group's fixed name (P2-039).
edge_stack() {
  [[ "$AWS_REGION" == us-east-1 ]] || printf '%s-%s-Edge' "$STACK" "$AWS_REGION"
}

state_value() { awk -F= -v k="$1" '$1==k{sub(/^[^=]*=/, ""); print; exit}' "$STATE"; }

if [[ "$MODE" == cleanup ]]; then
  if [[ ! -f "$STATE" ]]; then
    log "no deployment is recorded in $STATE: nothing to remove"
    exit 0
  fi
  PREFIX="$(state_value PREFIX)"
  WORK_DIR="$(state_value WORK_DIR)"
  REPO_DIR="$(state_value REPO_DIR)"
  AWS_REGION="$(state_value REGION)"
  export AWS_REGION AWS_DEFAULT_REGION="$AWS_REGION"
  CLEANUP=true
  [[ -n "$PREFIX" ]] || { log "$STATE does not name a deployment"; exit 1; }
  # Only a prefix this script generates: a stale or edited state file naming a real deployment's
  # prefix would otherwise purge it, its retained data and its backups with no prompt (SR2-10).
  [[ "$PREFIX" =~ ^mrv[0-9]{10}$ ]] \
    || { log "$STATE names prefix '$PREFIX', which no validation run creates (mrv and 10 digits): refusing to destroy it"; exit 1; }
else
  PREFIX="mrv$(date +%s)"   # unique per run; must match ^[a-z][a-z0-9-]{1,20}$
  WORK_DIR="$(mktemp -d -t mr-validate-XXXXXX)"
  REPO_DIR="$WORK_DIR/repo"
fi
STACK="AgenticInsightsRadar-$PREFIX"   # deploy.sh names the stack AgenticInsightsRadar-<prefix> for any non-default prefix

output_of() {
  aws cloudformation describe-stacks --stack-name "$STACK" --region "$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

cleanup() {
  local status=$1 edge
  trap - EXIT
  set +e
  edge="$(edge_stack)"
  if [[ "$CLEANUP" == "true" ]]; then
    if stack_exists "$STACK" "$AWS_REGION"; then
      log "Destroying $STACK with ./deployment/destroy.sh"
      # --purge --delete-backups: a validation run leaves nothing behind, not even the log groups
      # Lambda creates or a backup vault.
      if ! (cd "$REPO_DIR" && AWS_REGION="$AWS_REGION" \
            ./deployment/destroy.sh --stack "$STACK" --purge --delete-backups --yes); then
        log "destroy.sh failed; deleting the stacks with CloudFormation"
        aws cloudformation delete-stack --stack-name "$STACK" --region "$AWS_REGION"
        aws cloudformation wait stack-delete-complete --stack-name "$STACK" --region "$AWS_REGION" \
          || log "Stack $STACK was not deleted; delete it manually"
        if [[ -n "$edge" ]] && stack_exists "$edge" us-east-1; then
          aws cloudformation delete-stack --stack-name "$edge" --region us-east-1
          aws cloudformation wait stack-delete-complete --stack-name "$edge" --region us-east-1 \
            || log "Stack $edge was not deleted in us-east-1; delete it manually"
        fi
      fi
    else
      log "No stack $STACK to destroy"
    fi
    # Say so loudly: an orphaned stack keeps a distribution, keys and log groups billing, and the
    # next validation with the same prefix fails on their fixed names.
    LEFT=""
    stack_exists "$STACK" "$AWS_REGION" && LEFT="$STACK in $AWS_REGION"
    if [[ -n "$edge" ]] && stack_exists "$edge" us-east-1; then LEFT="$LEFT $edge in us-east-1"; fi
    if [[ -n "$LEFT" ]]; then
      fail "stacks were left behind and keep billing:$LEFT - delete them by hand"
    else
      pass "every stack of this validation was removed"
      rm -f "$STATE"
      rm -rf "$WORK_DIR"
    fi
  else
    log "AIR_VALIDATE_CLEANUP=false: stack $STACK and $WORK_DIR are kept."
    log "Remove them with: ./deployment/validate.sh --cleanup-only"
  fi
  log "Checks passed: $PASSES, failed: $FAILURES"
  if [[ $status -ne 0 || $FAILURES -gt 0 ]]; then
    log "RESULT: FAIL"
    exit 1
  fi
  log "RESULT: PASS"
  exit 0
}
trap 'cleanup $?' EXIT
# --cleanup-only does nothing but the destroy above, with its own (fresh) credentials.
[[ "$MODE" != cleanup ]] || exit 0

# ---- Tools --------------------------------------------------------------------------------------
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  # A pinned release, checked against its published SHA-256 before anything from it runs (this script
  # holds deploy credentials, so no installer is piped into a shell).
  UV_VERSION=0.12.18
  case "$(uname -m)" in
    x86_64) UV_TARGET=x86_64-unknown-linux-gnu UV_SHA256=89eadd7c76fc063887959510d5ba0ab1264dfd5f1143b925ddb73021a40acf16 ;;
    aarch64|arm64) UV_TARGET=aarch64-unknown-linux-gnu UV_SHA256=afb6291f3f0a6b4521fc67b947822506c41dde5b60d2189dd8f3695b2ac8c9e7 ;;
    *) log "no pinned uv build for $(uname -m); install uv $UV_VERSION first"; exit 1 ;;
  esac
  log "Installing uv $UV_VERSION ($UV_TARGET)"
  UV_TGZ="$(mktemp)"
  curl -fsSL -o "$UV_TGZ" "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$UV_TARGET.tar.gz"
  echo "$UV_SHA256  $UV_TGZ" | sha256sum -c - >/dev/null || { log "uv download failed its checksum"; exit 1; }
  mkdir -p "$HOME/.local/bin"
  tar -xzf "$UV_TGZ" -C "$HOME/.local/bin" --strip-components=1 "uv-$UV_TARGET/uv" "uv-$UV_TARGET/uvx"
  rm -f "$UV_TGZ"
fi
for tool in git aws uv node npm python3 curl; do
  command -v "$tool" >/dev/null 2>&1 || { log "$tool is not installed"; exit 1; }
done
log "aws $(aws --version 2>&1 | cut -d' ' -f1), node $(node --version), $(python3 --version), $(uv --version)"

ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
log "Account $ACCOUNT, Region $AWS_REGION, prefix $PREFIX, stack $STACK"

# ---- Clone and deploy (README: Deployment Steps) ------------------------------------------------
# fetch_release <ref or empty> <dir>: the tracked files of SOURCE (at <ref>, or HEAD), or a clone.
fetch_release() {
  local ref=$1 dir=$2
  mkdir -p "$dir"
  if [[ -n "$SOURCE" ]]; then
    # SOURCE may be the sample's folder inside a larger repository: archive that folder's tree only.
    local top prefix
    top="$(git -C "$SOURCE" rev-parse --show-toplevel)"
    prefix="$(git -C "$SOURCE" rev-parse --show-prefix)"
    log "Copying the tracked files of $SOURCE at ${ref:-HEAD}"
    git -C "$top" archive --format=tar "${ref:-HEAD}:${prefix}" | tar -x -C "$dir"
  elif [[ -n "$SUBDIR" ]]; then
    # Only the sample's folder: a shallow, sparse clone, then that folder's files.
    log "Cloning $SUBDIR of $REPO_URL (${ref:-$BRANCH})"
    git clone --depth 1 --filter=blob:none --sparse --branch "${ref:-$BRANCH}" "$REPO_URL" "$dir.clone"
    git -C "$dir.clone" sparse-checkout set "$SUBDIR"
    cp -a "$dir.clone/$SUBDIR/." "$dir/"
    rm -rf "$dir.clone"
  else
    log "Cloning $REPO_URL (${ref:-$BRANCH})"
    git clone --depth 1 --branch "${ref:-$BRANCH}" "$REPO_URL" "$dir"
  fi
}

deploy_from() {
  (cd "$1" && AIR_PREFIX="$PREFIX" AIR_RETAIN_DATA="$RETAIN_DATA" AIR_SUPPRESS_INVITATION=true AIR_E2E_SIGN_IN="$RUN_E2E" \
    ./deployment/deploy.sh "$ADMIN_EMAIL" </dev/null)
}

fetch_release "" "$REPO_DIR"
# Recorded before the deploy, so --cleanup-only can remove a deployment this run did not finish.
mkdir -p "$(dirname "$STATE")"
printf 'PREFIX=%s\nWORK_DIR=%s\nREPO_DIR=%s\nREGION=%s\n' "$PREFIX" "$WORK_DIR" "$REPO_DIR" "$AWS_REGION" >"$STATE"

# ---- Checks (README: Deployment Validation) -----------------------------------------------------
# checks <label>: every check below, each line prefixed with <label> ("" for a plain run).
checks() {
  local at=${1:+$1: } table
  STATUS="$(aws cloudformation describe-stacks --stack-name "$STACK" --region "$AWS_REGION" \
            --query "Stacks[0].StackStatus" --output text 2>/dev/null || echo MISSING)"
  if [[ "$STATUS" == "CREATE_COMPLETE" || "$STATUS" == "UPDATE_COMPLETE" ]]; then
    pass "${at}stack $STACK is $STATUS"
  else
    fail "${at}stack $STACK is $STATUS"
  fi

  APP_URL="$(output_of AppUrl 2>/dev/null || true)"
  USER_POOL_ID="$(output_of UserPoolId 2>/dev/null || true)"
  if [[ "$APP_URL" == https://* && -n "$USER_POOL_ID" && "$USER_POOL_ID" != "None" ]]; then
    pass "${at}stack outputs AppUrl ($APP_URL) and UserPoolId ($USER_POOL_ID)"
  else
    fail "${at}stack outputs AppUrl and UserPoolId are missing"
    exit 1
  fi

  # A new CloudFront distribution can take a few minutes to serve its first request.
  INDEX="$WORK_DIR/index.html"
  CODE="000"
  DEADLINE=$((SECONDS + APP_WAIT_SECONDS))
  while (( SECONDS < DEADLINE )); do
    CODE="$(curl -sS -o "$INDEX" -w '%{http_code}' "$APP_URL/" 2>/dev/null || echo 000)"
    [[ "$CODE" == "200" ]] && break
    sleep 20
  done
  if [[ "$CODE" == "200" ]] && grep -q '<div id="root"></div>' "$INDEX" && grep -q '<title>Agentic Insights Radar</title>' "$INDEX"; then
    pass "${at}AppUrl returns 200 and serves index.html"
  else
    fail "${at}AppUrl returned HTTP $CODE or not the app's index.html"
  fi

  CONFIG="$WORK_DIR/config.json"
  CODE="$(curl -sS -o "$CONFIG" -w '%{http_code}' "$APP_URL/api/config" 2>/dev/null || echo 000)"
  if [[ "$CODE" == "200" ]] && python3 - "$CONFIG" "$USER_POOL_ID" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as f:
    body = json.load(f)
sys.exit(0 if body.get("user_pool_id") == sys.argv[2] else 1)
PY
  then
    pass "${at}GET /api/config returns JSON with user_pool_id $USER_POOL_ID"
  else
    fail "${at}GET /api/config returned HTTP $CODE or no matching user_pool_id"
  fi

  CODE="$(curl -sS -o /dev/null -w '%{http_code}' "$APP_URL/api/radars" 2>/dev/null || echo 000)"
  if [[ "$CODE" == "401" ]]; then
    pass "${at}unauthenticated GET /api/radars returns 401"
  else
    fail "${at}unauthenticated GET /api/radars returned HTTP $CODE (expected 401)"
  fi

  USERS="$(aws cognito-idp list-users --user-pool-id "$USER_POOL_ID" --region "$AWS_REGION" \
           --filter "email = \"$ADMIN_EMAIL\"" --query "length(Users)" --output text 2>/dev/null || echo 0)"
  if [[ "$USERS" == "1" ]]; then
    pass "${at}first administrator $ADMIN_EMAIL exists in the user pool"
  else
    fail "${at}first administrator $ADMIN_EMAIL not found in the user pool"
  fi

  ADMINS="$(aws cognito-idp list-users-in-group --user-pool-id "$USER_POOL_ID" --region "$AWS_REGION" \
            --group-name admin --query "Users[].Attributes[?Name=='email'].Value[]" --output text 2>/dev/null || true)"
  if grep -qF "$ADMIN_EMAIL" <<<"$ADMINS"; then
    pass "${at}first administrator is in the admin group"
  else
    fail "${at}first administrator is not in the admin group"
  fi

  if [[ "$RETAIN_DATA" == true ]]; then
    # The second, protecting deploy ran only if these are on; without it a destroy would delete data
    # the customer was told is kept.
    for table in $(aws cloudformation list-stack-resources --stack-name "$STACK" --region "$AWS_REGION" \
                   --query "StackResourceSummaries[?ResourceType=='AWS::DynamoDB::GlobalTable'].PhysicalResourceId" \
                   --output text); do
      PROTECTED="$(aws dynamodb describe-table --table-name "$table" --region "$AWS_REGION" \
                   --query "Table.DeletionProtectionEnabled" --output text 2>/dev/null || echo unknown)"
      if [[ "$PROTECTED" == True ]]; then
        pass "${at}deletion protection is on for $table"
      else
        fail "${at}deletion protection is $PROTECTED for $table (the protecting update did not run)"
      fi
    done
  fi
}

if [[ -n "$UPGRADE_FROM" ]]; then
  OLD_DIR="$WORK_DIR/previous"
  fetch_release "$UPGRADE_FROM" "$OLD_DIR"
  log "Deploying the previous release $UPGRADE_FROM"
  deploy_from "$OLD_DIR"
  checks "before the upgrade from $UPGRADE_FROM"
  log "Upgrading $STACK to the code under validation"
  if deploy_from "$REPO_DIR"; then
    pass "the upgrade from $UPGRADE_FROM completed"
  else
    fail "the upgrade from $UPGRADE_FROM failed (see the stack events)"
    exit 1
  fi
  checks "after the upgrade"
else
  log "Deploying"
  deploy_from "$REPO_DIR"
  checks ""
fi

if [[ "$RUN_E2E" == true ]]; then
  log "Running the e2e tests matching: $E2E_MARKERS"
  # --frozen, as CI and CONTRIBUTING run it: the lock file, never a fresh resolution (P3-079).
  if [[ "$E2E_MARKERS" != *"not browser"* ]]; then
    (cd "$REPO_DIR/e2e" && uv run --frozen playwright install chromium)
  fi
  if (cd "$REPO_DIR/e2e" && AIR_E2E_TARGET="$STACK" uv run --frozen pytest tests -q -m "$E2E_MARKERS"); then
    pass "e2e tests ($E2E_MARKERS)"
  else
    fail "e2e tests ($E2E_MARKERS)"
  fi
fi

# The trap destroys the stack (CLEANUP=true) and prints the result.
