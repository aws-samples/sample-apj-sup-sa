#!/usr/bin/env bash
# Agent + app correlation scenario. Prerequisites: sample 02's shop running (./up.sh)
# and sample 01's venv active (pip install -r ../01-agent-observability/requirements.txt).
#
#   baseline  : agent tools call the live orders API; payments is healthy
#   incident  : payments slows to ~4s, past the agent's 3s tool timeout
#   recovery  : fault cleared
#
# Usage: ./run_scenario.sh [sessions-per-phase]   (default 15)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(cd "$HERE/.." && pwd)"
SESSIONS="${1:-15}"
CHAOS="$ROOT/02-microservices-apm/chaos.sh"
AGENT="$ROOT/01-agent-observability/run.sh"

cleanup() {
  "$CHAOS" off >/dev/null 2>&1 || true
}
trap cleanup EXIT

curl -fsS http://localhost:8001/healthz >/dev/null \
  || { echo "orders is not reachable on :8001. Start sample 02 first (02-microservices-apm/up.sh)."; exit 1; }

export ORDERS_API_URL=http://localhost:8001
export TOOL_TIMEOUT_SECONDS=3
export PROMPT_VERSION=v2          # isolate the downstream effect from the scope bug in 01

phase() {
  echo; echo "================ $1  ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
  SCENARIO_PHASE="$1" "$AGENT" --kind in_scope --sessions "$SESSIONS" --sleep 0.5
}

"$CHAOS" off >/dev/null
phase baseline

"$CHAOS" latency 4000
phase incident

"$CHAOS" off >/dev/null
trap - EXIT
phase recovery

echo
echo "Done. Wait 2-3 minutes for ingestion, then run the queries in ./queries (see README)."
