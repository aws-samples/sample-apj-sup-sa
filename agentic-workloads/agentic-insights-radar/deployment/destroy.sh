#!/usr/bin/env bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# Remove an Agentic Insights Radar deployment.
#
#   ./deployment/destroy.sh [--stack <name>] [--purge] [--delete-backups] [--yes]
#
# The stack is --stack, or the one AIR_PREFIX names (AgenticInsightsRadar for the default prefix). The
# script prints the account, Region and stack, lists the stack's stateful resources, and deletes nothing
# until you type the stack name. --yes skips that question and is accepted only with an explicit --stack,
# so automation always names what it deletes.
#
# With retainData=true (the default) tables, buckets, the user pool, the data key and the digest secrets are kept; the list
# is saved to build/purge-<stack>-<region>.json so you can delete them later. --purge deletes them right
# after the stack. Even with retainData=true the stack itself deletes the S3 Vectors indexes, the
# knowledge base, the chat history (AgentCore Memory), the runtimes and the run-token key.
#
# A backup vault from backupPlan=true is the one thing --purge keeps: its recovery points are encrypted
# with the data key, so --purge also keeps that key. --delete-backups (only with --purge) deletes the
# vault, every recovery point in it, and then the key.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=deployment/common.sh
source "$ROOT/deployment/common.sh"

PURGE=0; YES=0; STACK=""; DELETE_BACKUPS=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --purge) PURGE=1 ;;
    --delete-backups) DELETE_BACKUPS=1 ;;
    --yes) YES=1 ;;
    --stack) [[ $# -ge 2 ]] || air_die "--stack needs a stack name"; STACK="$2"; shift ;;
    *) echo "usage: destroy.sh [--stack <name>] [--purge] [--delete-backups] [--yes]" >&2; exit 2 ;;
  esac
  shift
done
[[ "$YES" == 0 || -n "$STACK" ]] || air_die "--yes needs --stack <name>: name the stack you want deleted"
[[ "$DELETE_BACKUPS" == 0 || "$PURGE" == 1 ]] || air_die "--delete-backups needs --purge: it deletes the backup vault and its recovery points"
[[ -n "$STACK" ]] || STACK="$(air_stack "${AIR_PREFIX:-air}")"
PREFIX="$(air_prefix_of "$STACK")"

# The same checks as deploy.sh: uv is needed to synthesise the app (infra/cdk.json), and without it
# the destroy used to fail at synth, after the typed confirmation (P2-058).
air_check_tools
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
REGION="$(air_region)"
export AWS_REGION="$REGION" CDK_DEFAULT_ACCOUNT="$ACCOUNT" CDK_DEFAULT_REGION="$REGION"
echo "Account $ACCOUNT, Region $REGION, stack $STACK"
STATUS="$(air_stack_status "$STACK")"
case "$STATUS" in
  NONE) air_die "there is no stack $STACK in account $ACCOUNT, Region $REGION (check AWS_REGION and --stack or AIR_PREFIX)" ;;
  *_IN_PROGRESS)
    air_die "stack $STACK is $STATUS: wait for it to finish (aws cloudformation describe-stacks --stack-name $STACK), then run this again" ;;
  UPDATE_ROLLBACK_FAILED)
    air_die "stack $STACK is UPDATE_ROLLBACK_FAILED: run 'aws cloudformation continue-update-rollback --stack-name $STACK', wait for UPDATE_ROLLBACK_COMPLETE, then run this again" ;;
esac

ARGS=(-c adminEmail=destroy@example.invalid -c "prefix=$PREFIX")
cd "$ROOT/infra"
# Synthesising the app needs the deployment bundles. A checkout that never ran deploy.sh gets empty
# stand-ins for the destroy only; nothing is uploaded when stacks are deleted.
PLACEHOLDERS=()
for d in lambda agents; do
  if [[ ! -d "$ROOT/build/$d" ]]; then
    mkdir -p "$ROOT/build/$d" && touch "$ROOT/build/$d/.destroy-placeholder" && PLACEHOLDERS+=("$ROOT/build/$d")
  fi
done
cleanup() { for p in ${PLACEHOLDERS[@]+"${PLACEHOLDERS[@]}"}; do rm -rf "$p"; done; }  # bash 3.2-safe
trap cleanup EXIT
if [[ "$REGION" != us-east-1 ]]; then
  # Synthesising the main stack needs an ACL ARN; destroy never reads it.
  ARGS+=(-c "webAclArn=arn:aws:wafv2:us-east-1:$ACCOUNT:global/webacl/destroy/0")
fi

# The stack's stateful resources, recorded while the stack still exists. Without --purge this is the
# inventory of what is kept, so it can be found and deleted later.
mkdir -p "$ROOT/build"
PLAN="$ROOT/build/purge-$STACK-$REGION.json"
# Outside us-east-1 the edge stack lives in us-east-1 and keeps its WAF log group (a fixed name the
# next deploy would collide with) and its KMS key, so it is recorded with the main stack (P2-004).
PLAN_ALSO=()
if [[ "$REGION" != us-east-1 ]]; then PLAN_ALSO+=("$STACK-$REGION-Edge@us-east-1"); fi
echo "Stateful resources of $STACK (deleted with the stack, or kept when it retains data):"
python3 "$ROOT/deployment/purge.py" plan "$STACK" "$PLAN" ${PLAN_ALSO[@]+"${PLAN_ALSO[@]}"}
if [[ "$PURGE" == 1 ]]; then
  echo "With --purge, every resource above is deleted with all its data (the list is saved in $PLAN)."
  if [[ "$DELETE_BACKUPS" == 1 ]]; then
    echo "With --delete-backups, a backup vault above is deleted with every recovery point in it."
  else
    echo "A backup vault above is kept, and so is every KMS key its recovery points need (they are"
    echo "encrypted with the data key). Add --delete-backups to delete the backups and the key too."
  fi
else
  echo "Resources the stack keeps stay after the destroy. The list is saved in $PLAN;"
  echo "delete them later with: python3 deployment/purge.py apply $PLAN"
fi
if [[ "$YES" != 1 ]]; then
  [[ -t 0 ]] || air_die "not a terminal: pass --stack $STACK --yes to delete without the question"
  read -r -p "Delete $STACK in $REGION? Type the stack name to confirm: " answer
  [[ "$answer" == "$STACK" ]] || { echo "Not confirmed; nothing was deleted." >&2; exit 1; }
fi

air_cdk
# The main stack depends on the edge stack, so --all removes the distribution before its web ACL.
# A failed delete used to end here under set -e with no word on what was left: --purge had deleted
# nothing, and a web-search gateway that AgentCore could not delete (r3 P3-001, P3-014) needs its
# own steps before any retry can succeed.
if ! "$CDK" destroy --all --force "${ARGS[@]}"; then
  echo "error: the stack was not deleted ($(air_stack_status "$STACK")). Nothing was purged." >&2
  echo "Its failed resources: aws cloudformation describe-stack-events --stack-name $STACK \\" >&2
  echo "  --query \"StackEvents[?ResourceStatus=='DELETE_FAILED'].[LogicalResourceId,ResourceStatusReason]\" --output text" >&2
  echo "A DELETE_FAILED web-search gateway (AgentsWebSearchGateway...) needs the implementation guide's" >&2
  echo "\"Clearing an orphaned web-search gateway\" first. Then run this script again; the list of what" >&2
  echo "the stack keeps is saved in $PLAN (python3 deployment/purge.py apply $PLAN, once the stack is gone)." >&2
  exit 1
fi
if [[ "$PURGE" == 1 ]]; then
  APPLY=("$PLAN")
  if [[ "$DELETE_BACKUPS" == 1 ]]; then APPLY+=(--delete-backups); fi
  echo "Deleting what the stack kept:"
  python3 "$ROOT/deployment/purge.py" apply "${APPLY[@]}" || {
    echo "Some resources were not deleted. Retry with: python3 deployment/purge.py apply ${APPLY[*]}" >&2
    exit 1
  }
fi
