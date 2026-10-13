#!/usr/bin/env bash
#
# provision-ai-agent.sh — create the AnyCompanyPay merchant-support Orchestration AI agent
# in Amazon Q in Connect (AI Agent Designer), wired to the query_transactions MCP tool.
#
# What it does (idempotent):
#   1. Resolves the Q in Connect domain (assistant) associated with the Connect instance.
#   2. Creates (or reuses) a CUSTOM orchestration AI prompt from
#      ai-agent/orchestration-prompt.yaml  (the agent's instructions).
#   3. Creates (or reuses) an ORCHESTRATION AI agent that references that prompt and the
#      query_transactions MCP tool (ai-agent/tool-query-transactions.json).
#   4. With --set-default, points the domain's ORCHESTRATION slot at the new agent.
#
# SAFETY: dry-run by default. It prints the exact API inputs and does NOT mutate
# anything unless you pass --apply. Creating an AI prompt/agent is additive and
# reversible (qconnect delete-ai-agent / delete-ai-prompt).
#
# Prerequisites:
#   - The MCP server (AgentCore Gateway) is registered AND its tool namespace has been
#     DISCOVERED by Connect (the query_transactions tool is visible in AI Agent Designer).
#     If discovery hasn't landed, create-ai-agent may reject the MCP tool — fix discovery
#     first (see README "namespace never appears").
#
# Usage:
#   ./provision-ai-agent.sh                 # dry-run (prints planned actions)
#   ./provision-ai-agent.sh --apply         # create prompt + agent
#   ./provision-ai-agent.sh --apply --set-default   # + make it the domain's orchestration agent
#
# Override via env:
#   REGION, CONNECT_INSTANCE_ID, ASSISTANT_ID, GATEWAY_ID,
#   AGENT_NAME, PROMPT_NAME, MODEL_ID, API_FORMAT
#
set -euo pipefail
cd "$(dirname "$0")"

# Region from the environment (never hardcoded).
REGION="${REGION:-${AWS_REGION:-${CDK_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || echo us-west-2)}}}"
if [[ -z "$REGION" ]]; then
  echo "ERROR: no region. Set AWS_REGION (or REGION), e.g. AWS_REGION=us-west-2 $0" >&2; exit 1
fi
# Optional env discriminator. Empty by default → clean common names (no suffix).
ENV_NAME="${ENV_NAME:-}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"

# Connect instance: discover by its deterministic alias (anycompany-pay-<account>[-<env>])
# unless CONNECT_INSTANCE_ID is provided. Nothing instance-specific is hardcoded.
CONNECT_ALIAS="${CONNECT_ALIAS:-anycompany-pay-${ACCOUNT_ID}${ENV_NAME:+-${ENV_NAME}}}"
if [[ -z "${CONNECT_INSTANCE_ID:-}" ]]; then
  CONNECT_INSTANCE_ID="$(aws connect list-instances --region "$REGION" \
    --query "InstanceSummaryList[?InstanceAlias=='${CONNECT_ALIAS}'].Id | [0]" --output text 2>/dev/null)"
fi
if [[ -z "${CONNECT_INSTANCE_ID:-}" || "$CONNECT_INSTANCE_ID" == "None" ]]; then
  echo "ERROR: no Connect instance for alias '$CONNECT_ALIAS' in $REGION. Set CONNECT_INSTANCE_ID=... or deploy AnyCompanyPayConnectStack-${ENV_NAME} first." >&2
  exit 1
fi

# Gateway id: discover from the AI-agent stack output (GatewayId) unless provided.
# REQUIRED: the discovered MCP tool id embeds the gateway id
# (gateway_<gatewayId>__query-transactions___query_transactions), so the tool
# config is generated from it — a wrong/blank id silently breaks tool discovery.
if [[ -z "${GATEWAY_ID:-}" ]]; then
  GATEWAY_ID="$(aws cloudformation describe-stacks --region "$REGION" --stack-name AnyCompanyPayConnectAiAgentStack \
    --query "Stacks[0].Outputs[?OutputKey=='GatewayId'].OutputValue | [0]" --output text 2>/dev/null)"
  [[ "$GATEWAY_ID" == "None" ]] && GATEWAY_ID=""
fi
if [[ -z "$GATEWAY_ID" ]]; then
  echo "ERROR: could not resolve the AgentCore Gateway id. Set GATEWAY_ID=... or deploy AnyCompanyPayConnectAiAgentStack (its GatewayId output) in $REGION first." >&2
  exit 1
fi
AGENT_NAME="${AGENT_NAME:-anycompany-pay-merchant-assistant}"
PROMPT_NAME="${PROMPT_NAME:-anycompany-pay-merchant-orchestration}"
MODEL_ID="${MODEL_ID:-global.anthropic.claude-sonnet-4-5-20250929-v1:0}"
API_FORMAT="${API_FORMAT:-MESSAGES}"
PROMPT_FILE="ai-agent/orchestration-prompt.yaml"
TOOL_FILE="ai-agent/tool-query-transactions.json"
# RETURN_TO_CONTROL tools that let the self-service orchestrator hand control
# back to the contact flow (Complete = resolved, Escalate = route to a human).
# The working SEA agent carries all three; Tokyo was missing these two.
COMPLETE_TOOL_FILE="ai-agent/tool-complete.json"
ESCALATE_TOOL_FILE="ai-agent/tool-escalate.json"

APPLY=0
SET_DEFAULT=0
for a in "$@"; do
  case "$a" in
    --apply) APPLY=1 ;;
    --set-default) SET_DEFAULT=1 ;;
    *) echo "unknown arg: $a" >&2; exit 2 ;;
  esac
done

# ACCOUNT_ID + CONNECT_INSTANCE_ID were resolved in the header (region-agnostic).
CONNECT_INSTANCE_ARN="arn:aws:connect:${REGION}:${ACCOUNT_ID}:instance/${CONNECT_INSTANCE_ID}"

run() {
  # Echo the command; only execute it when --apply is set.
  echo "+ $*"
  if [[ "$APPLY" == "1" ]]; then "$@"; fi
}

echo "Region:            $REGION"
echo "Connect instance:  $CONNECT_INSTANCE_ID"
echo "Gateway:           $GATEWAY_ID"
echo "Mode:              $([[ $APPLY == 1 ]] && echo APPLY || echo DRY-RUN)"
echo

# ---- 1) Resolve the domain (assistant) associated with the instance -----------
if [[ -z "${ASSISTANT_ID:-}" ]]; then
  ASSISTANT_ARN="$(aws connect list-integration-associations \
    --instance-id "$CONNECT_INSTANCE_ID" --region "$REGION" \
    --query "IntegrationAssociationSummaryList[?IntegrationType=='WISDOM_ASSISTANT'].IntegrationArn | [0]" \
    --output text)"
  if [[ -z "$ASSISTANT_ARN" || "$ASSISTANT_ARN" == "None" ]]; then
    echo "ERROR: no WISDOM_ASSISTANT (Q in Connect domain) associated with instance $CONNECT_INSTANCE_ID." >&2
    echo "Create the domain first (Connect console -> AI Agents -> Add domain)." >&2
    exit 1
  fi
  ASSISTANT_ID="${ASSISTANT_ARN##*/}"
fi
echo "Assistant (domain): $ASSISTANT_ID"

# ---- 2) Create or reuse the custom orchestration AI prompt --------------------
PROMPT_ID="$(aws qconnect list-ai-prompts --assistant-id "$ASSISTANT_ID" --region "$REGION" \
  --query "aiPromptSummaries[?name=='${PROMPT_NAME}'].aiPromptId | [0]" --output text 2>/dev/null || true)"

PROMPT_VER=""
if [[ -n "$PROMPT_ID" && "$PROMPT_ID" != "None" ]]; then
  echo "Reusing AI prompt: $PROMPT_ID"
  # Push edits to ai-agent/orchestration-prompt.yaml: when the live text differs,
  # update the prompt and cut a new version (the agent pins prompt id:version).
  LIVE_TEXT="$(aws qconnect get-ai-prompt --assistant-id "$ASSISTANT_ID" --region "$REGION" --ai-prompt-id "$PROMPT_ID" \
    --query 'aiPrompt.templateConfiguration.textFullAIPromptEditTemplateConfiguration.text' --output text 2>/dev/null || true)"
  if [[ "$LIVE_TEXT" != "$(cat "$PROMPT_FILE")" ]]; then
    echo "==> Prompt text changed — updating '$PROMPT_NAME' and creating a new version"
    TEMPLATE_JSON="$(python3 -c "import json,sys; print(json.dumps({'textFullAIPromptEditTemplateConfiguration':{'text':open('$PROMPT_FILE').read()}}))")"
    if [[ "$APPLY" == "1" ]]; then
      aws qconnect update-ai-prompt --assistant-id "$ASSISTANT_ID" --region "$REGION" --ai-prompt-id "$PROMPT_ID" \
        --visibility-status PUBLISHED --template-configuration "$TEMPLATE_JSON" >/dev/null
      PROMPT_VER="$(aws qconnect create-ai-prompt-version --assistant-id "$ASSISTANT_ID" --region "$REGION" \
        --ai-prompt-id "$PROMPT_ID" --query 'versionNumber' --output text)"
      echo "Prompt version: $PROMPT_VER"
    else
      echo "+ aws qconnect update-ai-prompt ... && aws qconnect create-ai-prompt-version ..."
    fi
  else
    echo "   prompt text unchanged"
  fi
  if [[ -z "$PROMPT_VER" ]]; then
    PROMPT_VER="$(aws qconnect list-ai-prompt-versions --assistant-id "$ASSISTANT_ID" --region "$REGION" --ai-prompt-id "$PROMPT_ID" \
      --query 'max_by(aiPromptVersionSummaries,&versionNumber).versionNumber' --output text 2>/dev/null || true)"
    [[ "$PROMPT_VER" == "None" ]] && PROMPT_VER=""
  fi
else
  echo "==> Creating orchestration AI prompt '$PROMPT_NAME' from $PROMPT_FILE"
  TEMPLATE_JSON="$(python3 -c "import json,sys; print(json.dumps({'textFullAIPromptEditTemplateConfiguration':{'text':open('$PROMPT_FILE').read()}}))")"
  if [[ "$APPLY" == "1" ]]; then
    PROMPT_ID="$(aws qconnect create-ai-prompt \
      --assistant-id "$ASSISTANT_ID" --region "$REGION" \
      --name "$PROMPT_NAME" --type ORCHESTRATION --template-type TEXT \
      --api-format "$API_FORMAT" --model-id "$MODEL_ID" \
      --visibility-status PUBLISHED \
      --template-configuration "$TEMPLATE_JSON" \
      --query 'aiPrompt.aiPromptId' --output text)"
    echo "Created AI prompt: $PROMPT_ID"
    PROMPT_VER="$(aws qconnect create-ai-prompt-version --assistant-id "$ASSISTANT_ID" --region "$REGION" \
      --ai-prompt-id "$PROMPT_ID" --query 'versionNumber' --output text)"
  else
    echo "+ aws qconnect create-ai-prompt --assistant-id $ASSISTANT_ID --name $PROMPT_NAME --type ORCHESTRATION --template-type TEXT --api-format $API_FORMAT --model-id $MODEL_ID --visibility-status PUBLISHED --template-configuration <text from $PROMPT_FILE>"
    PROMPT_ID="<new-prompt-id>"
  fi
fi

# ---- 3) Create or UPDATE the ORCHESTRATION AI agent ---------------------------
# The agent carries THREE tools (matching the working SEA agent):
#   1. query_transactions  (MODEL_CONTEXT_PROTOCOL — the gateway MCP tool)
#   2. Complete            (RETURN_TO_CONTROL — self-service resolved)
#   3. Escalate            (RETURN_TO_CONTROL — hand off to a human)
# The tool file carries GATEWAY_ID_PLACEHOLDER in its toolId/title; substitute the
# discovered gateway id so the tool config matches the tool this instance's MCP
# integration actually discovered. (No SEA gateway id is baked in.)
echo "==> Building AI-agent configuration (gateway id: $GATEWAY_ID; 3 tools)"
PROMPT_REF="$PROMPT_ID"; [[ -n "$PROMPT_VER" ]] && PROMPT_REF="${PROMPT_ID}:${PROMPT_VER}"
CONFIG_JSON="$(GATEWAY_ID="$GATEWAY_ID" python3 -c "
import json, os
raw = open('$TOOL_FILE').read().replace('GATEWAY_ID_PLACEHOLDER', os.environ['GATEWAY_ID'])
tool = json.loads(raw)
complete = json.load(open('$COMPLETE_TOOL_FILE'))
escalate = json.load(open('$ESCALATE_TOOL_FILE'))
cfg={'orchestrationAIAgentConfiguration':{
  'orchestrationAIPromptId':'$PROMPT_REF',
  'toolConfigurations':[tool, complete, escalate],
  'connectInstanceArn':'$CONNECT_INSTANCE_ARN',
  'locale':'en_US'
}}
print(json.dumps(cfg))
")"
echo "----- configuration -----"
echo "$CONFIG_JSON" | python3 -m json.tool
echo "-------------------------"

AGENT_ID="$(aws qconnect list-ai-agents --assistant-id "$ASSISTANT_ID" --region "$REGION" \
  --query "aiAgentSummaries[?name=='${AGENT_NAME}'].aiAgentId | [0]" --output text 2>/dev/null || true)"

if [[ -n "$AGENT_ID" && "$AGENT_ID" != "None" ]]; then
  echo "==> AI agent '$AGENT_NAME' exists ($AGENT_ID) — UPDATING its tool configuration"
  if [[ "$APPLY" == "1" ]]; then
    aws qconnect update-ai-agent \
      --assistant-id "$ASSISTANT_ID" --region "$REGION" \
      --ai-agent-id "$AGENT_ID" \
      --visibility-status PUBLISHED \
      --configuration "$CONFIG_JSON" >/dev/null
    echo "Updated AI agent: $AGENT_ID"
  else
    echo "+ aws qconnect update-ai-agent --assistant-id $ASSISTANT_ID --ai-agent-id $AGENT_ID --visibility-status PUBLISHED --configuration <above>"
  fi
else
  if [[ "$APPLY" == "1" ]]; then
    AGENT_ID="$(aws qconnect create-ai-agent \
      --assistant-id "$ASSISTANT_ID" --region "$REGION" \
      --name "$AGENT_NAME" --type ORCHESTRATION \
      --visibility-status PUBLISHED \
      --configuration "$CONFIG_JSON" \
      --description "AnyCompanyPay merchant support: tenant-isolated transaction Q&A + policy RAG" \
      --query 'aiAgent.aiAgentId' --output text)"
    echo "Created AI agent: $AGENT_ID"
  else
    echo "+ aws qconnect create-ai-agent --assistant-id $ASSISTANT_ID --name $AGENT_NAME --type ORCHESTRATION --visibility-status PUBLISHED --configuration <above>"
    AGENT_ID="<new-agent-id>"
  fi
fi

# Publish the agent change as a new version (the orchestrator binds id:version).
if [[ "$APPLY" == "1" && "$AGENT_ID" != "<new-agent-id>" ]]; then
  AGENT_VER="$(aws qconnect create-ai-agent-version --assistant-id "$ASSISTANT_ID" --region "$REGION" \
    --ai-agent-id "$AGENT_ID" --query 'versionNumber' --output text)"
  echo "AI agent version: $AGENT_VER"
fi

# ---- 3.5) Assign the tool security profile to the AI agent --------------------
# THIS is what makes the MCP tool actually INVOCABLE (tools/call). Without it the
# orchestrator can list the tool but never calls it. It IS scriptable via
# connect associate-security-profiles with EntityType=AI_AGENT + the AI-agent ARN
# (the same API the console uses — confirmed via CloudTrail). The security profile
# itself (with the MCP application grant) is created by CloudFormation
# (AnyCompanyPayConnectAiAgentStack output AiAgentSecurityProfileName).
SECURITY_PROFILE_NAME="${SECURITY_PROFILE_NAME:-anycompany-pay-ai-agent-tools}"
if [[ "$APPLY" == "1" && "$AGENT_ID" != "<new-agent-id>" ]]; then
  SP_ID="$(aws connect list-security-profiles --region "$REGION" --instance-id "$CONNECT_INSTANCE_ID" \
    --query "SecurityProfileSummaryList[?Name=='${SECURITY_PROFILE_NAME}'].Id | [0]" --output text 2>/dev/null || true)"
  if [[ -z "$SP_ID" || "$SP_ID" == "None" ]]; then
    echo "WARNING: security profile '$SECURITY_PROFILE_NAME' not found on instance $CONNECT_INSTANCE_ID."
    echo "         Deploy AnyCompanyPayConnectAiAgentStack first (it creates the profile), then re-run."
  else
    AGENT_ARN="arn:aws:wisdom:${REGION}:${ACCOUNT_ID}:ai-agent/${ASSISTANT_ID}/${AGENT_ID}"
    echo "==> Assigning security profile '$SECURITY_PROFILE_NAME' ($SP_ID) to AI agent (EntityType=AI_AGENT)"
    aws connect associate-security-profiles --region "$REGION" \
      --instance-id "$CONNECT_INSTANCE_ID" \
      --security-profiles Id="$SP_ID" \
      --entity-type AI_AGENT \
      --entity-arn "$AGENT_ARN" >/dev/null 2>&1 \
      && echo "   Assigned." \
      || echo "   (already assigned, or association returned non-zero — safe to ignore if idempotent)"
    # Security profiles attach PER AI-AGENT VERSION: a new version starts with none,
    # so its MCP tool calls would be refused. Copy the agent's profiles (incl. the
    # MCP grant) onto the version the orchestrator is about to bind.
    if [[ -n "${AGENT_VER:-}" && "$AGENT_VER" != "None" ]]; then
      PROFILE_IDS="$(aws connect list-entity-security-profiles --region "$REGION" --instance-id "$CONNECT_INSTANCE_ID" \
        --entity-type AI_AGENT --entity-arn "$AGENT_ARN" --query 'SecurityProfiles[].Id' --output text | tr '\t' ' ')"
      SP_ARGS=(); for id in $PROFILE_IDS $SP_ID; do SP_ARGS+=("Id=$id"); done
      aws connect associate-security-profiles --region "$REGION" --instance-id "$CONNECT_INSTANCE_ID" \
        --security-profiles "${SP_ARGS[@]}" --entity-type AI_AGENT --entity-arn "${AGENT_ARN}:${AGENT_VER}" >/dev/null 2>&1 \
        && echo "   Security profiles copied to version ${AGENT_VER}." \
        || echo "   WARNING: could not associate security profiles with version ${AGENT_VER} (MCP tool calls will fail)"
    fi
  fi
else
  echo "+ (would) aws connect associate-security-profiles --instance-id $CONNECT_INSTANCE_ID --security-profiles Id=<id of $SECURITY_PROFILE_NAME> --entity-type AI_AGENT --entity-arn arn:aws:wisdom:$REGION:$ACCOUNT_ID:ai-agent/$ASSISTANT_ID/$AGENT_ID"
fi

# ---- 4) Optionally bind it as the SELF-SERVICE orchestrator -------------------
# NOTE: for --ai-agent-type ORCHESTRATION the API REQUIRES --orchestrator-use-case;
# without it you get "Orchestrator use case is required for orchestration AIAgentType".
# The self-service chat flow uses the "Connect.SelfService" orchestrator binding.
# We pin a specific PUBLISHED version (id:version) because publishing creates a new
# version and the caller usually wants the one just created/reused.
if [[ "$SET_DEFAULT" == "1" ]]; then
  ORCH_USE_CASE="${ORCH_USE_CASE:-Connect.SelfService}"
  # Resolve the latest published version number for the agent.
  AGENT_VER="${AGENT_VER:-}"
  if [[ -z "$AGENT_VER" && "$APPLY" == "1" && "$AGENT_ID" != "<new-agent-id>" ]]; then
    AGENT_VER="$(aws qconnect list-ai-agent-versions \
      --assistant-id "$ASSISTANT_ID" --ai-agent-id "$AGENT_ID" --region "$REGION" \
      --query 'max_by(aiAgentVersionSummaries,&versionNumber).versionNumber' --output text 2>/dev/null)"
  fi
  AGENT_REF="$AGENT_ID"; [[ -n "$AGENT_VER" && "$AGENT_VER" != "None" ]] && AGENT_REF="${AGENT_ID}:${AGENT_VER}"
  echo "==> Binding $ORCH_USE_CASE orchestrator -> $AGENT_REF"
  run aws qconnect update-assistant-ai-agent \
    --assistant-id "$ASSISTANT_ID" --region "$REGION" \
    --ai-agent-type ORCHESTRATION \
    --orchestrator-use-case "$ORCH_USE_CASE" \
    --configuration "{\"aiAgentId\":\"${AGENT_REF}\"}"
  echo "   NOTE: re-run this after ANY console publish of the agent — publishing can"
  echo "   reset the $ORCH_USE_CASE orchestrator binding back to the AWS system agent."
fi

cat <<EOF

This script performs the FULL wiring end-to-end (all scripted — no console step required):
  1. orchestration AI prompt (create/reuse)
  2. ORCHESTRATION AI agent with 3 tools (query_transactions MCP + Complete + Escalate) — create/update
  3. security-profile ASSIGNMENT to the AI agent via
     'connect associate-security-profiles --entity-type AI_AGENT' — THIS is what makes the MCP
     tool actually invocable (tools/call); it is the same API the console uses (verified via CloudTrail)
  4. (with --set-default) bind the Connect.SelfService orchestrator to the agent's latest version

Prereq: the security profile '$SECURITY_PROFILE_NAME' (with the MCP tool grant) must exist — it is
created by CloudFormation (AnyCompanyPayConnectAiAgentStack, via connect-ai-agent/deploy.sh).

Notes:
  * merchant_id is resolved by the gateway interceptor from the Connect contact (no session seeding).
  * The agentic chat flow already routes chat to this orchestrator (connect-stack CFN).
  * Calls are idempotent; re-run with --apply --set-default any time (e.g. after a manual console publish,
    which can reset the orchestrator binding).
EOF
