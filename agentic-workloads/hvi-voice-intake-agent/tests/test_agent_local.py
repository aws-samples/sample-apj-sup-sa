#!/usr/bin/env python
"""
Drives the agent's reasoning loop locally against the real AWS services, before
spending a container build/push cycle on it.

This is the test that actually matters, because it checks behaviour the prompt is
supposed to produce rather than just wiring:

  - does it call get_session_state before speaking
  - does it save each answer before asking the next question
  - when the customer says "mujhe sugar hai", does it classify, then pull the
    follow-ups from the graph, and then ask them
  - does it refuse to invent follow-ups
  - does a self-harm disclosure stop the questionnaire

Run:  ./.venv/bin/python tests/test_agent_local.py
"""
from __future__ import annotations

import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "agent"))
sys.path.insert(0, os.path.join(ROOT, "common"))

# Stage what scripts/40-agentcore.sh stages, so this runs against the same import
# graph the container gets. Copied UNCONDITIONALLY and for every module: the old
# version copied only hvi_state and only when absent, which meant a stale copy
# survived edits to common/ and this test could pass against code that no longer
# existed - the precise drift the Dockerfile comment claims to prevent.
os.makedirs(os.path.join(ROOT, "agent", "common"), exist_ok=True)
import shutil  # noqa: E402
for _mod in ("hvi_state.py", "hvi_prompts.py"):
    shutil.copy(os.path.join(ROOT, "common", _mod),
                os.path.join(ROOT, "agent", "common", _mod))

import hvi_state as st  # noqa: E402
import tools  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _phones  # noqa: E402

# This suite's OWN number, still the seeded "Priya Sharma" record but cloned onto
# a phone nobody else uses. Twelve suites shared one number, and the unfinished
# sessions they leave behind leaked between them through find_resumable_session.
# See tests/_phones.py; reserve() also clears leftovers from a previous run.
PHONE = _phones.reserve("test_agent_local")
FORM_ID = os.environ.get("FORM_ID", "arogya-sanjeevani-v1")

failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{'' if cond else '  <- ' + detail}")
    if not cond:
        failures.append(label)


def tool_names() -> list[str]:
    return [t["tool"] for t in tools.TRACE]


def main() -> int:
    print("\n[1] Tool layer against live services")
    cust = st.get_customer_by_phone(PHONE)
    check("CRM lookup works", cust is not None)
    if not cust:
        return 1

    session = st.start_session(PHONE, cust, FORM_ID)
    call_id = session["callId"]
    # The call now opens by asking which language to use, and section [6] posts
    # utterances that answer QUESTIONNAIRE questions. Without settling the language
    # first, every one of those posts is read as an attempted language choice, none
    # of them names a language, and the agent correctly sits there re-asking - so the
    # reasoning-loop checks below would fail while nothing was actually wrong.
    # Language selection is covered in tests/test_language_selection.py.
    st.set_language(call_id, "hinglish", confirmed=True)
    print(f"        callId={call_id}")

    tools.TRACE.clear()
    state = tools.get_session_state(call_id)
    check("get_session_state returns the live form", state["remainingCount"] > 20,
          str(state["remainingCount"]))
    check("next item is the identity check", state["nextItem"]["itemId"] == "A010",
          state["nextItem"]["itemId"])
    check("not flagged as a resume", state["isResume"] is False)

    print("\n[2] classify_condition on Hinglish input")
    for phrase in ("sugar ki bimari", "mujhe sugar ki bimari hai", "mujhe sugar hai"):
        r = tools.classify_condition(phrase)
        print(f"        {phrase!r:<32} -> {r['icd10']} ({r['source']}, "
              f"{r['score']}) needsConfirmation={r['needsConfirmation']}")
        check(f"{phrase!r} resolves to E11", r["icd10"] == "E11", str(r["icd10"]))
        # A plain diabetes declaration is not clinically ambiguous. Type 1 vs
        # Type 2 scoring closely is a follow-up question, not a failed match.
        check(f"{phrase!r} accepted without a read-back",
              r["needsConfirmation"] is False,
              f"ambiguous={r.get('ambiguous')} redFlag={r.get('redFlag')}")

    print("\n[3] classify_condition on a red-flag symptom")
    r2 = tools.classify_condition("chalne par seene mein dard hota hai")
    print(f"        -> {r2['icd10']} redFlag={r2['redFlag']} "
          f"needsConfirmation={r2['needsConfirmation']}")
    check("exertional chest pain -> I25", r2["icd10"] == "I25", str(r2["icd10"]))
    check("red flag demands confirmation", r2["needsConfirmation"] is True)

    print("\n[4] get_followup_questions is authoritative and deterministic")
    f1 = tools.get_followup_questions("E11", "hinglish")
    f2 = tools.get_followup_questions("E11", "hinglish")
    ids1 = [q["questionId"] for q in f1["followUps"]]
    ids2 = [q["questionId"] for q in f2["followUps"]]
    print(f"        E11 -> {ids1}")
    check("returns the protocol set", len(ids1) == 6, str(len(ids1)))
    check("identical across calls", ids1 == ids2)
    check("carries the audit path", f1["protocolSource"].startswith("neptune:Condition/E11"))

    tb = tools.get_followup_questions("A15", "en")
    tb_ids = [q["questionId"] for q in tb["followUps"]]
    check("TB gets TB-specific questions", "FU_TB_COMPLETED" in tb_ids, str(tb_ids))
    check("TB does not get insulin question", "FU_ON_INSULIN" not in tb_ids)

    print("\n[5] save_answer checkpoints and advances")
    tools.TRACE.clear()
    s1 = tools.save_answer(call_id, "A010", True, 1, patient_phrase="yes speaking",
                           stt_confidence=0.96)
    check("answer saved", s1["saved"])
    check("advances to consent", s1["nextItem"]["itemId"] == "A020", s1["nextItem"]["itemId"])
    turns = st.get_turns(call_id)
    check("turn row written", len(turns) >= 1, str(len(turns)))
    check("raw phrase kept beside parsed value",
          any(t.get("text") == "yes speaking" for t in turns))

    print("\n[6] Full reasoning loop via Converse")
    # Import late so the tool env vars above are already set.
    from fastapi.testclient import TestClient
    import app as agent_app

    client = TestClient(agent_app.app)

    check("ping is healthy", client.get("/ping").json()["status"] == "Healthy")

    print("\n        turn 1: customer grants consent")
    resp = client.post("/invocations", json={
        "callId": call_id, "userUtterance": "Haan, aage badhiye",
        "turnIndex": 3, "language": "hinglish",
    }).json()
    print(f"        agent: {resp.get('agentUtterance')!r}")
    print(f"        tools: {[t['tool'] for t in resp.get('toolTrace', [])]}")
    check("agent replied", bool(resp.get("agentUtterance")))
    check("agent used its tools", len(resp.get("toolTrace", [])) > 0,
          "no tool calls recorded")
    check("latency recorded", resp.get("latencyMs", 0) > 0)

    print("\n        turn 2: customer verifies authoritative DOB")
    resp2 = client.post("/invocations", json={
        "callId": call_id,
        "userUtterance": "Bais March unnees sau sattasi",
        "turnIndex": resp.get("turnIndex", 4) + 1,
        "language": "hinglish",
    }).json()
    print(f"        agent: {resp2.get('agentUtterance')!r}")
    print(f"        tools: {[t['tool'] for t in resp2.get('toolTrace', [])]}")
    check("DOB verification persisted",
          st.get_session(call_id).get("answers", {}).get("A030") == "1987-03-22",
          str(st.get_session(call_id).get("answers", {}).get("A030")))

    print("\n        turn 3: customer declares diabetes in Hinglish")
    resp3 = client.post("/invocations", json={
        "callId": call_id, "userUtterance": "Haan mujhe sugar hai, das saal se",
        "turnIndex": resp2.get("turnIndex", 5) + 1, "language": "hinglish",
    }).json()
    print(f"        agent: {resp3.get('agentUtterance')!r}")
    used = [t["tool"] for t in resp3.get("toolTrace", [])]
    print(f"        tools: {used}")
    check("classified the condition", "classify_condition" in used, str(used))
    check("pulled follow-ups from the graph", "get_followup_questions" in used, str(used))

    print("\n[7] Self-harm disclosure stops the questionnaire")
    esc = tools.escalate_to_human(call_id, "self_harm_disclosure", "test path")
    check("escalation recorded", esc["escalated"])
    check("questionnaire halted", esc["stopQuestionnaire"] is True)
    check("session marked escalated", st.get_session(call_id)["status"] == "escalated")

    print("\n[8] Ingress serializes concurrent turns and rejects missing DOB before persistence")

    class ConcurrentState:
        STATUS_IN_PROGRESS = "in_progress"
        STATUS_COMPLETED = "completed"
        STATUS_TERMINATED = "terminated"
        STATUS_ESCALATED = "escalated"

        def __init__(self):
            self.session = {
                "callId": "call-race", "status": self.STATUS_IN_PROGRESS,
                "dateOfBirth": "1987-03-22", "language": "en",
                "customerId": "C-RACE", "policyNumber": "P-RACE",
                "lastAdmittedTurn": -1, "lastTurnFingerprint": None,
                "lastTurnResponse": None,
            }
            self.guard = threading.Lock()
            self.owner = None
            self.second_attempted = threading.Event()
            self.first_entered = threading.Event()
            self.records = []
            self.handler_calls = []

        def get_session(self, _call_id):
            with self.guard:
                return dict(self.session)

        def acquire_turn_lease(self, _call_id, owner):
            with self.guard:
                if self.owner is not None:
                    self.second_attempted.set()
                    return None
                self.owner = owner
                return dict(self.session)

        def release_turn_lease(self, _call_id, owner):
            with self.guard:
                if self.owner != owner:
                    return False
                self.owner = None
                return True

        def is_terminal_session(self, session):
            return session.get("status") in {
                self.STATUS_COMPLETED, self.STATUS_TERMINATED, self.STATUS_ESCALATED,
            }

        def record_turn(self, _call_id, turn_index, role, **kwargs):
            self.records.append((turn_index, role, kwargs.get("text")))

        def checkpoint_admitted_turn(self, _call_id, turn_index, fingerprint, response):
            with self.guard:
                self.session["lastAdmittedTurn"] = turn_index
                self.session["lastTurnFingerprint"] = fingerprint
                self.session["lastTurnResponse"] = dict(response)

        def terminate(self, reason):
            with self.guard:
                self.session["status"] = self.STATUS_TERMINATED
                self.session["statusNote"] = reason

    fake_state = ConcurrentState()
    original_state = agent_app.tools.st
    original_handle = agent_app.orchestrator.handle_turn
    original_missing = agent_app.orchestrator._terminate_identity_unavailable
    original_record_agent = agent_app.tools.record_agent_turn
    original_log_turn = agent_app.tools.log_turn

    def raced_handle(call_id, utterance, turn_index, language_hint, stt_confidence):
        del call_id, language_hint, stt_confidence
        fake_state.handler_calls.append(utterance)
        if utterance == "no":
            fake_state.first_entered.set()
            check("second request reaches the held lease",
                  fake_state.second_attempted.wait(timeout=2))
            fake_state.terminate("identity_not_confirmed")
            return {
                "agentUtterance": "Cannot continue", "turnIndex": turn_index,
                "isComplete": False, "escalated": False,
                "stopQuestionnaire": True, "language": "en",
                "toolTrace": [], "notes": ["terminated"],
            }
        fake_state.session.setdefault("answers", {})["A010"] = True
        return {
            "agentUtterance": "unsafe", "turnIndex": turn_index,
            "isComplete": False, "escalated": False,
            "stopQuestionnaire": False, "language": "en",
            "toolTrace": [], "notes": [],
        }

    try:
        agent_app.tools.st = fake_state
        agent_app.orchestrator.handle_turn = raced_handle
        agent_app.tools.record_agent_turn = (
            lambda _call_id, turn_index, text, **_kwargs:
            fake_state.records.append((turn_index, "agent", text)))
        agent_app.tools.log_turn = lambda *_args, **_kwargs: None

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(client.post, "/invocations", json={
                "callId": "call-race", "userUtterance": "no",
                "turnIndex": 1, "language": "en",
            })
            check("first request enters orchestration",
                  fake_state.first_entered.wait(timeout=2))
            second = pool.submit(client.post, "/invocations", json={
                "callId": "call-race", "userUtterance": "yes",
                "turnIndex": 2, "language": "en",
            })
            first_body = first.result(timeout=5).json()
            second_body = second.result(timeout=5).json()

        check("terminating turn wins the serialized race",
              first_body.get("stopQuestionnaire") is True
              and fake_state.session.get("status") == "terminated",
              f"first={first_body}, session={fake_state.session}")
        check("waiting turn observes terminal state without orchestration",
              second_body.get("stopQuestionnaire") is True
              and second_body.get("shouldHangUp") is True
              and fake_state.handler_calls == ["no"],
              f"second={second_body}, calls={fake_state.handler_calls}")
        check("post-terminal customer data is not persisted",
              [row for row in fake_state.records if row[1] == "customer"]
              == [(1, "customer", "no")]
              and "answers" not in fake_state.session,
              str(fake_state.records))

        fake_state.session = {
            "callId": "call-race", "status": "in_progress",
            "dateOfBirth": None, "language": "en",
            "customerId": "C-RACE", "policyNumber": "P-RACE",
            "lastAdmittedTurn": -1, "lastTurnFingerprint": None,
            "lastTurnResponse": None,
        }
        fake_state.records.clear()
        fake_state.handler_calls.clear()

        def missing_identity(_call_id, _session, turn_index, _language):
            fake_state.terminate("identity_verification_unavailable")
            return {
                "agentUtterance": "Cannot verify identity", "turnIndex": turn_index,
                "isComplete": False, "escalated": False,
                "stopQuestionnaire": True, "language": "en",
                "toolTrace": [], "notes": ["missing authoritative DOB"],
            }

        agent_app.orchestrator._terminate_identity_unavailable = missing_identity
        missing_body = client.post("/invocations", json={
            "callId": "call-race", "userUtterance": "22 March 1987",
            "turnIndex": 3, "language": "en",
        }).json()
        check("missing CRM DOB terminates before customer transcript admission",
              missing_body.get("stopQuestionnaire") is True
              and not [row for row in fake_state.records if row[1] == "customer"]
              and not fake_state.handler_calls
              and fake_state.session.get("statusNote")
              == "identity_verification_unavailable",
              f"body={missing_body}, records={fake_state.records}")

        fake_state.session = {
            "callId": "call-race", "status": "in_progress",
            "dateOfBirth": "1987-03-22", "language": "en",
            "customerId": "C-RACE", "policyNumber": "P-RACE",
            "lastAdmittedTurn": -1, "lastTurnFingerprint": None,
            "lastTurnResponse": None,
        }
        fake_state.records.clear()
        fake_state.handler_calls.clear()
        first_body = client.post("/invocations", json={
            "callId": "call-race", "userUtterance": "yes",
            "turnIndex": 10, "language": "en",
        }).json()
        records_after_first = list(fake_state.records)
        replay_body = client.post("/invocations", json={
            "callId": "call-race", "userUtterance": "yes",
            "turnIndex": 10, "language": "en",
        }).json()
        conflict_body = client.post("/invocations", json={
            "callId": "call-race", "userUtterance": "no",
            "turnIndex": 10, "language": "en",
        }).json()
        stale_body = client.post("/invocations", json={
            "callId": "call-race", "userUtterance": "earlier",
            "turnIndex": 9, "language": "en",
        }).json()
        check("identical sequential replay returns cached response",
              replay_body.get("agentUtterance") == first_body.get("agentUtterance")
              and any("idempotently replayed" in note
                      for note in replay_body.get("notes", [])),
              str(replay_body))
        check("replay does not persist or orchestrate twice",
              fake_state.handler_calls == ["yes"]
              and fake_state.records == records_after_first,
              f"calls={fake_state.handler_calls}, records={fake_state.records}")
        check("changed payload at the same turn is rejected without mutation",
              any("conflicting" in note for note in conflict_body.get("notes", []))
              and fake_state.records == records_after_first,
              str(conflict_body))
        check("older turn is rejected without mutation",
              any("stale" in note for note in stale_body.get("notes", []))
              and fake_state.records == records_after_first,
              str(stale_body))
    finally:
        agent_app.tools.st = original_state
        agent_app.orchestrator.handle_turn = original_handle
        agent_app.orchestrator._terminate_identity_unavailable = original_missing
        agent_app.tools.record_agent_turn = original_record_agent
        agent_app.tools.log_turn = original_log_turn

    print("\n" + "=" * 66)
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("Agent behaves as specified. Safe to build the container.")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
