#!/usr/bin/env python
"""
Exercises the DEPLOYED AgentCore runtime, not a local process.

Proves the container, the execution role, the in-VPC graph Lambda, OpenSearch,
DynamoDB and Bedrock all work together from inside AgentCore rather than from a
laptop holding admin credentials.

Walks a realistic call: the language question, identity, consent, a volunteered
diabetes declaration, then a forced drop mid-protocol and a resumed leg that must
still owe the outstanding Neptune follow-ups.

The caller CHOOSES ENGLISH AND THEN ANSWERS IN HINGLISH throughout, which is the
combination worth testing on the deployed runtime: the agent has to understand
code-mixed speech without drifting off the language it was explicitly asked for.

Run:  ./.venv/bin/python tests/test_agentcore_deployed.py
"""
from __future__ import annotations

import json
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "common"))

import boto3  # noqa: E402
import hvi_state as st  # noqa: E402

REGION = os.environ.get("DATA_REGION", "ap-south-1")
RUNTIME_ARN = os.environ.get("AGENT_RUNTIME_ARN", "")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _phones  # noqa: E402

# This suite's OWN number, still the seeded "Priya Sharma" record but cloned onto
# a phone nobody else uses. Twelve suites shared one number, and the unfinished
# sessions they leave behind leaked between them through find_resumable_session.
# See tests/_phones.py; reserve() also clears leftovers from a previous run.
PHONE = _phones.reserve("test_agentcore_deployed")
FORM_ID = os.environ.get("FORM_ID", "arogya-sanjeevani-v1")

ac = boto3.client("bedrock-agentcore", region_name=REGION)
failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{'' if cond else '  <- ' + detail}")
    if not cond:
        failures.append(label)


class RemoteCall:
    def __init__(self, call_id: str):
        self.call_id = call_id
        self.turn = 0
        self.last_request_turn = 0
        # AgentCore requires a runtime session id of at least 33 characters.
        self.session_id = f"hvi-{uuid.uuid4()}-{uuid.uuid4().hex[:8]}"
        self.tools: list[str] = []
        self.notes: list[str] = []
        self.latencies: list[int] = []

    def say(self, utterance: str = "", *, turn_index: int | None = None,
            stt_confidence: float | None = None) -> dict:
        request_turn = self.turn if turn_index is None else turn_index
        payload_body = {
            "callId": self.call_id,
            "userUtterance": utterance,
            "turnIndex": request_turn,
            "language": "hinglish",
        }
        if stt_confidence is not None:
            payload_body["sttConfidence"] = stt_confidence
        payload = json.dumps(payload_body).encode()
        r = ac.invoke_agent_runtime(
            agentRuntimeArn=RUNTIME_ARN,
            runtimeSessionId=self.session_id,
            payload=payload,
            contentType="application/json",
            accept="application/json",
        )
        resp = json.loads(r["response"].read())

        used = [t["tool"] for t in resp.get("toolTrace", [])]
        self.tools.extend(used)
        self.notes.extend(resp.get("notes") or [])
        if resp.get("latencyMs"):
            self.latencies.append(resp["latencyMs"])

        if utterance:
            print(f"    customer > {utterance}")
        print(f"    agent    > {resp.get('agentUtterance')}")
        if used:
            print(f"    tools    : {used}  ({resp.get('latencyMs')}ms)")
        if resp.get("notes"):
            print(f"    notes    : {resp['notes']}")

        self.last_request_turn = request_turn
        if turn_index is None:
            self.turn = resp.get("turnIndex", self.turn) + 2
        return resp


def preverified_session(customer: dict) -> dict:
    """Create a text-only session positioned at the first health question."""
    session = st.start_session(PHONE, customer, FORM_ID)
    call_id = session["callId"]
    st.set_language(call_id, "en", confirmed=True)
    st.save_answer(call_id, "A010", True, 1)
    st.save_answer(call_id, "A020", True, 3)
    st.save_answer(call_id, "A030", customer["dateOfBirth"], 5)
    return st.get_session(call_id)


def main() -> int:
    if not RUNTIME_ARN:
        print("AGENT_RUNTIME_ARN not set - source config/env.sh")
        return 1
    print(f"runtime: {RUNTIME_ARN}\n")

    cust = st.get_customer_by_phone(PHONE)
    if not cust:
        print("seed customer missing")
        return 1

    print("[0a] Sequential replay and conflicting replay are mutation-free")
    replay_session = preverified_session(cust)
    replay_call = RemoteCall(replay_session["callId"])
    first = replay_call.say("170 centimetres", turn_index=7)
    after_first = st.get_session(replay_session["callId"])
    turns_after_first = st.get_turns(replay_session["callId"])
    replayed = replay_call.say("170 centimetres", turn_index=7)
    conflicted = replay_call.say("I have diabetes", turn_index=7)
    after_replays = st.get_session(replay_session["callId"])
    turns_after_replays = st.get_turns(replay_session["callId"])
    check("first text turn checkpointed",
          after_first.get("lastAdmittedTurn") == 7
          and (after_first.get("answers") or {}).get("A100") == 170,
          str(after_first))
    check("identical replay returns cached response",
          replayed.get("agentUtterance") == first.get("agentUtterance")
          and any("idempotently replayed" in note
                  for note in replayed.get("notes", [])), str(replayed))
    check("changed same-index payload is rejected",
          any("conflicting" in note for note in conflicted.get("notes", [])),
          str(conflicted))
    check("replays do not change state or transcript",
          after_replays.get("answers") == after_first.get("answers")
          and after_replays.get("declaredConditions") == after_first.get("declaredConditions")
          and len(turns_after_replays) == len(turns_after_first),
          f"first={after_first}, after={after_replays}, "
          f"turns={len(turns_after_first)}->{len(turns_after_replays)}")

    print("\n[0b] Mixed and multiple condition scopes survive deployed extraction")
    mixed_session = preverified_session(cust)
    mixed_call = RemoteCall(mixed_session["callId"])
    mixed_call.say(
        "170 centimetres. My father had diabetes, but I have asthma.",
        turn_index=7)
    mixed_after = st.get_session(mixed_session["callId"])
    mixed_codes = [row.get("icd10") for row in mixed_after.get("declaredConditions") or []]
    check("family condition excluded while caller condition retained",
          "J45" in mixed_codes and "E11" not in mixed_codes,
          f"codes={mixed_codes}, session={mixed_after}")

    multiple_session = preverified_session(cust)
    multiple_call = RemoteCall(multiple_session["callId"])
    multiple_call.say("170 centimetres. I have diabetes and asthma.", turn_index=7)
    multiple_after = st.get_session(multiple_session["callId"])
    multiple_codes = [row.get("icd10") for row in
                      multiple_after.get("declaredConditions") or []]
    deferred = multiple_after.get("deferredMentions") or []
    check("two current conditions preserve both obligations",
          "E11" in multiple_codes and any("asthma" in phrase.lower() for phrase in deferred),
          f"codes={multiple_codes}, deferred={deferred}")

    low_session = preverified_session(cust)
    low_call = RemoteCall(low_session["callId"])
    low_call.say("170 centimetres. I have diabetes.", turn_index=7,
                 stt_confidence=0.2)
    low_after = st.get_session(low_session["callId"])
    check("low-confidence condition cannot mint deployed diagnosis",
          not (low_after.get("declaredConditions") or [])
          and not (low_after.get("deferredMentions") or [])
          and (low_after.get("answers") or {}).get("A100") == 170,
          str(low_after))

    print("\n[0c] Summary corrections never silently finalize")
    correction_session = preverified_session(cust)
    st.record_summary_ask(correction_session["callId"], True)
    correction_call = RemoteCall(correction_session["callId"])
    correction_response = correction_call.say(
        "All correct except my weight is 68 kilograms.", turn_index=7)
    correction_after = st.get_session(correction_session["callId"])
    check("unmapped deployed correction escalates without completion",
          correction_response.get("escalated") is True
          and correction_response.get("isComplete") is False
          and correction_after.get("status") == st.STATUS_ESCALATED
          and bool(correction_after.get("unclassifiedMentions")),
          f"response={correction_response}, session={correction_after}")

    repeat_summary_session = preverified_session(cust)
    st.record_summary_ask(repeat_summary_session["callId"], True)
    repeat_summary_call = RemoteCall(repeat_summary_session["callId"])
    repeated_summary = repeat_summary_call.say(
        "Please repeat the summary.", turn_index=7)
    repeat_summary_after = st.get_session(repeat_summary_session["callId"])
    check("deployed summary repeat preserves correction round",
          repeated_summary.get("askedKind") == "summaryConfirm"
          and repeat_summary_after.get("summaryAskCount") == 1
          and repeat_summary_after.get("summaryAskPending") is True,
          f"response={repeated_summary}, session={repeat_summary_after}")

    print("\n[1] Terminal sessions reject and do not persist later health data")
    terminal = st.start_session(PHONE, cust, FORM_ID)
    st.mark_status(terminal["callId"], st.STATUS_TERMINATED,
                   note="identity_verification_mismatch")
    terminal_before = st.get_session(terminal["callId"])
    turns_before = st.get_turns(terminal["callId"])
    terminal_call = RemoteCall(terminal["callId"])
    terminal_response = terminal_call.say(
        "Meri height ek sau pachhattar centimetre hai, aur mujhe sugar bhi hai")
    terminal_after = st.get_session(terminal["callId"])
    turns_after = st.get_turns(terminal["callId"])
    check("terminal response forces hangup without tools",
          terminal_response.get("stopQuestionnaire") is True
          and terminal_response.get("shouldHangUp") is True
          and not terminal_response.get("toolTrace"),
          str(terminal_response))
    check("post-terminal health utterance is not transcribed",
          len(turns_after) == len(turns_before),
          f"before={len(turns_before)}, after={len(turns_after)}")
    check("post-terminal health state is immutable",
          terminal_after.get("status") == st.STATUS_TERMINATED
          and terminal_after.get("answers") == terminal_before.get("answers")
          and terminal_after.get("declaredConditions") == terminal_before.get("declaredConditions")
          and terminal_after.get("pendingFollowUps") == terminal_before.get("pendingFollowUps"),
          str(terminal_after))

    session = st.start_session(PHONE, cust, FORM_ID)
    c = RemoteCall(session["callId"])
    print(f"callId: {c.call_id}")

    print("\n[1] Opening turn asks which language, before anything else")
    r = c.say()
    check("agent opened the call", bool(r.get("agentUtterance")))
    check("asked which language", r.get("askedKind") == "language",
          str(r.get("askedKind")))
    check("the question is bilingual",
          "English" in (r.get("agentUtterance") or "")
          and "हिंदी" in (r.get("agentUtterance") or ""),
          r.get("agentUtterance", "")[:70])
    check("no model call to read a language name", not r.get("toolTrace"),
          str(r.get("toolTrace")))

    # ENGLISH, while the rest of this call answers in HINGLISH. That combination is
    # deliberate and is the interesting one: it is the case where the agent must
    # understand code-mixed speech without abandoning the language the customer
    # explicitly asked for. Answering in Hindi here would test the easy path.
    print("\n[1b] Choosing English starts the questionnaire in English")
    r = c.say("English")
    check("used the customer's name", "Priya" in (r.get("agentUtterance") or ""),
          r.get("agentUtterance", "")[:60])
    check("moved on to a form item", r.get("askedKind") == "item",
          str(r.get("askedKind")))
    check("no markdown leaked", "**" not in (r.get("agentUtterance") or ""))
    live = st.get_session(c.call_id)
    check("choice persisted as en", live.get("language") == "en",
          str(live.get("language")))
    check("the choice was not recorded as a form answer",
          not (live.get("answers") or {}), str(live.get("answers")))

    print("\n[2] Identity and consent are checkpointed")
    c.say("Haan, Priya bol rahi hoon")
    c.say("Haan theek hai, poochhiye")
    live = st.get_session(c.call_id)
    check("A010 saved", "A010" in (live.get("answers") or {}), str(live.get("answers")))
    check("A020 saved", "A020" in (live.get("answers") or {}), str(live.get("answers")))
    check("save_answer ran on the server", "save_answer" in c.tools, str(c.tools))

    print("\n[3] Volunteered diabetes triggers the Neptune protocol")
    dob_response = c.say("Bais March unnees sau sattasi")
    live = st.get_session(c.call_id)
    dob_ok = (not dob_response.get("stopQuestionnaire")
              and not dob_response.get("shouldHangUp")
              and (live.get("answers") or {}).get("A030") == cust.get("dateOfBirth")
              and live.get("status") == st.STATUS_IN_PROGRESS)
    check("Roman-Hindi DOB passes exact CRM verification", dob_ok,
          f"response={dob_response}, A030={(live.get('answers') or {}).get('A030')}, "
          f"expected={cust.get('dateOfBirth')}, status={live.get('status')}")
    if not dob_ok:
        print("Refusing to submit health data after a failed identity gate.")
        return 1

    r = c.say("Meri height ek sau pachhattar centimetre hai, aur mujhe sugar bhi hai")
    check("classify_condition ran", "classify_condition" in c.tools, str(c.tools))
    check("get_followup_questions ran", "get_followup_questions" in c.tools, str(c.tools))

    live = st.get_session(c.call_id)
    pending = live.get("pendingFollowUps") or []
    declared = live.get("declaredConditions") or []
    print(f"        declared: {[d['icd10'] for d in declared]}")
    print(f"        queued:   {[p['questionId'] for p in pending]}")
    check("protocol follow-ups queued", len(pending) >= 4, str(len(pending)))
    check("E11 declared", any(d["icd10"] == "E11" for d in declared), str(declared))
    check("traversal path recorded",
          bool(pending and str(pending[0].get("protocolSource", "")).startswith("neptune:")),
          str(pending[0].get("protocolSource") if pending else None))

    print("\n[4] Force a drop mid-protocol")
    st.mark_status(c.call_id, st.STATUS_DROPPED, note="forced drop during deployed test")
    before = st.get_session(c.call_id)
    owed = len(before.get("pendingFollowUps") or [])
    answered = len(before.get("answers") or {})
    print(f"        at drop: {answered} answers, {owed} follow-ups still owed")

    print("\n[5] Callback resumes and still owes the protocol")
    resumable = st.find_resumable_session(PHONE)
    check("dropped call found by phone", resumable and resumable["callId"] == c.call_id)
    s2 = st.start_session(PHONE, cust, FORM_ID, resume_from=resumable)
    c2 = RemoteCall(s2["callId"])
    print(f"        resumed callId: {c2.call_id}")

    r = c2.say()
    check("answers carried over", len(s2.get("answers") or {}) == answered,
          f"{len(s2.get('answers') or {})} vs {answered}")
    check("follow-ups still owed", len(s2.get("pendingFollowUps") or []) == owed,
          f"{len(s2.get('pendingFollowUps') or [])} vs {owed}")
    check("resume acknowledged to the caller",
          any("resumed" in n for n in c2.notes), str(c2.notes))

    # A RESUMED CALL RE-VERIFIES IDENTITY BEFORE CONTINUING, deliberately: the
    # previous leg's answers are about to be attributed to whoever picked up. This
    # used to demand askedKind == "followup" here, which predates that decision,
    # and the demand then poisoned the next check too - the date below was answered
    # into the identity question, misheard, and no follow-up was ever reached.
    check("resumes by re-verifying identity, not by continuing blind",
          r.get("askedKind") == "item" and r.get("askedRef") == "A010",
          f"{r.get('askedKind')}/{r.get('askedRef')}")
    check("no unrendered placeholder in the resumed opening",
          "{{" not in (r.get("agentUtterance") or ""), r.get("agentUtterance"))

    # Clear the identity queue the way a real caller would, then the protocol
    # resumes on what it still owes.
    r = c2.say("Haan Priya bol rahi hoon")
    check("no unrendered placeholder in the identity re-verification",
          "{{" not in (r.get("agentUtterance") or ""), r.get("agentUtterance"))
    r = c2.say("Bais March unnees sau sattasi")
    check("once identity clears, the protocol resumes on the owed follow-up",
          r.get("askedKind") == "followup", str(r.get("askedKind")))

    r = c2.say("Two thousand fifteen se")
    live2 = st.get_session(c2.call_id)
    check("follow-up answered on the resumed leg",
          len(live2.get("followUpAnswers") or {}) >= 1,
          str(live2.get("followUpAnswers")))

    print("\n[6] Transcript and audit state")
    turns = st.get_turns(c.call_id) + st.get_turns(c2.call_id)
    roles = {t.get("role") for t in turns}
    check("both sides transcribed", {"agent", "customer"} <= roles, str(roles))
    check("turns persisted across both legs", len(turns) >= 10, str(len(turns)))
    check("resume lineage linked", c.call_id in (s2.get("priorCallIds") or []),
          str(s2.get("priorCallIds")))

    if c.latencies + c2.latencies:
        lat = c.latencies + c2.latencies
        avg = sum(lat) // len(lat)
        print(f"\n        turn latency: avg {avg}ms, max {max(lat)}ms over {len(lat)} turns")
        check("average turn latency under 4s", avg < 4000, f"{avg}ms")

    print("\n" + "=" * 70)
    if failures:
        print(f"FAILED: {len(failures)} check(s)")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("Deployed runtime enforces the protocol end to end, including resume.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
