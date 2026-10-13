#!/bin/bash

# Configure the Pipecat bot as an AgentCore agent (container runtime, no memory).

set -e

if [ ! -f "./agent/.env" ]; then
    echo "❌ Error: agent/.env not found (copy agent/env.example to agent/.env first)"
    exit 1
fi

source ./agent/.env
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>/dev/null)
ROLE_NAME="AmazonBedrockAgentCoreSDKRuntime-${AWS_REGION}-aisle"
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${ROLE_NAME}"

# Create the execution role if it doesn't exist yet.
aws iam get-role --role-name "$ROLE_NAME" &>/dev/null || ./scripts/setup-iam-role.sh

echo "Configuring AgentCore with execution role: $ROLE_ARN"
# NOTE: --disable-memory stays ON intentionally. It only disables the agentcore
# CLI's *managed* memory wiring. We self-manage our own Memory resource via
# MemoryClient + MEMORY_ID (see scripts/setup-memory.sh and agent/memory.py), so
# removing this flag would make the CLI provision/attach a second memory and
# fight our setup. Leave it as-is.
uv run agentcore configure \
    -e ./agent/pipecat-agent.py \
    --name aisle_agent \
    --container-runtime docker \
    --disable-memory \
    --execution-role "$ROLE_ARN" \
    --region "$AWS_REGION" \
    --requirements-file ./agent/requirements.txt \
    --non-interactive
