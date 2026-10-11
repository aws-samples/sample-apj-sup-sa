#!/usr/bin/env python3
"""Generated non-telephone conversation-class and interaction proof.

This suite runs the production orchestrator with deterministic in-memory state.
It exhausts the declared semantic classes that cannot be exhaustively enumerated
as raw human sentences: confirmation/correction meanings, scoped multi-condition
mentions, all resume-overlay combinations, high-risk interrupts, and safety
invariants after each transition.
"""
from __future__ import annotations

import copy
import itertools
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "common"))
sys.path.insert(0, str(ROOT / "tests"))

import orchestrator as orch  # noqa: E402
from test_dialogue_state_model import (  # noqa: E402
    ITEMS,
    FakeTools,
    MemoryState,
    ScriptedExtract,
    reading,
)

failures: list[str] = []
checks = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not condition:
        failures.append(label)
        print(f"  FAIL  {label} <- {detail}")


def stable(result: dict) -> bool:
    return all(key in result for key in (
        "agentUtterance", "turnIndex", "isComplete", "escalated",
        "stopQuestionnaire", "language", "toolTrace", "notes",
    ))


def main() -> int:  # noqa: PLR0915
    original_state, original_tools, original_extract = orch.st, orch.tools, orch.extract
    state = MemoryState()
    tools = FakeTools(state)
    extract = ScriptedExtract()
    orch.st, orch.tools, orch.extract = state, tools, extract
    item = next(row for row in ITEMS if row["itemId"] == "A100")
    identity = {"A010": True, "A020": True, "A030": "1987-03-22"}

    try:
        print("[1] Summary confirmation and correction semantics")
        confirmations = (
            "no", "nope", "correct", "all correct", "no, all correct",
            "nothing else", "nothing to add", "nothing else to change",
            "sab sahi", "sab theek", "kuch nahi", "bas", "सब सही",
            "कुछ नहीं", "ठीक है",
        )
        corrections = (
            "no wait, my weight is 68", "all correct except my weight is 68",
            "nothing else, but I also have asthma", "that's right, however add diabetes",
            "sab sahi lekin weight 68 hai", "सब सही लेकिन वजन 68 है",
            "actually my height is 170", "I meant 2019, not 2018",
            "one more thing, I have asthma", "please change the medicine",
            "wrong, my answer was no", "remove smoking from the record",
        )
        for utterance in confirmations:
            check(f"confirmation accepted: {utterance}", orch.is_all_correct(utterance))
        for utterance in corrections:
            check(f"correction never finalises: {utterance}",
                  not orch.is_all_correct(utterance))

        call_id, session = state.reset([], summaryAskPending=True, summaryAskCount=1)
        tools.reset()
        repeated = orch.handle_turn(call_id, "please repeat the summary", 1, "en")
        check("summary repeat does not consume correction round",
              session["summaryAskPending"] is True
              and session["summaryAskCount"] == 1
              and repeated.get("askedKind") == "summaryConfirm"
              and "submit_to_crm" not in tools.calls,
              f"session={session}, result={repeated}, calls={tools.calls}")

        call_id, session = state.reset([], summaryAskPending=True, summaryAskCount=1)
        tools.reset()
        extract.push(reading("my weight is 68"))
        corrected = orch.handle_turn(call_id, "all correct except my weight is 68", 1, "en")
        check("unmapped scalar correction is preserved for review",
              corrected.get("escalated") is True
              and corrected.get("isComplete") is False
              and session.get("status") == state.STATUS_ESCALATED
              and bool(session.get("unclassifiedMentions"))
              and "submit_to_crm" not in tools.calls,
              f"session={session}, result={corrected}, calls={tools.calls}")

        call_id, session = state.reset([], summaryAskPending=True, summaryAskCount=1)
        tools.reset()
        extract.push(reading(
            "I also have asthma", mentionsCondition=True,
            conditionPhrase="asthma",
            conditionMentions=[{"phrase": "asthma", "scope": "self_current"}],
        ))
        added = orch.handle_turn(call_id, "nothing else, but I also have asthma", 1, "en")
        check("late condition correction opens protocol instead of finalising",
              any(row.get("icd10") == "J45" for row in session["declaredConditions"])
              and bool(session["pendingFollowUps"])
              and added.get("isComplete") is False
              and "submit_to_crm" not in tools.calls,
              f"session={session}, result={added}, calls={tools.calls}")

        print("[2] Every two-mention scope combination")
        scopes = ("self_current", "family", "hypothetical", "negated")
        for left, right in itertools.product(scopes, repeat=2):
            reading_row = {
                "conditionMentions": [
                    {"phrase": "diabetes", "scope": left},
                    {"phrase": "asthma", "scope": right},
                ]
            }
            actual = orch._current_condition_phrases(  # noqa: SLF001
                reading_row, "my father had diabetes but I have asthma")
            expected = [phrase for phrase, scope in (
                ("diabetes", left), ("asthma", right)) if scope == "self_current"]
            check(f"scope pair {left}/{right}", actual == expected,
                  f"actual={actual}, expected={expected}")

        tools.reset()
        call_id, session = state.reset([item], answers=copy.deepcopy(identity))
        extract.push(reading(
            170, mentionsCondition=True, conditionPhrase="diabetes",
            conditionMentions=[
                {"phrase": "diabetes", "scope": "self_current"},
                {"phrase": "asthma", "scope": "self_current"},
            ],
        ))
        result = orch.handle_turn(call_id, "I have diabetes and asthma", 1, "en")
        check("two self conditions preserve both obligations in order",
              [row.get("icd10") for row in session["declaredConditions"]] == ["E11"]
              and session["deferredMentions"] == ["asthma"],
              f"session={session}, result={result}")

        tools.reset()
        call_id, session = state.reset([item], answers=copy.deepcopy(identity))
        extract.push(reading(
            170, mentionsCondition=True, conditionPhrase="my father had diabetes",
            conditionMentions=[
                {"phrase": "my father had diabetes", "scope": "family"},
                {"phrase": "asthma", "scope": "self_current"},
            ],
        ))
        result = orch.handle_turn(
            call_id, "my father had diabetes, but I have asthma", 1, "en")
        check("mixed family/self scope records only caller condition",
              [row.get("icd10") for row in session["declaredConditions"]] == ["J45"]
              and not session["deferredMentions"],
              f"session={session}, result={result}")

        tools.reset()
        call_id, session = state.reset([item], answers=copy.deepcopy(identity))
        extract.push(reading(
            170, mentionsCondition=True, conditionPhrase="diabetes",
            conditionMentions=[{"phrase": "diabetes", "scope": "self_current"}],
        ))
        result = orch.handle_turn(
            call_id, "I have diabetes", 1, "en", stt_confidence=0.2)
        check("low-confidence transcript cannot mint a diagnosis",
              not session["declaredConditions"]
              and not session["deferredMentions"]
              and "classify_condition" not in tools.calls
              and session["answers"].get("A100") == 170,
              f"session={session}, result={result}, calls={tools.calls}")

        print("[3] All 64 resume-overlay combinations preserve priority and state")
        overlays = (
            ("explanationPending", {
                "icd10": "E11", "explanation": "Explanation.",
                "questionText": "Original?", "askedKind": "item", "askedRef": "A100",
            }, "explanationCheck"),
            ("refusalConfirmPending", True, "refusalConfirm"),
            # AN UNNAMED CONDITION OUTRANKS THE SUMMARY AND THE MEDICINE CHECK.
            # It used to sit below both. The summary's whole job is to read the
            # record back as complete, and "you take Warfarin for this?" has no
            # referent until we know what "this" is - so a resumed call with both
            # outstanding has to ask which condition first. This order also has to
            # match next_question, which now returns the naming probe ahead of the
            # remaining form items and the summary; when the two disagreed, a
            # queued probe was simply never asked.
            ("pendingConditionDetail", {
                "ref": "A200", "phrase": "Which condition?",
            }, "conditionDetail"),
            ("summaryAskPending", True, "summaryConfirm"),
            ("medCheckPending", {
                "ref": "FU_TREATMENT", "med": "Warfarin", "icd10": "E11",
                "value": "Warfarin",
            }, "medVerify"),
            ("distressCheckPending", True, "distressCheck"),
        )
        customer = {"customerName": "Priya", "insurerName": "Test Insurer"}
        for mask in range(64):
            values = {
                key: copy.deepcopy(value)
                for bit, (key, value, _kind) in enumerate(overlays)
                if mask & (1 << bit)
            }
            _, session = state.reset([item], answers=copy.deepcopy(identity), **values)
            frozen = copy.deepcopy(session)
            pending = orch._pending_opening_question(  # noqa: SLF001
                session, state.items, customer, "en")
            expected = next((kind for bit, (_key, _value, kind) in enumerate(overlays)
                             if mask & (1 << bit)), None)
            check(f"overlay mask {mask:02d} priority",
                  (pending or {}).get("kind") == expected if expected else pending is None,
                  f"expected={expected}, pending={pending}")
            check(f"overlay mask {mask:02d} conservation", session == frozen,
                  f"before={frozen}, after={session}")

        print("[4] High-risk interrupt classes over an ordinary question")
        events = (
            ("repeat", "could you repeat that", None),
            ("language_switch", "Hindi", None),
            ("acknowledgement", "okay go ahead", None),
            ("refusal", "I want to stop", reading(None, isRefusal=True)),
            ("uncorroborated_distress", "I saw him suddenly",
             reading(None, distressSignal=True)),
            ("dont_know", "I do not know", reading(None, isDontKnow=True)),
            ("unusable", "garbled", {
                "understood": False, "needsRepeat": True, "confidence": 0.0,
                "detectedLanguage": "en", "acknowledgement": "",
            }),
            ("valid", "170 centimetres", reading(170)),
            ("condition", "170, and I have diabetes", reading(
                170, mentionsCondition=True, conditionPhrase="diabetes",
                conditionMentions=[{"phrase": "diabetes", "scope": "self_current"}],
            )),
            ("hypothetical", "170, and I might develop diabetes", reading(
                170, mentionsCondition=True, conditionPhrase="diabetes",
                conditionMentions=[{"phrase": "diabetes", "scope": "hypothetical"}],
            )),
        )
        for label, utterance, extraction in events:
            tools.reset()
            call_id, session = state.reset([item], answers=copy.deepcopy(identity))
            if extraction is not None:
                extract.push(extraction)
            result = orch.handle_turn(call_id, utterance, 1, "en")
            check(f"{label} emits stable response", stable(result), str(result))
            check(f"{label} never duplicates declared codes",
                  len([d.get("icd10") for d in session["declaredConditions"]])
                  == len(set(d.get("icd10") for d in session["declaredConditions"])),
                  str(session["declaredConditions"]))
            if label in {"repeat", "language_switch", "acknowledgement", "hypothetical"}:
                check(f"{label} does not invent a diagnosis",
                      not session["declaredConditions"], str(session))

    finally:
        orch.st, orch.tools, orch.extract = original_state, original_tools, original_extract

    print(f"Executed {checks} generated conversation-combination checks.")
    if failures:
        print(f"FAILED: {len(failures)}")
        for failure in failures:
            print("  -", failure)
        return 1
    print("All declared semantic classes, scope pairs, overlay powersets, and interrupts passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
