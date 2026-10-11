#!/usr/bin/env python3
"""The durable per-call archive: content, rendering, and the never-fail contract.

AWS-free. The S3 client is replaced with a double, so this asserts what the
archive CONTAINS and what it does when S3 is unavailable - not that boto3 works.
The live end-to-end path is covered by running scripts/55-call-archive.sh and
archiving a real call.

Three things are pinned here, each because it was broken or absent:

  1. The transcript carries EVERY turn, in order, including the turns that
     produced no answer. A summary of the answers already exists (the customer's
     confirmation email); the point of this artefact is the conversation, and the
     re-asks and misheard values are exactly what an audit of a disputed record
     needs.

  2. archive_call NEVER RAISES. The archive runs at finalisation, after the
     intake is complete and before the CRM writeback. An exception here would
     turn a finished call into a failed one, which is a strictly worse outcome
     than having no archive.

  3. The result keys are the ones the rest of the chain expects. The Salesforce
     fields Recording_URI__c / Transcript_URI__c were written blank on every call
     for exactly one reason: nothing set session.recordingUri /
     session.transcriptUri. The names are the contract, so they are asserted.

Run:  ./.venv/bin/python tests/test_call_archive.py
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "common"))

import hvi_archive as ar  # noqa: E402

failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + ("" if cond else f"  <{detail}>"))
    if not cond:
        failures.append(label)


SESSION = {
    "customerId": "cust-1",
    "customerName": "Asha Rao",
    "phone": "+911234567890",
    "policyNumber": "AS-2026-3986649",
    "formId": "arogya-sanjeevani-v1",
    "language": "hi",
    "status": "completed",
    "statusNote": "intake complete",
    "resumeCount": 1,
    "priorCallIds": ["call-old"],
    "createdAt": "2026-09-01T13:23:28+00:00",
    "answers": {"A010": True, "A030": "2000-01-05", "A270": True},
    "declaredConditions": [
        {"icd10": "F20", "label": "Schizophrenia",
         "patientPhrase": "स्किजोफ्रेनिया है मुझे"}],
    "followUpAnswers": {"F20::diagnosed_when": {"icd10": "F20", "value": "2019"}},
    "humanFollowUpItems": [
        {"ref": "diagnosed_when", "label": "Schizophrenia",
         "reason": "caller could not answer after an explanation and a re-ask"}],
    "unclassifiedMentions": [{"text": "kuch aur bhi tha"}],
}

# Deliberately OUT OF ORDER, and including a turn that produced no answer.
TURNS = [
    {"turnIndex": 2, "role": "customer", "itemId": "A270", "text": "हाँ, स्किजोफ्रेनिया है मुझे",
     "extractedValue": True, "valid": True, "sttConfidence": 0.91},
    {"turnIndex": 0, "role": "agent", "itemId": "A270", "text": "कोई मानसिक बीमारी?"},
    {"turnIndex": 3, "role": "agent", "itemId": "diagnosed_when",
     "text": "यह पहली बार कब पता चला था?"},
    {"turnIndex": 1, "role": "customer", "text": "क्या?",
     "validationError": "not an answer to this question"},
]


class FakeS3:
    """Records puts. get/copy are not needed by these paths."""

    def __init__(self, explode: bool = False):
        self.puts: list[dict] = []
        self.explode = explode

    def put_object(self, **kw):
        if self.explode:
            raise RuntimeError("AccessDenied")
        self.puts.append(kw)
        return {}

    def copy_object(self, **kw):
        raise RuntimeError("no recordings in this test")


def with_client(client, bucket="test-archive"):
    ar._s3 = client
    ar.BUCKET = bucket


def main() -> int:  # noqa: PLR0915
    original_bucket, original_client = ar.BUCKET, ar._s3
    try:
        print("[1] the transcript document carries the whole conversation")
        doc = ar.build_transcript("call-x", SESSION, TURNS)
        check("every turn is present", doc["turnCount"] == 4, str(doc["turnCount"]))
        check("turns are sorted by turnIndex",
              [t["turnIndex"] for t in doc["turns"]] == [0, 1, 2, 3],
              str([t["turnIndex"] for t in doc["turns"]]))
        check("a turn with no answer is kept",
              any(t.get("validationError") for t in doc["turns"]))
        check("sttConfidence is preserved",
              any(t.get("sttConfidence") == 0.91 for t in doc["turns"]))
        check("absent fields are not invented",
              "extractedValue" not in doc["turns"][0])
        check("answers are carried", doc["answers"]["A030"] == "2000-01-05")
        check("declared conditions are carried",
              doc["declaredConditions"][0]["icd10"] == "F20")
        check("skipped questions are carried",
              doc["humanFollowUpItems"][0]["ref"] == "diagnosed_when")
        check("resume provenance is carried",
              doc["resumeCount"] == 1 and doc["priorCallIds"] == ["call-old"])
        check("it is JSON-serialisable without escaping Devanagari",
              "स्किजोफ्रेनिया" in json.dumps(doc, ensure_ascii=False))

        print("\n[2] the text rendering is readable")
        text = ar.render_transcript_text(doc)
        for needle, label in (
            ("Asha Rao", "names the customer"),
            ("AS-2026-3986649", "names the policy"),
            ("--- conversation (4 turns) ---", "heads the conversation"),
            ("स्किजोफ्रेनिया", "keeps the caller's own words"),
            ("Schizophrenia", "names the declared condition"),
            ("questions left for a person to complete", "lists what was skipped"),
            ("not an answer to this question", "shows why a turn failed"),
        ):
            check(label, needle in text, needle)

        print("\n[3] a configured bucket writes both objects")
        fake = FakeS3()
        with_client(fake)
        res = ar.archive_call("call-x", SESSION, TURNS)
        check("ok", res["ok"] is True, str(res))
        keys = [p["Key"] for p in fake.puts]
        check("wrote transcript.json", "calls/call-x/transcript.json" in keys, str(keys))
        check("wrote transcript.txt", "calls/call-x/transcript.txt" in keys, str(keys))
        check("both are SSE-encrypted",
              all(p.get("ServerSideEncryption") == "AES256" for p in fake.puts))
        check("transcriptUri points at the json",
              res["transcriptUri"] == "s3://test-archive/calls/call-x/transcript.json",
              str(res["transcriptUri"]))
        check("no recording is claimed when there is none",
              res["recordingUri"] is None and res["recordingCount"] == 0)

        print("\n[4] no bucket is SKIPPED, not a failure")
        with_client(FakeS3(), bucket="")
        res = ar.archive_call("call-x", SESSION, TURNS)
        check("skipped is true", res["skipped"] is True)
        check("ok is false", res["ok"] is False)
        check("no error is reported for a normal unconfigured state",
              res["error"] is None, str(res["error"]))

        print("\n[5] S3 failing NEVER raises into the call")
        with_client(FakeS3(explode=True))
        try:
            res = ar.archive_call("call-x", SESSION, TURNS)
            raised = False
        except Exception as e:  # noqa: BLE001
            raised, res = True, {"error": str(e)}
        check("archive_call did not raise", raised is False, str(res.get("error")))
        check("it reports the failure instead", res.get("ok") is False)
        check("the error names the cause", "AccessDenied" in str(res.get("error")),
              str(res.get("error")))

        print("\n[6] a missing session is refused, not crashed")
        with_client(FakeS3())
        res = ar.archive_call("call-x", {}, TURNS)
        check("ok is false", res["ok"] is False)
        check("error explains why", res["error"] == "no session", str(res["error"]))

        print("\n[7] the key names are the contract the CRM writeback reads")
        # lambdas/crm_writeback maps session["recordingUri"] -> Recording_URI__c
        # and session["transcriptUri"] -> Transcript_URI__c. If these names drift,
        # both Salesforce fields silently go blank again.
        with_client(FakeS3())
        res = ar.archive_call("call-x", SESSION, TURNS)
        check("result exposes transcriptUri", "transcriptUri" in res)
        check("result exposes recordingUri", "recordingUri" in res)
        wb = os.path.join(ROOT, "lambdas", "crm_writeback", "handler.py")
        with open(wb, encoding="utf-8") as fh:
            src = fh.read()
        check("the writeback still reads transcriptUri", "transcriptUri" in src)
        check("the writeback still reads recordingUri", "recordingUri" in src)

        sys.path.insert(0, os.path.join(ROOT, "common"))
        import hvi_state as st
        check("the state layer can store them",
              hasattr(st, "set_archive_uris"))
    finally:
        ar.BUCKET, ar._s3 = original_bucket, original_client

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED: {failures}")
        return 1
    print("The call archive is complete, readable, and cannot fail a call.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
