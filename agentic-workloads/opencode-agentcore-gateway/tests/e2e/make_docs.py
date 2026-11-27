#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Regenerate the subfeature table in docs/testing.md from scenarios.py (run after changing the matrix)."""
import collections
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import scenarios as S  # noqa: E402

AREAS = {"F1": "Identity and tokens", "F2": "Client, proxy and opencode", "F3": "Model calls", "F4": "Model allow-list",
         "F5": "Budgets", "F6": "Rate limits", "F7": "Fallback", "F8": "Request hardening", "F9": "Web search",
         "F10": "Attribution", "F11": "Dashboard", "F12": "Infrastructure"}
START, END = "<!-- matrix:start -->", "<!-- matrix:end -->"


def table():
    sc = S.scenarios()
    by = collections.OrderedDict()
    for s in sc:
        by.setdefault(s.feature, collections.Counter())[s.idp] += 1
    rows = ["| Area | Subfeature | Kind | Cognito | Okta | JumpCloud | Total |", "|---|---|---|---:|---:|---:|---:|"]
    kinds = {s.feature: s.kind for s in sc}
    for f in sorted(by, key=lambda x: (int(x.split(".")[0][1:]), x)):
        c = by[f]
        rows.append(f"| {AREAS[f.split('.')[0]]} | `{f}` | {kinds[f]} | {c['cognito']} | {c['okta']} | {c['jumpcloud']} | {sum(c.values())} |")
    rows.append(f"| | **{len(by)} subfeatures** | | | | | **{len(sc)}** |")
    return "\n".join(rows)


def main():
    doc = HERE.parents[1] / "docs" / "testing.md"
    text = doc.read_text()
    a, b = text.index(START) + len(START), text.index(END)
    doc.write_text(text[:a] + "\n" + table() + "\n" + text[b:])
    print(f"updated {doc}")


if __name__ == "__main__":
    main()
