"""
Bedrock AgentCore runtime for the medical-history intake agent.

AgentCore Runtime contract: HTTP on port 8080, POST /invocations and GET /ping,
packaged as a linux/arm64 container.

This file is deliberately thin. The protocol lives in orchestrator.py and the
single narrow model call lives in extract.py. What used to be here was a ReAct
loop that handed the model eleven tools and trusted it to sequence them; in
testing it skipped checkpoints and follow-ups, which is not a defect you fix with
a firmer prompt when the audit trail depends on it.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

import orchestrator
import tools

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("hvi-agent")

MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "mistral.mistral-large-3-675b-instruct")

app = FastAPI(title="hvi-agent")

# Scripts with no business in an en-IN / hi-IN / Hinglish call. This guard exists
# because a real test run emitted the Russian word "свободны" inside an otherwise
# correct Hindi sentence. Rare, but not something to send to a patient.
_FOREIGN_SCRIPT = re.compile(
    r"[\u0400-\u04FF\u4E00-\u9FFF\u3040-\u30FF\uAC00-\uD7AF"
    r"\u0600-\u06FF\u0E00-\u0E7F\u0590-\u05FF]"
)


def _speakable(text: str) -> str:
    """Strip anything TTS would read as punctuation noise."""
    t = text or ""
    t = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", t)
    t = re.sub(r"`{1,3}([^`]+)`{1,3}", r"\1", t)
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.M)
    t = re.sub(r"^\s*[-*+]\s+", "", t, flags=re.M)
    t = re.sub(r"^\s*\d+[.)]\s+", "", t, flags=re.M)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def _sanitise(text: str) -> tuple[str, str | None]:
    """
    Make the utterance safe to speak.

    Only the acknowledgement is model-generated now; the question text itself
    comes from the form schema or the knowledge graph. So contamination can be
    dropped rather than retried - removing a stray token cannot damage a question
    that the model did not write.
    """
    t = _speakable(text)
    m = _FOREIGN_SCRIPT.search(t)
    if not m:
        return t, None
    cleaned = _speakable(_FOREIGN_SCRIPT.sub("", t))
    return cleaned, m.group(0)


FORM_ID = os.environ.get("FORM_ID", "arogya-sanjeevani-v1")

# Demo mode: every inbound call starts a brand new questionnaire. See _bootstrap.
# Off unless explicitly set, because it throws away collected answers.
ALWAYS_FRESH = os.environ.get("HVI_ALWAYS_FRESH", "0") not in ("0", "", "false")

# Said to a caller we cannot identify. Declining is the correct behaviour, not a
# limitation: this call collects medical history, and taking it from an
# unverified number would be both a privacy failure and a worthless record.
_UNKNOWN_CALLER = {
    "en": "Thank you for calling. I'm sorry, but I don't recognise this number, "
          "so I can't discuss policy details. Please call the number printed on "
          "your policy document and we'll be glad to help.",
    "hi": "कॉल करने के लिए धन्यवाद। मुझे खेद है, यह नंबर हमारे रिकॉर्ड में नहीं है, "
          "इसलिए मैं पॉलिसी की जानकारी साझा नहीं कर सकती। कृपया अपनी पॉलिसी पर "
          "दिए गए नंबर पर संपर्क करें।",
}


def _bootstrap_session(caller_phone: str) -> tuple[str | None, str | None]:
    """Resolve a caller's phone number to a session, creating or resuming one.

    The orchestrator requires a session to exist before the first turn, and
    until now nothing created one - /invocations assumed an outbound dialler had
    already done it. That left two real paths broken: any inbound call, and a
    customer ringing back to continue an interrupted questionnaire.

    Session creation belongs here rather than in the media layer for two reasons.
    The orchestrator owns all durable state, and the media host deliberately has
    no DynamoDB permissions at all - it is the only machine with a public IP and
    an open SIP port, and giving it write access to health records to solve a
    bookkeeping problem would be a poor trade.

    Returns (call_id, refusal_text). Exactly one is set.
    """
    customer = tools.st.get_customer_by_phone(caller_phone)
    if not customer:
        log.warning("inbound call from unrecognised number %s", caller_phone)
        return None, _UNKNOWN_CALLER["en"]

    # A number with an unfinished questionnaire resumes it. start_session carries
    # the answers AND the outstanding follow-ups across, then retires the old
    # session so a later callback cannot pick it up twice.
    #
    # HVI_ALWAYS_FRESH exists for demos and it is NOT a general-purpose switch.
    #
    # Resume is the point of the DynamoDB checkpoint: a caller cut off mid-protocol
    # must come back to the follow-ups still owed rather than start over. Working
    # exactly as designed, it makes the system undemonstrable - every test call from
    # the same number opens "We were cut off earlier" and drops the listener into
    # the middle of a protocol with no idea what is being asked, because the earlier
    # test never reached the end. Three consecutive calls landed on an orphaned
    # thyroid follow-up.
    #
    # Retiring the stale session instead of ignoring it, so the next call is
    # genuinely clean rather than resuming the same leftover again. Loud on purpose:
    # this discards real answers, and anyone who finds it enabled in an environment
    # that takes real calls needs to see it in the log.
    form_id = customer.get("assignedFormId") or FORM_ID
    if ALWAYS_FRESH:
        stale = tools.st.find_resumable_session(caller_phone)
        if stale:
            # ABANDONED, not DROPPED: RESUMABLE is (in_progress, dropped), so
            # marking it dropped would leave it eligible and the next call would
            # resume the very session this just tried to retire.
            tools.st.mark_status(stale["callId"], tools.st.STATUS_ABANDONED,
                                 note="retired by HVI_ALWAYS_FRESH (demo mode)")
            # Archive BEFORE walking away from it. This branch discards answers a
            # real person actually gave, and it is the one ending where that is
            # done deliberately rather than because the call failed - so throwing
            # the transcript away with them is the least defensible of all. Done
            # after mark_status so the archived document records the final
            # abandoned status rather than a stale in_progress. Never raises.
            tools.archive_now(stale["callId"], reason="abandoned:always_fresh")
            log.warning("HVI_ALWAYS_FRESH: DISCARDED resumable session %s for %s. "
                        "Answers already collected on it are abandoned. This must "
                        "not be set where real calls are taken.",
                        stale["callId"], caller_phone)
        resumable = None
    else:
        # Form id passed so a session collected against a DIFFERENT questionnaire
        # is not inherited. See hvi_state.find_resumable_session.
        resumable = tools.st.find_resumable_session(caller_phone, form_id)
    session = tools.st.start_session(
        caller_phone,
        customer,
        form_id,
        resume_from=resumable,
    )
    if resumable:
        log.info("resuming %s as %s for %s (resumeCount=%s)",
                 resumable["callId"], session["callId"], caller_phone,
                 session.get("resumeCount"))
        # start_session has just marked the previous leg ABANDONED ("superseded
        # by <new callId>"). That leg holds its own turns - the half of the
        # conversation this call is continuing from - and nothing else will ever
        # archive it, so without this the earlier half of a resumed intake is the
        # part that expires. Archived here rather than inside hvi_state.start_session
        # to keep the state layer free of S3. Never raises.
        tools.archive_now(resumable["callId"],
                          reason=f"abandoned:superseded_by_{session['callId']}")
    else:
        log.info("new session %s for %s", session["callId"], caller_phone)
    return session["callId"], None


def _form_state(session: dict, *, include_schema: bool) -> dict:
    """A compact view of the questionnaire, for a caller that renders it live.

    OPT-IN, and that is the point rather than an optimisation. The phone media
    host has no use for this: it speaks one question at a time and reads nothing
    back. It is also the only machine in the system with a public IP and an open
    SIP port, so putting the accumulated health record on every one of its
    replies would widen that exposure to solve a problem it does not have. The
    browser surface asks for it; the phone path never does, and its replies stay
    byte-for-byte what they were.

    Visibility is delegated to show_item rather than recomputed, so the totals a
    UI shows are the same ones the protocol will actually ask. Counting all 38
    items would promise questions that a `showIf` gate will never reach.
    """
    answers = dict(session.get("answers") or {})
    try:
        items = tools.st.get_form_items(session.get("formId") or FORM_ID)
    except Exception as e:  # noqa: BLE001
        # A UI that cannot draw a progress bar is a worse outcome than a UI that
        # draws nothing, but neither justifies failing the turn: the caller is
        # mid-sentence and the answer is already committed.
        log.warning("could not load form items for live state: %s", e)
        items = []

    visible = [i for i in items if tools.st.show_item(i, answers)]
    state: dict = {
        "answers": answers,
        "askedItems": list(session.get("askedItems") or []),
        "currentItemId": session.get("currentItemId"),
        "answeredCount": sum(1 for i in visible if i.get("itemId") in answers),
        "remainingCount": tools.st.remaining_count(items, answers),
        "visibleCount": len(visible),
        "status": session.get("status"),
    }

    # The schema is asked for once, on the opening turn, and never again. It is
    # the bulky half - 38 items in three languages - and it does not change
    # mid-call, so repeating it every turn would pay for it 30 times over.
    if include_schema:
        state["items"] = [
            {
                "itemId": i.get("itemId"),
                "order": i.get("order"),
                "section": i.get("section"),
                "label": i.get("label"),
                "answerType": i.get("answerType"),
                "prompt": i.get("prompt"),
                "promptHi": i.get("promptHi"),
                "promptHinglish": i.get("promptHinglish"),
                "sensitivity": i.get("sensitivity"),
                "required": bool(i.get("required")),
                "unit": i.get("unit"),
            }
            for i in items
        ]
    return state


@app.get("/ping")
def ping() -> dict:
    return {"status": "Healthy"}


def _recording_stored(call_id: str | None) -> JSONResponse:
    """The media host telling us this call's audio is now in S3.

    NOT A TURN. It takes no lease, runs no orchestration and says nothing to the
    caller - by the time it arrives the pipeline has already torn down. It exists
    because the recording is uploaded AFTER the call ends, so the archive's own
    lookup at completion time always ran too early and left recordingUri unset and
    Salesforce's Recording_URI__c blank. See tools.link_recording.

    The body carries only a callId. The S3 key is deterministic, so the agent
    verifies the object itself rather than trusting a URI from the media host,
    which is what keeps an unverified link off a medical record.
    """
    if not call_id:
        return JSONResponse({"error": "callId is required"}, status_code=400)
    if not tools.st.get_session(call_id):
        return JSONResponse({"error": f"unknown callId {call_id}"}, status_code=400)
    # link_recording never raises; a failure comes back as ok/error on the body.
    return JSONResponse(tools.link_recording(call_id))


@app.post("/invocations")
async def invocations(request: Request) -> JSONResponse:
    import asyncio

    started = time.time()
    # The stage accumulators are module-level and the container is reused across
    # turns, so without this a turn inherits the previous turn's timings.
    tools.reset_stage_timing()
    body = await request.json()

    # Side-effect-only actions are served BEFORE any turn machinery: no lease, no
    # fingerprint, no orchestration. Placed here so the turn path below is exactly
    # what it always was for a real utterance.
    if body.get("action") == "recordingStored":
        return _recording_stored(body.get("callId"))

    call_id = body.get("callId")
    utterance = (body.get("userUtterance") or "").strip()
    turn_index = int(body.get("turnIndex") or 0)
    language = body.get("language") or "hinglish"
    stt_conf = body.get("sttConfidence")
    caller_phone = body.get("callerPhone")
    # Live-UI extras. Deliberately not part of the turn fingerprint below: they
    # change what the reply CARRIES, not what the turn DOES, so a retry that
    # differs only in these must still be recognised as the same turn and
    # replayed rather than re-run.
    include_form_state = bool(body.get("includeFormState"))
    include_form_schema = bool(body.get("includeFormSchema"))
    turn_fingerprint = hashlib.sha256(json.dumps({
        "utterance": utterance,
        "language": language,
        "sttConfidence": stt_conf,
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()

    if not call_id and not caller_phone:
        return JSONResponse({"error": "callId or callerPhone is required"},
                            status_code=400)

    # The media layer opens a call knowing the caller's number and a media
    # session id, not a business callId - it cannot know one, because the session
    # may not exist yet. So an unknown callId plus a phone number means "start or
    # resume this caller's questionnaire", and the resolved callId goes back on
    # the reply for the bridge to adopt on later turns.
    if caller_phone and (not call_id or not tools.st.get_session(call_id)):
        resolved, refusal = _bootstrap_session(caller_phone)
        if refusal:
            return JSONResponse({
                "callId": call_id,
                "agentUtterance": refusal,
                "turnIndex": turn_index,
                "isComplete": True,
                "shouldHangUp": True,
                "language": "en",
                "latencyMs": int((time.time() - started) * 1000),
            }, status_code=200)
        call_id = resolved

    if not tools.st.get_session(call_id):
        return JSONResponse({"error": f"unknown callId {call_id}"}, status_code=400)

    # One complete turn is a critical section. A DynamoDB lease is required
    # rather than an in-process lock because overlapping requests can land on
    # different AgentCore workers. The lease is held through transcript admission,
    # orchestration, and the durable agent-turn write.
    lease_owner = f"{os.getpid()}-{time.time_ns()}"
    lease_deadline = time.monotonic() + 5.0
    session = None
    while session is None and time.monotonic() < lease_deadline:
        session = tools.st.acquire_turn_lease(call_id, lease_owner)
        if session is None:
            await asyncio.sleep(0.05)
    if session is None:
        return JSONResponse({
            "callId": call_id,
            "agentUtterance": "Please wait a moment and repeat that.",
            "turnIndex": turn_index,
            "isComplete": False,
            "escalated": False,
            "stopQuestionnaire": False,
            "shouldHangUp": False,
            "language": language,
            "toolTrace": [],
            "notes": ["turn not admitted because another request still owns the call lease"],
            "latencyMs": int((time.time() - started) * 1000),
        }, status_code=200)

    try:
        # A completed text turn is idempotent. Network retries commonly repeat the
        # same turn index; without this guard the orchestrator can consume another
        # follow-up or append the same declaration twice. A changed payload at the
        # same index, or any older index, is rejected without transcript admission.
        last_raw = session.get("lastAdmittedTurn", -1)
        last_admitted = int(last_raw) if last_raw is not None else -1
        if turn_index <= last_admitted:
            cached = session.get("lastTurnResponse")
            same_turn = turn_index == last_admitted
            same_payload = session.get("lastTurnFingerprint") == turn_fingerprint
            if same_turn and same_payload and isinstance(cached, dict):
                replay = dict(cached)
                replay["notes"] = list(replay.get("notes") or []) + [
                    "idempotently replayed an already completed turn"
                ]
                replay["latencyMs"] = int((time.time() - started) * 1000)
                return JSONResponse(replay, status_code=200)
            return JSONResponse({
                "callId": call_id,
                "agentUtterance": "That reply is out of sequence. Please answer the current question.",
                "turnIndex": turn_index,
                "isComplete": False,
                "escalated": False,
                "stopQuestionnaire": False,
                "shouldHangUp": False,
                "language": session.get("language") or language,
                "toolTrace": [],
                "notes": ["rejected stale or conflicting turn without mutation"],
                "latencyMs": int((time.time() - started) * 1000),
            }, status_code=200)

        # Terminality is checked on the lease-acquisition image before persisting
        # anything. A request waiting behind a terminating turn therefore observes
        # the terminal status and cannot write a transcript or answer.
        if tools.st.is_terminal_session(session):
            status = session.get("status")
            complete = status == tools.st.STATUS_COMPLETED
            closing = ("This questionnaire is already complete. Thank you." if complete
                       else "This call cannot continue. Our team will contact you separately.")
            return JSONResponse({
                "callId": call_id,
                "agentUtterance": closing,
                "turnIndex": turn_index,
                "isComplete": complete,
                "escalated": status == tools.st.STATUS_ESCALATED,
                "stopQuestionnaire": True,
                "shouldHangUp": True,
                "language": session.get("language") or language,
                "toolTrace": [],
                "notes": [f"rejected turn for terminal session status={status}"],
                "latencyMs": int((time.time() - started) * 1000),
            }, status_code=200)

        # Verification cannot succeed without the authoritative CRM value. Fail
        # before asking for or persisting a DOB, and before invoking the extractor.
        missing_dob = not session.get("dateOfBirth")
        if missing_dob:
            result = orchestrator._terminate_identity_unavailable(
                call_id, session, turn_index, language)
        else:
            # Persist only admitted customer words. The turn lease guarantees that
            # no concurrent request can terminate the call between this write and
            # the answer checkpoint.
            if utterance:
                try:
                    tools.st.record_turn(call_id, turn_index, "customer",
                                         text=utterance, stt_confidence=stt_conf)
                except Exception as e:
                    log.warning("could not record customer turn: %s", e)

            try:
                result = orchestrator.handle_turn(
                    call_id=call_id,
                    utterance=utterance,
                    turn_index=turn_index,
                    language_hint=language,
                    stt_confidence=stt_conf,
                )
            except Exception as e:
                log.exception("turn failed for %s", call_id)
                return JSONResponse({
                    "callId": call_id,
                    "error": str(e),
                    "agentUtterance": "I'm sorry, I'm having a technical problem. "
                                      "Someone will call you back shortly.",
                    "turnIndex": turn_index,
                    "shouldHangUp": True,
                    "latencyMs": int((time.time() - started) * 1000),
                }, status_code=200)

        reply, contaminant = _sanitise(result.get("agentUtterance", ""))
        if contaminant:
            log.warning("stripped foreign-script character %r from reply", contaminant)
            result.setdefault("notes", []).append(
                f"stripped foreign-script character {contaminant!r}")
        if not reply:
            reply = "Sorry, could you please repeat that?"
        result["agentUtterance"] = reply

        agent_turn = turn_index + 1
        try:
            tools.record_agent_turn(call_id, agent_turn, reply,
                                    item_id=result.get("askedRef"))
        except Exception as e:
            log.warning("could not record agent turn: %s", e)

        latency_ms = int((time.time() - started) * 1000)
        # Which backend spent the time, not just how long the turn took. Read
        # before anything below can add another call and skew it.
        stage_ms = tools.stage_summary()

        # Best-effort audit write. The missing-DOB preflight deliberately excludes
        # the rejected customer utterance from both durable and search transcripts.
        # Bound before the try so the live-UI block below can rely on it even if
        # the audit write throws. The audit log is best-effort; losing it must not
        # also cost the reply its form state.
        current: dict = {}
        try:
            current = tools.st.get_session(call_id) or {}
            if utterance and not missing_dob:
                tools.log_turn(call_id, turn_index, "customer", utterance,
                               language=result.get("language"), stt_confidence=stt_conf,
                               customer_id=current.get("customerId"),
                               policy_number=current.get("policyNumber"))
            tools.log_turn(call_id, agent_turn, "agent", reply,
                           item_id=result.get("askedRef"),
                           language=result.get("language"), latency_ms=latency_ms,
                           stage_ms=stage_ms,
                           customer_id=current.get("customerId"),
                           policy_number=current.get("policyNumber"))
        except Exception as e:
            log.warning("audit log failed: %s", e)

        result.update({
            "callId": call_id,
            "turnIndex": agent_turn,
            "latencyMs": latency_ms,
            "stageMs": stage_ms,
            "modelId": MODEL_ID,
            "shouldHangUp": bool(result.get("isComplete") or result.get("stopQuestionnaire")),
        })

        try:
            tools.st.checkpoint_admitted_turn(
                call_id, turn_index, turn_fingerprint, dict(result))
        except Exception as e:
            # The dialogue mutation may already have completed, so never retry it
            # inside this request. Surface the observability signal and let the
            # non-displaceable lease prevent concurrent corruption.
            log.error("could not checkpoint admitted turn %s/%s: %s",
                      call_id, turn_index, e)
            result.setdefault("notes", []).append(
                "turn completed but idempotency checkpoint failed")

        # ---- archive a call that just ended without completing ---------------
        # This is the turn that ENDED the call, and for every ending except a
        # clean completion nothing else would ever archive it: submit_to_crm is
        # the only other archive site, and a terminated or refused call never
        # reaches it. The transcript would then exist only in hvi-call-state and
        # be deleted by its 30-day TTL.
        #
        # Gated on transcriptUri so a completion is not archived twice - it was
        # already written inside submit_to_crm, before the CRM writeback, because
        # the writeback needs the URI. Re-archiving would be harmless but wasteful.
        #
        # AFTER the checkpoint and never in front of the reply: the caller is
        # already hearing the closing words, and the latency figure above has
        # been taken, so an S3 put here delays nothing the caller experiences.
        # archive_now never raises.
        try:
            ended = tools.st.get_session(call_id) or current or {}
            if tools.st.is_terminal_session(ended) and not ended.get("transcriptUri"):
                tools.archive_now(
                    call_id, reason=f"ended:{ended.get('status') or 'unknown'}")
        except Exception as e:
            log.warning("could not archive ended call %s: %s", call_id, e)

        # AFTER the checkpoint, deliberately. The checkpoint exists to make the
        # DIALOGUE idempotent, and this is a rendering aid: persisting it would
        # copy the answers map into lastTurnResponse inside the very session item
        # that already holds it, and with the schema attached that is a 400KB
        # item-limit hazard for a field nothing reads back. A network retry gets
        # a reply without formState, which costs the UI nothing - it still holds
        # the state from the turn that did land.
        if include_form_state:
            result["formState"] = _form_state(
                current or {}, include_schema=include_form_schema)
        return JSONResponse(result)
    finally:
        try:
            tools.st.release_turn_lease(call_id, lease_owner)
        except Exception as e:
            log.warning("could not release turn lease for %s: %s", call_id, e)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
