#!/bin/bash
# Launch the full drone simulation (Isaac Sim + PX4 + ROS 2 + Pegasus drone).
# One command, one bash script, same pattern as flying-agent-dev's
# `~/run_drone_sim.sh` — but config-driven via drone/configs/*.yaml.
#
# Usage:
#   ./run_simulation.sh                              # .current_config or default
#   ./run_simulation.sh --config warehouse_shelves   # set config + launch
#   ./run_simulation.sh --headless                   # no GUI (eval)
#
# Then in another terminal to fly:
#   python3 ./scripts/keyboard_fly.py

set -e

OUR_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRONE_DIR="$(cd "$OUR_SCRIPT_DIR/.." && pwd)"
CONFIGS_DIR="$DRONE_DIR/configs"
POINTER="$DRONE_DIR/.current_config"
FLY_PY="$OUR_SCRIPT_DIR/fly_drone.py"

HEADLESS=""
CONFIG_NAME=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)   CONFIG_NAME="$2"; shift 2 ;;
    --headless) HEADLESS="--headless"; shift ;;
    -h|--help)
      sed -n '2,12p' "$0"
      exit 0
      ;;
    *) echo "unknown arg: $1"; exit 2 ;;
  esac
done

# --- write pointer if --config given ---
if [[ -n "$CONFIG_NAME" ]]; then
  CFG_PATH="$CONFIGS_DIR/${CONFIG_NAME%.yaml}.yaml"
  if [[ ! -f "$CFG_PATH" ]]; then
    echo "[run_sim] config not found: $CFG_PATH"
    echo "available:"
    ls -1 "$CONFIGS_DIR"/*.yaml 2>/dev/null | xargs -n1 basename | sed 's/\.yaml$//' | sed 's/^/  /'
    exit 2
  fi
  echo "$CFG_PATH" > "$POINTER"
  echo "[run_sim] active config = $CFG_PATH"
fi

# --- clean slate ---
echo "[run_sim] clearing stale Kit/PX4/DDS/sim/mavsdk processes"
pkill -9 px4 2>/dev/null || true
pkill -9 -f 'isaacsim.exp.full.kit' 2>/dev/null || true
pkill -9 -f MicroXRCEAgent 2>/dev/null || true
# Our own standalone entry + keyboard flight — critical, otherwise a stale
# fly_drone clings to the PX4 MAVLink port and the new one can't bind.
pkill -9 -f fly_drone 2>/dev/null || true
pkill -9 -f keyboard_fly 2>/dev/null || true
# mavsdk_server is spawned by keyboard_fly (gRPC gateway to PX4 UDP :14540).
# If kbfly was SIGKILLed, mavsdk_server can orphan and hold the socket,
# making the next MAVSDK connect hang.
pkill -9 -f mavsdk_server 2>/dev/null || true
sleep 2

# --- environment ---
export DISPLAY="${DISPLAY:-:1}"

if [[ -f "$HOME/IsaacSim/setup_ros_env.sh" ]]; then
  source "$HOME/IsaacSim/setup_ros_env.sh"
  echo "[run_sim] sourced ROS 2 env"
fi

export PYTHONPATH="$HOME/.local/share/ov/data/exts/v2/pegasus.simulator-5.1.0:${PYTHONPATH:-}"

# Idempotent depth patch
DEPTH_PATCH="$OUR_SCRIPT_DIR/apply_pegasus_depth_patch.sh"
if [[ -x "$DEPTH_PATCH" ]]; then
  "$DEPTH_PATCH" || echo "[run_sim] warning: depth patch returned non-zero"
fi

# --- sanity-check the target file exists BEFORE handing to python.sh ---
if [[ ! -f "$FLY_PY" ]]; then
  echo "[run_sim] ERROR: $FLY_PY not found"
  exit 2
fi
echo "[run_sim] launching: $FLY_PY (config: ${CONFIG_NAME:-from pointer}) $HEADLESS"

# cd into the script's directory before invoking python.sh — some versions
# of IsaacSim's python.sh / setup_python_env.sh shuffle CWD in a way that
# breaks absolute-path resolution on the script arg. Passing basename from
# inside the correct cwd is the safest idiom.
cd "$OUR_SCRIPT_DIR"
exec "$HOME/IsaacSim/python.sh" "fly_drone.py" $HEADLESS
