#!/usr/bin/env bash
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ ! -f /opt/ros/jazzy/setup.bash ]]; then
  echo "[run-api] ERROR: /opt/ros/jazzy/setup.bash not found" >&2
  exit 2
fi

source /opt/ros/jazzy/setup.bash
cd "$SCRIPT_DIR"

if [[ ! -x "$SCRIPT_DIR/.venv/bin/python" ]]; then
  echo "[run-api] ERROR: expected venv python at $SCRIPT_DIR/.venv/bin/python" >&2
  exit 2
fi

# Perception worker at 0.5 Hz instead of 1 Hz — Qwen latency in practice
# runs 2.5-4.5s per call (vs 0.93s spike baseline), so 1 Hz means the worker
# runs back-to-back with no sleep, starving the planner of Bedrock budget.
# Override with `PERCEPTION_INTERVAL_S=1.0 ./run-api.sh` to restore aggressive.
export PERCEPTION_INTERVAL_S="${PERCEPTION_INTERVAL_S:-2.0}"
exec "$SCRIPT_DIR/.venv/bin/python" -m uvicorn api.control_api:app --host 0.0.0.0 --port 8888
