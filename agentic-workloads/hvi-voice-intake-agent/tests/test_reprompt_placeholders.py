#!/usr/bin/env python3
"""A schema placeholder must never be spoken to a caller.

THE LIVE EVIDENCE
-----------------
From a real call against the deployed agent, the identity question was re-asked
and the caller heard, verbatim:

    "That doesn't seem right - I may have misheard. Sorry, I did not catch that.
     Am I speaking with {{customerName}}?"

seed/form-arogya-sanjeevani-v1.json gives item A010 a reprompt of
"Sorry, I did not catch that. Am I speaking with {{customerName}}?". _reask read
that straight off the item, while only asked["text"] had been through
st.render_prompt. On the English path meta["reprompt"] exists, so it won the `or`
and reached the caller unfilled. Hindi escaped by accident: the schema defines no
repromptHi, so it fell through to the already-rendered asked["text"].

WHY A DEDICATED SUITE. The class of bug is "schema text reached the caller without
being rendered", and the reprompt was simply the second such path to exist. So
this checks the mechanism (one shared token table, and a refusal to speak braces)
rather than only the one string that was reported.

AWS-free.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "agent" / "common"))

import hvi_state as st  # noqa: E402
import orchestrator as orch  # noqa: E402

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"   {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


CUSTOMER = {
    "customerName": "Priya Sharma",
    "customerEmail": "priya@example.com",
    "policyNumber": "AS-2026-0098421",
    "insurerName": "Meridian Health Cover",
}

A010 = {
    "itemId": "A010",
    "prompt": "Hello, this is {{insurerName}} about policy {{policyNumber}}. "
              "Am I speaking with {{customerName}}?",
    "reprompt": "Sorry, I did not catch that. Am I speaking with {{customerName}}?",
    "answerType": "boolean",
}


def _asked(meta, language):
    return {"kind": "item", "meta": meta, "answerType": meta.get("answerType"),
            "text": st.render_prompt(meta, language, CUSTOMER), "ref": meta["itemId"]}


def main() -> int:
    print("[1] fill_placeholders covers every token the schema actually uses")
    used = set()
    for path in sorted((ROOT / "seed").glob("form-*.json")):
        blob = json.dumps(json.load(open(path, encoding="utf-8")))
        for token in {t for t in blob.split("{{")[1:]}:
            name = token.split("}}")[0]
            if name and len(name) < 40:
                used.add("{{" + name + "}}")
    print(f"      tokens in the seed forms: {sorted(used)}")
    for token in sorted(used):
        filled = st.fill_placeholders(f"x {token} y", CUSTOMER)
        check(f"{token} is substituted", "{{" not in filled, filled)

    print("\n[2] THE REPORTED BUG: the English re-ask of A010")
    asked = _asked(A010, "en")
    reply = orch._reask("c1", asked, "en", "", 3, [], "test",  # noqa: SLF001
                        customer=CUSTOMER)
    said = reply["agentUtterance"]
    print(f"      said: {said!r}")
    check("no braces reach the caller", "{{" not in said and "}}" not in said, said)
    check("the customer's real name is spoken", "Priya Sharma" in said, said)
    check("the SIMPLER reprompt is still used, not the full greeting",
          "Sorry, I did not catch that" in said and "Hello, this is" not in said,
          said)

    print("\n[3] the Hindi path, which escaped only by accident, still works")
    hi_item = dict(A010, promptHi="नमस्ते, क्या मैं {{customerName}} से बात कर रही हूँ?")
    asked = _asked(hi_item, "hi")
    said = orch._reask("c1", asked, "hi", "", 3, [], "test",  # noqa: SLF001
                       customer=CUSTOMER)["agentUtterance"]
    check("no braces in Hindi either", "{{" not in said, said)
    check("name present in Hindi", "Priya Sharma" in said, said)

    print("\n[4] an item with a placeholder-free reprompt is untouched")
    plain = {"itemId": "A100", "prompt": "What is your height?",
             "reprompt": "Your height, in feet and inches or in centimetres?",
             "answerType": "number"}
    said = orch._reask("c1", _asked(plain, "en"), "en", "", 3, [], "why",  # noqa: SLF001
                       customer=CUSTOMER)["agentUtterance"]
    check("the plain reprompt is spoken as authored",
          "feet and inches" in said, said)

    print("\n[5] THE GUARD: an UNKNOWN token must not be read out either")
    # A token fill_placeholders does not know about. The caller must not hear
    # braces, so _reask falls back to the rendered question.
    rogue = {"itemId": "A999", "prompt": "Confirm your address, {{customerName}}?",
             "reprompt": "Is your address still {{homeAddress}}?",
             "answerType": "text"}
    asked = _asked(rogue, "en")
    said = orch._reask("c1", asked, "en", "", 3, [], "why",  # noqa: SLF001
                       customer=CUSTOMER)["agentUtterance"]
    print(f"      said: {said!r}")
    check("an unknown token never reaches the caller", "{{" not in said, said)
    check("it fell back to the rendered question",
          "Confirm your address" in said, said)

    print("\n[6] no customer passed at all: still refuses to speak a template")
    said = orch._reask("c1", _asked(A010, "en"), "en", "", 3, [], "why")[  # noqa: SLF001
        "agentUtterance"]
    check("braces suppressed with no customer", "{{" not in said, said)

    print("\n[7] render_prompt still behaves, since it now delegates")
    rendered = st.render_prompt(A010, "en", CUSTOMER)
    check("prompt fully rendered",
          "{{" not in rendered and "Priya Sharma" in rendered
          and "Meridian Health Cover" in rendered, rendered)
    check("a missing customer field becomes empty, not a literal token",
          "{{" not in st.render_prompt(A010, "en", {}),
          st.render_prompt(A010, "en", {}))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) FAILED")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("Schema placeholders are filled from one shared table, and no path "
          "speaks an unfilled template.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
