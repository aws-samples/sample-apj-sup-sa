"""
Writes the completed intake back to Salesforce, the system of record.

The mentor's line was that completion means "an API call that will send the data
in a JSON format and the CRM will update it". This is that call.

TWO WRITES, AND THEY ARE DIFFERENT KINDS OF THING

  Medical_History__c   the underwriting record. Upserted on Call_Id__c, an
                       External Id, so a retry after a timeout updates the
                       existing row. A duplicate here is not untidiness - it is
                       two different answers to "what did this patient declare".

  Contact              the trigger flag flipped to complete, which is what takes
                       the customer out of the dialler's queue. Done SECOND on
                       purpose: if the medical history write fails, the contact
                       stays queued and the call is retried. Flipping the flag
                       first would lose the intake silently.

WHAT MAKES THE PAYLOAD WORTH READING
Two fields carry the auditability of the whole system:

  patientPhrase next to icd10 - the underwriter sees that the patient said
      "haan mujhe sugar hai" and that it was mapped to E11 at 0.94 confidence,
      rather than being asked to trust a code.

  protocolSource - the Gremlin traversal that mandated each follow-up. When an
      IRDAI auditor asks why the agent asked about insulin, the answer is a
      recorded graph path, not "the model decided to".

Run locally:  ./.venv/bin/python lambdas/crm_writeback/handler.py <callId>
              ./.venv/bin/python lambdas/crm_writeback/handler.py <callId> --dry-run
"""
from __future__ import annotations

import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "common"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "common"))

import hvi_state as st  # noqa: E402
from hvi_salesforce import SalesforceError, salesforce_client  # noqa: E402

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger("hvi-crm-writeback")

MEDICAL_HISTORY = "Medical_History__c"
SF_MOCK = os.environ.get("SF_MOCK", "false").lower() == "true"


def _soql_literal(value: str) -> str:
    """Escape a value for use inside single quotes in SOQL."""
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def _record_id(sf, upsert_result: object, call_id: str) -> str | None:
    """The Id of the underwriting record just written.

    Upsert returns a body ONLY when it created the row. On an update Salesforce
    answers 204 with no content, so the Id is simply absent - which means the
    retry path, the one most likely to be running when someone needs the link,
    is exactly the path that does not get it for free. Hence the fallback read.

    Best-effort by design: the record is already safely written by the time this
    runs, and a missing deep link is a cosmetic loss. Raising here would turn a
    successful intake into a failed one.
    """
    if isinstance(upsert_result, dict):
        found = upsert_result.get("id") or upsert_result.get("Id")
        if found:
            return str(found)
    try:
        rows = sf.query(
            f"SELECT Id FROM {MEDICAL_HISTORY} "
            f"WHERE Call_Id__c = '{_soql_literal(call_id)}' LIMIT 1")
    except Exception as e:  # noqa: BLE001
        log.warning("could not resolve the %s Id for %s: %s",
                    MEDICAL_HISTORY, call_id, e)
        return None
    if rows:
        return str(rows[0].get("Id") or "") or None
    return None


def _record_url(sf, record_id: str | None) -> str | None:
    """Lightning deep link to the underwriting record, if we can name the org.

    The instance URL is read off the transport when it has one and from the
    environment otherwise. The gateway transport deliberately exposes only the
    three data operations, so it does not know the org's host - and a link is not
    worth widening that interface for.
    """
    if not record_id:
        return None
    base = (getattr(sf, "instance_url", None)
            or os.environ.get("SF_INSTANCE_URL")
            or os.environ.get("SF_LOGIN_URL") or "")
    base = str(base).rstrip("/")
    if not base:
        return None
    return f"{base}/lightning/r/{MEDICAL_HISTORY}/{record_id}/view"

# Long Text Area caps at 131072 characters. A truncated JSON blob is worse than a
# flagged one, because it parses as nothing and loses the whole record.
MAX_JSON = int(os.environ.get("SF_MAX_JSON_CHARS", "131000"))


def _underwriting_flag(session: dict, conditions: list[dict],
                       unclassified: list[dict]) -> str:
    """Map the intake outcome onto the restricted picklist.

    Deliberately conservative. This flag routes a human underwriter's attention,
    so the failure mode to avoid is marking something clear that should have been
    looked at.

    UNCLASSIFIED MENTIONS FORCE A REFERRAL. If the patient described something the
    knowledge graph could not resolve, that is the single strongest reason to put
    the record in front of a human - and it was previously the one case that
    produced "clear", because no condition was ever recorded. The mention the
    machine could not interpret must never be the one it marks as needing no
    review.
    """
    if session.get("escalated") or session.get("medicalTestRequired"):
        return "medical-test-required"
    if unclassified:
        return "refer"
    # Any declared condition is worth a human look. "clear" is reserved for an
    # intake that declared nothing AND left nothing unresolved.
    if conditions:
        return "refer"
    return "clear"


def build_payload(call_id: str) -> tuple[dict, dict]:
    """Assemble the Salesforce payload from durable state.

    Reads DynamoDB rather than accepting a payload from the caller: the
    checkpoint table is the record of what was actually collected, and rebuilding
    from it means a writeback can be re-run for any completed call.
    """
    session = st.get_session(call_id)
    if not session:
        raise ValueError(f"unknown callId {call_id}")

    items = st.get_form_items(session["formId"])
    by_id = {i["itemId"]: i for i in items}
    answers = session.get("answers") or {}
    followup_answers = session.get("followUpAnswers") or {}
    declared = session.get("declaredConditions") or []
    pending = session.get("pendingFollowUps") or []

    # Group the answered follow-ups under the condition that mandated them, so the
    # record reads as "diabetes, and here is what we asked about it" rather than a
    # flat list of orphaned question ids.
    #
    # SOURCED FROM followUpAnswers, NOT pendingFollowUps. pendingFollowUps is the
    # queue of questions still OWED and it is drained as each one is answered, so
    # for a completed call it is empty by definition. Building the record from it
    # produced "followUps": {} on every finished intake - the protocol-mandated
    # clinical detail silently absent from the underwriting record, with the write
    # itself reporting success. followUpAnswers is keyed "{icd10}::{questionId}"
    # and each value already carries icd10, prompt and protocolSource.
    followups_by_icd: dict[str, dict] = {}
    protocol_by_icd: dict[str, str] = {}
    for key, entry in (followup_answers or {}).items():
        if isinstance(entry, dict):
            icd = entry.get("icd10") or (key.split("::", 1)[0] if "::" in key else "")
            qid = entry.get("questionId") or (key.split("::", 1)[-1])
            if entry.get("protocolSource"):
                protocol_by_icd.setdefault(icd, entry["protocolSource"])
            followups_by_icd.setdefault(icd, {})[qid] = {
                "answer": entry.get("value"),
                "question": entry.get("prompt"),
                "answeredAt": entry.get("answeredAt"),
            }
        else:
            # Tolerate a bare value, in case an older session shape turns up.
            icd, _, qid = key.partition("::")
            followups_by_icd.setdefault(icd, {})[qid or key] = {"answer": entry}

    conditions = []
    for d in declared:
        icd = d.get("icd10") or ""
        # classifiedBy must not claim the graph when the graph stayed silent.
        # Shared with hvi_state for the same reason the review rule is.
        corroborated = d.get("corroborated")
        conditions.append({
            "icd10": icd,
            "label": d.get("label"),
            # The provenance fields. Kept adjacent on purpose.
            "patientPhrase": d.get("patientPhrase"),
            "confidence": d.get("confidence"),
            "classifiedBy": st.classified_by(d),
            "corroborated": corroborated,
            "confirmedByPatient": d.get("confirmedByPatient"),
            "declaredAt": d.get("at"),
            "protocolSource": d.get("protocolSource") or protocol_by_icd.get(icd),
            "followUps": followups_by_icd.get(icd, {}),
        })

    sections: dict[str, dict] = {}
    for iid, val in sorted(answers.items()):
        meta = by_id.get(iid, {})
        section = meta.get("section", "unknown")
        sections.setdefault(section, {})[meta.get("label", iid)] = {
            "itemId": iid,
            "question": meta.get("prompt", ""),
            "answer": val,
            "answerType": meta.get("answerType", "text"),
        }

    # Things the patient said that we could not resolve to a code. Surfaced as a
    # TOP-LEVEL key, not buried in the audit trail, because it is the first thing
    # a reviewing underwriter should see - and often trivially resolvable by a
    # human who can read "mujhe sarcoidosis hai" and classify it on sight.
    unclassified = [
        {
            "patientPhrase": m.get("phrase"),
            "reason": m.get("reason"),
            "at": m.get("at"),
            "resolvedTo": m.get("itemId"),
        }
        for m in (session.get("unclassifiedMentions") or [])
    ]

    # A code the graph never corroborated is also a review item. Shared with
    # generate_summary via hvi_state so the two cannot drift - see
    # st.provenance_review_rows for the live call this came from.
    unclassified += st.provenance_review_rows(session)

    responses = {
        "sections": sections,
        "conditions": conditions,
        "needsHumanReview": unclassified,
        "auditTrail": {
            "turnCount": int(session.get("lastCompletedTurn") or 0) + 1,
            "callLegs": int(session.get("resumeCount") or 0) + 1,
            "priorCallIds": session.get("priorCallIds") or [],
            "answerCount": len(answers),
            # pendingFollowUps only ever holds unanswered questions, so its length
            # IS the number owed. A non-zero value on a completed intake means the
            # protocol was not finished, which an underwriter needs to know.
            "followUpsOwed": len(pending),
            "followUpsAnswered": len(followup_answers or {}),
            "language": session.get("language"),
        },
    }

    blob = json.dumps(responses, ensure_ascii=False, default=str)
    if len(blob) > MAX_JSON:
        # Flag rather than silently truncate. A truncated blob does not parse, so
        # it would lose everything instead of the overflow.
        log.warning("responses JSON is %d chars, over the %d limit for %s",
                    len(blob), MAX_JSON, call_id)
        responses["auditTrail"]["truncated"] = True
        responses["sections"] = {"_note": "omitted: payload exceeded the field limit"}
        blob = json.dumps(responses, ensure_ascii=False, default=str)

    language = session.get("language") or "hinglish"
    # The picklist is restricted to en-IN / hi-IN / hinglish, so a bare "hi" or
    # "en" from language detection has to be mapped or the write is rejected.
    language = {"hi": "hi-IN", "en": "en-IN"}.get(language, language)

    mh = {
        "Policy_Number__c": session.get("policyNumber"),
        "Form_Id__c": session.get("formId"),
        "Submitted_At__c": st.now_iso(),
        "Resume_Count__c": int(session.get("resumeCount") or 0),
        "Language_Used__c": language,
        "Underwriting_Flag__c": _underwriting_flag(session, conditions, unclassified),
        "Turn_Count__c": responses["auditTrail"]["turnCount"],
        "Call_Legs__c": responses["auditTrail"]["callLegs"],
        # First-class fields, not just JSON, so an underwriter can build a report
        # of "records awaiting human review" without parsing a blob.
        "Unclassified_Mentions__c": len(unclassified),
        "Needs_Human_Review__c": bool(unclassified),
        "Responses_JSON__c": blob,
    }
    for key in ("recordingUri", "transcriptUri"):
        if session.get(key):
            mh["Recording_URI__c" if key == "recordingUri"
               else "Transcript_URI__c"] = session[key]

    return mh, session


def writeback(call_id: str, dry_run: bool = False) -> dict:
    payload, session = build_payload(call_id)
    customer_id = session.get("customerId")

    if SF_MOCK or dry_run:
        # The mock is contract-identical on purpose: same field API names, same
        # upsert key. A looser mock proves nothing and hides field-name bugs until
        # the day Salesforce is switched on.
        log.info("SF_MOCK/dry-run: would upsert %s on Call_Id__c=%s\n%s",
                 MEDICAL_HISTORY, call_id, json.dumps(payload, indent=2)[:2000])
        return {"written": False, "mock": True, "callId": call_id,
                "payload": payload}

    sf = salesforce_client()
    result = sf.upsert(MEDICAL_HISTORY, "Call_Id__c", call_id, payload)
    log.info("upserted %s for %s: %s", MEDICAL_HISTORY, call_id, result)

    # Resolved before the contact flag, so a failure to flip the flag still
    # leaves the caller with a link to the record that was written.
    record_id = _record_id(sf, result, call_id)
    record_url = _record_url(sf, record_id)

    # Contact flag SECOND. If the write above failed we never get here, so the
    # customer stays in the dialler's queue and the intake is retried rather than
    # being lost.
    contact_updated = False
    if customer_id and str(customer_id).startswith("003"):
        status = ("medical-test-required"
                  if payload["Underwriting_Flag__c"] == "medical-test-required"
                  else "complete")
        try:
            sf.update_contact(customer_id,
                              {"HVI_Underwriting_Status__c": status,
                               "HVI_Last_Call_Id__c": call_id})
            contact_updated = True
            log.info("contact %s marked %s", customer_id, status)
        except SalesforceError as e:
            # Not fatal: the underwriting record is safely stored, and the worst
            # case is one redundant call. Losing the intake would be worse.
            log.error("could not update contact %s: %s", customer_id, e)
    else:
        log.warning("customerId %r is not a Salesforce Contact Id; "
                    "skipping the queue flag", customer_id)

    return {"written": True, "callId": call_id,
            "underwritingFlag": payload["Underwriting_Flag__c"],
            "conditions": len(json.loads(payload["Responses_JSON__c"])
                              .get("conditions", [])),
            "contactUpdated": contact_updated,
            "recordId": record_id,
            "recordUrl": record_url}


def lambda_handler(event, _context):
    call_id = (event or {}).get("callId")
    if not call_id:
        return {"written": False, "error": "callId is required"}
    return writeback(call_id, dry_run=bool(event.get("dryRun")))


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="write a completed intake to Salesforce")
    ap.add_argument("call_id")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    print(json.dumps(writeback(a.call_id, dry_run=a.dry_run),
                     indent=2, default=str))
