#!/usr/bin/env bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

# Run the CI checks locally, the same commands .github/workflows/ci.yml runs, before a commit or a deploy.
#
#   ./deployment/check.sh                 # every project
#   ./deployment/check.sh backend mcp     # only these (backend agents mcp infra frontend e2e shell)
#
# Each project's output goes to $AIR_CHECK_LOGS/<project>.log (default .check-logs in the repository,
# which git ignores). The script prints PASS or FAIL per project and exits 0 only when every one passes.
# Needs: uv, Node.js 22 and npm; the frontend and mcp need `npm ci` run once.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOGS="${AIR_CHECK_LOGS:-$ROOT/.check-logs}"
mkdir -p "$LOGS"
ALL=(backend agents mcp infra frontend e2e shell)
if [ "$#" -gt 0 ]; then PROJECTS=("$@"); else PROJECTS=("${ALL[@]}"); fi

run() {
  case "$1" in
    backend) (cd "$ROOT/backend" && uv sync --frozen && uv run ruff check . && uv run mypy --strict src \
               && uv run pytest -q --cov --cov-branch) ;;
    agents) (cd "$ROOT/agents" && uv sync --frozen && uv run ruff check . && uv run mypy --strict src \
               && uv run pytest -q --cov=insights_radar_agents --cov-branch) ;;
    infra) (cd "$ROOT/infra" && uv sync --frozen && uv run ruff check . \
               && uv run mypy --strict insights_radar_infra tests app.py \
               && uv run pytest -q --cov=insights_radar_infra --cov-branch) ;;
    mcp) (cd "$ROOT/mcp" && npm run lint && npm run typecheck && npm test) ;;
    frontend) (cd "$ROOT/frontend" && npm run lint && npm test && npm run build) ;;
    e2e) (cd "$ROOT/e2e" && uv sync --frozen && uvx ruff@0.16.9 check . \
               && uv run --frozen --with mypy==2.3.1 mypy --strict --ignore-missing-imports --explicit-package-bases . \
               && uv run --frozen pytest --collect-only -q) ;;
    shell) (cd "$ROOT" && uvx --from shellcheck-py==0.10.0.1 shellcheck -x deployment/*.sh) ;;
    *) echo "unknown project $1 (one of: ${ALL[*]})"; return 2 ;;
  esac
}

FAILED=0
for p in "${PROJECTS[@]}"; do
  if run "$p" > "$LOGS/$p.log" 2>&1; then echo "PASS $p"; else echo "FAIL $p (see $LOGS/$p.log)"; FAILED=1; fi
done
exit "$FAILED"
