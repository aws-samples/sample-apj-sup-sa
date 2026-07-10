#!/usr/bin/env bash
# Run all three test suites (batch / benchmark / sample-data)
# from the repo root. Exits non-zero on the first failure.
#
# Each subpackage has its own conftest.py + pytest config, so they can't be
# collected together — this script runs them sequentially with the shared
# .venv at the repo root.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${PYTHON:-${REPO_ROOT}/.venv/bin/python}"

if [[ ! -x "${PYTHON}" ]]; then
  echo "no venv at ${PYTHON}; create one with python3.11 -m venv .venv && source .venv/bin/activate && pip install -e batch -e benchmark"
  exit 1
fi

run_suite() {
  local label="$1"
  local dir="$2"
  echo "=== ${label} (${dir}) ==="
  ( cd "${REPO_ROOT}/${dir}" && "${PYTHON}" -m pytest tests/ -q )
}

run_suite "batch"          "batch"
run_suite "benchmark"      "benchmark"
run_suite "sample-data"    "sample-data"

echo
echo "All three suites passed."
