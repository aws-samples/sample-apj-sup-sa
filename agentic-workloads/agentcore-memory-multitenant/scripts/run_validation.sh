#!/usr/bin/env bash
# One-command validation runner: runs the full pytest suite (isolation, KB,
# Runtime, web UI, agent e2e), prints a pass/fail summary, and exits
# non-zero on failure.
#
# Usage: ./scripts/run_validation.sh [pytest extra args...]
set -uo pipefail
cd "$(dirname "$0")/.."

source .venv/bin/activate 2>/dev/null || { echo "ERROR: .venv not found. Run from the repo root after creating the venv."; exit 1; }

echo "=== AgentCore multi-tenant memory demo: validation suite ==="
echo "Gateway/Memory/IAM state: deploy/state.json"
echo

python3 -m pytest tests/ -v "$@"
code=$?

echo
if [ $code -eq 0 ]; then
  echo "=== RESULT: ALL VALIDATION CHECKS PASSED ==="
else
  echo "=== RESULT: VALIDATION FAILED (exit code $code) ==="
fi
exit $code
