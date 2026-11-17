#!/usr/bin/env bash
# Local checks. No AWS calls: unit tests stub every client with botocore Stubber.
#   python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
#   PYTHON=.venv/bin/python scripts/check.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python3}"

echo "== python syntax"
"$PYTHON" -m py_compile lambda/*.py tests/*.py
find lambda tests -name __pycache__ -type d -exec rm -rf {} +

echo "== ruff (default rules)"
"$PYTHON" -m ruff check --no-cache lambda/ tests/

echo "== pytest"
"$PYTHON" -m pytest -q -p no:cacheprovider tests/

echo "== cfn-lint"
CFN_LINT="$(dirname "$PYTHON")/cfn-lint"
[[ -x "$CFN_LINT" ]] || CFN_LINT=cfn-lint
"$CFN_LINT" cloudformation/*.yaml

echo "== shell syntax"
bash -n scripts/deploy.sh scripts/deploy-target-role.sh scripts/check.sh scripts/render-state-machine.sh scripts/teardown.sh

echo "All checks passed"
