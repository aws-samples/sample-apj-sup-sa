#!/usr/bin/env python3
"""Generated finite-model proof for deterministic underwriting dialogue transitions.

This suite replaces AWS and model dependencies with in-memory doubles, while running
the production orchestrator. It exhausts the bounded state/event classes: priority
lattices, every form item and condition screen, every governed protocol/language,
loop limits, pending overlays, termination gates, completion failures, and resume
obligation conservation.
"""
from __future__ import annotations

import copy
import itertools
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
sys.path.insert(0, str(ROOT / "agent"))
sys.path.insert(0, str(ROOT / "common"))

import extract as _real_extract  # noqa: E402
import hvi_state as real_state  # noqa: E402
import orchestrator as orch  # noqa: E402

FORM = json.loads((ROOT / "seed" / "form-arogya-sanjeevani-v1.json").read_text())
GRAPH = json.loads((ROOT / "seed" / "health-kg.json").read_text())
ITEMS = [item for item in FORM["items"] if item.get("active", True)]
CONDITIONS = {row["icd10"]: row for row in GRAPH["conditions"]}
FOLLOWUPS = {row["id"]: row for row in GRAPH["followUpQuestions"]}
EXPLANATIONS = GRAPH["conditionExplanations"]

failures: list[str] = []
checks = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not ok:
        failures.append(label)
        print(f"  FAIL  {label} <- {detail}")


def show_item(item: dict, answers: dict) -> bool:
    cond = item.get("showIf")
    if not cond:
        return True
    if cond.get("itemId") not in answers:
        return False
    actual = answers[cond["itemId"]]
    expected = cond.get("value")
    op = cond.get("operator", "eq")
    if op == "eq":
        return bool(actual) == expected if isinstance(expected, bool) else actual == expected
    if op == "neq":
        return actual != expected
    if op == "in":
        return actual in (expected or [])
    if op == "notin":
        return actual not in (expected or [])
    if op == "gt":
        return float(actual) > float(expected)
    if op == "exists":
        return actual not in (None, "")
    return True


class MemoryState:
    STATUS_DROPPED = "dropped"
    STATUS_COMPLETED = "completed"
    STATUS_ABANDONED = "abandoned"
    STATUS_TERMINATED = "terminated"
    STATUS_ESCALATED = "escalated"

    @staticmethod
    def is_terminal_session(session):
        return real_state.is_terminal_session(session)

    def __init__(self):
        self.sessions: dict[str, dict] = {}
        self.items: list[dict] = []
        self.serial = 0

    def reset(self, items: list[dict] | None = None, **overrides) -> tuple[str, dict]:
        self.sessions.clear()
        self.items = copy.deepcopy(items or [])
        self.serial += 1
        call_id = f"memory-{self.serial}"
        session = {
            "callId": call_id, "formId": "memory-form", "customerName": "Priya",
            "customerEmail": "priya@example.invalid", "dateOfBirth": "1987-03-22",
            "policyNumber": "POL-1", "insurerName": "Test Insurer", "language": "en",
            "languageConfirmed": True, "languageAskCount": 0, "status": "in_progress",
            "answers": {}, "pendingFollowUps": [], "followUpAnswers": {},
            "declaredConditions": [], "awaitingConfirmation": None,
            "pendingConditionDetail": None, "deferredMentions": [], "rejectedCodes": [],
            "retractedConditions": [],
            "unclassifiedMentions": [], "medCheckPending": None, "medChecksAsked": [],
            "distressCheckPending": False, "refusalConfirmPending": False,
            "summaryAskPending": False, "summaryAskCount": 0,
            "explanationPending": None, "repeatCounts": {}, "resumeCount": 0,
        }
        session.update(copy.deepcopy(overrides))
        self.sessions[call_id] = session
        return call_id, session

    def get_session(self, call_id):
        return self.sessions.get(call_id)

    def get_form_items(self, _form_id, include_inactive=False):
        return self.items

    def show_item(self, item, answers):
        return show_item(item, answers)

    def next_item(self, items, answers):
        return next((item for item in items
                     if item["itemId"] not in answers and show_item(item, answers)), None)

    def remaining_count(self, items, answers):
        return sum(1 for item in items
                   if item["itemId"] not in answers and show_item(item, answers))

    def peek_followup(self, session):
        queue = session.get("pendingFollowUps") or []
        return queue[0] if queue else None

    def set_language(self, call_id, language, confirmed=True):
        self.sessions[call_id]["language"] = language
        self.sessions[call_id]["languageConfirmed"] = confirmed
        return language

    def record_language_ask(self, call_id):
        session = self.sessions[call_id]
        session["languageAskCount"] = int(session.get("languageAskCount") or 0) + 1
        return session["languageAskCount"]

    def record_repeat(self, call_id, key):
        counts = self.sessions[call_id].setdefault("repeatCounts", {})
        counts[key] = int(counts.get(key) or 0) + 1
        return counts[key]

    def clear_repeats(self, call_id, key):
        self.sessions[call_id].setdefault("repeatCounts", {}).pop(key, None)

    def set_awaiting_confirmation(self, call_id, payload):
        self.sessions[call_id]["awaitingConfirmation"] = copy.deepcopy(payload)

    def set_pending_condition_detail(self, call_id, ref, phrase):
        self.sessions[call_id]["pendingConditionDetail"] = {"ref": ref, "phrase": phrase}

    def clear_pending_condition_detail(self, call_id):
        self.sessions[call_id]["pendingConditionDetail"] = None

    def set_explanation_pending(self, call_id, payload):
        self.sessions[call_id]["explanationPending"] = copy.deepcopy(payload)

    def set_refusal_confirm_pending(self, call_id, pending):
        self.sessions[call_id]["refusalConfirmPending"] = bool(pending)

    def set_distress_check_pending(self, call_id, pending):
        self.sessions[call_id]["distressCheckPending"] = bool(pending)

    def record_summary_ask(self, call_id, pending):
        session = self.sessions[call_id]
        session["summaryAskCount"] = int(session.get("summaryAskCount") or 0) + 1
        session["summaryAskPending"] = bool(pending)
        return session["summaryAskCount"]

    def clear_summary_ask_pending(self, call_id):
        self.sessions[call_id]["summaryAskPending"] = False

    def record_declaration(self, call_id):
        at = "2026-01-01T00:00:00+00:00"
        self.sessions[call_id]["declarationConfirmedAt"] = at
        return at

    # Delegated, not reimplemented: the prompt text the agent speaks and the text
    # the written transcript quotes come from one function, and a fake copy here
    # would let them drift without any suite noticing.
    @staticmethod
    def render_prompt(item, language, customer):
        return real_state.render_prompt(item, language, customer)

    def set_med_check(self, call_id, pending, mark_asked=None):
        session = self.sessions[call_id]
        session["medCheckPending"] = copy.deepcopy(pending)
        if mark_asked:
            session.setdefault("medChecksAsked", []).append(mark_asked)

    def defer_mention(self, call_id, phrase):
        self.sessions[call_id].setdefault("deferredMentions", []).append(phrase)

    def pop_deferred_mention(self, call_id):
        queue = self.sessions[call_id].setdefault("deferredMentions", [])
        return queue.pop(0) if queue else None

    def add_human_followup(self, call_id, ref, label=None, reason=None):
        items = self.sessions[call_id].setdefault("humanFollowUpItems", [])
        if ref and not any(isinstance(e, dict) and e.get("ref") == ref
                           for e in items):
            entry = {"ref": ref}
            if label:
                entry["label"] = label
            if reason:
                entry["reason"] = reason
            items.append(entry)

    def add_rejected_code(self, call_id, code):
        self.sessions[call_id].setdefault("rejectedCodes", []).append(code)

    def retract_condition(self, call_id, icd10, phrase, reason):
        session = self.sessions[call_id]
        declared = session.get("declaredConditions") or []
        session["declaredConditions"] = [row for row in declared
                                         if row.get("icd10") != icd10]
        moved = [dict(row, retractionPhrase=phrase[:300],
                      retractionReason=reason[:300])
                 for row in declared if row.get("icd10") == icd10]
        session.setdefault("retractedConditions", []).extend(moved)
        session["pendingFollowUps"] = [row for row in
                                       (session.get("pendingFollowUps") or [])
                                       if row.get("icd10") != icd10]
        if icd10 not in (session.get("rejectedCodes") or []):
            session.setdefault("rejectedCodes", []).append(icd10)
        return session

    # Mirrors the real supersede_condition, including the two properties the
    # orchestrator depends on: the displaced code leaves declaredConditions (so
    # nothing downstream reports two diagnoses for one disclosure), and its
    # answers are COPIED onto the survivor so the caller is not asked the same
    # follow-ups a second time. rejectedCodes is deliberately untouched - the
    # caller rejected nothing.
    def supersede_condition(self, call_id, superseded_icd10, surviving_icd10,
                            reason):
        session = self.sessions[call_id]
        declared = session.get("declaredConditions") or []
        moved = [dict(row, supersededBy=surviving_icd10,
                      supersessionReason=str(reason)[:300])
                 for row in declared if row.get("icd10") == superseded_icd10]
        if not moved:
            return session
        session["declaredConditions"] = [row for row in declared
                                         if row.get("icd10") != superseded_icd10]
        session.setdefault("supersededConditions", []).extend(moved)

        answers = dict(session.get("followUpAnswers") or {})
        for key, rec in list(answers.items()):
            if not isinstance(rec, dict) or rec.get("icd10") != superseded_icd10:
                continue
            new_key = f"{surviving_icd10}::{rec.get('questionId')}"
            if new_key not in answers:
                answers[new_key] = dict(rec, icd10=surviving_icd10,
                                        carriedFrom=key)
        session["followUpAnswers"] = answers
        answered = {rec.get("questionId") for rec in answers.values()
                    if isinstance(rec, dict)
                    and rec.get("icd10") == surviving_icd10}
        session["pendingFollowUps"] = [
            row for row in (session.get("pendingFollowUps") or [])
            if not (row.get("icd10") == superseded_icd10
                    or (row.get("icd10") == surviving_icd10
                        and row.get("questionId") in answered))
        ]
        return session

    # kind is accepted and STORED for the same reason corroborated is, below: it
    # decides whether the customer is asked to correct a phrase, and a fake that
    # swallowed it would let the real code stop distinguishing "we could not
    # identify this" from "this was never a diagnosis" with no suite noticing.
    # Also mirrors the real dedupe: one live call recorded the same phrase six
    # times, four of them inside two seconds.
    def record_unclassified_mention(self, call_id, phrase, item_id, reason="",
                                    kind="unidentified"):
        session = self.sessions[call_id]
        rows = session.setdefault("unclassifiedMentions", [])
        if any(str(r.get("phrase") or "").strip().casefold()
               == str(phrase or "").strip().casefold() for r in rows):
            return session
        if kind == "unidentified" and any(
                str(p or "").strip().casefold() == str(phrase or "").strip().casefold()
                for p in (session.get("noisePhrases") or [])):
            kind = "noise"
        rows.append({"phrase": phrase, "itemId": item_id, "reason": reason,
                     "kind": kind})
        return session

    def mark_noise_phrase(self, call_id, phrase):
        text = str(phrase or "").strip()
        if text:
            self.sessions[call_id].setdefault("noisePhrases", []).append(text)

    # Mirrors common.hvi_state.queue_followups. corroborated and
    # confirmed_by_patient are accepted and STORED, not swallowed: a fake that
    # drops provenance would let the real code stop recording it without any
    # suite noticing, and provenance is the thing that decides human review.
    def queue_followups(self, call_id, code, label, questions, protocol_source,
                        patient_phrase=None, confidence=None,
                        corroborated=None, confirmed_by_patient=None):
        session = self.sessions[call_id]
        for question in questions:
            session["pendingFollowUps"].append({
                "icd10": code, "conditionLabel": label,
                "questionId": question.get("questionId"),
                "prompt": question.get("prompt"),
                "answerType": question.get("answerType", "text"),
                "mandatory": bool(question.get("mandatory")),
                "sensitivity": question.get("sensitivity"),
                "clinicalNote": question.get("clinicalNote"),
                "formField": question.get("formField"),
                "protocolSource": protocol_source,
            })
        session["declaredConditions"].append({
            "icd10": code, "label": label, "protocolSource": protocol_source,
            "patientPhrase": patient_phrase, "confidence": confidence,
            "corroborated": corroborated,
            "confirmedByPatient": confirmed_by_patient,
        })
        return session

    def complete_followup(self, call_id, value):
        session = self.sessions[call_id]
        head = session["pendingFollowUps"].pop(0)
        session["followUpAnswers"][f"{head['icd10']}::{head['questionId']}"] = {
            "value": value, "questionId": head["questionId"], "icd10": head["icd10"],
            "prompt": head.get("prompt"), "protocolSource": head.get("protocolSource"),
            "formField": head.get("formField"),
        }
        return session

    # PURE FUNCTIONS ARE DELEGATED, NOT REIMPLEMENTED.
    #
    # These two derive a verdict from a session dict and touch nothing external,
    # so there is nothing for a fake to fake. Reimplementing them here would mean
    # the suite asserts the harness's opinion of the rule rather than the shipped
    # rule, and a divergence between the two would be invisible - which is exactly
    # the class of bug provenance exists to catch.
    def provenance_review_rows(self, session):
        return real_state.provenance_review_rows(session)

    def classified_by(self, declared_row):
        return real_state.classified_by(declared_row)

    def annotate_turn(self, *args, **kwargs):
        return None

    def mark_status(self, call_id, status, note=None):
        self.sessions[call_id]["status"] = status
        self.sessions[call_id]["statusNote"] = note


class ScriptedExtract:
    def __init__(self):
        self.queue: list[dict] = []
        self.real_coerce = orch.extract.coerce
        self.real_normalise_calendar_date = orch.extract.normalise_calendar_date
        # Deterministic and utterance-driven, so the real one is used rather than
        # scripted: the whole point of grounding is that the caller's words beat
        # whatever the extractor returned, and a fake here would hide that.
        self.real_ground_spoken_year = orch.extract.ground_spoken_year

    def push(self, *readings):
        # Each generated transition owns its extractor outcome. Replacing rather
        # than extending prevents an overlay that legitimately bypasses extraction
        # from leaking its unused reading into the next independent scenario.
        self.queue = copy.deepcopy(list(readings))

    def understand(self, question_text, answer_type, utterance, language, item, customer_name):
        if self.queue:
            reading = self.queue.pop(0)
        else:
            token = utterance.strip().lower()
            value: Any = token
            if token in {"yes", "haan", "true"}:
                value = True
            elif token in {"no", "nahi", "false"}:
                value = False
            reading = {"understood": True, "value": value, "confidence": 1.0}
        reading.setdefault("detectedLanguage", language)
        reading.setdefault("acknowledgement", "")
        reading.setdefault("mentionsCondition", False)
        reading.setdefault("distressSignal", False)
        return reading

    def coerce(self, value, answer_type, item=None):
        return self.real_coerce(value, answer_type, item)

    def normalise_calendar_date(self, value):
        return self.real_normalise_calendar_date(value)

    def ground_spoken_year(self, value, utterance):
        return self.real_ground_spoken_year(value, utterance)

    def __getattr__(self, name):
        """Anything not scripted comes from the real extract module.

        ONLY `understand` needs faking - it is the model call. Everything else in
        extract is deterministic, and using the real implementation is what makes
        these suites meaningful rather than tautological.

        Written as a fallback rather than one proxy method per function because
        the explicit list kept going stale: adding ground_spoken_year to the
        product broke six suites at once with AttributeError, and adding
        normalise_spoken_year broke them again the same afternoon. The failure was
        always in the double, never in the code under test, and it cost a
        fifteen-minute suite run each time.

        Named attributes above still win, so a deliberate stub is never bypassed.
        """
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(_real_extract, name)


class FakeTools:
    def __init__(self, state: MemoryState):
        self.state = state
        self.calls: list[str] = []
        self.submit_failure = False
        self.email_failure = False

    def reset(self):
        self.calls.clear()
        self.submit_failure = False
        self.email_failure = False

    def save_answer(self, call_id, item_id, value, turn_index, **kwargs):
        self.calls.append("save_answer")
        self.state.sessions[call_id]["answers"][item_id] = value
        return self.state.sessions[call_id]

    def classify_condition(self, phrase):
        self.calls.append("classify_condition")
        text = phrase.lower()
        code = next((candidate for candidate in CONDITIONS if candidate.lower() in text), None)
        if not code:
            aliases = {"diabetes": "E11", "sugar": "E11", "breast cancer": "C50",
                       "asthma": "J45", "heart": "I25"}
            code = next((value for key, value in aliases.items() if key in text), None)
        if not code:
            return {"icd10": None, "score": 0.2, "candidates": []}
        condition = CONDITIONS[code]
        confirm = int(condition.get("severityTier", 1)) >= 4
        return {
            "icd10": code, "label_en": condition["label_en"], "label_hi": condition["label_hi"],
            "score": 1.0, "source": "neptune_exact_synonym", "corroborated": True,
            "needsConfirmation": confirm, "redFlag": confirm, "candidates": [],
        }

    def get_followup_questions(self, code, language):
        self.calls.append("get_followup_questions")
        condition = CONDITIONS.get(code)
        if not condition:
            return {"found": False, "followUps": [], "mandatoryCount": 0}
        prompt_key = {"hi": "prompt_hi", "hinglish": "prompt_hinglish"}.get(
            language, "prompt_en")
        rows = []
        for question_id in condition["followUps"]:
            source = FOLLOWUPS[question_id]
            rows.append({
                "questionId": question_id, "prompt": source[prompt_key],
                "answerType": source.get("answerType", "text"),
                "mandatory": bool(source.get("mandatory")),
                "sensitivity": source.get("sensitivity"),
                "clinicalNote": source.get("clinicalNote"),
                "formField": source.get("formField"),
            })
        return {"found": True, "followUps": rows,
                "mandatoryCount": sum(row["mandatory"] for row in rows),
                "protocolSource": f"neptune:Condition/{code} -REQUIRES-> protocol"}

    def get_condition_explanation(self, code, language):
        self.calls.append("get_condition_explanation")
        condition = CONDITIONS[code]
        explanation = EXPLANATIONS[code]
        return {"found": True, "icd10": code, "label": condition["label_en"],
                "explanation": explanation["explain_hi" if language == "hi" else "explain_en"]}

    def check_medication_interactions(self, medications):
        self.calls.append("check_medication_interactions")
        return {"interactionCount": 0, "interactions": []}

    def escalate_to_human(self, call_id, reason, detail):
        self.calls.append("escalate_to_human")
        self.state.mark_status(call_id, "escalated", reason)
        return {"stopQuestionnaire": True}

    def generate_summary(self, call_id):
        self.calls.append("generate_summary")
        session = self.state.sessions[call_id]
        return {"answeredCount": len(session["answers"]), "underwritingFlag": "refer",
                "positiveDeclarations": []}

    def submit_to_crm(self, call_id):
        self.calls.append("submit_to_crm")
        if self.submit_failure:
            raise RuntimeError("CRM unavailable")
        return {"answerCount": len(self.state.sessions[call_id]["answers"])}

    def send_confirmation_email(self, call_id):
        self.calls.append("send_confirmation_email")
        if self.email_failure:
            raise RuntimeError("email unavailable")
        return {"sent": True}


def reading(value=None, **extra):
    return {"understood": True, "value": value, "confidence": 1.0,
            "detectedLanguage": "en", "acknowledgement": "", **extra}


def valid_value(item: dict):
    item_id = item["itemId"]
    if item_id in {"A010", "A020"}:
        return True
    answer_type = item.get("answerType", "text")
    if answer_type == "boolean":
        return False
    if answer_type == "date":
        return "1987-03-22"
    if answer_type == "number":
        validation = item.get("validation") or {}
        low, high = validation.get("min", 1), validation.get("max", 100)
        return (float(low) + float(high)) / 2
    if answer_type == "enum":
        return item["options"][0]
    if answer_type == "list":
        return ["none"]
    if answer_type == "email":
        return "priya@example.invalid"
    return "none"


def prerequisite_answers(item: dict) -> dict:
    answers = {"A020": True}
    by_id = {row["itemId"]: row for row in ITEMS}
    current = item
    seen = set()
    while current.get("showIf"):
        cond = current["showIf"]
        parent = cond["itemId"]
        if parent in seen:
            break
        seen.add(parent)
        op, expected = cond.get("operator", "eq"), cond.get("value")
        answers[parent] = (expected[0] if op == "in" else expected) if op in {"eq", "in"} else "present"
        current = by_id.get(parent, {})
    answers.pop(item["itemId"], None)
    return answers


def main() -> int:  # noqa: PLR0915
    original_state, original_tools, original_extract = orch.st, orch.tools, orch.extract
    state = MemoryState()
    tools = FakeTools(state)
    extract = ScriptedExtract()
    orch.st, orch.tools, orch.extract = state, tools, extract
    try:
        print("[1] Priority lattice")
        item = next(row for row in ITEMS if row["itemId"] == "A100")
        for confirmation, followup, form_item in itertools.product((False, True), repeat=3):
            answers = {} if form_item else {"A100": 170}
            _, session = state.reset([item], answers=answers)
            if confirmation:
                session["awaitingConfirmation"] = {"icd10": "E11", "question": "confirm"}
            if followup:
                session["pendingFollowUps"] = [{"icd10": "E11", "questionId": "FU_DIAG_WHEN",
                                                "prompt": "when", "answerType": "text"}]
            result = orch.next_question(session, state.items, "en")
            expected = "confirmation" if confirmation else "followup" if followup else "item" if form_item else None
            check(f"priority c={confirmation} f={followup} i={form_item}",
                  (result or {}).get("kind") == expected if expected else result is None, str(result))
            session["pendingConditionDetail"] = {"ref": "A200", "phrase": "which"}
            current = orch._current_question(session, state.items, "en", {})  # noqa: SLF001
            check(f"condition-detail dominates c={confirmation} f={followup} i={form_item}",
                  current.get("kind") == "conditionDetail", str(current))

        print("[2] Every active form item records one valid transition")
        for item in ITEMS:
            tools.reset()
            call_id, session = state.reset([item], answers=prerequisite_answers(item))
            value = valid_value(item)
            extract.push(reading(value))
            result = orch.handle_turn(call_id, str(value), 1, "en")
            check(f"{item['itemId']} recorded", item["itemId"] in session["answers"], str(result))
            check(f"{item['itemId']} emits stable envelope",
                  all(key in result for key in ("agentUtterance", "turnIndex", "isComplete",
                                                "escalated", "stopQuestionnaire", "language",
                                                "toolTrace", "notes")), str(result.keys()))

        print("[3] All 13 condition screens take both finite branches")
        screens = [row for row in ITEMS if row.get("followUpGroup") == "condition_detail"]
        check("exactly 13 condition screens", len(screens) == 13, str(len(screens)))
        for screen in screens:
            for answer in (False, True):
                tools.reset()
                call_id, session = state.reset([screen], answers={"A020": True})
                extract.push(reading(answer))
                result = orch.handle_turn(call_id, "yes" if answer else "no", 1, "en")
                check(f"{screen['itemId']}/{answer} records boolean",
                      session["answers"].get(screen["itemId"]) is answer, str(session["answers"]))
                check(f"{screen['itemId']}/positive opens detail; negative does not",
                      bool(session.get("pendingConditionDetail")) is answer, str(result))

        print("[4] Every governed protocol queues exact questions in every language")
        for code, condition in CONDITIONS.items():
            for language in ("en", "hi", "hinglish"):
                tools.reset()
                call_id, session = state.reset([])
                orch._queue_protocol(call_id, code, condition["label_en"], language, [], [],  # noqa: SLF001
                                     patient_phrase="declared", confidence=1.0)
                actual = [row["questionId"] for row in session["pendingFollowUps"]]
                check(f"{code}/{language} exact protocol", actual == condition["followUps"], str(actual))
                check(f"{code}/{language} single declaration",
                      [row["icd10"] for row in session["declaredConditions"]] == [code])
                prompt_key = {"en": "prompt_en", "hi": "prompt_hi", "hinglish": "prompt_hinglish"}[language]
                check(f"{code}/{language} exact prompts",
                      [row["prompt"] for row in session["pendingFollowUps"]]
                      == [FOLLOWUPS[qid][prompt_key] for qid in condition["followUps"]])

        print("[5] Confirmation, detail, and acknowledgement loops are bounded")
        next_item = next(row for row in ITEMS if row["itemId"] == "A100")
        call_id, session = state.reset([next_item], answers={"A020": True},
                                       awaitingConfirmation={"icd10": "E11", "label": "Diabetes",
                                                             "question": "Is that diabetes?",
                                                             "phrase": "sugar", "score": 1.0})
        extract.push(reading(True))
        result = orch.handle_turn(call_id, "yes", 1, "en")
        check("confirmation accept queues protocol", session["awaitingConfirmation"] is None
              and session["declaredConditions"][0]["icd10"] == "E11", str(result))

        call_id, session = state.reset([next_item], answers={"A020": True},
            awaitingConfirmation={"icd10": "E11", "label": "Diabetes", "question": "confirm",
                                  "phrase": "sugar", "score": 0.8,
                                  "alternatives": [{"icd10": "I10", "label_en": "Hypertension",
                                                    "score": 0.7}]})
        extract.push(reading(False))
        result = orch.handle_turn(call_id, "no", 1, "en")
        check("confirmation reject advances candidate",
              (session["awaitingConfirmation"] or {}).get("icd10") == "I10"
              and result.get("askedKind") == "confirmation", str(result))

        call_id, session = state.reset([next_item], answers={"A020": True},
            awaitingConfirmation={"icd10": "E11", "label": "Diabetes", "question": "confirm",
                                  "phrase": "sugar", "score": 0.8})
        for turn in range(3):
            extract.push(reading("maybe"))
            result = orch.handle_turn(call_id, "maybe", turn + 1, "en")
        check("confirmation expires at MAX_REPEATS",
              session["awaitingConfirmation"] is None and bool(session["unclassifiedMentions"]),
              str(result))

        call_id, session = state.reset([next_item], answers={"A020": True},
            pendingConditionDetail={"ref": "A200", "phrase": "Which condition?"})
        for turn in range(3):
            result = orch.handle_turn(call_id, "yes", turn + 1, "en")
        check("bare detail affirmations stop at MAX_REPEATS",
              session["pendingConditionDetail"] is None and bool(session["unclassifiedMentions"]),
              str(result))

        call_id, session = state.reset([next_item], answers={"A020": True},
            pendingConditionDetail={"ref": "A200", "phrase": "Which condition?"})
        for turn in range(3):
            result = orch.handle_turn(call_id, "can you repeat the list", turn + 1, "en")
        check("condition-detail repeat requests stop at MAX_REPEATS",
              session["pendingConditionDetail"] is None
              and bool(session["unclassifiedMentions"])
              and not session["repeatCounts"], str(result))

        call_id, session = state.reset([next_item], answers={"A020": True})
        for turn in range(5):
            if turn >= 2:
                extract.push({"understood": False, "needsRepeat": True, "confidence": 0.0,
                              "detectedLanguage": "en", "acknowledgement": ""})
            result = orch.handle_turn(call_id, "okay", turn + 1, "en")
        check("bare acknowledgements eventually leave the item",
              session["answers"].get("A100") == "not_captured", str(result))

        print("[6] Deferred topic, distress, refusal, and explanation overlays")
        q1 = {"icd10": "C50", "conditionLabel": "Breast cancer", "questionId": "Q1",
              "prompt": "first", "answerType": "text", "protocolSource": "test"}
        q2 = {**q1, "questionId": "Q2", "prompt": "second"}
        call_id, session = state.reset([], answers={
            "A010": True, "A020": True, "A030": "1987-03-22"
        },
            pendingFollowUps=[q1, q2], declaredConditions=[{"icd10": "C50", "label": "Breast cancer"}])
        extract.push(reading("2019", mentionsCondition=True, conditionPhrase="diabetes"))
        first = orch.handle_turn(call_id, "2019 and diabetes", 1, "en")
        check("second condition defers while protocol active",
              session["deferredMentions"] == ["diabetes"] and first.get("askedRef") == "Q2", str(first))
        extract.push(reading("done"))
        second = orch.handle_turn(call_id, "done", 2, "en")
        check("deferred condition drains when protocol ends",
              "E11" in [row["icd10"] for row in session["declaredConditions"]]
              and not session["deferredMentions"], str(second))

        call_id, session = state.reset([next_item], answers={"A020": True})
        extract.push(reading(True, distressSignal=True))
        first = orch.handle_turn(call_id, "I saw him suddenly", 1, "en")
        check("uncorroborated distress asks once", session["distressCheckPending"] is True
              and first.get("escalated") is False, str(first))
        extract.push(reading(False, distressSignal=False))
        second = orch.handle_turn(call_id, "no emergency", 2, "en")
        check("non-corroboration clears scope and reasks original",
              session["distressCheckPending"] is False and "A100" not in session["answers"]
              and second.get("askedRef") == "A100", str(second))

        call_id, session = state.reset([next_item], answers={"A020": True})
        extract.push(reading(True, distressSignal=True))
        result = orch.handle_turn(call_id, "I want to kill myself", 1, "en")
        check("corroborated distress escalates immediately",
              result.get("escalated") is True and result.get("stopQuestionnaire") is True, str(result))

        call_id, session = state.reset([next_item], answers={"A020": True})
        extract.push(reading(None, isRefusal=True))
        first = orch.handle_turn(call_id, "I want to stop", 1, "en")
        second = orch.handle_turn(call_id, "yes", 2, "en")
        check("refusal confirms once then stops",
              first.get("askedKind") == "refusalConfirm" and second.get("stopQuestionnaire") is True
              and session["status"] == "dropped", str(second))

        call_id, session = state.reset([], answers={"A020": True}, pendingFollowUps=[{
            "icd10": "E11", "conditionLabel": "Diabetes", "questionId": "FU_DIAG_WHEN",
            "prompt": "When diagnosed?", "answerType": "text", "protocolSource": "test"}])
        first = orch.handle_turn(call_id, "what is this disease?", 1, "en")
        second = orch.handle_turn(call_id, "yes", 2, "en")
        check("explanation uses authored graph text",
              first.get("askedKind") == "explanationCheck"
              and EXPLANATIONS["E11"]["explain_en"] in first.get("agentUtterance", ""), str(first))
        check("understanding returns to original question",
              second.get("askedKind") == "followup" and second.get("askedRef") == "FU_DIAG_WHEN",
              str(second))

        print("[7] Consent and identity gates")
        by_id = {row["itemId"]: row for row in ITEMS}
        for item_id, value, expected_reason in (
            ("A010", False, "identity_not_confirmed"),
            ("A020", False, "consent_not_granted"),
            ("A030", "1987-03-20", "identity_verification_mismatch"),
        ):
            item = by_id[item_id]
            call_id, session = state.reset([item], answers={"A020": True} if item_id != "A020" else {})
            extract.push(reading(value))
            result = orch.handle_turn(call_id, str(value), 1, "en")
            check(f"{item_id} termination gate", result.get("stopQuestionnaire") is True
                  and session.get("status") == state.STATUS_TERMINATED
                  and session.get("statusNote") == expected_reason, str(result))

            frozen = copy.deepcopy(session)
            tools.reset()
            extract.push(reading(True, mentionsCondition=True,
                                 conditionPhrase="diabetes"))
            later = orch.handle_turn(call_id, "I also have diabetes", 2, "en")
            check(f"{item_id} terminal session rejects later health data",
                  later.get("stopQuestionnaire") is True
                  and not later.get("toolTrace")
                  and not tools.calls
                  and session == frozen
                  and len(extract.queue) == 1,
                  f"later={later}, calls={tools.calls}, session={session}")

        for item_id in ("A010", "A020"):
            item = by_id[item_id]
            call_id, session = state.reset([item])
            extract.push(*(reading("maybe") for _ in range(3)))
            results = [orch.handle_turn(call_id, "maybe", turn, "en")
                       for turn in range(1, 4)]
            expected_reason = ("identity_not_confirmed" if item_id == "A010"
                               else "consent_not_granted")
            check(f"{item_id} coercion exhaustion fails closed",
                  results[-1].get("stopQuestionnaire") is True
                  and session.get("status") == state.STATUS_TERMINATED
                  and session.get("statusNote") == expected_reason,
                  str(results[-1]))

        for terminal_status in (state.STATUS_COMPLETED, state.STATUS_ABANDONED,
                                state.STATUS_ESCALATED):
            call_id, session = state.reset([], status=terminal_status)
            frozen = copy.deepcopy(session)
            tools.reset()
            extract.push(reading(True, mentionsCondition=True,
                                 conditionPhrase="diabetes"))
            result = orch.handle_turn(call_id, "I have diabetes", 1, "en")
            check(f"{terminal_status} session rejects direct later turns",
                  result.get("stopQuestionnaire") is True
                  and not result.get("toolTrace")
                  and not tools.calls
                  and session == frozen
                  and len(extract.queue) == 1,
                  f"result={result}, calls={tools.calls}, session={session}")

        item = by_id["A010"]
        tools.reset()
        call_id, session = state.reset([item])
        extract.push(reading(True, mentionsCondition=True, conditionPhrase="diabetes"))
        orch.handle_turn(call_id, "yes and diabetes", 1, "en")
        check("health mention before consent is not classified",
              not session["declaredConditions"] and "classify_condition" not in tools.calls,
              str(session))

        tools.reset()
        item = by_id["A020"]
        call_id, session = state.reset([item])
        extract.push(reading(True, mentionsCondition=True, conditionPhrase="diabetes"))
        orch.handle_turn(call_id, "yes and diabetes", 1, "en")
        check("same-turn consent still waits for complete identity verification",
              session["answers"].get("A020") is True
              and not session["declaredConditions"]
              and "classify_condition" not in tools.calls,
              str(session))

        tools.reset()
        item = by_id["A030"]
        call_id, session = state.reset(
            [item], answers={"A010": True, "A020": True})
        extract.push(reading("1987-03-22", mentionsCondition=True,
                             conditionPhrase="diabetes"))
        orch.handle_turn(call_id, "22 March 1987 and I have diabetes", 1, "en")
        check("DOB-turn disclosure waits until verification is checkpointed",
              session["answers"].get("A030") == "1987-03-22"
              and any(row["icd10"] == "E11" for row in session["declaredConditions"])
              and "save_answer" in tools.calls
              and "classify_condition" in tools.calls
              and tools.calls.index("save_answer") < tools.calls.index("classify_condition"),
              f"calls={tools.calls}, session={session}")

        for unsafe_dob in (
            "22 March 1987, I mean 23rd",
            "22 March 1987 or 23rd",
            "it isn't 22 March 1987",
            "22 March 1987 23rd",
            "22 March 1987, twenty-third",
            "it shouldn't be 22 March 1987",
        ):
            tools.reset()
            call_id, session = state.reset(
                [item], answers={"A010": True, "A020": True})
            extract.push(reading("1987-03-22", mentionsCondition=True,
                                 conditionPhrase="diabetes"))
            result = orch.handle_turn(call_id, unsafe_dob, 1, "en")
            check(f"ambiguous DOB is not checkpointed: {unsafe_dob}",
                  "A030" not in session["answers"]
                  and not session["declaredConditions"]
                  and "save_answer" not in tools.calls
                  and "classify_condition" not in tools.calls
                  and result.get("stopQuestionnaire") is False,
                  f"calls={tools.calls}, result={result}, session={session}")

        tools.reset()
        call_id, session = state.reset(
            [item], answers={"A010": True, "A020": True}, dateOfBirth=None)
        extract.push(reading("1987-03-22", mentionsCondition=True,
                             conditionPhrase="diabetes"))
        result = orch.handle_turn(call_id, "22 March 1987 and I have diabetes", 1, "en")
        check("missing authoritative DOB fails before collecting or classifying",
              result.get("stopQuestionnaire") is True
              and result.get("askedRef") is None
              and session.get("status") == state.STATUS_TERMINATED
              and session.get("statusNote") == "identity_verification_unavailable"
              and not session["declaredConditions"]
              and "save_answer" not in tools.calls
              and "classify_condition" not in tools.calls
              and len(extract.queue) == 1,
              f"calls={tools.calls}, result={result}, session={session}")

        print("[8] Completion is truthful under submit and email failures")
        call_id, session = state.reset([], summaryAskPending=True, summaryAskCount=1)
        tools.reset()
        result = orch.handle_turn(call_id, "no", 1, "en")
        check("successful completion requires submit and email attempt",
              result.get("isComplete") is True
              and tools.calls[-2:] == ["submit_to_crm", "send_confirmation_email"], str(result))

        call_id, session = state.reset([], summaryAskPending=True, summaryAskCount=1)
        tools.reset(); tools.submit_failure = True
        result = orch.handle_turn(call_id, "no", 1, "en")
        check("submit failure is not successful completion",
              result.get("isComplete") is False and "send_confirmation_email" not in tools.calls
              and "on its way" not in result.get("agentUtterance", ""), str(result))

        call_id, session = state.reset([], summaryAskPending=True, summaryAskCount=1)
        tools.reset(); tools.email_failure = True
        result = orch.handle_turn(call_id, "no", 1, "en")
        check("email failure does not claim email was sent",
              result.get("isComplete") is True and "could not be sent" in result.get("agentUtterance", "")
              and "on its way" not in result.get("agentUtterance", ""), str(result))

        print("[9] Every pending obligation survives resume; attempt counters reset")
        class Table:
            def __init__(self):
                self.rows = {}
            def put_item(self, Item):
                self.rows[Item["callId"]] = Item
            def update_item(self, Key, ExpressionAttributeValues=None, **kwargs):
                row = self.rows.setdefault(Key["callId"], {"callId": Key["callId"]})
                values = ExpressionAttributeValues or {}
                if ":s" in values:
                    row["status"] = values[":s"]
                if ":n" in values:
                    row["statusNote"] = values[":n"]
                return {"Attributes": row}
        class Ddb:
            def __init__(self):
                self.table = Table()
            def Table(self, _name):
                return self.table
        original_ddb = real_state._ddb
        real_state._ddb = Ddb()
        try:
            obligation_values = {
                "awaitingConfirmation": {"icd10": "E11"},
                "pendingConditionDetail": {"ref": "A200", "phrase": "which"},
                "deferredMentions": ["asthma"], "rejectedCodes": ["C50"],
                "unclassifiedMentions": [{"phrase": "rare"}],
                "medCheckPending": {"ref": "FU_TREATMENT"},
                "medChecksAsked": ["FU_TREATMENT"], "distressCheckPending": True,
                "refusalConfirmPending": True, "summaryAskPending": True,
                "summaryAskCount": 1, "explanationPending": {"icd10": "E11"},
            }
            prior = {"callId": "old-call", "answers": {"A010": True},
                     "pendingFollowUps": [q1], "followUpAnswers": {"C50::Q0": {"value": "x"}},
                     "declaredConditions": [{"icd10": "C50"}], "priorCallIds": [],
                     "resumeCount": 0, "language": "hi", "languageConfirmed": True,
                     "repeatCounts": {"A100": 99}, **copy.deepcopy(obligation_values)}
            real_state._ddb.table.rows["old-call"] = copy.deepcopy(prior)
            customer = {"customerId": "C1", "customerName": "Priya",
                        "customerEmail": "p@example.invalid", "dateOfBirth": "1987-03-22",
                        "policyNumber": "P1", "insurerName": "Exact Insurer",
                        "preferredLanguage": "en"}
            resumed = real_state.start_session("+100", customer, "form", resume_from=prior)
            for key, expected in obligation_values.items():
                check(f"resume preserves {key}", resumed.get(key) == expected,
                      f"{resumed.get(key)!r} != {expected!r}")
            check("resume resets attempts", resumed.get("repeatCounts") == {}, str(resumed))
            check("session preserves insurer", resumed.get("insurerName") == "Exact Insurer")
            check("resume preserves protocol state",
                  resumed["pendingFollowUps"] == [q1]
                  and resumed["followUpAnswers"] == prior["followUpAnswers"]
                  and resumed["declaredConditions"] == prior["declaredConditions"])
        finally:
            real_state._ddb = original_ddb

        print("[10] Resumed openings speak every preserved obligation first")
        resumed_overlays = [
            ("explanation", {
                "explanationPending": {"icd10": "E11", "explanation": "Diabetes explanation.",
                                       "questionText": "Original question?",
                                       "askedKind": "followup", "askedRef": "Q1"}},
             "explanationCheck"),
            ("refusal", {"refusalConfirmPending": True}, "refusalConfirm"),
            ("summary", {"summaryAskPending": True, "summaryAskCount": 2},
             "summaryConfirm"),
            ("medicine", {"medCheckPending": {"ref": "Q1", "med": "Warfarin",
                                                "icd10": "E11", "value": "Warfarin"}},
             "medVerify"),
            ("condition detail", {
                "pendingConditionDetail": {"ref": "A200", "phrase": "Which condition?"}},
             "conditionDetail"),
            ("distress", {"distressCheckPending": True}, "distressCheck"),
        ]
        for label, overlay, expected_kind in resumed_overlays:
            tools.reset()
            call_id, session = state.reset([next_item], answers={"A020": True},
                                           resumeCount=1, **overlay)
            result = orch.handle_turn(call_id, "", 0, "en")
            check(f"resume speaks {label} obligation before form item",
                  result.get("askedKind") == expected_kind
                  and result.get("askedRef") != "A100",
                  str(result))
            if label == "summary":
                check("pending second summary is re-spoken, not auto-submitted",
                      "submit_to_crm" not in tools.calls, str(tools.calls))

        print("[11] DialogueState agrees with what handle_turn actually asks")
        # current_dialogue_state is a read-only projection with its own function -
        # see its docstring for why. The strongest test available is not a second,
        # independent reading of the same flags (which could drift from this one
        # in exactly the same way it could drift from handle_turn), but agreement
        # with what handle_turn REALLY does, on the same fixtures [10] already
        # built to exercise every resumed overlay.
        kind_to_state = {
            "confirmation": "awaiting_confirmation",
            "conditionDetail": "condition_detail_probe",
            "explanationCheck": "explanation_pending",
            "explanationClarify": "explanation_pending",
            "explanationCandidateConfirm": "explanation_pending",
            "refusalConfirm": "refusal_confirm",
            "medVerify": "med_verify",
            "distressCheck": "distress_check",
            "summaryConfirm": "summary_confirm",
            "followup": "protocol_followup",
            "item": "form_item",
        }
        for label, overlay, expected_kind in resumed_overlays:
            tools.reset()
            call_id, session = state.reset([next_item], answers={"A020": True},
                                           resumeCount=1, **overlay)
            classified = orch.current_dialogue_state(session, state.items, "en")
            check(f"classifier names the {label} overlay before it is answered",
                  classified.value == kind_to_state[expected_kind],
                  f"{classified} for overlay {overlay}")
            # THEN answer it the way [10] did, through the real dispatch, and
            # confirm the classifier's name matched what handle_turn actually asked.
            result = orch.handle_turn(call_id, "", 0, "en")
            check(f"... which is what handle_turn asked for {label}",
                  kind_to_state.get(result.get("askedKind")) == classified.value,
                  f"asked {result.get('askedKind')!r}, classified {classified}")

        # No overlay pending: an ordinary form item, and the classifier must agree
        # with next_question - the function it delegates to for exactly this case.
        tools.reset()
        call_id, session = state.reset([next_item], answers={"A020": True})
        classified = orch.current_dialogue_state(session, state.items, "en")
        check("no overlay classifies as an ordinary form item",
              classified is orch.DialogueState.FORM_ITEM, str(classified))
        asked = orch.next_question(session, state.items, "en")
        check("... matching what next_question actually returns",
              (asked or {}).get("kind") == "item", str(asked))

        # An unconfirmed language is the one state neither next_question nor
        # _pending_opening_question can see - handle_turn resolves it itself,
        # ahead of both. The classifier must still name it correctly, since a
        # session record with languageConfirmed=False is exactly what a fresh or
        # resumed call looks like before the caller has answered.
        tools.reset()
        call_id, session = state.reset([next_item], answers={},
                                       languageConfirmed=False)
        classified = orch.current_dialogue_state(session, state.items, "en")
        check("an unconfirmed language classifies as awaiting_language",
              classified is orch.DialogueState.AWAITING_LANGUAGE, str(classified))

        # Nothing left to ask: every item answered, no overlay pending.
        tools.reset()
        all_answered = {item["itemId"]: valid_value(item) for item in ITEMS
                        if not item.get("showIf")}
        call_id, session = state.reset(ITEMS, answers=all_answered)
        classified = orch.current_dialogue_state(session, state.items, "en")
        check("a fully answered session classifies as complete",
              classified is orch.DialogueState.COMPLETE, str(classified))
    finally:
        orch.st, orch.tools, orch.extract = original_state, original_tools, original_extract

    print(f"Executed {checks} generated dialogue transition checks.")
    if failures:
        print(f"FAILED: {len(failures)}")
        for failure in failures:
            print("  -", failure)
        return 1
    print("The bounded deterministic dialogue model is closed over all enumerated states and events.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
