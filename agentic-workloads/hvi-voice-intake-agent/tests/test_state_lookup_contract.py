"""Hermetic contracts for deterministic CRM projection lookup."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

from botocore.exceptions import ClientError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "common"))

import hvi_state as state  # noqa: E402


class Table:
    def __init__(self):
        self.calls: list[dict] = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        if "ExclusiveStartKey" not in kwargs:
            return {
                "Items": [
                    {"customerId": "old", "phone": "+100",
                     "sourceLastModified": "2026-01-01T00:00:00+00:00",
                     "syncedAt": "2026-01-01T00:00:01+00:00"},
                    {"customerId": "middle", "phone": "+100",
                     "sourceLastModified": "2026-02-01T00:00:00+00:00",
                     "syncedAt": "2026-02-01T00:00:01+00:00"},
                ],
                "LastEvaluatedKey": {"phone": "+100", "customerId": "middle"},
            }
        return {"Items": [
            {"customerId": "new", "phone": "+100",
             "sourceLastModified": "2026-03-01T00:00:00+00:00",
             "syncedAt": "2026-03-01T00:00:01+00:00"},
        ]}


class SessionTable:
    def __init__(self, items):
        self.items = items
        self.calls: list[dict] = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        return {"Items": self.items}


class Ddb:
    def __init__(self, table):
        self.table = table

    def Table(self, _name):
        return self.table


class LeaseTable:
    """Small conditional-update model for the per-call DynamoDB lease."""

    def __init__(self):
        self.row = {"callId": "call-lease", "status": state.STATUS_IN_PROGRESS}

    @staticmethod
    def _conditional_failure(operation: str):
        raise ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException",
                       "Message": operation}}, operation)

    def update_item(self, **kwargs):
        values = state.from_ddb(kwargs.get("ExpressionAttributeValues") or {})
        expression = kwargs["UpdateExpression"]
        if expression.startswith("SET turnLeaseOwner"):
            if "turnLeaseOwner" in self.row:
                self._conditional_failure("acquire")
            self.row["turnLeaseOwner"] = values[":owner"]
            self.row["turnLeaseUntil"] = int(values[":until"])
            self.row["updatedAt"] = values[":updated"]
            return {"Attributes": state.to_ddb(dict(self.row))}
        if expression.startswith("REMOVE turnLeaseOwner"):
            if self.row.get("turnLeaseOwner") != values[":owner"]:
                self._conditional_failure("release")
            self.row.pop("turnLeaseOwner", None)
            self.row.pop("turnLeaseUntil", None)
            self.row["updatedAt"] = values[":updated"]
            return {"Attributes": state.to_ddb(dict(self.row))}
        raise AssertionError(expression)


def load_sma_handler():
    os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
    os.environ.setdefault("AUDIO_BUCKET", "unit-test-audio")
    path = Path(ROOT) / "lambdas" / "sma" / "handler.py"
    spec = importlib.util.spec_from_file_location("hvi_sma_contract_handler", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def main() -> int:
    failures: list[str] = []
    checks = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal checks
        checks += 1
        print(f"  {'PASS' if ok else 'FAIL'}  {label}" +
              (f"  <- {detail}" if detail and not ok else ""))
        if not ok:
            failures.append(label)

    table = Table()
    original = state._ddb
    state._ddb = Ddb(table)
    try:
        customer = state.get_customer_by_phone("+100")
    finally:
        state._ddb = original

    check("all phone-index pages are read", len(table.calls) == 2,
          str(table.calls))
    check("phone-index query is not arbitrarily limited to one row",
          all("Limit" not in call for call in table.calls), str(table.calls))
    check("latest Salesforce projection wins", customer.get("customerId") == "new",
          str(customer))

    print("[2] Only recoverable interruptions are resumable")
    # STATUS is the dimension under test here, so every row carries one recorded
    # answer to hold the other disqualifiers constant. find_resumable_session also
    # refuses a session with nothing to carry (see tests/test_resume_guards.py [9]),
    # and without an answer the carrier-drop row would be rejected for being empty
    # rather than for its status - passing or failing for the wrong reason.
    session_cases = [
        ({"callId": "new-terminal", "phone": "+100", "status": state.STATUS_TERMINATED,
          "statusNote": "identity_verification_mismatch",
          "answers": {"A010": True}}, None),
        ({"callId": "legacy-terminal", "phone": "+100", "status": state.STATUS_DROPPED,
          "statusNote": "identity_verification_mismatch",
          "answers": {"A010": True}}, None),
        ({"callId": "completed", "phone": "+100", "status": state.STATUS_COMPLETED,
          "answers": {"A010": True}}, None),
        ({"callId": "carrier-drop", "phone": "+100", "status": state.STATUS_DROPPED,
          "statusNote": "carrier disconnected",
          "answers": {"A010": True}}, "carrier-drop"),
    ]
    for row, expected_call_id in session_cases:
        session_table = SessionTable([row])
        state._ddb = Ddb(session_table)
        try:
            resumed = state.find_resumable_session("+100")
        finally:
            state._ddb = original
        actual_call_id = (resumed or {}).get("callId")
        check(f"{row['callId']} resume eligibility is safe",
              actual_call_id == expected_call_id,
              f"expected={expected_call_id}, actual={actual_call_id}")

    check("legacy dropped security failure is terminal",
          state.is_terminal_session({"status": state.STATUS_DROPPED,
                                     "statusNote": "identity_not_confirmed"}))
    check("ordinary dropped call remains recoverable",
          not state.is_terminal_session({"status": state.STATUS_DROPPED,
                                         "statusNote": "carrier disconnected"}))

    hangup_cases = [
        ({"status": state.STATUS_TERMINATED}, False, False, state.STATUS_TERMINATED),
        ({"status": state.STATUS_TERMINATED}, False, True, state.STATUS_TERMINATED),
        ({"status": state.STATUS_TERMINATED}, True, False, state.STATUS_TERMINATED),
        ({"status": state.STATUS_COMPLETED}, False, True, state.STATUS_COMPLETED),
        ({"status": state.STATUS_ESCALATED}, True, False, state.STATUS_ESCALATED),
        ({"status": state.STATUS_DROPPED,
          "statusNote": "identity_verification_mismatch"}, False, False,
         state.STATUS_DROPPED),
        ({"status": state.STATUS_ESCALATED}, False, False, state.STATUS_ESCALATED),
        ({"status": state.STATUS_IN_PROGRESS}, False, False, state.STATUS_DROPPED),
        ({"status": state.STATUS_IN_PROGRESS}, False, True, state.STATUS_ESCALATED),
        ({"status": state.STATUS_IN_PROGRESS}, True, False, state.STATUS_COMPLETED),
    ]
    for session, is_complete, escalated, expected in hangup_cases:
        actual = state.status_after_agent_hangup(
            session, is_complete=is_complete, escalated=escalated)
        check(f"hangup preserves or advances {session['status']} safely",
              actual == expected, f"expected={expected}, actual={actual}")

    print("[3] Turn leases serialize workers and are owner-safe")
    lease_table = LeaseTable()
    state._ddb = Ddb(lease_table)
    try:
        first = state.acquire_turn_lease("call-lease", "worker-1", lease_seconds=60)
        blocked = state.acquire_turn_lease("call-lease", "worker-2", lease_seconds=60)
        wrong_release = state.release_turn_lease("call-lease", "worker-2")
        right_release = state.release_turn_lease("call-lease", "worker-1")
        second = state.acquire_turn_lease("call-lease", "worker-2", lease_seconds=60)
        lease_table.row["turnLeaseUntil"] = 0
        expired_takeover = state.acquire_turn_lease(
            "call-lease", "worker-3", lease_seconds=60)
    finally:
        state._ddb = original
    check("first worker acquires the session image",
          first and first.get("turnLeaseOwner") == "worker-1", str(first))
    check("overlapping worker is blocked", blocked is None, str(blocked))
    check("non-owner cannot release the lease", wrong_release is False)
    check("owner releases the lease", right_release is True)
    check("next worker acquires after release",
          second and second.get("turnLeaseOwner") == "worker-2", str(second))
    check("expired age signal does not displace a possibly paused owner",
          expired_takeover is None, str(expired_takeover))

    print("[4] SMA preserves terminal decisions under stale response flags")
    sma = load_sma_handler()

    class FakeSmaState:
        STATUS_IN_PROGRESS = state.STATUS_IN_PROGRESS
        STATUS_DROPPED = state.STATUS_DROPPED
        STATUS_COMPLETED = state.STATUS_COMPLETED
        STATUS_TERMINATED = state.STATUS_TERMINATED
        STATUS_ESCALATED = state.STATUS_ESCALATED

        def __init__(self, session):
            self.session = dict(session)
            self.mark_calls = []
            self.attach_calls = []

        def get_session(self, _call_id):
            return dict(self.session)

        def is_terminal_session(self, session):
            return state.is_terminal_session(session)

        def attach_call_identifiers(self, *args):
            self.attach_calls.append(args)

        def status_after_agent_hangup(self, session, **flags):
            return state.status_after_agent_hangup(session, **flags)

        def mark_status(self, call_id, status, note=None):
            self.mark_calls.append((call_id, status, note))

    event = {
        "InvocationEventType": "ACTION_SUCCESSFUL",
        "CallDetails": {"TransactionAttributes": {
            "hviCallId": "call-terminal", "language": "en", "turn": "2"}},
        "ActionData": {"Type": "RecordAudio", "RecordingDestination": {
            "BucketName": "unit-test-audio", "Key": "recordings/turn.wav"}},
    }
    original_st = sma.st
    original_transcribe = sma.transcribe_recording
    original_ask = sma.ask_agent
    original_synthesise = sma.synthesise
    try:
        sma.transcribe_recording = lambda _bucket, _key: {"text": "hello"}
        sma.ask_agent = lambda *_args, **_kwargs: {
            "agentUtterance": "Stale continuation", "shouldHangUp": False,
            "isComplete": False, "escalated": False, "turnIndex": 3,
            "language": "en",
        }
        sma.synthesise = lambda *_args, **_kwargs: "prompts/closing.wav"
        for terminal in (
            {"status": state.STATUS_TERMINATED,
             "statusNote": "identity_verification_mismatch"},
            {"status": state.STATUS_COMPLETED, "statusNote": "intake complete"},
            {"status": state.STATUS_ESCALATED, "statusNote": "human support"},
        ):
            fake_state = FakeSmaState(terminal)
            sma.st = fake_state
            response = sma.lambda_handler(event, None)
            check(f"SMA does not rewrite persisted {terminal['status']}",
                  fake_state.mark_calls == [], str(fake_state.mark_calls))
            check(f"SMA overrides stale continuation for {terminal['status']}",
                  any(action.get("Type") == "Hangup"
                      for action in response.get("Actions", []))
                  and not any(action.get("Type") == "RecordAudio"
                              for action in response.get("Actions", [])),
                  str(response))

            answered_event = {
                "InvocationEventType": "CALL_ANSWERED",
                "CallDetails": {"Participants": [{"CallId": "sip-1"}],
                                "TransactionId": "tx-1",
                                "TransactionAttributes": {
                                    "hviCallId": "call-terminal",
                                    "language": "en", "turn": "0"}},
            }
            answered = sma.lambda_handler(answered_event, None)
            check(f"CALL_ANSWERED refuses terminal {terminal['status']}",
                  any(action.get("Type") == "Hangup"
                      for action in answered.get("Actions", []))
                  and not any(action.get("Type") == "RecordAudio"
                              for action in answered.get("Actions", [])),
                  str(answered))

        class TerminalAfterAgent(FakeSmaState):
            def __init__(self):
                super().__init__({"status": state.STATUS_IN_PROGRESS})
                self.reads = 0

            def get_session(self, _call_id):
                self.reads += 1
                if self.reads >= 2:
                    return {"status": state.STATUS_TERMINATED,
                            "statusNote": "identity_not_confirmed"}
                return {"status": state.STATUS_IN_PROGRESS}

        transitioned = TerminalAfterAgent()
        sma.st = transitioned
        transitioned_response = sma.lambda_handler(event, None)
        check("terminal status written during AgentCore turn overrides stale response",
              any(action.get("Type") == "Hangup"
                  for action in transitioned_response.get("Actions", []))
              and not any(action.get("Type") == "RecordAudio"
                          for action in transitioned_response.get("Actions", [])),
              str(transitioned_response))
    finally:
        sma.st = original_st
        sma.transcribe_recording = original_transcribe
        sma.ask_agent = original_ask
        sma.synthesise = original_synthesise

    print(f"Executed {checks} state lookup contract checks.")
    if failures:
        print(f"FAILED: {len(failures)}")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("Phone lookup is paginated and deterministic across reused numbers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
