#!/usr/bin/env bash
###############################################################################
# AnyCompanyPay — full teardown / cleanup
#
# Removes EVERYTHING this sample provisioned, and ONLY this sample's resources.
# It targets resources by their exact stack names and this project's name
# prefixes (the `PREFIX` variable below); it never uses account-wide wildcards,
# so unrelated resources in the account are left untouched.
#
# What it removes, in dependency-safe order:
#   1. AgentCore Gateway + target + interceptor + role — now part of the CDK
#      stack (AWS::BedrockAgentCore::*), so removed by `cdk destroy` in step 6.
#      This step only DETECTS a legacy, CLI-created gateway (pre-migration).
#   2. Q in Connect: AI agents, AI prompts, assistant/domain + its
#      Connect integration association                                  [CLI]
#   3. Lex bot  anycompany-pay-agentic-selfservice                              [CLI]
#   4. Claimed phone number (AnyCompanyPay voice demo)                         [CLI]
#   5. Connect integration associations that block stack deletion
#      (CASES_DOMAIN, WISDOM_ASSISTANT, APPLICATION)                     [CLI]
#   5b. AppIntegrations MCP-server application — deleted HERE, before the Connect
#      instance is destroyed (its association can only be cleared while the
#      instance still exists; there is no disassociate API)              [CLI]
#   6. The CFN/CDK stacks (Lex, QicDomain, Connect, ConnectAiAgent,
#      ZeroEtl, App, Aurora) via CloudFormation delete-stack, with retry +
#      GuardDuty-VPC-blocker clearing                                     [CFN]
#   7. Orphans left behind: CloudWatch log groups, the SSM runtime config
#      param, the anycompany-pay-web ECR repo (7b re-checks the AppIntegrations app) [CLI]
#   8. LISTS (does not auto-delete) ambiguous leftovers for manual review:
#      KMS aliases and S3 buckets matching the project prefix.
#
# SAFETY:
#   * DRY-RUN by default. Nothing is deleted unless you pass --apply.
#   * With --apply you must type the AWS account id to confirm.
#   * Every destructive call is guarded by an existence check and is idempotent.
#
# Usage:
#   ./cleanup.sh                 # dry-run: print everything it WOULD delete
#   ./cleanup.sh --apply         # actually delete (prompts for confirmation)
#   ./cleanup.sh --apply --yes   # actually delete, skip the interactive prompt
#
# Env overrides:
#   REGION            (default: AWS session region)  main solution region
#   ENV_NAME          (default: empty)           OPTIONAL stack/name suffix; empty = clean common names
#   PREFIX            (default "anycompany-pay")          primary resource name prefix
#   DELETE_ECR        (default 1)                also delete the anycompany-pay-web ECR repo
#   USE_CDK           (default 0)                0 = CloudFormation delete-stack (reliable,
#                                                context-free); 1 = `cdk destroy` (needs the
#                                                same -c context the deploys used)
###############################################################################
set -uo pipefail

# Region from the environment (never hardcoded).
REGION="${REGION:-${AWS_REGION:-${CDK_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || echo us-west-2)}}}"
if [ -z "$REGION" ]; then
  echo "ERROR: no region. Set AWS_REGION (or REGION), e.g. AWS_REGION=us-west-2 $0" >&2; exit 1
fi
# Optional env discriminator. Empty by default → clean common names (no suffix).
ENV_NAME="${ENV_NAME:-}"
PREFIX="${PREFIX:-anycompany-pay}"
GATEWAY_NAME="${GATEWAY_NAME:-anycompany-pay-transaction-tools}"
GATEWAY_ROLE="${GATEWAY_ROLE:-anycompany-pay-agentcore-gateway-role}"
LEX_BOT_NAME="${LEX_BOT_NAME:-anycompany-pay-agentic-selfservice${ENV_NAME:+-${ENV_NAME}}}"
PHONE_DESC="${PHONE_DESC:-AnyCompanyPay voice demo}"
ECR_REPO="${ECR_REPO:-anycompany-pay-web}"
SSM_PARAM="${SSM_PARAM:-/anycompany-pay${ENV_NAME:+/${ENV_NAME}}/runtime-config}"
DELETE_ECR="${DELETE_ECR:-1}"
# Teardown via CloudFormation delete-stack by default (NOT cdk destroy). Reason:
# these stacks are instantiated CONDITIONALLY on -c context in their CDK apps
# (e.g. connect-ai-agent only creates AnyCompanyPayLexStack when -c assistantArn is
# present), so `cdk destroy <name>` with no context finds "no stacks" and cannot
# tear them down. `delete-stack` works by deployed stack name with no synth/context
# and still runs every custom-resource Delete handler. Set USE_CDK=1 only if you
# pass the same -c context the deploys used.
USE_CDK="${USE_CDK:-0}"

# The CDK stacks, in the order they must be DESTROYED (dependents first).
# Module dir | stack name.
# Ordering rules (dependents first):
#  - AnyCompanyPayConnectScreenShareStack (opt-in screen sharing: queue, flow,
#    security profile, API) -> first.
#  - AnyCompanyPayConnectRoutingStack (opt-in routing module: flows, queues, hours,
#    routing profiles, Cases fields on the instance/domain) -> first.
#  - AnyCompanyPayLexStack + AnyCompanyPayQicDomainStack (connect-ai-agent app) attach to the
#    Connect instance -> remove before the Connect stack.
#  - AnyCompanyPayConnectStack (the Connect INSTANCE) is deleted BEFORE AnyCompanyPayConnectAiAgentStack:
#    the AI-agent security profile lives on the instance and is ASSOCIATED with the AI
#    agent, so deleting it while the instance/agent still exist fails ("in use").
#    Deleting the instance first cascades the profile away, so the ai-agent stack's
#    security-profile delete then succeeds (instance-not-found = already deleted).
#  - AnyCompanyPayConnectAiAgentStack (tool Lambda) must go before Aurora (its ENIs live in
#    the Aurora VPC and block VPC/subnet deletion until they drain).
STACKS=(
  "connect-screenshare|AnyCompanyPayConnectScreenShareStack${ENV_NAME:+-${ENV_NAME}}"
  "connect-routing|AnyCompanyPayConnectRoutingStack${ENV_NAME:+-${ENV_NAME}}"
  "connect-ai-agent|AnyCompanyPayLexStack"
  "connect-ai-agent|AnyCompanyPayQicDomainStack"
  "infra|AnyCompanyPayConnectStack${ENV_NAME:+-${ENV_NAME}}"
  "connect-ai-agent|AnyCompanyPayConnectAiAgentStack"
  "opensearch-zeroetl|AnyCompanyPayZeroEtlStack"
  "infra|AnyCompanyPayAppStack${ENV_NAME:+-${ENV_NAME}}"
  "database|AnyCompanyPayAuroraStack"
)

# ----------------------------------------------------------------------------
APPLY=0
ASSUME_YES=0
for a in "$@"; do
  case "$a" in
    --apply) APPLY=1 ;;
    --yes|-y) ASSUME_YES=1 ;;
    -h|--help) sed -n '2,45p' "$0"; exit 0 ;;
    *) echo "unknown arg: $a (see --help)" >&2; exit 2 ;;
  esac
done

c_red=$'\e[31m'; c_grn=$'\e[32m'; c_ylw=$'\e[33m'; c_dim=$'\e[2m'; c_rst=$'\e[0m'
log()  { printf '%s\n' "$*"; }
hdr()  { printf '\n%s==> %s%s\n' "$c_grn" "$*" "$c_rst"; }
warn() { printf '%s! %s%s\n' "$c_ylw" "$*" "$c_rst"; }
err()  { printf '%sERROR: %s%s\n' "$c_red" "$*" "$c_rst" >&2; }

# run <human description> -- <command...>
run() {
  local desc="$1"; shift
  [ "$1" = "--" ] && shift
  if [ "$APPLY" = "1" ]; then
    printf '%s· %s%s\n' "$c_dim" "$desc" "$c_rst"
    "$@"
  else
    printf '%s[dry-run] would: %s%s\n' "$c_dim" "$desc" "$c_rst"
    printf '           %s%s%s\n' "$c_dim" "$*" "$c_rst"
  fi
}

command -v aws >/dev/null 2>&1 || { err "aws CLI not found on PATH"; exit 1; }
ACCOUNT="$(aws sts get-caller-identity --query Account --output text 2>/dev/null)" || {
  err "could not resolve AWS account (are credentials configured?)"; exit 1; }

hdr "AnyCompanyPay teardown"
log "Account         : ${ACCOUNT}"
log "Region          : ${REGION}"
log "Env / prefix    : ${ENV_NAME} / ${PREFIX}"
log "Mode            : $([ "$APPLY" = 1 ] && echo "${c_red}APPLY (deletes resources)${c_rst}" || echo 'DRY-RUN (no changes)')"

if [ "$APPLY" = "1" ] && [ "$ASSUME_YES" != "1" ]; then
  printf '\nThis will PERMANENTLY DELETE the AnyCompanyPay solution in account %s.\n' "$ACCOUNT"
  printf 'Type the account id to confirm: '
  read -r reply
  [ "$reply" = "$ACCOUNT" ] || { err "confirmation did not match; aborting."; exit 1; }
fi

# ============================================================================
# 1) AgentCore Gateway — now CDK-managed (removed by cdk destroy in step 6).
#    Only DETECT a legacy, CLI-created gateway/role (from before the migration)
#    and print manual removal commands. We do NOT auto-delete here, because
#    deleting a stack-managed gateway out-of-band would conflict with step 6.
# ============================================================================
hdr "1. Bedrock AgentCore Gateway (CDK-managed — detect legacy only)"
log "The gateway/target/interceptor/role are part of AnyCompanyPayConnectAiAgentStack"
log "and are removed by 'cdk destroy' in step 6."
GID="$(aws bedrock-agentcore-control list-gateways --region "$REGION" \
        --query "items[?starts_with(name, '${GATEWAY_NAME}')].gatewayId | [0]" --output text 2>/dev/null || true)"
if [ -n "${GID:-}" ] && [ "$GID" != "None" ]; then
  warn "Found gateway ${GATEWAY_NAME}* (${GID}). If it LINGERS after step 6, it is a"
  warn "legacy CLI-created gateway; remove it manually:"
  log  "    for t in \$(aws bedrock-agentcore-control list-gateway-targets --region ${REGION} --gateway-identifier ${GID} --query 'items[].targetId' --output text); do \\"
  log  "      aws bedrock-agentcore-control delete-gateway-target --region ${REGION} --gateway-identifier ${GID} --target-id \$t; done"
  log  "    aws bedrock-agentcore-control delete-gateway --region ${REGION} --gateway-identifier ${GID}"
  if aws iam get-role --role-name "$GATEWAY_ROLE" >/dev/null 2>&1; then
    log "    # legacy gateway role (CDK now creates its own, auto-named):"
    log "    aws iam delete-role-policy --role-name ${GATEWAY_ROLE} --policy-name invoke-lambdas; aws iam delete-role --role-name ${GATEWAY_ROLE}"
  fi
else
  log "No standalone gateway found (expected — the CDK stack owns it)."
fi

# ============================================================================
# 2) Q in Connect (AI agents, prompts, assistant) + its Connect association
#    Discover the Connect instance and the WISDOM_ASSISTANT association first.
# ============================================================================
hdr "2. Q in Connect (AI agents / prompts / assistant)"
INSTANCE_ID="$(aws connect list-instances --region "$REGION" \
  --query "InstanceSummaryList[?starts_with(InstanceAlias, '${PREFIX}')].Id | [0]" --output text 2>/dev/null || true)"
INSTANCE_ARN="$(aws connect list-instances --region "$REGION" \
  --query "InstanceSummaryList[?starts_with(InstanceAlias, '${PREFIX}')].Arn | [0]" --output text 2>/dev/null || true)"
INSTANCE_ALIAS="$(aws connect list-instances --region "$REGION" \
  --query "InstanceSummaryList[?starts_with(InstanceAlias, '${PREFIX}')].InstanceAlias | [0]" --output text 2>/dev/null || true)"
if [ -n "${INSTANCE_ID:-}" ] && [ "$INSTANCE_ID" != "None" ]; then
  log "Connect instance: ${INSTANCE_ALIAS} (${INSTANCE_ID})"
else
  warn "no Connect instance with alias ${PREFIX}* found (Q in Connect / associations may already be gone)"
fi

ASSISTANT_ARN=""
if [ -n "${INSTANCE_ID:-}" ] && [ "$INSTANCE_ID" != "None" ]; then
  ASSISTANT_ARN="$(aws connect list-integration-associations --region "$REGION" --instance-id "$INSTANCE_ID" \
    --query "IntegrationAssociationSummaryList[?IntegrationType=='WISDOM_ASSISTANT'].IntegrationArn | [0]" --output text 2>/dev/null || true)"
fi
if [ -n "${ASSISTANT_ARN:-}" ] && [ "$ASSISTANT_ARN" != "None" ]; then
  ASSISTANT_ID="${ASSISTANT_ARN##*/}"
  log "Q in Connect assistant (domain): $ASSISTANT_ID"
  # Detach the default orchestrator bindings are removed implicitly when the assistant is deleted.
  for AID in $(aws qconnect list-ai-agents --assistant-id "$ASSISTANT_ID" --region "$REGION" \
                 --query 'aiAgentSummaries[].aiAgentId' --output text 2>/dev/null); do
    run "delete AI agent $AID" -- aws qconnect delete-ai-agent --assistant-id "$ASSISTANT_ID" --region "$REGION" --ai-agent-id "$AID"
  done
  for PID in $(aws qconnect list-ai-prompts --assistant-id "$ASSISTANT_ID" --region "$REGION" \
                 --query 'aiPromptSummaries[].aiPromptId' --output text 2>/dev/null); do
    run "delete AI prompt $PID" -- aws qconnect delete-ai-prompt --assistant-id "$ASSISTANT_ID" --region "$REGION" --ai-prompt-id "$PID"
  done
  run "delete Q in Connect assistant $ASSISTANT_ID" -- aws qconnect delete-assistant --assistant-id "$ASSISTANT_ID" --region "$REGION"
else
  warn "no WISDOM_ASSISTANT association found (skipping Q in Connect)"
fi

# ============================================================================
# 3) Lex bot — now CFN-managed (AnyCompanyPayLexStack, removed by delete-stack in step 6).
#    Only DETECT a legacy, CLI-created bot and print a manual removal command; do
#    NOT delete it here (deleting a stack-managed bot out-of-band is unnecessary).
# ============================================================================
hdr "3. Amazon Lex bot (CFN-managed — detect legacy only)"
BOT_ID="$(aws lexv2-models list-bots --region "$REGION" \
  --query "botSummaries[?botName=='${LEX_BOT_NAME}'].botId | [0]" --output text 2>/dev/null || true)"
if [ -n "${BOT_ID:-}" ] && [ "$BOT_ID" != "None" ]; then
  log "Lex bot ${LEX_BOT_NAME} (${BOT_ID}) is owned by AnyCompanyPayLexStack and is removed by step 6."
  warn "If it LINGERS after step 6, it is a legacy CLI-created bot; remove it manually:"
  log  "    aws lexv2-models delete-bot --region ${REGION} --bot-id ${BOT_ID} --skip-resource-in-use-check"
else
  log "No standalone Lex bot ${LEX_BOT_NAME} found (expected — the CDK stack owns it)."
fi

# ============================================================================
# 4) Claimed phone number (billable — release it)
# ============================================================================
hdr "4. Claimed phone number ('${PHONE_DESC}')"
if [ -n "${INSTANCE_ARN:-}" ] && [ "$INSTANCE_ARN" != "None" ]; then
  # list phone numbers on the instance, then match by description via describe.
  for PNID in $(aws connect list-phone-numbers-v2 --region "$REGION" \
                  --target-arn "$INSTANCE_ARN" --query 'ListPhoneNumbersSummaryList[].PhoneNumberId' --output text 2>/dev/null); do
    D="$(aws connect describe-phone-number --region "$REGION" --phone-number-id "$PNID" \
          --query 'ClaimedPhoneNumberSummary.PhoneNumberDescription' --output text 2>/dev/null || true)"
    if [ "$D" = "$PHONE_DESC" ]; then
      run "release phone number $PNID" -- aws connect release-phone-number --region "$REGION" --phone-number-id "$PNID"
    fi
  done
else
  warn "no instance ARN resolved; skipping phone-number release"
fi

# ============================================================================
# 5) Connect integration associations that block stack deletion
#    (Cases + Wisdom were enabled/associated outside CloudFormation.)
# ============================================================================
hdr "5. Connect integration associations (unblock stack deletion)"
if [ -n "${INSTANCE_ID:-}" ] && [ "$INSTANCE_ID" != "None" ]; then
  for ITYPE in CASES_DOMAIN WISDOM_ASSISTANT APPLICATION; do
    for IAID in $(aws connect list-integration-associations --region "$REGION" --instance-id "$INSTANCE_ID" \
                    --query "IntegrationAssociationSummaryList[?IntegrationType=='${ITYPE}'].IntegrationAssociationId" --output text 2>/dev/null); do
      run "delete ${ITYPE} integration association $IAID" -- aws connect delete-integration-association \
        --region "$REGION" --instance-id "$INSTANCE_ID" --integration-association-id "$IAID"
    done
  done
else
  warn "no instance; skipping association cleanup"
fi

# ============================================================================
# 5b) AppIntegrations application (MCP-server registration) — MUST run BEFORE the
#     Connect instance is destroyed (step 6).
#
#     WHY ORDER MATTERS: the app carries an ApplicationAssociation with
#     ClientId=connect.amazonaws.com. AppIntegrations has NO delete-application-
#     association / disassociate API (only list-application-associations), so that
#     association can ONLY be cleared by the Connect client — i.e. by deleting the
#     Connect APPLICATION integration association (step 5) WHILE THE INSTANCE STILL
#     EXISTS. If the instance is destroyed first, the association dangles forever
#     and 'delete-application' fails with "An Application with ApplicationAssociations
#     cannot be deleted" — an unrecoverable orphan. Step 5 removed the Connect-side
#     association just above; now delete the app while we still can.
# ============================================================================
cleanup_appintegrations_apps() {
  local arns aarn appid assoc
  arns="$(aws appintegrations list-applications --region "$REGION" \
    --query "Applications[?starts_with(Namespace, '${GATEWAY_NAME}') || starts_with(Name, '${PREFIX}')].Arn" \
    --output text 2>/dev/null || true)"
  if [ -z "${arns:-}" ] || [ "$arns" = "None" ]; then
    log "No AppIntegrations application matching '${GATEWAY_NAME}*'/'${PREFIX}*' (skipping)."
    return 0
  fi
  for aarn in $arns; do
    appid="${aarn##*/}"
    if [ "$APPLY" != "1" ]; then
      run "delete AppIntegrations application $aarn" -- aws appintegrations delete-application --region "$REGION" --arn "$aarn"
      continue
    fi
    if aws appintegrations delete-application --region "$REGION" --arn "$aarn" >/dev/null 2>&1; then
      log "· deleted AppIntegrations application $aarn"
    else
      assoc="$(aws appintegrations list-application-associations --region "$REGION" \
        --application-id "$appid" --query 'ApplicationAssociations[].ClientId' --output text 2>/dev/null || true)"
      warn "AppIntegrations app ${aarn} not deletable yet (associations: ${assoc:-unknown})."
      if [ -n "${INSTANCE_ID:-}" ] && [ "$INSTANCE_ID" != "None" ]; then
        warn "Retrying after re-checking Connect APPLICATION associations on instance ${INSTANCE_ID}…"
        for iaid in $(aws connect list-integration-associations --region "$REGION" --instance-id "$INSTANCE_ID" \
                        --query "IntegrationAssociationSummaryList[?IntegrationType=='APPLICATION'].IntegrationAssociationId" --output text 2>/dev/null); do
          aws connect delete-integration-association --region "$REGION" --instance-id "$INSTANCE_ID" --integration-association-id "$iaid" >/dev/null 2>&1 || true
        done
        sleep 10
        aws appintegrations delete-application --region "$REGION" --arn "$aarn" >/dev/null 2>&1 \
          && log "· deleted AppIntegrations application $aarn (after re-clearing association)" \
          || warn "still blocked — the instance may already be gone; this app will be an orphan (see note)."
      else
        warn "Connect instance already deleted — the association can no longer be cleared (no disassociate API)."
        warn "This AppIntegrations app is now a stuck, zero-cost orphan. Deleting the app BEFORE the"
        warn "instance (this step's correct order) prevents it. Left for manual review."
      fi
    fi
  done
}
hdr "5b. AppIntegrations application (delete BEFORE the Connect instance)"
cleanup_appintegrations_apps

# ============================================================================
# 6) CDK stacks (dependents first)
# ============================================================================
hdr "6. CDK stacks"
stack_exists() {
  aws cloudformation describe-stacks --region "$REGION" --stack-name "$1" >/dev/null 2>&1
}
stack_status() {
  local st; st="$(aws cloudformation describe-stacks --region "$REGION" --stack-name "$1" \
    --query 'Stacks[0].StackStatus' --output text 2>/dev/null || true)"
  [ -z "$st" ] && st="DELETED"; printf '%s' "$st"
}
# VPC ids that belong to a stack (so we only touch THIS project's VPCs).
stack_vpc_ids() {
  aws cloudformation describe-stack-resources --region "$REGION" --stack-name "$1" \
    --query "StackResources[?ResourceType=='AWS::EC2::VPC'].PhysicalResourceId" --output text 2>/dev/null || true
}
# The #1 cause of App-stack DELETE_FAILED: GuardDuty Runtime Monitoring
# auto-provisions a 'guardduty-data' interface endpoint + a
# 'GuardDutyManagedSecurityGroup-*' in the VPC. They are not in the stack, so
# their ENIs/SG block subnet+VPC deletion. Remove them (scoped to the stack's own
# VPC) so the retry can finish. GuardDuty may re-provision, hence the retry loop.
clear_vpc_guardduty() {
  local vpc="$1" did=1 e sg
  for e in $(aws ec2 describe-vpc-endpoints --region "$REGION" --filters Name=vpc-id,Values="$vpc" \
               --query "VpcEndpoints[?contains(ServiceName,'guardduty')].VpcEndpointId" --output text 2>/dev/null); do
    run "delete GuardDuty VPC endpoint $e (in $vpc)" -- aws ec2 delete-vpc-endpoints --region "$REGION" --vpc-endpoint-ids "$e" && did=0
  done
  for sg in $(aws ec2 describe-security-groups --region "$REGION" --filters Name=vpc-id,Values="$vpc" \
               --query "SecurityGroups[?starts_with(GroupName,'GuardDutyManagedSecurityGroup')].GroupId" --output text 2>/dev/null); do
    # SG deletion can fail while its ENIs still detach; give it a moment.
    run "delete GuardDuty managed SG $sg (in $vpc)" -- bash -c "for i in 1 2 3 4 5; do aws ec2 delete-security-group --region '$REGION' --group-id '$sg' 2>/dev/null && break; sleep 15; done" && did=0
  done
  return $did
}

# Robust CloudFormation teardown: delete-stack, wait, and on DELETE_FAILED clear
# the known external blocker (GuardDuty in the stack's VPC) and retry, up to 3x.
# Also recovers transient ordering failures (e.g. a security profile briefly
# "in use") that succeed on a plain retry.
delete_stack_robust() {
  local name="$1" attempt=1 max=3 st
  while [ "$attempt" -le "$max" ]; do
    aws cloudformation delete-stack --region "$REGION" --stack-name "$name" 2>/dev/null || true
    aws cloudformation wait stack-delete-complete --region "$REGION" --stack-name "$name" 2>/dev/null || true
    st="$(stack_status "$name")"
    [ "$st" = "DELETED" ] && { log "· ${name} deleted (attempt ${attempt})"; return 0; }
    warn "${name} status after attempt ${attempt}: ${st}"
    # Clear GuardDuty blockers in this stack's VPC(s), then retry.
    local vpc cleared=1
    for vpc in $(stack_vpc_ids "$name"); do
      clear_vpc_guardduty "$vpc" && cleared=0
    done
    [ "$cleared" = 0 ] && sleep 20
    attempt=$((attempt+1))
  done
  err "${name} still not deleted after ${max} attempts. Latest DELETE_FAILED reasons:"
  aws cloudformation describe-stack-events --region "$REGION" --stack-name "$name" \
    --query "StackEvents[?ResourceStatus=='DELETE_FAILED']|[0:3].{r:LogicalResourceId,reason:ResourceStatusReason}" \
    --output text 2>/dev/null | head -6
  return 1
}

for entry in "${STACKS[@]}"; do
  DIR="${entry%%|*}"; NAME="${entry##*|}"
  if ! stack_exists "$NAME"; then
    warn "stack ${NAME} not found (skipping)"
    continue
  fi
  if [ "$USE_CDK" = "1" ] && [ -f "${DIR}/cdk.json" ]; then
    run "cdk destroy ${NAME} (in ${DIR}/)" -- bash -c "cd '${DIR}' && npx cdk destroy '${NAME}' --force"
  elif [ "$APPLY" = "1" ]; then
    # Robust delete with GuardDuty-blocker handling + retry.
    delete_stack_robust "$NAME"
  else
    run "delete-stack ${NAME} (robust: retries + clears GuardDuty VPC blockers)" -- aws cloudformation delete-stack --region "$REGION" --stack-name "$NAME"
    run "wait for ${NAME} deletion" -- aws cloudformation wait stack-delete-complete --region "$REGION" --stack-name "$NAME"
  fi
done

# ============================================================================
# 7) Orphans CDK leaves behind (scoped to this project's names)
# ============================================================================
hdr "7. Orphan cleanup (log groups, SSM param, ECR repo)"

# CloudWatch log groups created outside the stacks (Lambda default groups,
# Connect flow logs, AgentCore gateway vended logs, OSIS pipeline logs).
delete_log_groups_by_prefix() {
  local pfx="$1"; local rgn="${2:-$REGION}"
  for lg in $(aws logs describe-log-groups --region "$rgn" --log-group-name-prefix "$pfx" \
                --query 'logGroups[].logGroupName' --output text 2>/dev/null); do
    # Only touch groups that clearly belong to this project.
    case "$lg" in
      *AnyCompanyPay*|*anycompany-pay*) run "delete log group $lg (${rgn})" -- aws logs delete-log-group --region "$rgn" --log-group-name "$lg" ;;
    esac
  done
}
delete_log_groups_by_prefix "/aws/lambda/AnyCompanyPay"
[ -n "${INSTANCE_ALIAS:-}" ] && [ "$INSTANCE_ALIAS" != "None" ] && \
  delete_log_groups_by_prefix "/aws/connect/${INSTANCE_ALIAS}"
delete_log_groups_by_prefix "/aws/vendedlogs/bedrock-agentcore/gateway"
delete_log_groups_by_prefix "/aws/vendedlogs/OpenSearchIngestion"

# SSM runtime-config parameter.
if aws ssm get-parameter --region "$REGION" --name "$SSM_PARAM" >/dev/null 2>&1; then
  run "delete SSM parameter $SSM_PARAM" -- aws ssm delete-parameter --region "$REGION" --name "$SSM_PARAM"
else
  warn "SSM parameter ${SSM_PARAM} not found (skipping)"
fi

# ECR repo for the SPA image (created by build-and-push.sh, not CDK).
if [ "$DELETE_ECR" = "1" ]; then
  if aws ecr describe-repositories --region "$REGION" --repository-names "$ECR_REPO" >/dev/null 2>&1; then
    run "delete ECR repo $ECR_REPO (with images)" -- aws ecr delete-repository --region "$REGION" --repository-name "$ECR_REPO" --force
  else
    warn "ECR repo ${ECR_REPO} not found (skipping)"
  fi
fi

# AppIntegrations application (the MCP-server registration for the AgentCore
# Gateway). Created out-of-band (console / API); its Connect integration
# association is removed in step 5, but the underlying application resource
# lingers unless deleted here. Match by name prefix or gateway namespace.
hdr "7b. AppIntegrations application (fallback — primary deletion is step 5b)"
# Primary deletion happens in step 5b (before the instance is destroyed). This is
# a fallback for the case where an app still lingers (idempotent).
cleanup_appintegrations_apps

# ============================================================================
# 8) Manual-review leftovers (LISTED, never auto-deleted)
# ============================================================================
hdr "8. Review these manually (not auto-deleted)"

log "KMS keys/aliases matching the project prefix (delete only if unused):"
for al in $(aws kms list-aliases --region "$REGION" --query "Aliases[?starts_with(AliasName, 'alias/${PREFIX}')].AliasName" --output text 2>/dev/null); do
  log "    ${al}   ->  aws kms schedule-key-deletion --region ${REGION} --key-id <keyId> --pending-window-in-days 7"
done

log "S3 buckets matching the project prefix (empty + delete only if this project's):"
for b in $(aws s3api list-buckets --query "Buckets[?starts_with(Name, '${PREFIX}')].Name" --output text 2>/dev/null); do
  log "    ${b}   ->  aws s3 rm s3://${b} --recursive && aws s3api delete-bucket --bucket ${b}"
done

log ""
log "GuardDuty-managed VPC endpoints / security groups: if GuardDuty Runtime Monitoring is"
log "enabled, it auto-provisions a 'com.amazonaws.*.guardduty-data' interface endpoint and a"
log "'GuardDutyManagedSecurityGroup-*' in the app VPC. These are NOT AnyCompanyPay resources but can"
log "BLOCK the App stack's subnet/VPC deletion (leftover ENIs). If AnyCompanyPayAppStack${ENV_NAME:+-${ENV_NAME}}"
log "DELETE_FAILED on a subnet/VPC 'has dependencies', delete them and retry the stack delete:"
log "    VPC=<app-vpc-id>"
log "    aws ec2 describe-vpc-endpoints --region ${REGION} --filters Name=vpc-id,Values=\$VPC --query 'VpcEndpoints[].VpcEndpointId' --output text | xargs -r aws ec2 delete-vpc-endpoints --region ${REGION} --vpc-endpoint-ids"
log "    aws ec2 describe-security-groups --region ${REGION} --filters Name=vpc-id,Values=\$VPC --query \"SecurityGroups[?starts_with(GroupName,'GuardDutyManagedSecurityGroup')].GroupId\" --output text | xargs -r -n1 aws ec2 delete-security-group --region ${REGION} --group-id"
log "    aws cloudformation delete-stack --region ${REGION} --stack-name AnyCompanyPayAppStack${ENV_NAME:+-${ENV_NAME}}   # retry"
log "(GuardDuty may re-provision while the VPC exists; delete then retry the stack promptly.)"

hdr "Done"
[ "$APPLY" = 1 ] || log "${c_ylw}Dry-run only — nothing was deleted. Re-run with --apply to execute.${c_rst}"
