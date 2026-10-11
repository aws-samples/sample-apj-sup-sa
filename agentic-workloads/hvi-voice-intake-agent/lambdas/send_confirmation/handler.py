"""Send the customer their medical-examination transcript and correction notice.

WHY THIS IS A TRANSCRIPT AND NOT A SUMMARY

The insurer's own telephonic Medical Examination Report is a question-and-answer
transcript: every question as it was put to the customer, every answer as they
gave it, then a declaration and a correction window. It is written that way
because of what it is FOR - it is the document the policy can later be voided
against, so the customer has to be able to check it. An answer they cannot match
to a question is not checkable.

This used to send a list of internal field labels ("respiratory: No",
"substance_use: No") and, worse, omitted the clinical follow-ups entirely: when a
condition was diagnosed, what treatment, whether it is controlled, the last test
result. Those are the answers underwriting actually decides on, they went to
Salesforce, and the customer never saw them - so the correction window could not
do its job on the only content that mattered.

Everything here is rebuilt from the durable session. The handler accepts a callId
and nothing else, and never trusts medical content supplied by an invoker.
``dryRun`` renders the message without sending it.
"""
from __future__ import annotations

import html
import os
import sys
from typing import Any

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "common"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "common"))

import hvi_state as st  # noqa: E402

REGION = os.environ.get("SES_REGION", os.environ.get("DATA_REGION", "ap-south-1"))
FROM_EMAIL = os.environ.get("SES_FROM_EMAIL", "").strip()
REPLY_TO = os.environ.get("CORRECTION_REPLY_TO", "").strip()
SUBJECT_PREFIX = os.environ.get("EMAIL_SUBJECT_PREFIX", "Health history summary").strip()

# ONE SOURCE FOR THE CORRECTION WINDOW. The window used to be the literal string
# "48 hours" in this file while SLA_HOURS_FROM_PURCHASE configured it elsewhere,
# so the promise made to the customer and the deadline the business worked to
# could disagree with nobody noticing. The insurer's reference report allows 15
# days; whatever the number, it is set in one place and rendered from there.
CORRECTION_WINDOW_HOURS = int(os.environ.get(
    "CORRECTION_WINDOW_HOURS", os.environ.get("SLA_HOURS_FROM_PURCHASE", "48")))

# Who is accountable for the interview. Left EMPTY by default and omitted from the
# transcript when unset: the reference report is signed by a named doctor with a
# registration number, and inventing either would be worse than saying nothing.
# Set these once a medical officer actually supervises the queue.
VERIFIER_NAME = os.environ.get("MEDICAL_VERIFIER_NAME", "").strip()
VERIFIER_REG = os.environ.get("MEDICAL_VERIFIER_REGISTRATION", "").strip()
AGENT_VERSION = os.environ.get("HVI_AGENT_VERSION", "").strip()
INSURER_FALLBACK = os.environ.get("INSURER_NAME", "your insurer").strip()


def _display(value: Any, language: str = "en") -> str:
    """Render one answer the way the caller gave it.

    YES/NO IS TRANSLATED, because the questions are. A Hindi call produced a
    transcript of Hindi questions answered "Yes" and "No" in English, which is
    not what the caller said and is the wrong language to ask them to verify in.
    """
    if isinstance(value, bool):
        return {
            "hi": "हाँ" if value else "नहीं",
            "hinglish": "Haan" if value else "Nahi",
        }.get(language, "Yes" if value else "No")
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _window_phrase(hours: int) -> str:
    """Render the correction window the way a person would say it."""
    if hours >= 48 and hours % 24 == 0:
        days = hours // 24
        return f"{days} days"
    if hours == 24:
        return "24 hours"
    return f"{hours} hours"


def build_message(call_id: str) -> dict:
    """Build the customer-readable transcript from durable session state."""
    session = st.get_session(call_id)
    if not session:
        raise ValueError(f"unknown callId {call_id}")
    recipient = str(session.get("customerEmail") or "").strip()
    if not recipient:
        raise ValueError(f"session {call_id} has no customer email")

    items = st.get_form_items(session["formId"])
    by_id = {item["itemId"]: item for item in items}
    answers = session.get("answers") or {}
    language = session.get("language") or "en"
    customer = {
        "customerName": session.get("customerName"),
        "customerEmail": session.get("customerEmail"),
        "policyNumber": session.get("policyNumber"),
        "insurerName": session.get("insurerName") or INSURER_FALLBACK,
    }

    # THE QUESTION AS IT WAS ASKED, IN THE LANGUAGE IT WAS ASKED IN. A Hindi
    # caller cannot verify an English label, and rendering through the same
    # function the agent speaks from is what keeps the two identical.
    def question_text(item_id: str) -> str:
        item = by_id.get(item_id)
        if not item:
            return item_id
        rendered = st.render_prompt(item, language, customer)
        return rendered or str(item.get("label") or item_id)

    qa = [
        {"question": question_text(item_id), "answer": _display(value, language)}
        for item_id, value in sorted(
            answers.items(),
            key=lambda pair: int(by_id.get(pair[0], {}).get("order", 10**9)))
    ]

    # Clinical follow-ups, grouped under the condition that mandated them, so the
    # transcript reads "Asthma - and here is what we asked about it". Same source
    # the Salesforce writeback uses, so the customer's copy and the underwriter's
    # copy cannot disagree.
    followup_answers = session.get("followUpAnswers") or {}
    grouped: dict[str, list[dict]] = {}
    for key, entry in followup_answers.items():
        if isinstance(entry, dict):
            icd = entry.get("icd10") or (key.split("::", 1)[0] if "::" in key else "")
            grouped.setdefault(icd, []).append({
                "question": str(entry.get("prompt") or entry.get("questionId") or ""),
                "answer": _display(entry.get("value")),
            })
        else:
            icd, _, qid = key.partition("::")
            grouped.setdefault(icd, []).append({"question": qid or key,
                                                "answer": _display(entry)})

    conditions = []
    for entry in session.get("declaredConditions") or []:
        icd = str(entry.get("icd10") or "")
        conditions.append({
            "icd10": icd,
            "label": str(entry.get("label") or icd),
            "patientPhrase": str(entry.get("patientPhrase") or ""),
            "followUps": grouped.get(icd, []),
        })
    # Follow-ups whose condition is no longer declared (a retraction, say) still
    # belong in the transcript: the customer answered those questions.
    for icd, rows in grouped.items():
        if not any(c["icd10"] == icd for c in conditions):
            conditions.append({"icd10": icd, "label": icd, "patientPhrase": "",
                               "followUps": rows})

    # What we heard but would not turn into a diagnosis. Shown deliberately: this
    # is where the record is least certain, so it is exactly where an invitation
    # to correct is worth most.
    # AN ELABORATION IS NOT AN UNIDENTIFIED CONDITION. When a caller adds detail
    # to a disease already on the record, the classifier deliberately declines to
    # mint a second condition and files the phrase for review. That is right, but
    # showing it here under "we could not identify it" is false: it WAS
    # identified, and it is recorded. A live Hinglish call listed
    # 'Haan, mujhe asthma hai' as unidentifiable while Asthma sat in the declared
    # conditions two paragraphs above.
    #
    # The row is kept in the session for the underwriter, and only withheld from
    # the customer-facing invitation to correct, which is about doubt.
    unresolved = [
        {"phrase": str(row.get("phrase") or ""), "reason": str(row.get("reason") or "")}
        for row in (session.get("unclassifiedMentions") or [])
        if str(row.get("phrase") or "").strip()
        and str(row.get("kind") or "unidentified") not in ("elaboration",
                                                           "ungoverned", "noise")
    ]

    # A "YES" WITH NOTHING RECORDED AGAINST IT MUST NOT READ AS A CONTRADICTION.
    # A real transcript showed the diabetes screen answered हाँ and, four lines
    # later, "Conditions recorded: None declared." Both statements were true - the
    # condition was never named, so it was filed for review instead - but from the
    # customer's side it reads as the disclosure having been lost, which is the one
    # impression this document must never give. Only claimed when NOTHING was
    # recorded at all: with a condition on the record there is no way to say from
    # here which screen it came from, and a wrong pairing would be worse than
    # silence.
    unrecorded_screens = []
    if not conditions:
        unrecorded_screens = [
            question_text(item_id)
            for item_id, value in sorted(
                answers.items(),
                key=lambda pair: int(by_id.get(pair[0], {}).get("order", 10**9)))
            if value is True
            and (by_id.get(item_id) or {}).get("followUpGroup") == "condition_detail"
        ]

    name = str(session.get("customerName") or "Customer")
    policy = str(session.get("policyNumber") or "")
    insurer = str(customer["insurerName"])
    window = _window_phrase(CORRECTION_WINDOW_HOURS)
    declared_at = session.get("declarationConfirmedAt")

    header = [("Application / policy", policy),
              ("Name", name),
              ("Contact number", str(session.get("phone") or "")),
              ("Date of birth", str(session.get("dateOfBirth") or "")),
              # Title-cased because it arrives from two places with different
              # conventions: the Salesforce picklist is Male/Female/Other, the
              # local seed fixtures are lower case.
              ("Gender", str(session.get("gender") or "").strip().title()),
              ("Language of the call", {"hi": "Hindi", "hinglish": "Hinglish"}
               .get(language, "English")),
              ("Call reference", call_id),
              ("Completed at", str(session.get("updatedAt") or ""))]
    header = [(k, v) for k, v in header if v]

    verified_by = (f"Automated medical-history interview"
                   + (f" (agent {AGENT_VERSION})" if AGENT_VERSION else ""))
    attribution = [("Interview conducted by", verified_by)]
    if VERIFIER_NAME:
        attribution.append(("Reviewed by",
                            f"{VERIFIER_NAME}"
                            + (f", registration {VERIFIER_REG}" if VERIFIER_REG else "")))
    if declared_at:
        attribution.append(("Declaration confirmed at", str(declared_at)))

    # ---- plain text --------------------------------------------------------
    lines: list[str] = []
    lines.append("MEDICAL HISTORY - TRANSCRIPT OF YOUR TELEPHONIC INTERVIEW")
    lines.append("")
    lines.append(
        f"Hello {name}, this is a transcript of the answers you gave during your "
        f"health-history call with {insurer}. These answers form part of your "
        "application for insurance.")
    lines.append("")
    lines.append(
        f"Please read it carefully. If anything is wrong or incomplete, tell us "
        f"within {window} of receiving this, using the reply address on this "
        "email. After that we will take this record as accepted by you. Please "
        "keep it for your records.")
    lines.append("")
    for key, value in header:
        lines.append(f"{key:22s} {value}")
    lines.append("")
    lines.append("IMPORTANT")
    lines.append(
        "Complete and accurate health information matters. If information is "
        "missing or incorrect, your policy may be cancelled or a claim declined, "
        "and premiums are not refundable where full medical disclosure was not "
        "given. If you remember something you did not mention, tell us now "
        "rather than later.")
    lines.append("")
    lines.append("QUESTIONS AND ANSWERS")
    if qa:
        for row in qa:
            lines.append(f"Q: {row['question']}")
            lines.append(f"A: {row['answer']}")
            lines.append("")
    else:
        lines.append("No questionnaire answers were recorded on this call.")
        lines.append("")

    lines.append("CONDITIONS RECORDED")
    if conditions:
        for cond in conditions:
            title = f"{cond['label']}" + (f" ({cond['icd10']})" if cond["icd10"] else "")
            lines.append(title)
            if cond["patientPhrase"]:
                lines.append(f"  In your words: \"{cond['patientPhrase']}\"")
            for row in cond["followUps"]:
                lines.append(f"  Q: {row['question']}")
                lines.append(f"  A: {row['answer']}")
            lines.append("")
    elif unrecorded_screens:
        lines.append(
            "No condition was recorded. You answered yes to the question(s) below, "
            "but we could not establish which condition you meant during the call, "
            "so nothing has been entered as a diagnosis and a member of our team "
            "will follow this up with you:")
        for question in unrecorded_screens:
            lines.append(f"  - {question}")
        lines.append("")
    else:
        lines.append("None declared.")
        lines.append("")

    if unresolved:
        lines.append("NOTED FOR REVIEW BY A PERSON")
        lines.append(
            "You mentioned the following, and we did not record it as a "
            "diagnosis because we could not identify it confidently. Someone "
            "from our team will review it. Please correct us if it is wrong.")
        for row in unresolved:
            lines.append(f"  - \"{row['phrase']}\"")
        lines.append("")

    lines.append("DECLARATION")
    if declared_at:
        lines.append(
            "During the call this record was read back to you and you confirmed "
            "that you had understood the questions and that the information you "
            "gave is complete, true and accurate to the best of your knowledge.")
    else:
        lines.append(
            "This record was NOT confirmed back to you before the call ended, so "
            f"nothing here is treated as declared by you yet. Please check it and "
            f"reply within {window} to confirm or correct it.")
    lines.append("")

    if attribution:
        lines.append("HOW THIS WAS VERIFIED")
        for key, value in attribution:
            lines.append(f"{key:26s} {value}")
        lines.append("")

    lines.append("This message contains private health information. Please do not "
                 "forward it.")
    plain = "\n".join(lines)

    # ---- html --------------------------------------------------------------
    def esc(value: Any) -> str:
        return html.escape(str(value))

    def rows_table(pairs: list[tuple[str, str]]) -> str:
        return ("<table style='border-collapse:collapse'>" + "".join(
            f"<tr><th style='text-align:left;padding:4px 12px 4px 0;"
            f"vertical-align:top'>{esc(k)}</th>"
            f"<td style='padding:4px 0'>{esc(v)}</td></tr>" for k, v in pairs)
            + "</table>")

    def qa_block(pairs: list[dict], indent: bool = False) -> str:
        pad = "margin:0 0 10px 20px" if indent else "margin:0 0 12px 0"
        return "".join(
            f"<p style='{pad}'><strong>Q:</strong> {esc(row['question'])}<br>"
            f"<strong>A:</strong> {esc(row['answer'])}</p>" for row in pairs)

    parts: list[str] = []
    parts.append("<h2>Medical history - transcript of your telephonic interview</h2>")
    parts.append(
        f"<p>Hello {esc(name)}, this is a transcript of the answers you gave "
        f"during your health-history call with {esc(insurer)}. These answers form "
        "part of your application for insurance.</p>")
    parts.append(
        f"<p>Please read it carefully. If anything is wrong or incomplete, tell us "
        f"within <strong>{esc(window)}</strong> of receiving this, using the reply "
        "address on this email. After that we will take this record as accepted by "
        "you. Please keep it for your records.</p>")
    parts.append(rows_table(header))
    parts.append("<h3>Important</h3>")
    parts.append(
        "<p>Complete and accurate health information matters. If information is "
        "missing or incorrect, your policy may be cancelled or a claim declined, "
        "and premiums are not refundable where full medical disclosure was not "
        "given. If you remember something you did not mention, tell us now rather "
        "than later.</p>")
    parts.append("<h3>Questions and answers</h3>")
    parts.append(qa_block(qa) if qa
                 else "<p>No questionnaire answers were recorded on this call.</p>")
    parts.append("<h3>Conditions recorded</h3>")
    if conditions:
        for cond in conditions:
            title = esc(cond["label"]) + (f" ({esc(cond['icd10'])})"
                                          if cond["icd10"] else "")
            parts.append(f"<p style='margin:12px 0 4px 0'><strong>{title}</strong></p>")
            if cond["patientPhrase"]:
                parts.append(
                    f"<p style='margin:0 0 6px 20px'><em>In your words:</em> "
                    f"&quot;{esc(cond['patientPhrase'])}&quot;</p>")
            parts.append(qa_block(cond["followUps"], indent=True))
    elif unrecorded_screens:
        parts.append(
            "<p>No condition was recorded. You answered <strong>yes</strong> to the "
            "question(s) below, but we could not establish which condition you "
            "meant during the call, so nothing has been entered as a diagnosis and "
            "a member of our team will follow this up with you:</p><ul>"
            + "".join(f"<li>{esc(q)}</li>" for q in unrecorded_screens) + "</ul>")
    else:
        parts.append("<p>None declared.</p>")
    if unresolved:
        parts.append("<h3>Noted for review by a person</h3>")
        parts.append(
            "<p>You mentioned the following, and we did not record it as a "
            "diagnosis because we could not identify it confidently. Someone from "
            "our team will review it. Please correct us if it is wrong.</p><ul>"
            + "".join(f"<li>&quot;{esc(row['phrase'])}&quot;</li>"
                      for row in unresolved) + "</ul>")
    parts.append("<h3>Declaration</h3>")
    if declared_at:
        parts.append(
            "<p>During the call this record was read back to you and you confirmed "
            "that you had understood the questions and that the information you "
            "gave is complete, true and accurate to the best of your knowledge.</p>")
    else:
        parts.append(
            "<p>This record was <strong>not</strong> confirmed back to you before "
            "the call ended, so nothing here is treated as declared by you yet. "
            f"Please check it and reply within {esc(window)} to confirm or correct "
            "it.</p>")
    if attribution:
        parts.append("<h3>How this was verified</h3>")
        parts.append(rows_table(attribution))
    parts.append("<p><strong>This message contains private health information. "
                 "Please do not forward it.</strong></p>")
    html_body = "".join(parts)

    return {
        "recipient": recipient,
        "subject": f"{SUBJECT_PREFIX} - {policy}" if policy else SUBJECT_PREFIX,
        "plain": plain,
        "html": html_body,
        "answerCount": len(qa),
        "conditionCount": len(conditions),
        "followUpCount": sum(len(c["followUps"]) for c in conditions),
        "unresolvedCount": len(unresolved),
        "declared": bool(declared_at),
        "correctionWindow": window,
    }


def send_confirmation(call_id: str, dry_run: bool = False) -> dict:
    message = build_message(call_id)
    if dry_run:
        return {
            "sent": False,
            "dryRun": True,
            "recipient": message["recipient"],
            "subject": message["subject"],
            "answerCount": message["answerCount"],
            "conditionCount": message["conditionCount"],
            "followUpCount": message["followUpCount"],
            "unresolvedCount": message["unresolvedCount"],
            "declared": message["declared"],
        }
    if not FROM_EMAIL:
        raise RuntimeError("SES_FROM_EMAIL is not configured")

    request: dict[str, Any] = {
        "Source": FROM_EMAIL,
        "Destination": {"ToAddresses": [message["recipient"]]},
        "Message": {
            "Subject": {"Data": message["subject"], "Charset": "UTF-8"},
            "Body": {
                "Text": {"Data": message["plain"], "Charset": "UTF-8"},
                "Html": {"Data": message["html"], "Charset": "UTF-8"},
            },
        },
    }
    if REPLY_TO:
        request["ReplyToAddresses"] = [REPLY_TO]

    result = boto3.client("ses", region_name=REGION).send_email(**request)
    return {
        "sent": True,
        "messageId": result.get("MessageId"),
        "answerCount": message["answerCount"],
        "conditionCount": message["conditionCount"],
        "followUpCount": message["followUpCount"],
        "unresolvedCount": message["unresolvedCount"],
        "declared": message["declared"],
    }


def lambda_handler(event, _context):
    event = event or {}
    call_id = event.get("callId")
    if not call_id:
        return {"sent": False, "error": "callId is required"}
    try:
        return send_confirmation(call_id, dry_run=bool(event.get("dryRun")))
    except Exception as exc:  # surfaced to the synchronous caller
        return {"sent": False, "error": str(exc)}
