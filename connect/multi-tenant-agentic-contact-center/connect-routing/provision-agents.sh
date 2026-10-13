#!/bin/bash
# Create demo agents on the routing module's profiles (design §7.3), reusing
# infra/provision-agent.sh: passwords are GENERATED into Secrets Manager
# (anycompany-pay[/<env>]/connect/<login>) — never hardcoded or printed.
#
#   LIVE_AGENTS (default "agent-live1 agent-live2") -> anycompany-pay-rp-live
#   OOH_AGENTS  (default "agent-ooh1")              -> anycompany-pay-rp-ooh-backlog
set -uo pipefail
cd "$(dirname "$0")"
ENV_NAME="${ENV_NAME:-}"
SFX="${ENV_NAME:+-${ENV_NAME}}"
for a in ${LIVE_AGENTS:-agent-live1 agent-live2}; do
  RP_NAME="anycompany-pay-rp-live${SFX}" AGENT="$a" AGENT_EMAIL="${a}@anycompany-pay.example" \
    bash ../infra/provision-agent.sh
done
for a in ${OOH_AGENTS:-agent-ooh1}; do
  RP_NAME="anycompany-pay-rp-ooh-backlog${SFX}" AGENT="$a" AGENT_EMAIL="${a}@anycompany-pay.example" \
    bash ../infra/provision-agent.sh
done
