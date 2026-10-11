"""
Chime SDK SIP Media Application handler. This is the telephony edge.

Amazon Connect is NOT used anywhere in this stack. Chime SDK Voice Connector is a
SIP trunk and SBC, not a contact centre: no queues, no agents, no contact flows.
It exists here to terminate a SIP trunk, which is precisely the role Tata
Communications fills in production. Swapping carriers is an origination route and
an IP allowlist entry on the Voice Connector, not an architecture change.

Worth knowing: Chime SDK Voice is not available in ap-south-1 at all, which is
the concrete reason an Indian carrier is required rather than an AWS one. The
telephony edge therefore runs in us-east-1 while every store holding health data
stays in ap-south-1.

The turn loop, driven by SMA action events:

    CALL_ANSWERED        -> PlayAudio(greeting) then RecordAudio
    ACTION_SUCCESSFUL    -> if the last action was RecordAudio:
                              transcribe with Whisper
                              invoke AgentCore for the next utterance
                              synthesise it, PlayAudio, RecordAudio again
    HANGUP               -> mark the session dropped or completed

This is half-duplex by design. True barge-in would need Voice Connector media
streaming into KVS plus a persistent consumer; the record-then-respond loop is
the honest trade for a demo, and the latency is covered by keeping each turn
under about two seconds.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "common"))
import hvi_state as st  # noqa: E402

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
log = logging.getLogger("hvi-sma")

DATA_REGION = os.environ.get("DATA_REGION", "ap-south-1")
TELEPHONY_REGION = os.environ.get("TELEPHONY_REGION", "us-east-1")
AUDIO_BUCKET = os.environ["AUDIO_BUCKET"]
PROMPT_PREFIX = os.environ.get("PROMPT_PREFIX", "prompts/")
RECORDING_PREFIX = os.environ.get("RECORDING_PREFIX", "recordings/")
AGENT_RUNTIME_ARN = os.environ.get("AGENT_RUNTIME_ARN", "")
TTS_FN = os.environ.get("TTS_FN", "hvi-tts")
STT_ENDPOINT = os.environ.get("STT_ENDPOINT", "hvi-whisper")

# Silence that ends a recording. Long enough that a thoughtful pause is not cut
# off, short enough that the caller is not left waiting.
SILENCE_SECONDS = int(os.environ.get("SILENCE_SECONDS", "2"))
MAX_RECORD_SECONDS = int(os.environ.get("MAX_RECORD_SECONDS", "30"))

s3 = boto3.client("s3", region_name=TELEPHONY_REGION)
agentcore = boto3.client("bedrock-agentcore", region_name=DATA_REGION)
lambda_client = boto3.client("lambda", region_name=DATA_REGION)


# --------------------------------------------------------------------------- #
# SMA actions
# --------------------------------------------------------------------------- #
def play(key: str) -> dict:
    return {
        "Type": "PlayAudio",
        "Parameters": {
            "AudioSource": {"Type": "S3", "BucketName": AUDIO_BUCKET, "Key": key},
            "Repeat": 1,
        },
    }


def record(call_id: str, turn: int) -> dict:
    return {
        "Type": "RecordAudio",
        "Parameters": {
            "DurationInSeconds": MAX_RECORD_SECONDS,
            "SilenceDurationInSeconds": SILENCE_SECONDS,
            "SilenceThreshold": 200,
            "RecordingTerminators": ["#"],
            "RecordingDestination": {
                "Type": "S3",
                "BucketName": AUDIO_BUCKET,
                "Prefix": f"{RECORDING_PREFIX}{call_id}/turn-{turn:03d}",
            },
        },
    }


def hangup() -> dict:
    return {"Type": "Hangup", "Parameters": {"SipResponseCode": "0"}}


def pause(ms: int = 500) -> dict:
    return {"Type": "Pause", "Parameters": {"DurationInMilliseconds": ms}}


# --------------------------------------------------------------------------- #
# pipeline steps
# --------------------------------------------------------------------------- #
def transcribe_recording(bucket: str, key: str) -> dict:
    """
    Pull the recording and run Whisper on it.

    Reads from us-east-1 (where Chime writes) and sends to the ap-south-1
    endpoint. The recording is transient; the durable archive lives in-region.
    """
    try:
        audio = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    except Exception as e:
        log.warning("could not fetch recording %s/%s: %s", bucket, key, e)
        return {"text": "", "empty": True, "error": str(e)}

    import hvi_stt
    return hvi_stt.transcribe(audio, endpoint=STT_ENDPOINT)


def ask_agent(call_id: str, utterance: str, turn: int, language: str,
              stt_confidence: float | None = None) -> dict:
    """Invoke the AgentCore runtime for the next thing to say."""
    if not AGENT_RUNTIME_ARN:
        return {"agentUtterance": "Configuration error, no agent runtime.",
                "shouldHangUp": True}
    payload = json.dumps({
        "callId": call_id,
        "userUtterance": utterance,
        "turnIndex": turn,
        "language": language,
        "sttConfidence": stt_confidence,
    }).encode()
    r = agentcore.invoke_agent_runtime(
        agentRuntimeArn=AGENT_RUNTIME_ARN,
        # One runtime session per call, padded to the 33-character minimum.
        runtimeSessionId=f"{call_id}-sma-pad-000000000000",
        payload=payload,
        contentType="application/json",
        accept="application/json",
    )
    return json.loads(r["response"].read())


def synthesise(text: str, call_id: str, turn: int, language: str) -> str:
    """
    Turn agent text into an S3 audio key.

    Fixed prompts are pre-rendered, so this is only reached for dynamic
    utterances. Returns the S3 key to play.
    """
    r = lambda_client.invoke(
        FunctionName=TTS_FN,
        Payload=json.dumps({
            "text": text,
            "language": language,
            "bucket": AUDIO_BUCKET,
            "key": f"{PROMPT_PREFIX}dynamic/{call_id}/turn-{turn:03d}.wav",
        }).encode(),
    )
    body = json.loads(r["Payload"].read())
    if not body.get("key"):
        raise RuntimeError(f"tts failed: {body}")
    return body["key"]


def speak_and_listen(call_id: str, text: str, turn: int, language: str) -> list[dict]:
    """Say something, then listen. The unit of a conversational turn."""
    key = synthesise(text, call_id, turn, language)
    return [play(key), pause(300), record(call_id, turn + 1)]


def _terminal_text(session: dict, language: str) -> str:
    if session.get("status") == st.STATUS_COMPLETED:
        return {
            "hi": "यह प्रश्नावली पहले ही पूरी हो चुकी है। धन्यवाद।",
            "hinglish": "Yeh questionnaire pehle hi complete ho chuka hai. Dhanyavaad.",
        }.get(language, "This questionnaire is already complete. Thank you.")
    return {
        "hi": "यह कॉल आगे जारी नहीं रह सकती। हमारी टीम आपसे अलग से संपर्क करेगी।",
        "hinglish": ("Yeh call aage continue nahi ho sakti. Hamari team aapse "
                      "alag se contact karegi."),
    }.get(language, "This call cannot continue. Our team will contact you separately.")


def _closing_actions(call_id: str, text: str, turn: int, language: str) -> list[dict]:
    try:
        closing_key = synthesise(text, call_id, turn, language)
        return [play(closing_key), pause(300), hangup()]
    except Exception as exc:
        log.warning("closing synthesis failed: %s", exc)
        return [hangup()]


# --------------------------------------------------------------------------- #
# handler
# --------------------------------------------------------------------------- #
def lambda_handler(event, _context):
    log.info("SMA event: %s", json.dumps(event)[:800])

    event_type = event.get("InvocationEventType")
    details = event.get("CallDetails") or {}
    participants = details.get("Participants") or []
    tx_attrs = details.get("TransactionAttributes") or {}

    call_id = tx_attrs.get("hviCallId")
    language = tx_attrs.get("language", "hinglish")
    turn = int(tx_attrs.get("turn", "0"))

    # ---- NEW_OUTBOUND_CALL: the call is being placed ----------------------
    if event_type == "NEW_OUTBOUND_CALL":
        log.info("outbound call starting for hviCallId=%s", call_id)
        return {"SchemaVersion": "1.0", "Actions": [],
                "TransactionAttributes": tx_attrs}

    # ---- CALL_ANSWERED: open the conversation ----------------------------
    if event_type == "CALL_ANSWERED":
        if not call_id:
            log.error("no hviCallId in transaction attributes; hanging up")
            return {"SchemaVersion": "1.0", "Actions": [hangup()]}

        persisted = st.get_session(call_id) or {}
        if st.is_terminal_session(persisted):
            actions = _closing_actions(
                call_id, _terminal_text(persisted, language), 0, language)
            return {"SchemaVersion": "1.0", "Actions": actions,
                    "TransactionAttributes": tx_attrs}

        sip_call_id = participants[0].get("CallId") if participants else None
        try:
            st.attach_call_identifiers(call_id, details.get("TransactionId"), sip_call_id)
        except Exception as e:
            log.warning("could not attach call identifiers: %s", e)

        # Empty utterance means "you are connected, open the call".
        result = ask_agent(call_id, "", 0, language)
        text = result.get("agentUtterance") or "Hello."
        actions = speak_and_listen(call_id, text, 0, result.get("language", language))

        return {
            "SchemaVersion": "1.0",
            "Actions": actions,
            "TransactionAttributes": {**tx_attrs,
                                      "turn": str(result.get("turnIndex", 1) + 1),
                                      "language": result.get("language", language)},
        }

    # ---- ACTION_SUCCESSFUL: a recording finished, so take the turn -------
    if event_type == "ACTION_SUCCESSFUL":
        action = event.get("ActionData") or {}
        if action.get("Type") != "RecordAudio":
            # PlayAudio finished; the RecordAudio queued behind it is already
            # running, so nothing to do.
            return {"SchemaVersion": "1.0", "Actions": [],
                    "TransactionAttributes": tx_attrs}

        loc = (action.get("RecordingDestination") or {})
        bucket, key = loc.get("BucketName"), loc.get("Key")
        if not key:
            log.warning("RecordAudio succeeded with no key; re-listening")
            return {"SchemaVersion": "1.0",
                    "Actions": [record(call_id, turn)],
                    "TransactionAttributes": tx_attrs}

        persisted = st.get_session(call_id) or {}
        if st.is_terminal_session(persisted):
            actions = _closing_actions(
                call_id, _terminal_text(persisted, language), turn + 1, language)
            return {"SchemaVersion": "1.0", "Actions": actions,
                    "TransactionAttributes": tx_attrs}

        t0 = time.time()
        stt = transcribe_recording(bucket, key)
        utterance = stt.get("text", "")
        log.info("turn %s transcript (%.2fs): %r", turn, time.time() - t0, utterance)

        if not utterance:
            # Silence or noise. Re-listen rather than sending nothing to the
            # agent, which would look like an unanswered question.
            silent = int(tx_attrs.get("silentTurns", "0")) + 1
            if silent >= 3:
                log.info("three silent turns; ending the call")
                return {"SchemaVersion": "1.0", "Actions": [hangup()],
                        "TransactionAttributes": tx_attrs}
            return {
                "SchemaVersion": "1.0",
                "Actions": [record(call_id, turn)],
                "TransactionAttributes": {**tx_attrs, "silentTurns": str(silent)},
            }

        try:
            result = ask_agent(call_id, utterance, turn, language)
        except Exception as e:
            log.exception("agent invocation failed")
            return {"SchemaVersion": "1.0", "Actions": [hangup()],
                    "TransactionAttributes": tx_attrs}

        text = result.get("agentUtterance") or "Sorry, could you repeat that?"
        next_turn = int(result.get("turnIndex", turn + 1)) + 1
        lang = result.get("language", language)
        persisted = st.get_session(call_id) or {}
        persisted_terminal = st.is_terminal_session(persisted)

        if result.get("shouldHangUp") or persisted_terminal:
            # Persisted terminality outranks stale response flags. Never queue a
            # new recording after another request has already closed this call.
            if persisted_terminal:
                text = _terminal_text(persisted, lang)
            actions = _closing_actions(call_id, text, next_turn, lang)
            status = st.status_after_agent_hangup(
                persisted,
                is_complete=bool(result.get("isComplete")),
                escalated=bool(result.get("escalated")),
            )
            try:
                # The orchestrator normally persists the stronger reason first.
                # Only synthesize a status when it did not, so a security
                # termination and its reason cannot be rewritten as a resumable
                # carrier drop at the telephony edge.
                if (status != st.STATUS_COMPLETED
                        and status != persisted.get("status")):
                    st.mark_status(call_id, status, note="closed by agent")
            except Exception:
                pass
            return {"SchemaVersion": "1.0", "Actions": actions,
                    "TransactionAttributes": {**tx_attrs, "turn": str(next_turn)}}

        actions = speak_and_listen(call_id, text, next_turn, lang)
        return {
            "SchemaVersion": "1.0",
            "Actions": actions,
            "TransactionAttributes": {**tx_attrs, "turn": str(next_turn + 1),
                                      "language": lang, "silentTurns": "0"},
        }

    # ---- ACTION_FAILED ---------------------------------------------------
    if event_type == "ACTION_FAILED":
        log.warning("action failed: %s", json.dumps(event.get("ActionData") or {})[:400])
        # One retry at listening, then give up rather than looping on a broken
        # media path.
        retries = int(tx_attrs.get("actionRetries", "0")) + 1
        if retries > 2 or not call_id:
            return {"SchemaVersion": "1.0", "Actions": [hangup()],
                    "TransactionAttributes": tx_attrs}
        return {"SchemaVersion": "1.0",
                "Actions": [record(call_id, turn)],
                "TransactionAttributes": {**tx_attrs, "actionRetries": str(retries)}}

    # ---- HANGUP ----------------------------------------------------------
    if event_type == "HANGUP":
        log.info("hangup for hviCallId=%s", call_id)
        if call_id:
            try:
                session = st.get_session(call_id)
                if session and session["status"] == st.STATUS_IN_PROGRESS:
                    # In progress at hangup means the line dropped mid-intake.
                    # Marking it dropped is what makes the callback resumable -
                    # this single line is the call-drop recovery entry point.
                    st.mark_status(call_id, st.STATUS_DROPPED,
                                   note="line dropped during intake")
                    log.info("marked %s dropped; eligible for callback resume", call_id)
            except Exception as e:
                log.warning("could not update status on hangup: %s", e)
        return {"SchemaVersion": "1.0", "Actions": []}

    log.info("unhandled event type %s", event_type)
    return {"SchemaVersion": "1.0", "Actions": []}
