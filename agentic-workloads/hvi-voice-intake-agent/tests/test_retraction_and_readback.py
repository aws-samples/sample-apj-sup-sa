#!/usr/bin/env python3
"""Retraction, quoted read-backs, deferred floor, and feet-to-cm coercion.

All four fixes came from one live browser call:
  - "फाइब्रॉइड नहीं है मुझे" mid-protocol had no path to act; the fibroid
    protocol kept running at a caller who had just denied having one
  - the deferred-mention read-back never quoted "सास फूलती थी मेरी", so the
    caller confirmed a misclassification they could not recognise
  - that symptom phrase was classified at 0.8064, above the old 0.78 floor
  - "5 feet." for height failed twice and passed once - LLM unit conversion
    is not deterministic

Run:  ./.venv/bin/python tests/test_retraction_and_readback.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_dialogue_state_model as harness  # noqa: E402

orch = harness.orch
extract_real = harness.orch.extract if hasattr(harness.orch, "extract") else None

failures: list[str] = []
checks = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}"
          + (f"  <- {detail[:200]}" if not ok and detail else ""))
    if not ok:
        failures.append(label)


def main() -> int:
    original = (orch.st, orch.tools, orch.extract)
    state = harness.MemoryState()
    tools = harness.FakeTools(state)
    extract = harness.ScriptedExtract()
    orch.st, orch.tools, orch.extract = state, tools, extract
    try:
        fu = {"icd10": "C50", "conditionLabel": "Breast cancer", "questionId": "Q1",
              "prompt": "When diagnosed?", "answerType": "text",
              "protocolSource": "test"}

        print("[1] explicit named denial retracts the condition")
        call_id, session = state.reset(
            [], answers={"A010": True, "A020": True, "A030": "1987-03-22"},
            pendingFollowUps=[dict(fu)],
            declaredConditions=[{"icd10": "C50", "label": "Breast cancer",
                                 "patientPhrase": "cancer tha mujhe"}])
        extract.push({"understood": False, "needsRepeat": True, "confidence": 0.0,
                      "detectedLanguage": "en", "acknowledgement": ""})
        result = orch.handle_turn(call_id, "I don't have breast cancer", 1, "en")
        check("declared condition removed",
              not session["declaredConditions"], str(session["declaredConditions"]))
        check("its pending follow-ups dropped",
              not session["pendingFollowUps"], str(session["pendingFollowUps"]))
        check("moved to retractedConditions with the caller's words",
              session["retractedConditions"]
              and session["retractedConditions"][0]["icd10"] == "C50",
              str(session.get("retractedConditions")))
        check("code joins rejectedCodes so it cannot be re-offered",
              "C50" in session["rejectedCodes"], str(session["rejectedCodes"]))
        check("call continues rather than re-asking the dead protocol",
              result.get("stopQuestionnaire") is False
              and result.get("askedRef") != "Q1", str(result))

        print("[2] cross-script denial: फाइब्रॉइड retracts Uterine Fibroid")
        call_id, session = state.reset(
            [], answers={"A010": True, "A020": True, "A030": "1987-03-22"},
            pendingFollowUps=[dict(fu, icd10="D25",
                                   conditionLabel="Uterine Fibroid")],
            declaredConditions=[{"icd10": "D25", "label": "Uterine Fibroid",
                                 "patientPhrase": "सास फूलती थी मेरी"}])
        extract.push({"understood": False, "needsRepeat": True, "confidence": 0.0,
                      "detectedLanguage": "hi", "acknowledgement": ""})
        result = orch.handle_turn(call_id, "फाइब्रॉइड नहीं है मुझे", 1, "hi")
        check("Devanagari name matches the English label",
              not session["declaredConditions"]
              and session["retractedConditions"]
              and session["retractedConditions"][0]["icd10"] == "D25",
              str(session))

        print("[3] what must NOT retract")
        for label, utterance, reading in (
            ("bare no to a boolean follow-up", "no",
             harness.reading(False)),
            ("don't know", "I don't know",
             {"understood": True, "isDontKnow": True, "value": None,
              "confidence": 1.0, "detectedLanguage": "en", "acknowledgement": ""}),
            ("family-scoped denial", "my father doesn't have breast cancer",
             harness.reading("father history")),
            # A vague negation that denies neither the condition nor having it.
            # ("nahi hai mujhe" DOES retract when the question just named the
            # condition - that is [9c], and it is the intended behaviour.)
            ("uncertainty, not denial", "pata nahi exactly",
             {"understood": False, "needsRepeat": True, "confidence": 0.0,
              "detectedLanguage": "hinglish", "acknowledgement": ""}),
        ):
            call_id, session = state.reset(
                [], answers={"A010": True, "A020": True, "A030": "1987-03-22"},
                pendingFollowUps=[dict(fu)],
                declaredConditions=[{"icd10": "C50", "label": "Breast cancer",
                                     "patientPhrase": "cancer tha"}])
            extract.push(reading)
            orch.handle_turn(call_id, utterance, 1, "en")
            check(f"{label} keeps the condition",
                  session["declaredConditions"]
                  and not session.get("retractedConditions"),
                  str(session))

        print("[4] read-back quotes the caller's phrase")
        item = next(row for row in harness.ITEMS if row["itemId"] == "A100")
        call_id, session = state.reset(
            [item], answers={"A010": True, "A020": True, "A030": "1987-03-22"})
        # FakeTools returns needsConfirmation for severityTier>=4; C50 is tier 5.
        extract.push(harness.reading(170, mentionsCondition=True,
                                     conditionPhrase="breast cancer settled"))
        orch.handle_turn(call_id, "170 and breast cancer settled", 1, "en")
        parked = session.get("awaitingConfirmation") or {}
        check("confirmation parked", parked.get("icd10") == "C50", str(parked))
        check("question quotes the phrase",
              "breast cancer settled" in (parked.get("question") or ""),
              str(parked.get("question")))

        print("[5] deferred floor blocks sub-threshold nearest neighbours")
        original_classify = tools.classify_condition

        def low_score_classify(phrase):
            tools.calls.append("classify_condition")
            return {"icd10": "D25", "label_en": "Uterine Fibroid",
                    "score": 0.8064, "source": "opensearch_knn",
                    "needsConfirmation": True, "redFlag": False,
                    "candidates": []}
        tools.classify_condition = low_score_classify
        try:
            call_id, session = state.reset(
                [], answers={"A010": True, "A020": True, "A030": "1987-03-22"},
                deferredMentions=["सास फूलती थी मेरी"])
            extract.push(harness.reading("done"))
            orch.handle_turn(call_id, "done", 1, "hi")
            check("0.8064 nearest neighbour is NOT offered from a deferred drain",
                  not session.get("awaitingConfirmation"),
                  str(session.get("awaitingConfirmation")))
            check("the phrase is preserved for human review",
                  any("सास" in (row.get("phrase") or "")
                      for row in session.get("unclassifiedMentions") or []),
                  str(session.get("unclassifiedMentions")))
        finally:
            tools.classify_condition = original_classify

        print("[7] naming an already-declared condition resolves the probe")
        # The live loop: asthma on the record, caller answers the
        # which-condition probe with "अस्थमा" and is told three times that the
        # name was not understood.
        call_id, session = state.reset(
            [], answers={"A010": True, "A020": True, "A030": "1987-03-22"},
            declaredConditions=[{"icd10": "J45", "label": "Asthma",
                                 "patientPhrase": "saans lene mein takleef"}],
            pendingConditionDetail={"ref": "A220",
                                    "phrase": "What was it, and when?"})
        result = orch.handle_turn(call_id, "asthma", 1, "en")
        check("probe cleared instead of re-asking",
              session.get("pendingConditionDetail") is None,
              str(session.get("pendingConditionDetail")))
        check("acknowledges it is already on the record",
              "Asthma" in (result.get("agentUtterance") or "")
              and "didn't catch" not in (result.get("agentUtterance") or ""),
              str(result.get("agentUtterance"))[:120])
        check("no duplicate condition minted",
              len(session["declaredConditions"]) == 1, str(session))
        check("asthma not blacklisted",
              "J45" not in (session.get("rejectedCodes") or []),
              str(session.get("rejectedCodes")))

        print("[8] a rejected read-back never offers or blacklists a declared code")
        call_id, session = state.reset(
            [], answers={"A010": True, "A020": True, "A030": "1987-03-22"},
            declaredConditions=[{"icd10": "J45", "label": "Asthma",
                                 "patientPhrase": "saans"}],
            awaitingConfirmation={
                "icd10": "I50", "label": "Heart Failure",
                "question": "Was it Heart Failure?", "phrase": "saans phoolti",
                "score": 0.884,
                "alternatives": [{"icd10": "J45", "label_en": "Asthma",
                                  "score": 0.884},
                                 {"icd10": "J44", "label_en": "COPD",
                                  "score": 0.851}]})
        extract.push(harness.reading(False))
        result = orch.handle_turn(call_id, "no", 1, "en")
        check("declared Asthma is not re-offered as the next candidate",
              "Asthma" not in (result.get("agentUtterance") or ""),
              str(result.get("agentUtterance"))[:120])
        check("rejected guess is blacklisted",
              "I50" in (session.get("rejectedCodes") or []),
              str(session.get("rejectedCodes")))
        check("declared Asthma is NOT blacklisted",
              "J45" not in (session.get("rejectedCodes") or []),
              str(session.get("rejectedCodes")))

        print("[9] elaboration of the active condition mints nothing")
        original_classify2 = tools.classify_condition

        def ambiguous_classify(phrase):
            tools.calls.append("classify_condition")
            # The live numbers: a 0.0003 margin between a new cardiac guess and
            # the asthma already being discussed.
            return {"icd10": "I50", "label_en": "Heart Failure", "score": 0.8843,
                    "source": "opensearch_knn", "needsConfirmation": True,
                    "redFlag": False,
                    "candidates": [{"icd10": "I50", "score": 0.8843},
                                   {"icd10": "J45", "score": 0.884},
                                   {"icd10": "J44", "score": 0.851}]}
        tools.classify_condition = ambiguous_classify
        try:
            call_id, session = state.reset(
                [], answers={"A010": True, "A020": True, "A030": "1987-03-22"},
                declaredConditions=[{"icd10": "J45", "label": "Asthma",
                                     "patientPhrase": "saans"}],
                deferredMentions=["जब सांस फूलती तभी यूज़ करती हूँ"])
            extract.push(harness.reading("done"))
            orch.handle_turn(call_id, "done", 1, "hi")
            check("no second condition read back when the active one is a "
                  "co-equal candidate",
                  not session.get("awaitingConfirmation"),
                  str(session.get("awaitingConfirmation")))
            check("kept as an elaboration for review",
                  any("सांस" in (row.get("phrase") or "")
                      for row in session.get("unclassifiedMentions") or []),
                  str(session.get("unclassifiedMentions")))
        finally:
            tools.classify_condition = original_classify2

        print("[9b] a genuine second disclosure is NOT suppressed")
        # Same shape, but the declared condition is a distant candidate rather
        # than a co-equal one: this must still be read back. Two suites caught
        # the first version of the guard swallowing exactly this.
        def distant_classify(phrase):
            tools.calls.append("classify_condition")
            return {"icd10": "C50", "label_en": "Breast cancer", "score": 0.93,
                    "source": "opensearch_knn", "needsConfirmation": True,
                    "redFlag": True,
                    "candidates": [{"icd10": "C50", "score": 0.93},
                                   {"icd10": "D25", "score": 0.79}]}
        tools.classify_condition = distant_classify
        try:
            call_id, session = state.reset(
                [], answers={"A010": True, "A020": True, "A030": "1987-03-22"},
                declaredConditions=[{"icd10": "D25", "label": "Uterine Fibroid",
                                     "patientPhrase": "fibroid"}],
                deferredMentions=["it was breast cancer"])
            extract.push(harness.reading("done"))
            orch.handle_turn(call_id, "done", 1, "en")
            parked = session.get("awaitingConfirmation") or {}
            check("distant declared candidate does not suppress the new "
                  "disclosure", parked.get("icd10") == "C50", str(parked))
        finally:
            tools.classify_condition = original_classify2

        print("[9c] 'I don't have it' retracts the condition just named")
        for answer_type, prompt, should_retract in (
            ("text", "What treatment are you taking for it?", True),
            ("boolean", "Is it currently under control?", False),
        ):
            call_id, session = state.reset(
                [], answers={"A010": True, "A020": True, "A030": "1987-03-22"},
                pendingFollowUps=[{"icd10": "E78", "conditionLabel": "Hyperlipidaemia",
                                   "questionId": "FU_TREATMENT", "prompt": prompt,
                                   "answerType": answer_type,
                                   "protocolSource": "test"}],
                declaredConditions=[{"icd10": "E78", "label": "Hyperlipidaemia",
                                     "patientPhrase": "cholesterol"}])
            extract.push({"understood": True, "value": False, "confidence": 1.0,
                          "detectedLanguage": "hi", "acknowledgement": ""})
            orch.handle_turn(call_id, "नहीं है मुझे", 1, "hi")
            retracted = not session["declaredConditions"]
            check(f"{answer_type} follow-up: "
                  f"{'retracts' if should_retract else 'keeps the condition'}",
                  retracted is should_retract,
                  f"declared={session['declaredConditions']}")

        print("[9d] 'I didn't say yes or no' is not a request to hang up")
        item = next(row for row in harness.ITEMS if row["itemId"] == "A100")
        call_id, session = state.reset(
            [item], answers={"A010": True, "A020": True, "A030": "1987-03-22"})
        extract.push({"understood": False, "isRefusal": True, "value": None,
                      "confidence": 1.0, "detectedLanguage": "hi",
                      "acknowledgement": ""})
        result = orch.handle_turn(call_id, "मैंने कुछ भी हाँ या नहीं नहीं बोला।",
                                  1, "hi")
        check("does not offer to end the call",
              "समाप्त" not in (result.get("agentUtterance") or ""),
              str(result.get("agentUtterance"))[:100])
        check("call continues",
              result.get("stopQuestionnaire") is False
              and session.get("refusalConfirmPending") is False, str(result))
        check("the pending question is re-asked",
              result.get("askedRef") == "A100", str(result.get("askedRef")))

        print("[10] 'I didn't say yes' retracts the screen answer")
        call_id, session = state.reset(
            [], answers={"A010": True, "A020": True, "A030": "1987-03-22"},
            pendingConditionDetail={"ref": "A220",
                                    "phrase": "What was it, and when?"})
        result = orch.handle_turn(call_id, "नहीं, मैंने हाँ नहीं बोला।", 1, "hi")
        check("probe abandoned rather than pressed",
              session.get("pendingConditionDetail") is None, str(session))
        check("flagged for human review",
              any("हाँ नहीं बोला" in (row.get("phrase") or "")
                  for row in session.get("unclassifiedMentions") or []),
              str(session.get("unclassifiedMentions")))
        check("does not claim the disease name was unclear",
              "समझ नहीं" not in (result.get("agentUtterance") or ""),
              str(result.get("agentUtterance"))[:120])

        print("[6] deterministic feet-to-cm coercion")
        import extract as extract_module
        a100 = {"validation": {"min": 100, "max": 230,
                               "coerceFromFeetInches": True}}
        for said, want in (
            ("5 feet", 152.4), ("5 feet.", 152.4), ("Five feet.", 152.4),
            ("5 feet 2 inches", 157.5), ("5 ft 2", 157.5),
            ("5'2\"", 157.5), ("पाँच फीट", 152.4), ("5 फुट", 152.4),
            (5, 152.4), (5.5, 167.6), ("162", 162), (162, 162),
            ("six feet", 182.9),
        ):
            got, err = extract_module.coerce(said, "number", a100)
            check(f"height {said!r} -> {want}",
                  err is None and abs(float(got) - want) < 0.15,
                  f"got {got!r} err={err}")
        got, err = extract_module.coerce("2 feet", "number", a100)
        check("2 feet still fails the plausibility minimum", err is not None,
              f"got {got!r}")
        got, err = extract_module.coerce(50, "number",
                                         {"validation": {"min": 25, "max": 300}})
        check("weight 50 untouched (no flag, no conversion)",
              err is None and got == 50, f"got {got!r} err={err}")

        # A pure duration is not a disease. Every string below was spoken on the
        # January call as the answer to "when were you diagnosed?", sent to the
        # ICD-10 index anyway, and filed as an unresolvable health mention at
        # 0.80-0.83. An embedding index has a nearest neighbour for any input;
        # the only defence is not asking it about clinically empty strings.
        print("[7] pure time expressions are not screened as conditions")
        for said in ("तीन साल पहले", "पांच साल पहले।", "हाँ, छह साल पहले।",
                     "two years back", "3 saal pehle", "about five years ago",
                     "छह महीने पहले", "do mahine pehle", "कुछ साल पहले",
                     "10 years", "एक साल", "a few days ago", "नहीं, दो दिन पहले"):
            check(f"time-only: {said!r}", orch.is_time_only(said))
        # ...but anything that also NAMES something must still be classified,
        # because callers habitually answer with condition plus date together.
        for said in ("sugar teen saal pehle", "asthma", "अस्थमा",
                     "मुझे तीन साल पहले शुगर हुआ", "diabetes two years ago",
                     "Cholesterol.", "सांस फूलती", "heart disease five years back",
                     "thyroid", "बीपी है"):
            check(f"still classifies: {said!r}", not orch.is_time_only(said))
        # the seam itself: the model attributing a date to the caller
        check("time-only utterance yields no condition phrases",
              orch._current_condition_phrases(
                  {"conditionMentions": [{"scope": "self_current",
                                          "phrase": "तीन साल पहले"}]},
                  "तीन साल पहले") == [])
        check("a real sibling mention survives a time-only one",
              orch._current_condition_phrases(
                  {"conditionMentions": [
                      {"scope": "self_current", "phrase": "पांच साल पहले"},
                      {"scope": "self_current", "phrase": "शुगर"}]},
                  "शुगर, पांच साल पहले") == ["शुगर"])
        check("legacy conditionPhrase time-only dropped",
              orch._current_condition_phrases(
                  {"conditionPhrase": "तीन साल पहले"}, "तीन साल पहले") == [])

        # A FIRST DISCLOSURE IS FLOORED ONLY WHERE THE FLOOR CAN DISCRIMINATE.
        #
        # Measured against the live index, a garbled echo and a real one-word
        # disclosure are the same object: "Cyst." 0.8971 and "Cholesterol."
        # 0.8982, both uncorroborated. Meanwhile chest pain on exertion scores
        # 0.8786 and carries a RED FLAG. So the floor sits at 0.78 - under the
        # genuine band, over the content-free noise - and the read-back, not a
        # threshold, is what settles whether the caller has the condition.
        # FakeTools always returns an exact synonym at 1.0, so the bands have to
        # be scripted.
        print("[8] first disclosures: noise is floored, real ones reach the caller")
        item = next(row for row in harness.ITEMS if row["itemId"] == "A100")
        identity = {"A010": True, "A020": True, "A030": "1987-03-22"}
        real_classify = tools.classify_condition

        def weak(icd10, score, corroborated):
            def _classify(phrase):
                tools.calls.append("classify_condition")
                return {"icd10": icd10, "label_en": "Chronic ischaemic heart disease",
                        "label_hi": "हृदय रोग", "score": score,
                        "source": ("opensearch_knn+neptune_corroborated"
                                   if corroborated else "opensearch_knn"),
                        "corroborated": corroborated,
                        "uncorroborated": not corroborated,
                        "needsConfirmation": True, "redFlag": False,
                        "candidates": []}
            return _classify

        try:
            # Content-free noise: below the floor, so nothing is asserted and the
            # caller is asked to name it. Measured example: "शुलूज़े" -> E11 0.7668.
            tools.classify_condition = weak("E11", 0.7668, False)
            call_id, session = state.reset([item], answers=dict(identity))
            extract.push(harness.reading(
                170, mentionsCondition=True, conditionPhrase="शुलूज़े",
                conditionMentions=[{"phrase": "शुलूज़े", "scope": "self_current"}]))
            res = orch.handle_turn(call_id, "170, शुलूज़े", 1, "en")
            check("sub-floor noise does not become a declared condition",
                  not session["declaredConditions"],
                  str(session["declaredConditions"]))
            check("sub-floor noise opens no protocol",
                  not session["pendingFollowUps"], str(session["pendingFollowUps"]))
            check("sub-floor noise is not read back as a diagnosis",
                  not session.get("awaitingConfirmation"),
                  str(session.get("awaitingConfirmation")))
            check("the caller is asked to name the condition instead",
                  (session.get("pendingConditionDetail") or {}).get("ref")
                  == "weak-disclosure",
                  str(session.get("pendingConditionDetail")))
            check("the mention is kept for human review",
                  any("शुलूज़े" in (row.get("phrase") or "")
                      for row in session.get("unclassifiedMentions") or []),
                  str(session.get("unclassifiedMentions")))
            check("the naming probe is what gets spoken",
                  res.get("askedKind") == "conditionDetail",
                  f"askedKind={res.get('askedKind')} "
                  f"utterance={str(res.get('agentUtterance'))[:90]}")

            # A RED-FLAG SYMPTOM MUST NEVER BE SUPPRESSED INTO "what is it
            # called?". Chest pain on exertion scores 0.8786 uncorroborated on the
            # live index; an earlier 0.85 floor dropped it intermittently, which is
            # the most dangerous thing this file guards against.
            tools.classify_condition = weak("I25", 0.8786, False)
            call_id, session = state.reset([item], answers=dict(identity))
            extract.push(harness.reading(
                170, mentionsCondition=True,
                conditionPhrase="chalne par seene mein dard",
                conditionMentions=[{"phrase": "chalne par seene mein dard",
                                    "scope": "self_current"}]))
            orch.handle_turn(
                call_id, "170, mujhe chalne par seene mein dard hota hai", 1, "en")
            check("an uncorroborated red-flag symptom still reaches the caller",
                  bool(session.get("awaitingConfirmation"))
                  or bool(session["pendingFollowUps"]),
                  f"awaiting={session.get('awaitingConfirmation')} "
                  f"pending={session['pendingFollowUps']} "
                  f"probe={session.get('pendingConditionDetail')}")

            # Graph agreement is the evidence that matters, not the number: a
            # corroborated match below the floor is still trustworthy, because
            # two independent methods named the same code.
            tools.classify_condition = weak("I25", 0.8010, True)
            call_id, session = state.reset([item], answers=dict(identity))
            extract.push(harness.reading(
                170, mentionsCondition=True, conditionPhrase="heart disease",
                conditionMentions=[{"phrase": "heart disease",
                                    "scope": "self_current"}]))
            orch.handle_turn(call_id, "170, and I have heart disease", 1, "en")
            check("corroborated 0.80 still reaches the caller as a read-back",
                  bool(session.get("awaitingConfirmation"))
                  or bool(session["pendingFollowUps"]),
                  f"awaiting={session.get('awaitingConfirmation')} "
                  f"pending={session['pendingFollowUps']}")

            # THE REJECTION CHAIN, PINNED DETERMINISTICALLY.
            #
            # A rejection is information - "not that code" - so the agent walks the
            # classifier's own remaining candidates rather than abandoning the
            # disclosure, and asks in the caller's own words once they run out.
            # This lived only in tests/test_underwriting_stress2.py against the
            # live index, where how many candidates come back is not something a
            # test can rely on; it failed intermittently with "0 read-backs
            # offered" because resolving confidently on the first pass is also
            # correct. Scripted here, the mechanics are pinned; the live suite now
            # asserts only the invariant.
            def with_candidates(phrase):
                tools.calls.append("classify_condition")
                return {
                    "icd10": "C50", "label_en": "Breast cancer",
                    "label_hi": "स्तन कैंसर", "score": 0.91,
                    "source": "opensearch_knn", "corroborated": False,
                    "needsConfirmation": True, "redFlag": True,
                    "candidates": [
                        {"icd10": "C50", "label_en": "Breast cancer", "score": 0.91},
                        {"icd10": "C34", "label_en": "Lung cancer", "score": 0.89},
                        {"icd10": "C18", "label_en": "Colon cancer", "score": 0.88},
                    ],
                }

            tools.classify_condition = with_candidates
            call_id, session = state.reset([item], answers=dict(identity))
            extract.push(harness.reading(
                170, mentionsCondition=True, conditionPhrase="cancer",
                conditionMentions=[{"phrase": "cancer", "scope": "self_current"}]))
            orch.handle_turn(call_id, "170, I had cancer", 1, "en")
            first = (session.get("awaitingConfirmation") or {}).get("icd10")
            check("the first guess is read back, not recorded", first == "C50",
                  f"parked={session.get('awaitingConfirmation')}")
            check("the caller's own phrase is quoted in the read-back",
                  "cancer" in ((session.get("awaitingConfirmation") or {})
                               .get("question") or ""),
                  str((session.get("awaitingConfirmation") or {}).get("question")))

            offered = ["C50"]
            for _ in range(3):
                if not session.get("awaitingConfirmation"):
                    break
                extract.push(harness.reading(False))
                res = orch.handle_turn(call_id, "no, not that", 9, "en")
                nxt = (session.get("awaitingConfirmation") or {}).get("icd10")
                if nxt and nxt not in offered:
                    offered.append(nxt)
            check("rejecting a guess offers the next candidate rather than "
                  "abandoning the disclosure", offered == ["C50", "C34", "C18"],
                  f"offered={offered}")
            check("no rejected code was recorded as a condition",
                  not session["declaredConditions"],
                  str(session["declaredConditions"]))
            check("every rejected code is blacklisted so it cannot return",
                  set(session.get("rejectedCodes") or []) == {"C50", "C34", "C18"},
                  str(session.get("rejectedCodes")))
            check("once the candidates run out the caller is ASKED in their own "
                  "words", (session.get("pendingConditionDetail") or {}).get("ref")
                  == "rejected-readback",
                  f"probe={session.get('pendingConditionDetail')} "
                  f"kind={res.get('askedKind')}")
            check("the disclosure survives the whole chain for a human",
                  any("cancer" in (row.get("phrase") or "")
                      for row in session.get("unclassifiedMentions") or []),
                  str(session.get("unclassifiedMentions"))[:120])

            # And a strong uncorroborated one-word disclosure: "Cholesterol."
            # 0.8982, indistinguishable by score from "Cyst." 0.8971. Both are read
            # back, because the caller is the only one who can tell them apart.
            tools.classify_condition = weak("E78", 0.8982, False)
            call_id, session = state.reset([item], answers=dict(identity))
            extract.push(harness.reading(
                170, mentionsCondition=True, conditionPhrase="Cholesterol.",
                conditionMentions=[{"phrase": "Cholesterol.",
                                    "scope": "self_current"}]))
            orch.handle_turn(call_id, "170, Cholesterol.", 1, "en")
            check("uncorroborated 0.90 above the floor is still offered",
                  bool(session.get("awaitingConfirmation"))
                  or bool(session["pendingFollowUps"]),
                  f"awaiting={session.get('awaitingConfirmation')} "
                  f"pending={session['pendingFollowUps']}")
        finally:
            tools.classify_condition = real_classify

        # A DEFERRED MENTION IS NOT A DISCARDED ONE. It is drained later and can be
        # declared on its own, so it needs the same grounding test as a direct
        # declaration - and it had none. _phrase_grounded was applied to the FIRST
        # extracted phrase only; every phrase after it was deferred unchecked.
        #
        # That is a hallucination channel, because a condition screen's prompt is a
        # list of diseases. Measured on the live stack: a bare "Yes" to A200
        # ("...cancer, tumour, cyst, polyp...") returned conditionMentions
        # ['tumour', 'polyp', 'cyst'], none of them spoken. The first was rejected
        # by grounding; the other two were deferred, and draining the queue
        # resolved 'cyst' to D25 Uterine Fibroid at 0.897 - over the 0.85 deferred
        # floor - and declared it. A caller acquired a fibroid by answering a
        # cancer question "yes", and because the disclosure path treated the turn
        # as handled, the which-condition probe never opened either, which is the
        # second way this reads as "it did not probe".
        #
        # Intermittent live (1 run in 3) because the extractor is a model and only
        # sometimes copies the question's vocabulary - which is exactly why it is
        # scripted here instead.
        print("[11] question words are not disclosures, however many there are")
        a200 = next(row for row in harness.ITEMS if row["itemId"] == "A200")
        who = {"A010": True, "A020": True, "A030": "1987-03-22"}

        call_id, session = state.reset([a200], answers=dict(who))
        extract.push({"understood": True, "value": True, "confidence": 1.0,
                      "mentionsCondition": True,
                      "conditionMentions": [
                          {"phrase": "tumour", "scope": "self_current"},
                          {"phrase": "polyp", "scope": "self_current"},
                          {"phrase": "cyst", "scope": "self_current"}]})
        result = orch.handle_turn(call_id, "Yes", 1, "en")
        check("no condition is declared from the question's own vocabulary",
              not session["declaredConditions"], str(session["declaredConditions"]))
        check("and none is queued for later either",
              not session["deferredMentions"], str(session["deferredMentions"]))
        check("the bare yes still opens the which-condition probe",
              result.get("askedKind") == "conditionDetail"
              or bool(session.get("pendingConditionDetail")),
              f"askedKind={result.get('askedKind')} "
              f"probe={session.get('pendingConditionDetail')}")
        check("the screen answer itself is still recorded",
              session["answers"].get("A200") is True, str(session["answers"]))

        # The control that keeps the fix from becoming "never defer anything".
        # Two conditions the caller really said must still queue in spoken order,
        # which is the one-condition-at-a-time contract.
        call_id, session = state.reset([a200], answers=dict(who))
        extract.push({"understood": True, "value": "I have diabetes and asthma",
                      "confidence": 1.0, "mentionsCondition": True,
                      "conditionMentions": [
                          {"phrase": "diabetes", "scope": "self_current"},
                          {"phrase": "asthma", "scope": "self_current"}]})
        orch.handle_turn(call_id, "I have diabetes and asthma", 1, "en")
        spoken = [row if isinstance(row, str)
                  else (row.get("phrase") or row.get("label"))
                  for row in session["deferredMentions"]]
        check("a second condition the caller DID say is still deferred",
              any("asthma" in str(row).lower() for row in spoken),
              f"deferred={spoken} declared={session['declaredConditions']}")
    finally:
        orch.st, orch.tools, orch.extract = original

    print(f"\nExecuted {checks} retraction/read-back/floor/height checks.")
    if failures:
        print(f"FAILED: {len(failures)}")
        for failure in failures:
            print("  -", failure)
        return 1
    print("Denials retract, read-backs quote, weak deferrals are flagged not "
          "asked, and feet convert in code.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
