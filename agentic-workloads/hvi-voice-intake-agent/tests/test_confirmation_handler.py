#!/usr/bin/env python3
"""Offline contract tests for the confirmation-email Lambda."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")
os.environ.setdefault("SES_FROM_EMAIL", "underwriting@example.invalid")

# The real prompt renderer, so the transcript is proven to quote questions the way
# the agent actually speaks them rather than through a test-only reimplementation.
sys.path.insert(0, str(ROOT / "common"))
import hvi_state as st_real  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "confirmation_handler_contract", ROOT / "lambdas" / "send_confirmation" / "handler.py")
handler = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = handler
spec.loader.exec_module(handler)

failures: list[str] = []
checks = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global checks
    checks += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f" <- {detail}" if not ok else ""))
    if not ok:
        failures.append(label)


class FakeState:
    session = {
        "callId": "call-test",
        "formId": "form-test",
        "customerEmail": "customer@example.invalid",
        "customerName": "A <Customer>",
        "policyNumber": "POL-123",
        "phone": "+911234567890",
        "dateOfBirth": "1987-03-22",
        "gender": "female",
        "language": "en",
        "insurerName": "Test Insurer",
        "answers": {"A010": True, "A500": "<private>"},
        "declaredConditions": [
            {"icd10": "E11", "label": "Type 2 Diabetes",
             "patientPhrase": "mujhe <sugar> hai"},
        ],
        # The clinical answers underwriting decides on. These used to be absent
        # from the customer's copy entirely.
        "followUpAnswers": {
            "E11::FU_DIAG_WHEN": {"icd10": "E11", "questionId": "FU_DIAG_WHEN",
                                  "prompt": "When were you first diagnosed?",
                                  "value": "three years ago"},
            "E11::FU_TREATMENT": {"icd10": "E11", "questionId": "FU_TREATMENT",
                                  "prompt": "What treatment are you taking?",
                                  "value": "Metformin"},
        },
        "unclassifiedMentions": [
            {"phrase": "<unclear phrase>", "reason": "no confident match"},
        ],
        "declarationConfirmedAt": "2026-08-13T09:20:00+00:00",
    }
    items = [
        {"itemId": "A010", "order": 1, "label": "Identity",
         "prompt": "Am I speaking with {{customerName}}?",
         "promptHi": "क्या मैं {{customerName}} से बात कर रही हूँ?",
         "promptHinglish": "Kya main {{customerName}} se baat kar rahi hoon?"},
        {"itemId": "A500", "order": 2, "label": "History",
         "prompt": "Have you ever been hospitalised?",
         "promptHi": "क्या आप कभी अस्पताल में भर्ती हुई हैं?",
         "promptHinglish": "Kya aap kabhi hospital mein admit hui hain?"},
    ]

    render_prompt = staticmethod(st_real.render_prompt)

    @classmethod
    def get_session(cls, call_id):
        return dict(cls.session) if call_id == "call-test" else None

    @classmethod
    def get_form_items(cls, _form_id):
        return list(cls.items)


class FakeSes:
    def __init__(self):
        self.request = None

    def send_email(self, **request):
        self.request = request
        return {"MessageId": "message-123"}


def main() -> int:
    original_state = handler.st
    original_client = handler.boto3.client
    fake_ses = FakeSes()
    handler.st = FakeState
    handler.boto3.client = lambda service, region_name=None: (
        fake_ses if service == "ses" else SimpleNamespace())
    try:
        print("[1] Message is rebuilt from durable state")
        message = handler.build_message("call-test")
        check("recipient comes from session", message["recipient"] == "customer@example.invalid")
        check("all answers included", message["answerCount"] == 2)
        check("condition included", "Type 2 Diabetes" in message["plain"])
        check("HTML escapes customer data", "A &lt;Customer&gt;" in message["html"]
              and "&lt;private&gt;" in message["html"], message["html"])
        check("correction window is rendered from configuration",
              handler._window_phrase(handler.CORRECTION_WINDOW_HOURS)
              in message["plain"], message["correctionWindow"])

        print("[2] It is a question-and-answer transcript, not a label dump")
        # The customer can only dispute an answer they can match to a question,
        # so the question text has to appear - with placeholders filled, exactly
        # as it was asked on the call.
        check("question text is quoted, not the internal label",
              "Am I speaking with A <Customer>?" in message["plain"]
              and "Q: Have you ever been hospitalised?" in message["plain"],
              message["plain"][:400])
        check("internal field labels are not shown as questions",
              "Q: Identity" not in message["plain"]
              and "Q: History" not in message["plain"])
        check("answers are rendered readably",
              "A: Yes" in message["plain"] and "A: <private>" in message["plain"])

        print("[3] The clinical follow-ups reach the customer")
        check("follow-up questions are included",
              "When were you first diagnosed?" in message["plain"]
              and "What treatment are you taking?" in message["plain"])
        check("follow-up answers are included",
              "three years ago" in message["plain"] and "Metformin" in message["plain"])
        check("follow-ups are counted", message["followUpCount"] == 2,
              str(message["followUpCount"]))
        check("the caller's own phrase is quoted next to the diagnosis",
              "mujhe <sugar> hai" in message["plain"])
        check("the phrase is escaped in HTML",
              "mujhe &lt;sugar&gt; hai" in message["html"])

        print("[4] Unresolved mentions are surfaced for correction")
        check("unclassified mention shown", "<unclear phrase>" in message["plain"]
              and message["unresolvedCount"] == 1)
        check("unclassified mention escaped in HTML",
              "&lt;unclear phrase&gt;" in message["html"])

        print("[5] The declaration is evidence-based, never boilerplate")
        check("a confirmed declaration is asserted",
              message["declared"] is True
              and "complete, true and accurate" in message["plain"])
        unconfirmed = dict(FakeState.session)
        unconfirmed.pop("declarationConfirmedAt")
        original_session = FakeState.session
        try:
            FakeState.session = unconfirmed
            plain_unconfirmed = handler.build_message("call-test")
            check("without a confirmation nothing is claimed as declared",
                  plain_unconfirmed["declared"] is False
                  and "not" in plain_unconfirmed["plain"].split("DECLARATION")[1][:120]
                  and "complete, true and accurate"
                  not in plain_unconfirmed["plain"].split("DECLARATION")[1],
                  plain_unconfirmed["plain"].split("DECLARATION")[1][:200])
        finally:
            FakeState.session = original_session

        print("[6] The header identifies the applicant the way the insurer's does")
        check("gender is printed, title-cased, from the application record",
              "Gender                 Female" in message["plain"],
              message["plain"].split("IMPORTANT")[0])
        check("date of birth and contact number are printed",
              "1987-03-22" in message["plain"] and "+911234567890" in message["plain"])
        no_gender = dict(FakeState.session)
        no_gender["gender"] = None
        original_session = FakeState.session
        try:
            FakeState.session = no_gender
            header = handler.build_message("call-test")["plain"].split("IMPORTANT")[0]
            check("an unknown gender is omitted, not shown as blank or unknown",
                  "Gender" not in header, header)
        finally:
            FakeState.session = original_session

        # Found by rendering a real Hindi call: the questions were Hindi and the
        # answers were "Yes"/"No", which is neither what the caller said nor a
        # language they were asked to verify in.
        print("[7] Answers are rendered in the language of the call")
        original_session = FakeState.session
        try:
            for lang, yes, no in (("hi", "हाँ", "नहीं"),
                                  ("hinglish", "Haan", "Nahi"),
                                  ("en", "Yes", "No")):
                localised = dict(FakeState.session)
                localised["language"] = lang
                localised["answers"] = {"A010": True, "A500": False}
                FakeState.session = localised
                plain = handler.build_message("call-test")["plain"]
                check(f"{lang}: booleans read as {yes}/{no}",
                      f"A: {yes}" in plain and f"A: {no}" in plain,
                      plain.split("QUESTIONS AND ANSWERS")[1][:200])
        finally:
            FakeState.session = original_session

        # Also found on that real call: the diabetes screen answered हाँ, and four
        # lines below it "Conditions recorded: None declared." Both true - it was
        # never coded, so it went to review - and together they read as the
        # disclosure having been lost.
        print("[8] A yes with nothing recorded against it is explained, not "
              "left as a contradiction")
        try:
            stranded = dict(FakeState.session)
            stranded["declaredConditions"] = []
            stranded["followUpAnswers"] = {}
            stranded["answers"] = {"A010": True, "A250": True}
            FakeState.session = stranded
            FakeState.items = FakeState.items + [
                {"itemId": "A250", "order": 250, "label": "diabetes",
                 "followUpGroup": "condition_detail",
                 "prompt": "Diabetes, high blood sugar, or insulin?",
                 "promptHi": "मधुमेह?", "promptHinglish": "Diabetes?"}]
            plain = handler.build_message("call-test")["plain"]
            recorded = plain.split("CONDITIONS RECORDED")[1]
            check("the unresolved screen is named back to the customer",
                  "Diabetes, high blood sugar, or insulin?" in recorded
                  and "could not establish which condition" in recorded,
                  recorded[:260])
            check("it does not simply say none declared",
                  "None declared" not in recorded, recorded[:200])

            # With a condition on the record there is no way to know which screen
            # it came from, so the claim must not be made at all.
            paired = dict(stranded)
            paired["declaredConditions"] = [{"icd10": "E11",
                                             "label": "Type 2 Diabetes"}]
            FakeState.session = paired
            recorded2 = handler.build_message("call-test")["plain"].split(
                "CONDITIONS RECORDED")[1]
            check("no such claim is made once a condition is on the record",
                  "could not establish which condition" not in recorded2,
                  recorded2[:200])
        finally:
            FakeState.session = original_session
            FakeState.items = FakeState.items[:2]

        print("[9] Verification attribution never invents a clinician")
        check("no doctor is named unless one is configured",
              ("Reviewed by" in message["plain"]) is bool(handler.VERIFIER_NAME),
              message["plain"])
        check("the interview is attributed to the automated agent",
              "Automated medical-history interview" in message["plain"])

        print("[10] Dry run never calls SES")
        dry = handler.lambda_handler({"callId": "call-test", "dryRun": True}, None)
        check("dry run reports not sent", dry.get("dryRun") is True and dry.get("sent") is False)
        check("dry run does not invoke SES", fake_ses.request is None)

        print("[11] Send uses SES and returns proof")
        sent = handler.lambda_handler({"callId": "call-test"}, None)
        check("send succeeded", sent.get("sent") is True, str(sent))
        check("message ID returned", sent.get("messageId") == "message-123", str(sent))
        check("destination exact",
              fake_ses.request["Destination"]["ToAddresses"] == ["customer@example.invalid"])
        check("UTF-8 text and HTML bodies present",
              set(fake_ses.request["Message"]["Body"]) == {"Text", "Html"})

        print("[12] Invalid requests fail explicitly")
        missing = handler.lambda_handler({}, None)
        check("missing call ID rejected", missing == {"sent": False, "error": "callId is required"})
        unknown = handler.lambda_handler({"callId": "missing"}, None)
        check("unknown session rejected", unknown.get("sent") is False
              and "unknown callId" in unknown.get("error", ""), str(unknown))
    finally:
        handler.st = original_state
        handler.boto3.client = original_client

    print(f"Executed {checks} confirmation-handler checks.")
    if failures:
        print(f"FAILED: {len(failures)}")
        return 1
    print("Confirmation messages are state-derived, escaped, dry-runnable, and synchronously verifiable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
