#!/bin/bash

# Provision the AgentCore Memory resource for the Aisle voice agent.
#
# Creates (idempotently) ONE Memory resource with a single USER_PREFERENCE
# long-term strategy. The namespace is set deterministically to
# /users/{actorId}/preferences so retrieval needs no wildcard (retrieve_memories
# rejects "*"). Short-term events are written per turn by the agent; the service
# extracts long-term preference records from them asynchronously (~20-60s).
#
# Run under YOUR own (admin) credentials — resource creation is NOT done by the
# runtime execution role:
#   AWS_PROFILE=your-profile ./scripts/setup-memory.sh
#
# On success it prints the memory id. Copy it into agent/.env as MEMORY_ID=...

set -e

if [ ! -f "./agent/.env" ]; then
    echo "❌ Error: agent/.env not found (run from the agentcore-pipecat/ directory)"
    exit 1
fi

source ./agent/.env
AWS_REGION="${AWS_REGION:-ap-southeast-2}"

# Memory name: alphanumeric/underscore only (no hyphens) per the service.
MEMORY_NAME="${MEMORY_NAME:-aisle_memory}"
STRATEGY_NAME="${STRATEGY_NAME:-aisle_user_pref}"

echo "Provisioning AgentCore Memory '$MEMORY_NAME' in $AWS_REGION ..."
echo "(create_or_get_memory waits for ACTIVE; this can take a couple of minutes.)"

AWS_REGION="$AWS_REGION" MEMORY_NAME="$MEMORY_NAME" STRATEGY_NAME="$STRATEGY_NAME" \
uv run python <<'PY'
import os
from bedrock_agentcore.memory import MemoryClient

region = os.environ["AWS_REGION"]
mem_name = os.environ["MEMORY_NAME"]
strat_name = os.environ["STRATEGY_NAME"]

client = MemoryClient(region_name=region)

# One USER_PREFERENCE strategy with a deterministic, wildcard-free namespace.
strategies = [
    {
        "userPreferenceMemoryStrategy": {
            "name": strat_name,
            "description": "Aisle shopper preferences: usuals, soft dietary/allergen hints, "
                           "preferred brands, budget, shopping style. Advisory only.",
            "namespaces": ["/users/{actorId}/preferences"],
        }
    }
]

memory = client.create_or_get_memory(
    name=mem_name,
    strategies=strategies,
    description="Aisle voice grocery agent — short-term turns + long-term user preferences.",
    event_expiry_days=90,
)

mid = memory.get("id") or memory.get("memoryId") or ""
status = memory.get("status", "?")
print("\n=== AgentCore Memory ready ===")
print("MEMORY_ID:", mid)
print("status   :", status)

# Surface the resolved strategy namespace for confirmation.
try:
    for s in client.get_memory_strategies(mid):
        stype = s.get("type") or s.get("memoryStrategyType") or "?"
        print(f"strategy : {stype} -> namespaces={s.get('namespaces')}")
except Exception as e:
    print("(could not list strategies:", e, ")")

print("\nNext: set this in agent/.env  ->  MEMORY_ID=" + mid)
PY

echo ""
echo "✅ Done. Put the printed MEMORY_ID into agent/.env (MEMORY_ID=...)."
