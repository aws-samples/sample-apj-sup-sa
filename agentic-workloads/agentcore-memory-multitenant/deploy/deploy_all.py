"""Orchestrates the full deploy pipeline, in order. Idempotent: safe to
re-run; each step checks for existing resources by name before creating.
Account and region come from the caller's AWS credentials (AWS_REGION /
AWS_DEFAULT_REGION, default us-east-1); every resource id the steps create
is written to the gitignored deploy/state.json.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STEPS = [
    ["python3", "deploy/01_cognito.py"],
    ["python3", "deploy/02_iam.py"],
    ["python3", "deploy/03_memory.py"],
    ["bash", "deploy/04_build_lambdas.sh"],
    ["python3", "deploy/05_lambdas.py"],
    ["python3", "deploy/06_gateway.py"],
    ["python3", "deploy/07_gateway_targets.py"],
    ["python3", "deploy/11_guardrail.py"],
    ["python3", "deploy/09_webui_backend.py"],
    ["python3", "deploy/10_webui_cdn.py"],
    ["python3", "deploy/13_abuse_controls.py"],
    ["python3", "deploy/14_knowledge_base.py"],
    ["python3", "deploy/15_runtime.py"],
    # Observability last: it covers every resource above (incl. the Runtime).
    ["python3", "deploy/12_observability.py"],
]


def main() -> None:
    for step in STEPS:
        print(f"\n{'=' * 70}\n>>> {' '.join(step)}\n{'=' * 70}", flush=True)
        result = subprocess.run(step, cwd=ROOT)
        if result.returncode != 0:
            print(f"\nDeploy FAILED at step: {' '.join(step)} (exit {result.returncode})", file=sys.stderr)
            sys.exit(result.returncode)
    print("\nDeploy complete. See deploy/state.json for all resource IDs/ARNs.")
    print("Next: ./scripts/run_validation.sh")


if __name__ == "__main__":
    main()
