#!/bin/bash
# Register the external (Level3) A2A agent with Amazon Connect and create the
# Connect AI agent that hands conversations to it. API-only steps (no
# CloudFormation support for A2A_SERVER applications yet), all idempotent:
#
#   1. AppIntegrations application, type A2A_SERVER (AccessUrl = the wss://
#      endpoint, AuthConfig = API key in Secrets Manager)
#   2. integration association (type APPLICATION) with the Connect instance
#   3. security profile that allow-lists the application (type THIRD_PARTY)
#   4. ORCHESTRATION prompt + AI agent with an IMMEDIATE handoff to the application
#      (text streaming on chat; audioStreamingEnabled=false) and Escalate / Complete
#      return-to-control tools, so the partner's FINISH reaches the flow
#   5. publish an AI agent version and attach the security profile to the agent
#      AND the version (profiles attach per version)
#
# Prints A2A_AGENT_ARN=<versioned ARN> on the last line for deploy.sh.
#
# Required env: R, INSTANCE_ARN, ASSISTANT_ARN, ENDPOINT_URL, SECRET_ARN
# Optional env: ENV_NAME, MODEL_ID, API_FORMAT
set -euo pipefail
cd "$(dirname "$0")"

: "${R:?}" "${INSTANCE_ARN:?}" "${ASSISTANT_ARN:?}" "${ENDPOINT_URL:?}" "${SECRET_ARN:?}"
SFX="${ENV_NAME:+-${ENV_NAME}}"
INSTANCE_ID="${INSTANCE_ARN##*/}"
ASSISTANT_ID="${ASSISTANT_ARN##*/}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
APP_NAME="anycompany-pay-level3${SFX}"
SP_NAME="anycompany-pay-a2a-agent${SFX}"
PROMPT_NAME="anycompany-pay-a2a-orchestration${SFX}"
AGENT_NAME="anycompany-pay-a2a-orchestrator${SFX}"
MODEL_ID="${MODEL_ID:-global.anthropic.claude-sonnet-4-5-20250929-v1:0}"
API_FORMAT="${API_FORMAT:-MESSAGES}"
INSTRUCTION="Hand every conversation to the Level3 partner assistant. It returns COMPLETE or ESCALATE."

# ---- 1) A2A_SERVER application ---------------------------------------------------
APP_ARN="$(aws appintegrations list-applications --region "$R" \
  --query "Applications[?Name=='${APP_NAME}'].Arn | [0]" --output text)"
SOURCE="{\"ExternalUrlConfig\":{\"AccessUrl\":\"${ENDPOINT_URL}\"}}"
AUTH="{\"AuthType\":\"API_KEY\",\"CredentialProviderIdentifier\":\"${SECRET_ARN}\"}"
if [[ -z "$APP_ARN" || "$APP_ARN" == "None" ]]; then
  echo "==> Creating A2A_SERVER application $APP_NAME -> $ENDPOINT_URL" >&2
  APP_ARN="$(aws appintegrations create-application --region "$R" \
    --name "$APP_NAME" --namespace "$APP_NAME" \
    --description "Level3 external AI agent (mock), Amazon Connect A2A" \
    --application-source-config "$SOURCE" --application-type A2A_SERVER --auth-config "$AUTH" \
    --query Arn --output text)"
else
  echo "==> Updating application $APP_NAME (endpoint + key)" >&2
  aws appintegrations update-application --region "$R" --arn "$APP_ARN" \
    --application-source-config "$SOURCE" --auth-config "$AUTH" >/dev/null
fi
APP_ID="$(aws appintegrations get-application --region "$R" --arn "$APP_ARN" --query Id --output text)"
echo "    application $APP_ID" >&2

# ---- 2) associate it with the instance -------------------------------------------
ASSOC="$(aws connect list-integration-associations --region "$R" --instance-id "$INSTANCE_ID" \
  --integration-arn "$APP_ARN" \
  --query 'IntegrationAssociationSummaryList[0].IntegrationAssociationId' --output text)"
if [[ -z "$ASSOC" || "$ASSOC" == "None" ]]; then
  echo "==> Associating the application with the instance" >&2
  aws connect create-integration-association --region "$R" --instance-id "$INSTANCE_ID" \
    --integration-type APPLICATION --integration-arn "$APP_ARN" >/dev/null
fi

# ---- 3) security profile allow-listing the partner --------------------------------
ALLOWED="[{\"Arn\":\"${APP_ARN}\",\"Type\":\"THIRD_PARTY\"}]"
SP_ID="$(aws connect list-security-profiles --region "$R" --instance-id "$INSTANCE_ID" \
  --query "SecurityProfileSummaryList[?Name=='${SP_NAME}'].Id | [0]" --output text)"
if [[ -z "$SP_ID" || "$SP_ID" == "None" ]]; then
  echo "==> Creating security profile $SP_NAME (allows the Level3 application)" >&2
  SP_ID="$(aws connect create-security-profile --region "$R" --instance-id "$INSTANCE_ID" \
    --security-profile-name "$SP_NAME" --description "Lets the A2A orchestrator hand off to Level3" \
    --allowed-ai-agents "$ALLOWED" --query SecurityProfileId --output text)"
else
  aws connect update-security-profile --region "$R" --instance-id "$INSTANCE_ID" \
    --security-profile-id "$SP_ID" --allowed-ai-agents "$ALLOWED" >/dev/null
fi

# ---- 4) prompt + ORCHESTRATION agent with an immediate handoff --------------------
TEMPLATE="$(python3 -c "import json; print(json.dumps({'textFullAIPromptEditTemplateConfiguration':{'text':open('orchestration-prompt.yaml').read()}}))")"
PROMPT_ID="$(aws qconnect list-ai-prompts --region "$R" --assistant-id "$ASSISTANT_ID" \
  --query "aiPromptSummaries[?name=='${PROMPT_NAME}'].aiPromptId | [0]" --output text)"
if [[ -z "$PROMPT_ID" || "$PROMPT_ID" == "None" ]]; then
  echo "==> Creating prompt $PROMPT_NAME" >&2
  PROMPT_ID="$(aws qconnect create-ai-prompt --region "$R" --assistant-id "$ASSISTANT_ID" \
    --name "$PROMPT_NAME" --type ORCHESTRATION --template-type TEXT --api-format "$API_FORMAT" \
    --model-id "$MODEL_ID" --visibility-status PUBLISHED --template-configuration "$TEMPLATE" \
    --query aiPrompt.aiPromptId --output text)"
fi
LIVE_TEXT="$(aws qconnect get-ai-prompt --region "$R" --assistant-id "$ASSISTANT_ID" --ai-prompt-id "$PROMPT_ID" \
  --query 'aiPrompt.templateConfiguration.textFullAIPromptEditTemplateConfiguration.text' --output text)"
NEW_PROMPT_VER=""
if [[ "$LIVE_TEXT" != "$(cat orchestration-prompt.yaml)" ]]; then
  echo "==> Prompt text changed: updating $PROMPT_NAME" >&2
  aws qconnect update-ai-prompt --region "$R" --assistant-id "$ASSISTANT_ID" --ai-prompt-id "$PROMPT_ID" \
    --visibility-status PUBLISHED --template-configuration "$TEMPLATE" >/dev/null
  NEW_PROMPT_VER="$(aws qconnect create-ai-prompt-version --region "$R" --assistant-id "$ASSISTANT_ID" \
    --ai-prompt-id "$PROMPT_ID" --query versionNumber --output text)"
fi
PROMPT_VER="${NEW_PROMPT_VER:-}"
[[ -n "$PROMPT_VER" ]] || PROMPT_VER="$(aws qconnect list-ai-prompt-versions --region "$R" --assistant-id "$ASSISTANT_ID" --ai-prompt-id "$PROMPT_ID" \
  --query 'max_by(aiPromptVersionSummaries,&versionNumber).versionNumber' --output text 2>/dev/null || true)"
if [[ -z "$PROMPT_VER" || "$PROMPT_VER" == "None" ]]; then
  PROMPT_VER="$(aws qconnect create-ai-prompt-version --region "$R" --assistant-id "$ASSISTANT_ID" \
    --ai-prompt-id "$PROMPT_ID" --query versionNumber --output text)"
fi

CONFIG="$(python3 - "$PROMPT_ID:$PROMPT_VER" "$INSTANCE_ARN" "$APP_ID" "$INSTRUCTION" <<'EOF'
import json, sys
prompt, instance, app, instruction = sys.argv[1:5]
print(json.dumps({"orchestrationAIAgentConfiguration": {
    "orchestrationAIPromptId": prompt,
    "connectInstanceArn": instance,
    "locale": "en_US",
    # Return-to-control tools: the partner ESCALATE / COMPLETE reach the flow as
    # $.Lex.SessionAttributes.Tool = Escalate / Complete.
    "toolConfigurations": [json.load(open("tool-complete.json")), json.load(open("tool-escalate.json"))],
    "multiAgentConfigurations": [{"handoffAgentConfiguration": {
        "agentTarget": {"applicationId": app},
        "instruction": {"instruction": instruction},
        "audioStreamingEnabled": False,
        "immediateHandoff": True,
    }}],
}}))
EOF
)"
AGENT_ID="$(aws qconnect list-ai-agents --region "$R" --assistant-id "$ASSISTANT_ID" \
  --query "aiAgentSummaries[?name=='${AGENT_NAME}'].aiAgentId | [0]" --output text)"
if [[ -z "$AGENT_ID" || "$AGENT_ID" == "None" ]]; then
  echo "==> Creating AI agent $AGENT_NAME (immediate handoff -> $APP_NAME)" >&2
  AGENT_ID="$(aws qconnect create-ai-agent --region "$R" --assistant-id "$ASSISTANT_ID" \
    --name "$AGENT_NAME" --type ORCHESTRATION --visibility-status PUBLISHED \
    --configuration "$CONFIG" --query aiAgent.aiAgentId --output text)"
else
  echo "==> Updating AI agent $AGENT_NAME" >&2
  aws qconnect update-ai-agent --region "$R" --assistant-id "$ASSISTANT_ID" --ai-agent-id "$AGENT_ID" \
    --visibility-status PUBLISHED --configuration "$CONFIG" >/dev/null
fi

# ---- 5) publish a version; profiles on the agent AND the version -----------------
AGENT_VER="$(aws qconnect create-ai-agent-version --region "$R" --assistant-id "$ASSISTANT_ID" \
  --ai-agent-id "$AGENT_ID" --query versionNumber --output text)"
AGENT_ARN="arn:aws:wisdom:${R}:${ACCOUNT_ID}:ai-agent/${ASSISTANT_ID}/${AGENT_ID}"
for arn in "$AGENT_ARN" "${AGENT_ARN}:${AGENT_VER}"; do
  aws connect associate-security-profiles --region "$R" --instance-id "$INSTANCE_ID" \
    --security-profiles Id="$SP_ID" --entity-type AI_AGENT --entity-arn "$arn" >/dev/null 2>&1 || true
done
echo "    AI agent $AGENT_ID version $AGENT_VER (security profile $SP_NAME attached)" >&2
echo "A2A_AGENT_ARN=${AGENT_ARN}:${AGENT_VER}"
