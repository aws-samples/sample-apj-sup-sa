#!/bin/bash
# Claude Code on Bedrock - Developer Environment Setup
set -euo pipefail

REGION="${AWS_REGION:-ap-northeast-2}"
MODEL="${ANTHROPIC_MODEL:-us.anthropic.claude-sonnet-4-6-20250514-v1:0}"

SHELL_RC="$HOME/.zshrc"
if [ -n "${BASH_VERSION:-}" ]; then
  SHELL_RC="$HOME/.bashrc"
fi

echo "Checking AWS credentials..."
if ! aws sts get-caller-identity &>/dev/null; then
  echo "ERROR: AWS credentials not configured. Run 'aws configure' first."
  exit 1
fi

CALLER_ARN=$(aws sts get-caller-identity --query 'Arn' --output text)
echo "Authenticated as: $CALLER_ARN"

echo "Checking Bedrock access in $REGION..."
if ! aws bedrock list-foundation-models --region "$REGION" --query 'modelSummaries[0].modelId' --output text &>/dev/null; then
  echo "ERROR: No Bedrock access in $REGION. Check IAM permissions."
  exit 1
fi
echo "Bedrock access confirmed."

if grep -q 'CLAUDE_CODE_USE_BEDROCK' "$SHELL_RC" 2>/dev/null; then
  echo "Claude Code Bedrock config already exists in $SHELL_RC"
  echo "To update, remove the existing block and re-run this script."
  exit 0
fi

cat >> "$SHELL_RC" << EOF

# Claude Code Bedrock Configuration
export CLAUDE_CODE_USE_BEDROCK=1
export AWS_REGION=$REGION
export ANTHROPIC_MODEL=$MODEL
EOF

echo "Setup complete. Run 'source $SHELL_RC' then 'claude' to start."
