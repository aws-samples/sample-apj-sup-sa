#!/usr/bin/env python3
"""A caller who does not understand a follow-up gets explained to, not repeated at.

WHY THIS EXISTS
---------------
From a live Hindi call (call-203d24cb, 51 turns). The caller disclosed
schizophrenia, and three separate defects stacked into a dead end:

  1. "स्किजोफ्रेनिया" was not in the concept index, so nearest-neighbour handed
     the disclosure to F21 Schizotypal disorder - a DIFFERENT diagnosis, on a
     medical record an underwriter reads. Fixed in seed/icd10-reference.json by
     giving F20 its Devanagari aliases; guarded by tests/diag_devanagari_recall.py
     and tests/diag_surface_form_sweep.py, which hit the live index.

  2. F21 has no curated protocol, so the default protocol ran and named the
     condition from the caller's RAW words: "यह पहली बार कब पता चला था —
     स्किजोफ्रेनिया है मुझे के लिए?" ("...for 'I have schizophrenia'?"). That is
     section [1] and [2] here.

  3. The caller's "हाँ" and then "What was that?" were read as a bad answer and a
     repeat request, so the agent re-asked the SAME confusing question three
     times and gave up silently. The ladder is now explain -> re-ask -> skip,
     and anything skipped is promised to a human at the end. Sections [3]-[6].

AWS-free: pure question-phrasing functions plus the in-memory harness from
tests/test_dialogue_state_model.py, so it runs anywhere without DynamoDB, the
graph, or the classifier.

Run:  ./.venv/bin/python tests/test_stuck_followup_recovery.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "common"))
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

import orchestrator as orch  # noqa: E402

failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + ("" if cond else f"  <{detail}>"))
    if not cond:
        failures.append(label)


def _sess(icd10: str, phrase: str) -> dict:
    return {"declaredConditions": [{"icd10": icd10, "patientPhrase": phrase}]}


print("[1] _caller_condition_term strips the copula/possessive wrapper")
# The caller's own word is PREFERRED over the clinical label on purpose - an
# English label read aloud mid-Hindi-sentence means nothing to them - so the
# wrapper they say it in ("... है मुझे") is what has to come off, not the
# preference. Anchored on whitespace, so a name that merely ends in these
# letters is left alone.
cases = [
    ("F20", "स्किजोफ्रेनिया है मुझे", "स्किजोफ्रेनिया"),
    ("E11", "हाँ, डायबिटीज है", "डायबिटीज"),
    ("E11", "मुझे शुगर है", "शुगर"),
    ("I10", "बीपी है ना", "बीपी"),
    ("E78", "cholesterol", "cholesterol"),         # unchanged
    ("J45", "अस्थमा", "अस्थमा"),                     # unchanged, no wrapper
]
for icd10, phrase, expected in cases:
    got = orch._caller_condition_term(_sess(icd10, phrase), icd10)
    check(f"{phrase!r} -> {expected!r}", got == expected, f"got {got!r}")


print("\n[2] _name_condition names the clean term, not the whole sentence")
fu = {"prompt": "यह पहली बार कब पता चला था?", "conditionLabel": "Schizophrenia",
      "icd10": "F20"}
sess = _sess("F20", "स्किजोफ्रेनिया है मुझे")
named = orch._name_condition(fu, "hi", sess)
check("hi prompt names स्किजोफ्रेनिया", "स्किजोफ्रेनिया" in named, named)
check("hi prompt drops the 'है मुझे' wrapper", "है मुझे" not in named, named)
check("hi prompt keeps the '... के लिए?' shape", "के लिए?" in named, named)


print("\n[3] _explain_then_reask uses the authored explanation when there is one")
orig = orch.tools.get_condition_explanation
try:
    orch.tools.get_condition_explanation = lambda icd10, language="en": {
        "explanation": "डायबिटीज़ में खून में शक्कर की मात्रा बढ़ जाती है।"}
    asked = {"kind": "followup", "answerType": "text",
             "text": "आप इसके लिए क्या इलाज ले रहे हैं?",
             "meta": {"icd10": "E11", "prompt": "आप इसके लिए क्या इलाज ले रहे हैं?",
                      "questionId": "FU_TREATMENT"}}
    r = orch._explain_then_reask("c1", asked, "hi", 4, [], [])
    utt = r["agentUtterance"]
    check("explanation is spoken first", utt.startswith("डायबिटीज़"), utt)
    check("the question is re-asked after it", "क्या इलाज ले रहे हैं" in utt, utt)
    check("reply still targets the follow-up", r["askedKind"] == "followup"
          and r["askedRef"] == "FU_TREATMENT", str(r.get("askedRef")))

    # A reference-only code (F20 is identifiable, not governed) has no authored
    # lay explanation, and the graph raises rather than returning empty. The
    # caller must still get more than the same sentence again.
    print("\n[4] _explain_then_reask degrades gracefully when there is no text")

    def _boom(icd10, language="en"):
        raise RuntimeError("no graph node for F20 (reference-only)")

    orch.tools.get_condition_explanation = _boom
    asked2 = {"kind": "followup", "answerType": "text",
              "text": "यह पहली बार कब पता चला था — स्किजोफ्रेनिया के लिए?",
              "meta": {"icd10": "F20", "prompt": "यह पहली बार कब पता चला था?",
                       "questionId": "diagnosed_when"}}
    r2 = orch._explain_then_reask("c1", asked2, "hi", 4, [], [])
    utt2 = r2["agentUtterance"]
    check("falls back to a plain-language lead", "आसान शब्दों" in utt2, utt2)
    check("still re-asks the question", "स्किजोफ्रेनिया के लिए?" in utt2, utt2)
finally:
    orch.tools.get_condition_explanation = orig


print("\n[5] the state layer exposes add_human_followup")
check("orchestrator's st has add_human_followup",
      hasattr(orch.st, "add_human_followup"))


print("\n[6] the closing promises a human callback iff questions were skipped")
# The promise must be TRUE in both directions: a call that captured everything
# must not tell the caller someone will ring them back about nothing.
from test_dialogue_state_model import (  # noqa: E402
    FakeTools,
    MemoryState,
    ScriptedExtract,
)

_o = (orch.st, orch.tools, orch.extract)
_state = MemoryState()
orch.st, orch.tools, orch.extract = _state, FakeTools(_state), ScriptedExtract()
try:
    cid, _ = _state.reset([], language="en", languageConfirmed=True,
                          answers={"A010": True})
    _state.sessions[cid]["humanFollowUpItems"] = [
        {"ref": "diagnosed_when", "label": "Schizophrenia"}]
    utt = orch._complete(cid, _state.sessions[cid], 9, [],
                         confirmed=True)["agentUtterance"]
    check("skipped -> closing says a person will call",
          "someone from our team" in utt, utt)

    cid2, _ = _state.reset([], language="en", languageConfirmed=True,
                           answers={"A010": True})
    _state.sessions[cid2]["humanFollowUpItems"] = []
    utt2 = orch._complete(cid2, _state.sessions[cid2], 9, [],
                          confirmed=True)["agentUtterance"]
    check("none skipped -> no human-callback line",
          "someone from our team" not in utt2, utt2)
finally:
    orch.st, orch.tools, orch.extract = _o


print()
if failures:
    print(f"{len(failures)} check(s) FAILED: {failures}")
    sys.exit(1)
print("Explain-then-reask-then-skip recovery holds, and the closing is truthful.")
