#!/usr/bin/env python3
"""End-to-end perception grader.

Runs a target Bedrock model THROUGH the bridge (bedrock-bridge --model <id>
--claude --print) to describe a committed, known image, then grades how close
the description is to the ground-truth annotation using a separate, direct
`claude -p` call (the judge bypasses the bridge on purpose: it must be a
trusted, fixed reference that stays independent of the system under test).

Exits nonzero if the score is below --threshold, so it can gate a PR. This is
NOT part of the pytest suite and never runs on pre-commit: it costs tokens,
needs the `claude` CLI, and is non-deterministic.

Usage:
  scripts/e2e_grade.py --model moonshotai.kimi-k2.5
  scripts/e2e_grade.py --model moonshotai.kimi-k2.5 --threshold 0.7 --json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures"
IMAGE = FIXTURES / "sample_01.jpg"
ANNOTATION = FIXTURES / "sample_01.annotation.md"

BRIDGE = os.environ.get("BEDROCK_BRIDGE_BIN") or shutil.which("bedrock-bridge") or "bedrock-bridge"
CLAUDE = shutil.which("claude") or "claude"

# Schema the judge must return. --json-schema forces Claude to emit exactly
# this shape, so parsing is reliable.
GRADE_SCHEMA = {
    "type": "object",
    "required": ["score", "covered", "missing", "hallucinated", "reasoning"],
    "additionalProperties": False,
    "properties": {
        "score": {"type": "number", "minimum": 0, "maximum": 1},
        "covered": {"type": "array", "items": {"type": "string"}},
        "missing": {"type": "array", "items": {"type": "string"}},
        "hallucinated": {"type": "array", "items": {"type": "string"}},
        "reasoning": {"type": "string"},
    },
}


def describe_via_bridge(model: str, timeout: int, vision_model: str | None = None) -> str:
    """Ask the target model, through the bridge, to describe the image.

    The image is copied to a randomly named temp file first so nothing in the
    path (e.g. a content word in the filename) hints at the answer; the model
    must rely solely on the image bytes.

    When vision_model is set, the main model runs through the describe_image
    path: it is text-only (or treated as such), and the bridge routes the image
    to the vision model on its behalf. This grades the side-channel end to end.
    """
    import shutil as _shutil
    import tempfile
    import uuid

    tmpdir = tempfile.mkdtemp(prefix="e2e_grade_")
    neutral = Path(tmpdir) / f"{uuid.uuid4().hex}{IMAGE.suffix}"
    _shutil.copyfile(IMAGE, neutral)
    prompt = (
        f"Look at the image file {neutral} and describe what you see in 3-4 "
        f"sentences. Name the specific subject and any colors or notable "
        f"features. Describe only what is actually visible."
    )
    cmd = [BRIDGE, "--model", model]
    if vision_model:
        cmd += ["--vision-model", vision_model]
    cmd += [
        "--claude",
        "--print",
        prompt,
        "--",
        "--dangerously-skip-permissions",
        "--no-session-persistence",
    ]
    try:
        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            cwd=REPO_ROOT,
        )
    finally:
        _shutil.rmtree(tmpdir, ignore_errors=True)
    out = p.stdout.decode(errors="replace").strip()
    if p.returncode != 0:
        raise RuntimeError(f"bridge run failed (rc={p.returncode}):\n{out}")
    return out


def grade(description: str, timeout: int) -> dict:
    """Score the description against the annotation via a direct claude judge."""
    annotation = ANNOTATION.read_text()
    judge_prompt = (
        "You are grading whether an image description matches a known "
        "ground-truth annotation. You will NOT see the image; rely only on "
        "the annotation as truth.\n\n"
        "Score 0.0 to 1.0 for how well the description matches. Weight "
        "hallucinations heavily: if the description asserts specifics that the "
        "annotation lists under 'Must-NOT-claim', or invents detail not "
        "supported by the annotation, that is a serious failure (a model "
        "confabulating contents it cannot actually see). A description that is "
        "vague but not wrong should score moderately; one that is accurate and "
        "specific should score high; one that hallucinates should score low.\n\n"
        f"=== GROUND-TRUTH ANNOTATION ===\n{annotation}\n\n"
        f"=== DESCRIPTION TO GRADE ===\n{description}\n"
    )
    cmd = [CLAUDE, "-p", judge_prompt, "--output-format", "json", "--json-schema", json.dumps(GRADE_SCHEMA)]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, timeout=timeout)
    raw = p.stdout.decode(errors="replace")
    if p.returncode != 0:
        raise RuntimeError(f"judge run failed (rc={p.returncode}):\n{raw}")
    envelope = json.loads(raw)
    # With --json-schema, `claude --output-format json` puts the schema-shaped
    # payload in `structured_output`; `result` holds the prose form. Prefer the
    # structured field, fall back to parsing result for older CLI versions.
    structured = envelope.get("structured_output")
    if isinstance(structured, dict):
        return structured
    result = envelope.get("result", envelope)
    if isinstance(result, str):
        result = json.loads(result)
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="E2E perception grader for a Bedrock model via the bridge.")
    ap.add_argument("--model", required=True, help="Bedrock model ID to test (the main model).")
    ap.add_argument(
        "--vision-model",
        default=None,
        help="Optional --vision-model ID. When set, the main model runs the "
        "describe_image path and the image is inspected by this model, grading "
        "the side channel end to end. Without it, --model must accept images.",
    )
    ap.add_argument(
        "--threshold", type=float, default=0.6, help="Minimum acceptable score (default: 0.6). Exit nonzero below this."
    )
    ap.add_argument("--describe-timeout", type=int, default=240)
    ap.add_argument("--judge-timeout", type=int, default=120)
    ap.add_argument("--json", action="store_true", help="Emit the full grade as JSON.")
    args = ap.parse_args()

    if not IMAGE.exists() or not ANNOTATION.exists():
        print(f"missing fixture(s) under {FIXTURES}", file=sys.stderr)
        return 2

    via = f"{args.model} (vision={args.vision_model})" if args.vision_model else args.model
    print(f"[1/2] describing {IMAGE.name} via bridge model {via} ...", file=sys.stderr)
    description = describe_via_bridge(args.model, args.describe_timeout, args.vision_model)
    print(f"\n--- model description ---\n{description}\n", file=sys.stderr)

    print("[2/2] grading against ground-truth annotation ...", file=sys.stderr)
    result = grade(description, args.judge_timeout)
    score = float(result.get("score", 0.0))

    if args.json:
        print(
            json.dumps(
                {
                    "model": args.model,
                    "vision_model": args.vision_model,
                    "description": description,
                    "threshold": args.threshold,
                    **result,
                },
                indent=2,
            )
        )
    else:
        print(f"\n=== grade for {args.model} ===")
        print(f"score:        {score:.2f}  (threshold {args.threshold:.2f})")
        print(f"covered:      {', '.join(result.get('covered', [])) or '-'}")
        print(f"missing:      {', '.join(result.get('missing', [])) or '-'}")
        print(f"hallucinated: {', '.join(result.get('hallucinated', [])) or '-'}")
        print(f"reasoning:    {result.get('reasoning', '')}")

    passed = score >= args.threshold
    print(
        f"\n{'PASS' if passed else 'FAIL'}: score {score:.2f} {'>=' if passed else '<'} threshold {args.threshold:.2f}",
        file=sys.stderr,
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
