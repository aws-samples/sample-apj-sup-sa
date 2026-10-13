#!/bin/bash

# Remove the AgentCore agent runtime (does not touch the VPC — use cleanup-vpc.sh for that).

set -e
echo "Destroying AgentCore agent 'aisle_agent'..."
uv run agentcore destroy || echo "agentcore destroy returned non-zero (agent may already be gone)."
echo "✅ Agent removed. Run ./scripts/cleanup-vpc.sh to also tear down the VPC + NAT."
