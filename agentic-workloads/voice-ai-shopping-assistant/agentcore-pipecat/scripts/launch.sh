#!/bin/bash

# Deploy the agent to AgentCore Runtime in VPC mode, passing agent/.env values
# as runtime environment variables. Writes the resulting Agent ARN to
# lambda/start.env for the Lambda /start function to consume.

AGENT_ENV_FILE="./agent/.env"
AGENTCORE_CONFIG=".bedrock_agentcore.yaml"
LAMBDA_ENV_FILE="./lambda/start.env"

if [ ! -f "$AGENTCORE_CONFIG" ]; then
    echo "❌ $AGENTCORE_CONFIG not found. Run ./scripts/configure.sh first."
    exit 1
fi
if [ ! -f "$AGENT_ENV_FILE" ]; then
    echo "❌ $AGENT_ENV_FILE not found."
    exit 1
fi

echo "Loading environment variables..."
set -a
source "$AGENT_ENV_FILE"
set +a

# --- Apply VPC config (required for Daily UDP) ---
if [ -f "vpc-config.env" ]; then
    echo "Applying VPC configuration from vpc-config.env..."
    source vpc-config.env
    cp .bedrock_agentcore.yaml .bedrock_agentcore.yaml.backup
    sed -i.tmp "s/network_mode: PUBLIC/network_mode: VPC/" .bedrock_agentcore.yaml
    sed -i.tmp "s/network_mode_config: null/network_mode_config:\\
          subnets:\\
            - $PRIVATE_SUBNET_1\\
            - $PRIVATE_SUBNET_2\\
          security_groups:\\
            - $SG_ID/" .bedrock_agentcore.yaml
    rm -f .bedrock_agentcore.yaml.tmp
    echo "✅ VPC mode applied (subnets $PRIVATE_SUBNET_1, $PRIVATE_SUBNET_2; sg $SG_ID)"
else
    echo "⚠️  No vpc-config.env found. Run ./scripts/setup-vpc.sh first (Daily needs UDP)."
    exit 1
fi

# --- Build launch command with --env flags from agent/.env ---
LAUNCH_CMD="uv run agentcore launch --auto-update-on-conflict"
FOUND_ENV_VARS=false
while IFS= read -r line || [ -n "$line" ]; do
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    if [[ "$line" =~ ^[^=]+=(.*)$ ]]; then
        VAR_NAME="${line%%=*}"; VAR_VALUE="${line#*=}"
        VAR_NAME="$(echo "$VAR_NAME" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
        VAR_VALUE="$(echo "$VAR_VALUE" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
        if [[ "${VAR_VALUE}" =~ ^\"(.*)\"$ ]] || [[ "${VAR_VALUE}" =~ ^\'(.*)\'$ ]]; then
            VAR_VALUE="${BASH_REMATCH[1]}"
        fi
        [[ "$VAR_NAME" == "PIPECAT_LOCAL_DEV" || "$VAR_NAME" == "DAILY_ROOM_URL" ]] && continue
        if [[ -n "$VAR_NAME" && -n "$VAR_VALUE" ]]; then
            LAUNCH_CMD+=" --env $VAR_NAME=\"$VAR_VALUE\""
            FOUND_ENV_VARS=true
            echo "  Added env: $VAR_NAME"
        fi
    fi
done < "$AGENT_ENV_FILE"

$FOUND_ENV_VARS || { echo "Warning: no env vars found in $AGENT_ENV_FILE"; exit 1; }

echo ""
echo "Executing: $LAUNCH_CMD"
eval "$LAUNCH_CMD"

# --- Read Agent ARN and persist for the Lambda /start ---
echo "Reading Agent ARN from agentcore status..."
AGENT_ARN=$(uv run agentcore status | grep "Agent ARN:" | sed 's/.*Agent ARN: //' | sed 's/│//g' | xargs)
if [ -z "$AGENT_ARN" ]; then
    echo "Error: could not extract Agent ARN. Run 'uv run agentcore status' once deploy completes,"
    echo "then set AGENT_RUNTIME_ARN in $LAMBDA_ENV_FILE manually."
    exit 1
fi
echo "Agent ARN: $AGENT_ARN"

mkdir -p ./lambda
if [ -f "$LAMBDA_ENV_FILE" ] && grep -q "^AGENT_RUNTIME_ARN=" "$LAMBDA_ENV_FILE"; then
    sed -i.bak "s|^AGENT_RUNTIME_ARN=.*|AGENT_RUNTIME_ARN=$AGENT_ARN|" "$LAMBDA_ENV_FILE"
else
    echo "AGENT_RUNTIME_ARN=$AGENT_ARN" >> "$LAMBDA_ENV_FILE"
fi
# Carry the Daily room URL through for the Lambda too, if present in agent/.env.
if [ -n "$DAILY_ROOM_URL" ]; then
    if grep -q "^DAILY_ROOM_URL=" "$LAMBDA_ENV_FILE" 2>/dev/null; then
        sed -i.bak "s|^DAILY_ROOM_URL=.*|DAILY_ROOM_URL=$DAILY_ROOM_URL|" "$LAMBDA_ENV_FILE"
    else
        echo "DAILY_ROOM_URL=$DAILY_ROOM_URL" >> "$LAMBDA_ENV_FILE"
    fi
fi

echo ""
echo "✅ Deployment complete. AGENT_RUNTIME_ARN written to $LAMBDA_ENV_FILE"
LOG_GROUP=$(uv run agentcore describe 2>/dev/null | grep -o '/aws/bedrock-agentcore/runtimes/[^"]*' | head -1)
[ -n "$LOG_GROUP" ] && echo "Logs: aws logs tail $LOG_GROUP --log-stream-name-prefix \"$(date +%Y/%m/%d)/[runtime-logs]\" --follow"
echo ""
echo "Next: deploy the /start Lambda -> ./lambda/deploy.sh"
