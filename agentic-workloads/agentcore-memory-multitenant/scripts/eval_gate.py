#!/usr/bin/env python3
"""Lightweight "eval gate": runs the isolation test suite and the RAG eval
(scripts/rag_eval.py), and compares both against a recorded baseline.
Exits non-zero (blocking a release) if the isolation pass count drops, the
RAG score drops by more than RAG_TOLERANCE points, or the RAG eval sees any
cross-brand policy leak or request error. This is the gate logic a
real CI pipeline would call after deploy -- there is no actual CI system
wired up for this repo (Daniel will decide where this goes / what runs
it), but running this script IS a real, working gate, not a mock.

Usage:
    python3 scripts/eval_gate.py             # run + compare + exit code
    python3 scripts/eval_gate.py --update    # run + record new baseline
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = Path(__file__).resolve().parent / "eval_baseline.json"
# One of the 12 answer-key questions failing correctness costs ~4.2 points;
# allow that much LLM run-to-run variance, block on anything more.
RAG_TOLERANCE = 5.0

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rag_eval  # noqa: E402


def check_frontend_syntax() -> bool:
    """A broken webui/app.js is a silent, invisible-to-the-user failure
    (no JS runs at all, so the whole UI is dead), so the gate
    checks this before ever running the isolation suite."""
    js_path = ROOT / "webui" / "app.js"
    result = subprocess.run(["node", "--check", str(js_path)], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"GATE FAILED: {js_path} fails `node --check`:\n{result.stderr}")
        return False
    print(f"node --check {js_path.name}: OK")
    return True


def run_tests() -> dict:
    result = subprocess.run(
        ["python3", "-m", "pytest", "tests/test_isolation.py", "-q", "--no-header"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    passed = failed = 0
    for line in output.splitlines():
        line = line.strip()
        if line.endswith("passed") or " passed" in line:
            # pytest summary line like "26 passed in 12.3s" or "25 passed, 1 failed in ..."
            parts = line.replace(",", " ").split()
            for i, tok in enumerate(parts):
                if tok == "passed" and i > 0:
                    passed = int(parts[i - 1])
                if tok == "failed" and i > 0:
                    failed = int(parts[i - 1])
    return {"passed": passed, "failed": failed, "returncode": result.returncode, "raw_tail": output[-1500:]}


def gate_failures(current: dict, rag: dict, baseline: dict) -> list[str]:
    """Pure comparison logic (unit-tested in tests/test_eval_gate.py)."""
    failures = []
    if current["failed"] > baseline.get("failed", 0):
        failures.append(f"isolation: {current['failed']} failures > baseline {baseline.get('failed', 0)}")
    if current["passed"] < baseline.get("passed", 0):
        failures.append(f"isolation: {current['passed']} passed < baseline {baseline['passed']} (score regression)")
    if rag["leaks"]:
        failures.append(f"rag: {rag['leaks']} cross-brand policy leak(s)")
    if rag["errors"]:
        failures.append(f"rag: {rag['errors']} question(s) errored")
    if "rag_score" in baseline and rag["score"] < baseline["rag_score"] - RAG_TOLERANCE:
        failures.append(
            f"rag: score {rag['score']} dropped more than {RAG_TOLERANCE} below baseline {baseline['rag_score']}"
        )
    return failures


def main() -> None:
    update = "--update" in sys.argv
    if not check_frontend_syntax():
        sys.exit(1)
    print("Running isolation test suite for the eval gate (this takes a few minutes)...")
    current = run_tests()
    print(f"Isolation: passed={current['passed']} failed={current['failed']}")
    print("Running RAG eval (scripts/rag_eval.py) against the live site...")
    rag = rag_eval.run()
    print(f"RAG: score={rag['score']} leaks={rag['leaks']} errors={rag['errors']}")

    if update or not BASELINE_PATH.exists():
        BASELINE_PATH.write_text(
            json.dumps({"passed": current["passed"], "failed": current["failed"], "rag_score": rag["score"]}, indent=2)
            + "\n"
        )
        print(f"Baseline recorded: {BASELINE_PATH}")
        sys.exit(0 if current["failed"] == 0 and not rag["leaks"] and not rag["errors"] else 1)

    baseline = json.loads(BASELINE_PATH.read_text())
    print(f"Baseline: {baseline}")
    failures = gate_failures(current, rag, baseline)
    for f in failures:
        print(f"GATE FAILED: {f}")
    if failures:
        sys.exit(1)
    print("GATE PASSED: isolation and RAG scores did not regress.")
    sys.exit(0)


if __name__ == "__main__":
    main()
