"""Durable per-call archive: the verbatim transcript, and any recording, in S3.

WHY THIS EXISTS
---------------
Before this module the call record was not durable, in two separate ways that
both looked fine from the outside:

  1. The turn-by-turn transcript lives in hvi-call-state, which carries a
     THIRTY-DAY TTL (TURN_TTL_DAYS). That is correct for a conversational
     checkpoint - its job is resume, and a month-old call is not resumable - but
     it is the only place the caller's actual words were ever written. So the
     evidence behind an underwriting decision deleted itself a month after the
     call, while the decision it supported lived on in Salesforce forever.

  2. The Salesforce medical-history object already HAS Recording_URI__c and
     Transcript_URI__c, and lambdas/crm_writeback already copies them from
     session.recordingUri / session.transcriptUri. Nothing ever set those two
     session keys, so both fields were written blank on every single call. The
     plumbing was complete except for the end that produces the artefact.

That matters more here than it would elsewhere. This system writes a medical
record a claims assessor may later rely on to refuse a payout, so "what did the
customer actually say?" has to remain answerable for as long as the policy can
be contested - which is years, not thirty days.

WHAT IS WRITTEN
---------------
    s3://<bucket>/calls/<callId>/transcript.json   structured, machine-readable
    s3://<bucket>/calls/<callId>/transcript.txt    the same thing, readable
    s3://<bucket>/calls/<callId>/audio/...         per-turn recordings, if any

The JSON is the audit artefact and the .txt exists because the first person to
open one of these in anger will be a human being, not a parser.

This is deliberately NOT the same document as the customer's confirmation email
(lambdas/send_confirmation). That one is a courteous Q&A summary of the ANSWERS.
This one is the verbatim record of the CONVERSATION, including the turns that
produced no answer at all - the re-asks, the misheard values, the questions we
gave up on. Those turns are precisely what someone auditing a disputed record
needs and precisely what a summary drops.

NO OBJECT LOCK, DELIBERATELY
----------------------------
S3 Object Lock is the usual reflex for an audit trail and it is not used here.
Object Lock is retention policy enforced by making deletion IMPOSSIBLE, including
deletion this project may be legally obliged to perform: DPDP gives the customer
erasure rights, and a compliance-mode lock would leave no lawful way to honour
one. It also cannot be enabled on an existing bucket and it makes every test that
writes an object unclean up-able.

What protects the archive instead:
  - versioning, so an overwrite or delete is recoverable rather than silent
  - SSE at rest and a TLS-only bucket policy in transit
  - a public-access block, and no bucket policy granting anonymous read
  - the bucket lives in ap-south-1 with the rest of the health data (DPDP)

That is recoverability without immutability, which is the right trade for a
system that may be required to delete.

NEVER FAILS A CALL
------------------
Every entry point swallows its own exceptions and reports what happened in the
return value. An archive is worth a great deal after the call and nothing at all
during it, so a missing bucket, a denied PutObject or an S3 outage must not be
able to turn a completed intake into a failed one. The caller checks the returned
``ok`` flag; nothing raises.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

log = logging.getLogger("hvi.archive")

DATA_REGION = os.environ.get("DATA_REGION", os.environ.get("AWS_REGION", "ap-south-1"))

# Unset means archiving is OFF and every call reports skipped rather than failing.
# Same forgiving posture as the prompt cache: a missing bucket degrades, it does
# not break. scripts/55-call-archive.sh creates it and appends the export.
BUCKET = os.environ.get("CALL_ARCHIVE_BUCKET", "")
PREFIX = os.environ.get("CALL_ARCHIVE_PREFIX", "calls")

# Where the telephony leg drops per-turn recordings. Chime writes them in
# TELEPHONY_REGION because Voice Connector is not available in ap-south-1, and
# lambdas/sma/handler.py calls them transient for exactly that reason - this is
# the in-region durable copy its comment promises.
AUDIO_BUCKET = os.environ.get("AUDIO_BUCKET", "")

_s3: Any = None


def _client() -> Any:
    global _s3
    if _s3 is None:
        import boto3
        _s3 = boto3.client("s3", region_name=DATA_REGION)
    return _s3


def call_prefix(call_id: str) -> str:
    return f"{PREFIX}/{call_id}"


def transcript_key(call_id: str, ext: str = "json") -> str:
    return f"{call_prefix(call_id)}/transcript.{ext}"


def s3_uri(bucket: str, key: str) -> str:
    return f"s3://{bucket}/{key}"


def build_transcript(call_id: str, session: dict, turns: list[dict]) -> dict:
    """The structured archive document.

    Ordered by turnIndex so the file replays the call in the order it happened
    rather than in DynamoDB's. Everything recorded per turn is carried through
    verbatim - including sttConfidence, the coerced value, and any validation
    error - because a transcript that keeps only the clean turns cannot answer
    the one question an audit asks, which is whether the record is faithful to
    what was said.
    """
    ordered = sorted(turns or [], key=lambda t: int(t.get("turnIndex") or 0))
    rows = []
    for t in ordered:
        row = {
            "turnIndex": int(t.get("turnIndex") or 0),
            "role": t.get("role"),
            "text": t.get("text"),
            "timestamp": t.get("timestamp"),
        }
        # Only present when the turn actually carried them, so the file does not
        # imply a measurement that was never taken.
        for key in ("itemId", "extractedValue", "valid", "validationError",
                    "sttConfidence", "audioKey", "annotatedAt"):
            if t.get(key) is not None:
                row[key] = t.get(key)
        rows.append(row)

    return {
        "schemaVersion": 1,
        "callId": call_id,
        "archivedAt": _now_iso(),
        # Identity and provenance an underwriter needs to tie this file to a
        # policy. Deliberately the same fields the CRM writeback uses.
        "customerId": session.get("customerId"),
        "customerName": session.get("customerName"),
        "phone": session.get("phone"),
        "policyNumber": session.get("policyNumber"),
        "formId": session.get("formId"),
        "language": session.get("language"),
        "status": session.get("status"),
        "statusNote": session.get("statusNote"),
        "resumeCount": session.get("resumeCount", 0),
        "priorCallIds": session.get("priorCallIds") or [],
        "createdAt": session.get("createdAt"),
        "updatedAt": session.get("updatedAt"),
        "declarationConfirmedAt": session.get("declarationConfirmedAt"),
        # The record itself.
        "answers": session.get("answers") or {},
        "declaredConditions": session.get("declaredConditions") or [],
        "retractedConditions": session.get("retractedConditions") or [],
        # Codes WE displaced, having classified one disclosure twice. Distinct from
        # retracted, which is the caller withdrawing a disclosure - an auditor must
        # be able to tell "we corrected our own coding" from "she said she does not
        # have it".
        "supersededConditions": session.get("supersededConditions") or [],
        "followUpAnswers": session.get("followUpAnswers") or {},
        # Where the record is least certain, which is where an auditor looks
        # first: what we could not classify, and what we gave up asking.
        "unclassifiedMentions": session.get("unclassifiedMentions") or [],
        "humanFollowUpItems": session.get("humanFollowUpItems") or [],
        "turnCount": len(rows),
        "turns": rows,
    }


def render_transcript_text(doc: dict) -> str:
    """The same archive as something a person can read without a JSON viewer."""
    lines: list[str] = []
    lines.append(f"Call {doc.get('callId')}")
    lines.append(f"Customer   : {doc.get('customerName') or '-'} "
                 f"({doc.get('phone') or '-'})")
    lines.append(f"Policy     : {doc.get('policyNumber') or '-'}")
    lines.append(f"Form       : {doc.get('formId') or '-'}")
    lines.append(f"Language   : {doc.get('language') or '-'}")
    lines.append(f"Status     : {doc.get('status') or '-'}"
                 + (f" ({doc['statusNote']})" if doc.get("statusNote") else ""))
    lines.append(f"Started    : {doc.get('createdAt') or '-'}")
    lines.append(f"Archived   : {doc.get('archivedAt') or '-'}")
    if int(doc.get("resumeCount") or 0):
        lines.append(f"Resumed    : {doc['resumeCount']} time(s); "
                     f"prior legs {', '.join(doc.get('priorCallIds') or []) or '-'}")
    lines.append("")

    lines.append(f"--- conversation ({doc.get('turnCount', 0)} turns) ---")
    for t in doc.get("turns") or []:
        who = str(t.get("role") or "?")
        tag = f"[{t['itemId']}]" if t.get("itemId") else ""
        lines.append(f"{str(t.get('turnIndex')):>4} {who:<9}{tag:<10} "
                     f"{t.get('text') or ''}".rstrip())
        detail = []
        if t.get("extractedValue") is not None:
            detail.append(f"recorded={t['extractedValue']!r}")
        if t.get("valid") is not None:
            detail.append(f"valid={t['valid']}")
        if t.get("validationError"):
            detail.append(f"error={t['validationError']}")
        if detail:
            lines.append(" " * 25 + "  ".join(detail))

    conditions = doc.get("declaredConditions") or []
    if conditions:
        lines.append("")
        lines.append("--- declared conditions ---")
        for c in conditions:
            lines.append(f"  {c.get('icd10') or '-'}  {c.get('label') or ''}"
                         + (f"   (said: {c['patientPhrase']!r})"
                            if c.get("patientPhrase") else ""))

    superseded = doc.get("supersededConditions") or []
    if superseded:
        lines.append("")
        lines.append("--- codes we replaced (one disclosure classified twice) ---")
        for c in superseded:
            lines.append(f"  {c.get('icd10') or '-'}  {c.get('label') or ''}"
                         + f"   replaced by {c.get('supersededBy') or '-'}"
                         + (f"   (said: {c['patientPhrase']!r})"
                            if c.get("patientPhrase") else ""))

    owed = doc.get("humanFollowUpItems") or []
    if owed:
        lines.append("")
        lines.append("--- questions left for a person to complete ---")
        for e in owed:
            lines.append(f"  {e.get('ref')}  {e.get('label') or ''}"
                         + (f"   ({e['reason']})" if e.get("reason") else ""))

    unknown = doc.get("unclassifiedMentions") or []
    if unknown:
        lines.append("")
        lines.append("--- heard but not classified ---")
        for e in unknown:
            if isinstance(e, dict):
                lines.append(f"  {e.get('text') or e.get('phrase') or e}")
            else:
                lines.append(f"  {e}")

    return "\n".join(lines) + "\n"


def _copy_recordings(call_id: str, turns: list[dict]) -> tuple[str | None, int]:
    """Copy any per-turn recordings into the archive, in-region.

    Returns (prefix uri or None, count). The browser leg records no audio at all
    - a WebRTC session has no RecordAudio equivalent wired - so on the surface
    that is actually in use today this copies nothing and returns None, which is
    the honest answer rather than a URI pointing at an empty prefix. The
    telephony leg does produce per-turn WAVs, and those carry audioKey on the
    turn, which is what this walks.
    """
    keys = [t.get("audioKey") for t in (turns or []) if t.get("audioKey")]
    if not keys or not AUDIO_BUCKET:
        return None, 0
    copied = 0
    for key in keys:
        name = str(key).rsplit("/", 1)[-1]
        dest = f"{call_prefix(call_id)}/audio/{name}"
        try:
            _client().copy_object(
                Bucket=BUCKET, Key=dest,
                CopySource={"Bucket": AUDIO_BUCKET, "Key": str(key)},
                ServerSideEncryption="AES256",
            )
            copied += 1
        except Exception as e:  # noqa: BLE001 - one missing clip is not a failure
            log.warning("could not archive recording %s: %s", key, e)
    if not copied:
        return None, 0
    return s3_uri(BUCKET, f"{call_prefix(call_id)}/audio/"), copied


def _find_archived_recording(call_id: str) -> str | None:
    """The call's own WAV, if the media host already wrote one. None otherwise.

    THE OWNERSHIP SPLIT THIS EXISTS TO BRIDGE. Audio only exists inside the media
    process - pipecat holds the frames - and that host deliberately has no
    DynamoDB permissions (see media/agent_bridge.py), so it cannot set
    session.recordingUri itself. It CAN write S3, and common/hvi_recording writes
    to one deterministic key per call. So the agent, which does own the session,
    discovers the object by asking whether it exists rather than by being told.

    A HEAD, not a LIST: one request, no pagination, and it cannot be fooled by an
    empty prefix left behind by a failed upload - which is the bug that would put
    a dead link on a medical record.
    """
    if not BUCKET:
        return None
    key = f"{call_prefix(call_id)}/audio/call.wav"
    try:
        _client().head_object(Bucket=BUCKET, Key=key)
    except Exception:  # noqa: BLE001 - absent is the normal answer, not an error
        return None
    return s3_uri(BUCKET, key)


def find_recording(call_id: str) -> str | None:
    """This call's archived WAV, or None. The LATE-LINKING entry point.

    Public because archive_call is not the only caller that needs this, and the
    other one is the reason the recording URI used to be permanently blank.

    THE RACE THIS EXISTS TO CLOSE. archive_call runs when the call ENDS - inside
    submit_to_crm, or on the turn that made the session terminal - and at that
    moment the caller is still hearing the closing words. The audio does not exist
    yet: it lives in pipecat's buffer until the pipeline tears down, seconds later,
    and only then does the media host upload it. So the HEAD inside archive_call
    always asked too early and always got "absent", which is why every call had a
    transcriptUri and no recordingUri, and why Salesforce's Recording_URI__c was
    blank even for calls whose WAV was sitting in the bucket.

    Discovery is deliberately still a HEAD here rather than trusting a URI sent by
    the media host: the key is deterministic, so the host has nothing to tell us
    that we cannot verify, and verifying is what keeps a dead link off a medical
    record. See agent tools.link_recording for the caller.
    """
    return _find_archived_recording(call_id)


def archive_call(call_id: str, session: dict,
                 turns: list[dict] | None = None) -> dict:
    """Write the archive for one call. NEVER RAISES.

    Returns {"ok": bool, "skipped": bool, "transcriptUri": str|None,
             "recordingUri": str|None, "recordingCount": int, "error": str|None}.

    ``skipped`` is true when no bucket is configured, which is a normal local
    state and not an error - the caller should not treat it as a failure.
    """
    result: dict[str, Any] = {"ok": False, "skipped": False,
                             "transcriptUri": None, "recordingUri": None,
                             "recordingCount": 0, "error": None}
    if not BUCKET:
        result["skipped"] = True
        log.info("CALL_ARCHIVE_BUCKET unset - not archiving %s", call_id)
        return result
    if not session:
        result["error"] = "no session"
        return result

    try:
        doc = build_transcript(call_id, session, turns or [])
        body = json.dumps(doc, ensure_ascii=False, indent=2,
                          default=str).encode("utf-8")
        # ensure_ascii=False because the transcript is Hindi more often than not
        # and an archive full of \u escapes is unreadable to the person who has
        # to audit it.
        meta = {"callid": call_id, "formid": str(session.get("formId") or "")}
        _client().put_object(
            Bucket=BUCKET, Key=transcript_key(call_id, "json"), Body=body,
            ContentType="application/json; charset=utf-8",
            ServerSideEncryption="AES256", Metadata=meta,
        )
        _client().put_object(
            Bucket=BUCKET, Key=transcript_key(call_id, "txt"),
            Body=render_transcript_text(doc).encode("utf-8"),
            ContentType="text/plain; charset=utf-8",
            ServerSideEncryption="AES256", Metadata=meta,
        )
        result["transcriptUri"] = s3_uri(BUCKET, transcript_key(call_id, "json"))

        # Recordings are a best-effort extra: the transcript is the artefact that
        # matters and it is already written by here, so a copy failure must not
        # discard it.
        try:
            uri, count = _copy_recordings(call_id, turns or [])
            if not uri:
                # No per-turn clips to copy (the telephony Chime path). Look for a
                # whole-call WAV written straight into this prefix by the media
                # host instead - the live surfaces record that way.
                uri = _find_archived_recording(call_id)
                count = 1 if uri else 0
            result["recordingUri"], result["recordingCount"] = uri, count
        except Exception as e:  # noqa: BLE001
            log.warning("recording archive failed for %s: %s", call_id, e)

        result["ok"] = True
        log.info("archived %s -> %s (%d recording(s))", call_id,
                 result["transcriptUri"], result["recordingCount"])
        return result
    except Exception as e:  # noqa: BLE001 - an archive never fails a call
        result["error"] = f"{type(e).__name__}: {e}"
        log.warning("archive failed for %s: %s", call_id, result["error"])
        return result


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
