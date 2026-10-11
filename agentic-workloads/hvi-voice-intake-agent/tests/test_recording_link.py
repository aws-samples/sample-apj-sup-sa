#!/usr/bin/env python3
"""The recording actually gets LINKED to the call, not just uploaded.

WHY THIS SUITE EXISTS
---------------------
tests/test_call_recording.py proves a real stereo WAV reaches S3 under the call's
own prefix. tests/test_call_archive.py proves the transcript is written and that
the writeback reads session.recordingUri. Both passed while the feature was still
broken end to end, because nothing tested the SEAM between them.

Measured on the live bucket: one call had calls/<id>/audio/call.wav in S3 with
session.recordingUri = None, and the calls that had a transcript had no audio
linked. No call had both. The cause is ordering, not permissions - archive_call
looks for the recording when the call ENDS, and the media host only uploads once
the pipeline tears down, seconds later. The HEAD always ran too early, so
Salesforce's Recording_URI__c was blank even when the audio existed.

WHAT IS PINNED HERE
  1. a found recording is written onto the session
  2. the CRM is refreshed ONLY for a completed call - re-invoking the writeback
     for a terminated or escalated call would CREATE an underwriting record for an
     intake that never finished
  3. no recording is not a failure (a surface may record nothing)
  4. it is idempotent - a second notification writes nothing
  5. a CRM refresh failure still leaves the URI on the session, and never raises

Run:  ./.venv/bin/python tests/test_recording_link.py
"""
from __future__ import annotations

import os
import sys
import types

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "common"))
sys.path.insert(0, os.path.join(ROOT, "agent"))

URI = "s3://hvi-archive/calls/call-x/audio/call.wav"

failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + ("" if cond else f"  <{detail}>"))
    if not cond:
        failures.append(label)


class FakeState:
    STATUS_COMPLETED = "completed"

    def __init__(self, session: dict):
        self.session = session
        self.archive_calls: list[tuple] = []

    def get_session(self, call_id):
        return self.session

    def set_archive_uris(self, call_id, transcript_uri=None, recording_uri=None):
        self.archive_calls.append((call_id, transcript_uri, recording_uri))
        if recording_uri:
            self.session["recordingUri"] = recording_uri
        if transcript_uri:
            self.session["transcriptUri"] = transcript_uri


def fake_archive(uri: str | None):
    mod = types.ModuleType("hvi_archive")
    mod.find_recording = lambda call_id: uri  # type: ignore[attr-defined]
    return mod


def fake_gateway():
    """crm_client() -> None forces the direct Lambda path, which the test observes."""
    mod = types.ModuleType("hvi_crm_gateway")
    mod.crm_client = lambda: None  # type: ignore[attr-defined]
    return mod


def main() -> int:
    import tools

    original_st = tools.st
    original_invoke = tools._timed_lambda_invoke
    sys.modules["hvi_crm_gateway"] = fake_gateway()

    try:
        print("[1] a found recording is written onto the session")
        sys.modules["hvi_archive"] = fake_archive(URI)
        state = FakeState({"callId": "call-x", "status": "completed"})
        tools.st = state
        invokes: list[dict] = []
        tools._timed_lambda_invoke = lambda *a, **kw: (
            invokes.append(kw) or _ok_payload())
        res = tools.link_recording("call-x")
        check("ok", res["ok"] is True, str(res))
        check("found", res["found"] is True, str(res))
        check("recordingUri returned", res["recordingUri"] == URI, str(res))
        check("session recordingUri set", state.session.get("recordingUri") == URI)
        check("transcriptUri was NOT touched",
              state.archive_calls and state.archive_calls[0][1] is None,
              str(state.archive_calls))

        print("\n[2] a completed call refreshes the CRM record")
        check("writeback invoked once", len(invokes) == 1, str(len(invokes)))
        check("crmUpdated reported", res["crmUpdated"] is True, str(res))
        payload = invokes[0].get("Payload", b"{}") if invokes else b"{}"
        check("writeback asked to submit this call",
              b"call-x" in payload and b"submit" in payload, str(payload))

        print("\n[3] an UNFINISHED call must NOT write an underwriting record")
        sys.modules["hvi_archive"] = fake_archive(URI)
        state = FakeState({"callId": "call-y", "status": "in_progress"})
        tools.st = state
        invokes.clear()
        res = tools.link_recording("call-y")
        check("recording still linked", state.session.get("recordingUri") == URI)
        check("writeback NOT invoked", not invokes, str(invokes))
        check("crmUpdated is False", res["crmUpdated"] is False, str(res))

        print("\n[4] no recording is not a failure")
        sys.modules["hvi_archive"] = fake_archive(None)
        state = FakeState({"callId": "call-z", "status": "completed"})
        tools.st = state
        invokes.clear()
        res = tools.link_recording("call-z")
        check("ok", res["ok"] is True, str(res))
        check("found is False", res["found"] is False, str(res))
        check("nothing written to the session", not state.archive_calls)
        check("writeback NOT invoked", not invokes)

        print("\n[5] idempotent: a second notification writes nothing")
        sys.modules["hvi_archive"] = fake_archive(URI)
        state = FakeState({"callId": "call-x", "status": "completed",
                           "recordingUri": URI})
        tools.st = state
        invokes.clear()
        res = tools.link_recording("call-x")
        check("ok", res["ok"] is True, str(res))
        check("no second session write", not state.archive_calls,
              str(state.archive_calls))
        check("no second CRM write", not invokes, str(invokes))

        print("\n[6] a CRM failure keeps the URI and never raises")
        sys.modules["hvi_archive"] = fake_archive(URI)
        state = FakeState({"callId": "call-w", "status": "completed"})
        tools.st = state

        def _boom(*a, **kw):
            raise RuntimeError("salesforce is down")
        tools._timed_lambda_invoke = _boom
        res = tools.link_recording("call-w")
        check("still ok", res["ok"] is True, str(res))
        check("URI still on the session",
              state.session.get("recordingUri") == URI)
        check("crmUpdated is False", res["crmUpdated"] is False, str(res))
        check("the reason is reported", "crm refresh failed" in (res["error"] or ""),
              str(res.get("error")))
    finally:
        tools.st = original_st
        tools._timed_lambda_invoke = original_invoke

    print()
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        return 1
    print("All checks passed.")
    return 0


class _Body:
    @staticmethod
    def read():
        return b'{"written": true}'


def _ok_payload():
    return {"Payload": _Body()}


if __name__ == "__main__":
    raise SystemExit(main())
