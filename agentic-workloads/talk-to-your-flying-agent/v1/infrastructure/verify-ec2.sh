#!/usr/bin/env bash
set -euo pipefail

# Validate whether a fresh EC2 is ready to run the v1 stack.
#
# Usage:
#   ./verify-ec2.sh
#   ./verify-ec2.sh /path/to/repo

REPO_ROOT="${1:-}"
DEFAULT_PX4_DIR="$HOME/workspace/PX4-Autopilot"
DEFAULT_ISAAC_DIR="$HOME/IsaacSim"
PEGASUS_GLOB="$HOME/.local/share/ov/data/exts/v2/pegasus.simulator-*"

FAILS=0
WARNS=0

pass() {
  printf '[PASS] %s\n' "$1"
}

warn() {
  printf '[WARN] %s\n' "$1"
  WARNS=$((WARNS + 1))
}

fail() {
  printf '[FAIL] %s\n' "$1"
  FAILS=$((FAILS + 1))
}

check_file() {
  local path="$1"
  local label="$2"
  if [[ -e "$path" ]]; then
    pass "$label: $path"
  else
    fail "$label missing: $path"
  fi
}

check_cmd() {
  local name="$1"
  local label="$2"
  if command -v "$name" >/dev/null 2>&1; then
    pass "$label: $(command -v "$name")"
  else
    fail "$label missing: $name"
  fi
}

echo "== EC2 readiness check for v1 =="
echo

check_cmd python3 "Python 3"
check_cmd aws "AWS CLI"
check_cmd git "git"

check_file "$DEFAULT_ISAAC_DIR/python.sh" "Isaac Sim python launcher"
check_file "$DEFAULT_ISAAC_DIR/setup_ros_env.sh" "Isaac Sim ROS setup script"
check_file "/opt/ros/jazzy/setup.bash" "ROS 2 Jazzy setup"
check_file "$DEFAULT_PX4_DIR" "PX4 checkout"

shopt -s nullglob
pegasus_matches=($PEGASUS_GLOB)
shopt -u nullglob
if (( ${#pegasus_matches[@]} > 0 )); then
  pass "Pegasus extension: ${pegasus_matches[0]}"
else
  fail "Pegasus extension missing under ~/.local/share/ov/data/exts/v2/"
fi

if command -v MicroXRCEAgent >/dev/null 2>&1; then
  pass "MicroXRCEAgent: $(command -v MicroXRCEAgent)"
else
  warn "MicroXRCEAgent not on PATH. If sim launch later fails, install/build it."
fi

if aws sts get-caller-identity >/dev/null 2>&1; then
  pass "AWS credentials active"
else
  fail "AWS credentials inactive or missing"
fi

if [[ -n "$REPO_ROOT" ]]; then
  check_file "$REPO_ROOT/v1/README.md" "v1 repo root"
  check_file "$REPO_ROOT/v1/simulation/drone/scripts/run_simulation.sh" "simulation launcher"
  check_file "$REPO_ROOT/v1/app/backend/run-api.sh" "backend launcher"
  check_file "$REPO_ROOT/v1/app/backend/requirements.txt" "backend requirements"

  if [[ -x "$REPO_ROOT/v1/app/backend/.venv/bin/python" ]]; then
    pass "backend venv: $REPO_ROOT/v1/app/backend/.venv/bin/python"
  else
    warn "backend venv missing at $REPO_ROOT/v1/app/backend/.venv"
  fi
fi

echo
echo "== Summary =="
printf 'Fails: %d\n' "$FAILS"
printf 'Warnings: %d\n' "$WARNS"

if (( FAILS > 0 )); then
  exit 1
fi

exit 0
