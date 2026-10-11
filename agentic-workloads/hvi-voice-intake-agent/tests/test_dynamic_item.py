#!/usr/bin/env python
"""
Proves a health question added at runtime is live on the next call, with no
redeployment, no container rebuild and no code change.

This is the "new health items must be addable dynamically" requirement. The test
adds an item, checks it appears in the live schema read and is correctly gated by
its parent condition, then removes it again so the suite is repeatable.

Run:  ./.venv/bin/python tests/test_dynamic_item.py
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "common"))
import hvi_state as st  # noqa: E402

FORM_ID = os.environ.get("FORM_ID", "arogya-sanjeevani-v1")
NEW_ITEM = "A455"
PY = os.path.join(ROOT, ".venv", "bin", "python")
ADMIN = os.path.join(ROOT, "tools", "form_admin.py")

failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{'' if cond else '  <- ' + detail}")
    if not cond:
        failures.append(label)


def admin(*args: str) -> int:
    r = subprocess.run([PY, ADMIN, *args], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"        admin failed: {r.stderr.strip()[:200]}")
    return r.returncode


def main() -> int:
    print(f"\n[1] Baseline schema for {FORM_ID}")
    before = st.get_form_items(FORM_ID)
    print(f"        {len(before)} active items")
    check("baseline schema loads", len(before) >= 29, str(len(before)))
    check(f"{NEW_ITEM} not present yet",
          NEW_ITEM not in [i["itemId"] for i in before])

    print(f"\n[2] Add {NEW_ITEM} through the admin CLI, nothing else touched")
    rc = admin(
        "add-item", "--form-id", FORM_ID,
        "--item-id", NEW_ITEM, "--order", "455",
        "--section", "medications", "--label", "uses_insulin",
        "--answer-type", "boolean", "--required",
        "--prompt", "Do you use insulin for it?",
        "--show-if", '{"itemId":"A250","operator":"eq","value":true}',
        "--parent-item-id", "A250",
    )
    check("admin CLI succeeded", rc == 0)

    print("\n[3] A fresh schema read sees it immediately")
    after = st.get_form_items(FORM_ID)
    ids = [i["itemId"] for i in after]
    check(f"{NEW_ITEM} is live", NEW_ITEM in ids, str(ids[-6:]))
    check("item count grew by one", len(after) == len(before) + 1,
          f"{len(after)} vs {len(before)}")

    item = next((i for i in after if i["itemId"] == NEW_ITEM), None)
    check("ordered after the diabetes question",
          item is not None and 250 < int(item["order"]) < 500,
          str(item and item.get("order")))

    print("\n[4] Gated correctly by its parent condition")
    if item:
        # Parent unanswered: a conditional child cannot show.
        check("hidden before A250 is answered", st.show_item(item, {}) is False)
        # Parent false: still hidden.
        check("hidden when A250 is no", st.show_item(item, {"A250": False}) is False)
        # Parent true: now due.
        check("shown when A250 is yes", st.show_item(item, {"A250": True}) is True)

        print("\n[5] It is genuinely reached by the sequencer, not just visible")
        # Answer everything up to and including the diabetes question, saying yes.
        answers: dict = {}
        guard = 0
        while guard < 60:
            guard += 1
            nxt = st.next_item(after, answers)
            if nxt is None or nxt["itemId"] == NEW_ITEM:
                break
            t = nxt.get("answerType")
            if nxt["itemId"] == "A250":
                answers[nxt["itemId"]] = True          # declare diabetes
            elif t == "boolean":
                answers[nxt["itemId"]] = False
            elif t == "number":
                answers[nxt["itemId"]] = 165
            elif t == "enum":
                answers[nxt["itemId"]] = (nxt.get("options") or ["never"])[0]
            elif t == "list":
                answers[nxt["itemId"]] = ["none"]
            else:
                answers[nxt["itemId"]] = "none"
        reached = st.next_item(after, answers)
        check("sequencer reaches the new item",
              reached is not None and reached["itemId"] == NEW_ITEM,
              str(reached and reached["itemId"]))

    print("\n[6] Disabling removes it from the flow at once")
    rc = admin("disable", "--form-id", FORM_ID, "--item-id", NEW_ITEM)
    check("disable succeeded", rc == 0)
    final = st.get_form_items(FORM_ID)
    check(f"{NEW_ITEM} gone from the active set",
          NEW_ITEM not in [i["itemId"] for i in final],
          str([i["itemId"] for i in final][-6:]))
    check("back to the baseline count", len(final) == len(before),
          f"{len(final)} vs {len(before)}")

    print("\n" + "=" * 66)
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("A question added at runtime is live on the next call. No redeploy.")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
