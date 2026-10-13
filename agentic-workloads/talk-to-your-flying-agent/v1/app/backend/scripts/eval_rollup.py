#!/usr/bin/env python3
"""Scan every eval.md under a sessions/ root, tally verdicts, print a summary.

Usage:
  python3 eval_rollup.py <sessions_root>
  python3 eval_rollup.py test/sessions

Each eval.md is expected to have a `**verdict:**` line. The reviewer fills it
in manually by editing eval.md (see scripts/eval_report.py). This script just
aggregates.

Verdicts recognised (case-insensitive):
  TP / FP / TN / FN / INCONCLUSIVE

Anything else (including the untouched `TP / FP / TN / FN / INCONCLUSIVE`
placeholder) is counted as UNSCORED.

Stdlib only — no deps.
"""

import re
import sys
from pathlib import Path

VERDICTS = ("TP", "FP", "TN", "FN", "INCONCLUSIVE")
VERDICT_RE = re.compile(r"^\s*[-*]?\s*\*\*verdict[:\*]+\s*`?([A-Z]+)`?", re.IGNORECASE)


def extract_verdict(eval_md: Path) -> str:
    """Return the first recognised verdict, or 'UNSCORED' if not filled."""
    try:
        for raw in eval_md.read_text().splitlines():
            m = VERDICT_RE.match(raw)
            if not m:
                continue
            token = m.group(1).upper()
            # Skip the placeholder line itself: "TP / FP / TN / FN / INCONCLUSIVE"
            # That line has all five words — its first match is TP but we also
            # see "/" after → heuristic: if there are slashes on the line, it's
            # the template.
            if "/" in raw:
                return "UNSCORED"
            if token in VERDICTS:
                return token
            return "UNSCORED"
    except Exception as e:
        print(f"[warn] {eval_md}: {e}", file=sys.stderr)
    return "UNSCORED"


def main():
    if len(sys.argv) != 2:
        print("usage: eval_rollup.py <sessions_root>", file=sys.stderr)
        sys.exit(2)

    root = Path(sys.argv[1]).expanduser().resolve()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        sys.exit(1)

    eval_files = sorted(root.glob("*/eval.md"))
    if not eval_files:
        print(f"no eval.md files under {root}", file=sys.stderr)
        sys.exit(1)

    counts = {v: 0 for v in VERDICTS}
    counts["UNSCORED"] = 0
    rows = []

    for path in eval_files:
        v = extract_verdict(path)
        counts[v] = counts.get(v, 0) + 1
        rows.append((path.parent.name, v))

    print(f"Eval rollup — {root}")
    print("=" * 60)
    for sid, v in rows:
        print(f"  {sid:40s}  {v}")
    print()

    total = sum(counts.values())
    scorable = counts["TP"] + counts["FP"] + counts["TN"] + counts["FN"]
    tp = counts["TP"]

    print(f"Total runs:          {total}")
    print(f"  TP (good success): {counts['TP']}")
    print(f"  FP (false succ):   {counts['FP']}")
    print(f"  TN (good giveup):  {counts['TN']}")
    print(f"  FN (wrong giveup): {counts['FN']}")
    print(f"  INCONCLUSIVE:      {counts['INCONCLUSIVE']}")
    print(f"  UNSCORED:          {counts['UNSCORED']}")
    print()

    if scorable == 0:
        print("No scored runs yet. Open each eval.md and fill in **verdict:** `<letter>`.")
        return

    pct = 100.0 * tp / scorable
    target = 70.0
    mark = "✓" if pct >= target else "✗"
    print(f"Scorable runs (excl INCONCLUSIVE/UNSCORED): {scorable}")
    print(f"TP / Scorable = {tp}/{scorable} = {pct:.1f}%  (target {target:.0f}%)  {mark}")


if __name__ == "__main__":
    main()
