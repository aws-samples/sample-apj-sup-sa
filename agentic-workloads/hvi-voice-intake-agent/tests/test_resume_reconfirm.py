"""Resumed calls re-verify identity (name, then DOB) before anything carried over.

AWS-free: loads the form from the seed JSON and drives the pure question-selection
functions, so it runs anywhere without DynamoDB. Covers the queue that routes a
resumed session (identityReconfirmQueue) back through A010 (name) then A030
(dob_verify) ahead of an inherited follow-up AND an inherited overlay, that the
queue drains name->DOB->done, that a first call is unchanged, AND that the resumed
call uses the SHORT "before we continue, can you confirm..." prompt rather than
replaying A010's full first-call greeting.

The name/DOB are re-verified by the SAME onFalse/onMismatch path a first call
uses (a wrong answer terminates), exercised by tests/test_agent_local.py and
tests/test_spoken_dates.py; this test covers the resume ROUTING and PHRASING only.

Run:  ./.venv/bin/python tests/test_resume_reconfirm.py
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "common"))
sys.path.insert(0, os.path.join(ROOT, "agent"))

import orchestrator as orch  # noqa: E402

with open(os.path.join(ROOT, "seed", "form-arogya-sanjeevani-v1.json")) as f:
    ITEMS = json.load(f)["items"]

failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + ("" if cond else f"  <{detail}>"))
    if not cond:
        failures.append(label)


def _ref(desc: dict | None) -> str | None:
    return (desc or {}).get("meta", {}).get("itemId") or (desc or {}).get("ref")


def _prompt(desc: dict | None) -> str:
    return (desc or {}).get("meta", {}).get("_reconfirmPromptText", "") or ""


def resumed(queue):
    """A resumed session carrying identity answers from the prior leg PLUS an
    inherited follow-up and overlay (the two things that outrank a form item),
    with the given identity-reconfirm queue."""
    return {
        "identityReconfirmQueue": list(queue),
        "languageConfirmed": True, "language": "en",
        "customerName": "Asha", "dateOfBirth": "1987-03-22",
        "answers": {"A010": True, "A020": True, "A030": "1987-03-22", "A100": 170},
        "pendingFollowUps": [{"questionId": "FU_DIAG_WHEN", "prompt": "when?",
                              "answerType": "text", "icd10": "E11"}],
        "awaitingConfirmation": None,
        "summaryAskPending": True,
        "followUpAnswers": {},
    }


def main() -> int:
    full = resumed(["A010", "A030"])

    print("[1] resumed call asks NAME (A010) first, ahead of follow-up and overlay")
    d = orch._identity_reconfirm_descriptor(full, ITEMS, "en")
    check("descriptor head is A010", _ref(d) == "A010", f"got {_ref(d)}")
    check("next_question -> A010", _ref(orch.next_question(full, ITEMS, "en")) == "A010")
    check("_pending_opening_question -> A010",
          _ref(orch._pending_opening_question(full, ITEMS, {}, "en")) == "A010")

    print("[2] A010 uses the short 'before we continue' prompt, not the full greeting")
    p = _prompt(d)
    check("says 'before we continue'", "before we continue" in p.lower(), f"got {p!r}")
    check("names the caller", "Asha" in p, f"got {p!r}")
    check("does NOT replay the first-call greeting",
          "medical information call" not in p.lower(), f"got {p!r}")

    print("[3] after NAME drains, DOB (A030) is next, still ahead of everything")
    dob = resumed(["A030"])
    d2 = orch._identity_reconfirm_descriptor(dob, ITEMS, "en")
    check("next_question -> A030", _ref(orch.next_question(dob, ITEMS, "en")) == "A030")
    check("A030 uses a short DOB re-confirm prompt",
          "date of birth" in _prompt(d2).lower(), f"got {_prompt(d2)!r}")

    print("[4] once the queue drains, resume proceeds at what it owed")
    done = resumed([])
    nq = orch.next_question(done, ITEMS, "en")
    check("drained -> not an identity item", _ref(nq) not in ("A010", "A030"), f"got {_ref(nq)}")
    check("drained -> the inherited follow-up", (nq or {}).get("kind") == "followup",
          f"got {(nq or {}).get('kind')}")

    print("[5] a first call is unchanged: identity NOT re-asked once answered")
    first = {
        "languageConfirmed": True, "language": "en", "dateOfBirth": "1987-03-22",
        "customerName": "Asha", "answers": {"A010": True, "A020": True,
                                              "A030": "1987-03-22", "A100": 170},
        "pendingFollowUps": [], "awaitingConfirmation": None, "followUpAnswers": {},
    }
    check("first call -> A110", _ref(orch.next_question(first, ITEMS, "en")) == "A110")

    print("\n" + ("All checks passed." if not failures
                  else f"{len(failures)} FAILED: {failures}"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
