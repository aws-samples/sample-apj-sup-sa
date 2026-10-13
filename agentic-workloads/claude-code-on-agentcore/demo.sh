#!/usr/bin/env bash
# ============================================================
# Demo: Claude Code on AgentCore with GitHub MCP Gateway
# ============================================================
# Run this on your local machine after exporting AWS creds.
#
# Prerequisites:
#   - pip install boto3 bedrock-agentcore websockets
#   - AWS credentials with bedrock-agentcore access
#
# Usage:
#   ./demo.sh                           # Interactive TUI
#   ./demo.sh "fix issue #1 and open a PR"  # Headless one-shot
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/coding_agents/claude-code"

export AWS_REGION="${AWS_REGION:-us-east-2}"

PROMPT="${1:-}"

if [ -n "$PROMPT" ]; then
    echo "🤖 Sending prompt to Claude Code on AgentCore..."
    echo "   Prompt: $PROMPT"
    echo ""
    python3 connect.py --prompt "$PROMPT"
else
    echo "🤖 Connecting to Claude Code on AgentCore (interactive mode)..."
    echo ""
    echo "   Try: 'Use the GitHub MCP tools to list open issues in wirjo/agentcore-demo-app,"
    echo "         pick issue #1, fix it, and open a pull request.'"
    echo ""
    python3 connect.py
fi
