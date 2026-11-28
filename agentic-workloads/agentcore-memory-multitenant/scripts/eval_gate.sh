#!/usr/bin/env bash
# "CI-style" eval-gate script: runs the isolation test suite and the RAG
# eval, and FAILS (non-zero exit) if the isolation pass count or the RAG
# score drops below the recorded baseline in scripts/eval_baseline.json, or
# the RAG eval sees a cross-brand leak. A real CI pipeline would
# call this after deploy and block the release on non-zero exit; there is
# no actual CI system wired up for this repo, but the gate logic itself is
# real and runnable.
set -uo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate

python3 scripts/eval_gate.py "$@"
exit $?
