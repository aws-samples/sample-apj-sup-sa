"""The browser channel: WebRTC in, the same agent out, with the form on screen.

    browser mic --WebRTC--> SmallWebRTC transport
        -> Silero VAD          speech boundaries and interruption
        -> hybrid STT          Voxtral, escalating to Whisper on script mismatch
        -> AgentCore bridge    the deterministic orchestrator, over HTTP
        -> TTS                 OmniVoice
        -> SmallWebRTC transport --WebRTC--> browser speaker
                             \\
                              -> RTVI data channel --> live form, transcript

WHY THIS FILE EXISTS SEPARATELY FROM bot.py
The pipeline is identical and deliberately so: same VAD tuning, same barge-in
processor, same STT, same bridge, same TTS. Only the transport differs, and the
transport is the one thing that cannot be shared - AudioSocket carries 8kHz PCM
from Asterisk on the loopback interface, this carries Opus from a browser over
ICE. Forking the file rather than branching inside bot.py keeps the phone path,
which takes real calls, free of any code that only the browser needs.

WHY SmallWebRTC AND NOT DAILY
Pipecat's own guidance is that SmallWebRTC is for development and Daily is for
production, and for most projects that is the right advice - Daily brings global
infrastructure, echo cancellation and TURN. It is not available here. This call
collects health data under DPDP, which keeps the audio in ap-south-1, and routing
it through a third party's global media network takes it out of India. So the
transport is self-hosted on this box and we own what Daily would have given us:
TURN (see HVI_WEB_ICE_SERVERS) and scale. Echo cancellation is the one we get for
free, because the browser applies it to getUserMedia before we ever see a frame -
which is more than the WebSocket test harness managed, where audio came from an
AudioContext buffer that Chrome's AEC does not cover.

THE DATA CHANNEL IS NOT A SECOND CONNECTION
Live transcript, form fill and the underwriting link all travel as RTVI messages
over the same peer connection as the audio. No polling, no second WebSocket.
Both halves of the conversation are pushed explicitly, on the same message as the
form state, and NEITHER is taken from the RTVI observer's own transcript events.

That is not because those events are missing. The STT emits TranscriptionFrame and
the observer forwards it, and pipecat emits bot text too - TTSService defaults
push_text_frames=True and RTVIObserverParams defaults bot_output_enabled=True, so
a bot-output message arrives on every turn. An earlier version of this comment
claimed our TTS emitted no TTSTextFrame and that no bot text existed to observe;
that was wrong, and web/app.js was written to trust it.

They are unused because none of them carry the orchestrator's turn index, and
without it a line cannot be placed in the conversation or recognised if it repeats.
The caller's transcript event is also not filtered the way the bridge filters it,
so it includes fragments discarded as the agent's own echo - things the caller
never said. One message per turn carrying both halves and an index is ordered,
de-duplicable and truthful; four independent event streams are none of those.

Usage
    python -m media.bot_web            # serve on 0.0.0.0:8095
"""
from __future__ import annotations

import argparse
import asyncio
import hmac
import logging
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from aiortc import RTCIceServer
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.processors.frameworks.rtvi import (
    RTVIObserverParams,
    RTVIProcessor,
)
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.request_handler import (
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

from common import hvi_recording
from media.agent_bridge import AgentCoreBridge
from media.bargein import BargeInProcessor
from media.stt import HybridSTTService
from media.tts import build_tts

logging.basicConfig(
    level=os.environ.get("HVI_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("hvi.media.bot_web")

# LOOPBACK BY DEFAULT. This was 0.0.0.0, which is the usual default for a web
# server and the wrong one here: on a laptop it publishes a live medical intake
# session to every device on the coffee-shop wifi, and on a server it publishes
# it to whatever the security group happens to allow. Binding loopback means the
# only way to reach this is from the same machine, and anything else - a tunnel, a
# VPN, a reverse proxy - has to be set up deliberately rather than by forgetting.
WEB_HOST = os.environ.get("HVI_WEB_HOST", "127.0.0.1")
WEB_PORT = int(os.environ.get("HVI_WEB_PORT", "8095"))

# Shared secret, checked on /api/offer.
#
# Loopback binding is not on its own enough. Any page open in any other tab can
# POST to http://127.0.0.1:8095 - the browser blocks it from READING the reply,
# but the request still runs, and here a request that runs starts a real intake
# session against a real customer record. The token turns that from "possible"
# into "needs a secret it cannot see".
#
# Unset means unauthenticated, which is why scripts/78-web-local.sh always sets
# one. It is left permissive rather than mandatory so that `python -m
# media.bot_web` still works for someone poking at it directly.
WEB_TOKEN = os.environ.get("HVI_WEB_TOKEN", "")

# Which authentication guards /api/offer:
#   none    - no gate. Dev only; logs a loud warning. (default when unset AND no
#             token, to preserve the "just poke at it" behaviour.)
#   token   - the shared HVI_WEB_TOKEN, compare_digest. The demo path.
#   cognito - a verified Cognito id-token; the caller phone comes from a verified
#             claim, not from client request_data. The real-deploy path.
# Default is "token" when a token is set, else "none", so existing scripts are
# unchanged and turning on cognito is an explicit opt-in.
WEB_AUTH = (os.environ.get("HVI_WEB_AUTH", "") or
            ("token" if WEB_TOKEN else "none")).strip().lower()

# Built lazily on first use so importing this module (and the whole test suite)
# never requires the Cognito env vars to be present.
_verifier = None


def _get_verifier():
    """The Cognito verifier, constructed once. Raises AuthError if the pool env
    is not configured, which /api/offer turns into a 403 rather than a 500."""
    global _verifier
    if _verifier is None:
        from common.hvi_web_auth import CognitoConfig, CognitoVerifier
        _verifier = CognitoVerifier(CognitoConfig.from_env(os.environ))
    return _verifier

# INPUT rate is raised from the phone's 8kHz because a browser has no reason to
# be band-limited: getUserMedia gives us wideband audio, and HybridSTTService
# reads its rate off the service base rather than assuming one, so it simply gets
# a better signal than the PSTN leg does.
#
# OUTPUT stays at the TTS's native 8kHz. That figure is not an oversight - see
# media/tts.py: OmniVoice synthesises at 24kHz and the SageMaker endpoint
# downsamples before sending, precisely so this 2GB t4g.small is not shipping
# three times the payload. Raising it here would mean changing the endpoint, and
# the browser upsamples to Opus either way. Same voice the phone hears.
WEB_IN_RATE = int(os.environ.get("HVI_WEB_IN_SAMPLE_RATE", "16000"))

# stop_secs is the same 1.5s as the phone path, for the same measured reason: a
# caller recalling a date pauses longer than 0.8s mid-answer, and closing the turn
# inside that pause accepts a fragment as the whole answer. See media/bot.py.
#
# min_volume DEFAULTS LOWER HERE THAN media/bot.py's 0.55, and that is not a
# guess. scripts/72-media-deploy.sh already overrides it to 0.35 on the box with
# this note: a live BROWSER session delivered real speech at RMS ~830 that never
# fired the VAD, so the caller's "haan" went unheard and they repeated themselves
# into dead air. That failure was diagnosed on a browser leg, which is exactly
# what this file serves, so the working value is the default here rather than
# something a deploy script has to remember to inject.
VAD_PARAMS = VADParams(
    confidence=float(os.environ.get("VAD_CONFIDENCE", "0.6")),
    start_secs=float(os.environ.get("VAD_START_SECS", "0.2")),
    stop_secs=float(os.environ.get("VAD_STOP_SECS", "1.5")),
    min_volume=float(os.environ.get("VAD_MIN_VOLUME", "0.35")),
)

# Who the browser is. There is no dialplan here and no carrier to tell us a
# number, so the page supplies one in the WebRTC offer's request_data and it must
# already exist in the CRM - the orchestrator declines any caller it cannot
# identify, which is the correct behaviour for a call that collects medical
# history. HVI_WEB_DEFAULT_PHONE is a demo convenience, not an auth mechanism.
DEFAULT_PHONE = os.environ.get("HVI_WEB_DEFAULT_PHONE", "")

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web")


def _ice_servers() -> list[RTCIceServer]:
    """STUN/TURN, from the environment.

    Empty by default, which is correct for same-host and LAN use and wrong for
    anything crossing a NAT. STUN only reveals addresses so it does not carry
    audio out of region; TURN RELAYS media, so a TURN server used here must be
    one we run in ap-south-1 or the residency argument above is undone.

    Format: comma-separated URLs, optionally url|user|credential.
    """
    raw = (os.environ.get("HVI_WEB_ICE_SERVERS") or "").strip()
    if not raw:
        return []
    servers: list[RTCIceServer] = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split("|")
        if len(parts) == 3:
            servers.append(RTCIceServer(urls=parts[0], username=parts[1],
                                        credential=parts[2]))
        else:
            servers.append(RTCIceServer(urls=parts[0]))
    return servers


def _ice_servers_json() -> list[dict]:
    """The same STUN/TURN servers as plain dicts for the BROWSER's
    RTCPeerConnection.

    The browser is the NAT'd peer, so it needs these too: with an empty ICE
    config it can only offer host candidates (its private LAN address), which a
    server behind EC2's 1:1 NAT cannot route back to, so media fails on any real
    network. Serving them from here (rather than hard-coding in app.js) keeps the
    single source of truth in HVI_WEB_ICE_SERVERS and lets the credential rotate
    without shipping a new page. Empty on loopback, which is correct there.
    """
    out: list[dict] = []
    for s in _ice_servers():
        entry: dict = {"urls": s.urls}
        if getattr(s, "username", None):
            entry["username"] = s.username
        if getattr(s, "credential", None):
            entry["credential"] = s.credential
        out.append(entry)
    return out


app = FastAPI(title="hvi-web")

# The page is served from this same origin in the deployed setup, so CORS is only
# needed when a developer runs the UI on a separate dev server.
_origins = [o for o in (os.environ.get("HVI_WEB_CORS_ORIGINS") or "").split(",") if o]
if _origins:
    app.add_middleware(
        CORSMiddleware, allow_origins=_origins, allow_credentials=True,
        allow_methods=["*"], allow_headers=["*"],
    )

@app.middleware("http")
async def _no_cache(request, call_next):
    """Serve the page and its assets uncached.

    This is a surface under active change, and a cached app.js is indistinguishable
    from a bug that was not fixed: a corrected ordering bug reappeared, was
    re-diagnosed, and the browser had simply kept the old file. Telling people to
    hard-refresh is not a fix, because it has to be remembered every single time.

    Costs nothing here - the assets are three small local files on loopback.
    """
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


_handler = SmallWebRTCRequestHandler(ice_servers=_ice_servers())

# Pipelines are owned here for the lifetime of their connection. Without a strong
# reference asyncio is free to garbage-collect a running task, which ends the
# call for no visible reason.
_sessions: set[asyncio.Task] = set()


def _progress(form_state: dict | None) -> dict:
    """Answered/total as the UI wants to draw it."""
    if not isinstance(form_state, dict):
        return {}
    answered = form_state.get("answeredCount")
    remaining = form_state.get("remainingCount")
    total = None
    if isinstance(answered, int) and isinstance(remaining, int):
        # Visible-item count moves as showIf gates open, so the denominator is
        # derived per turn rather than fixed at 38 - otherwise a progress bar
        # would go backwards when declaring a condition adds follow-ups.
        total = answered + remaining
    return {"answered": answered, "remaining": remaining, "total": total}


async def _run_pipeline(connection, caller_phone: str | None) -> None:
    """One browser session, from connect to hang-up."""
    session_id = str(uuid.uuid4())
    log.info("web session %s starting for phone=%s", session_id,
             caller_phone or "(unidentified)")

    transport = SmallWebRTCTransport(
        webrtc_connection=connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=WEB_IN_RATE,
        ),
    )

    stt = HybridSTTService()
    tts = build_tts()

    # Created before the bridge because the bridge's per-turn callback sends
    # through it. No transport= argument: it has been deprecated and ignored
    # since Pipecat 1.4, and passing it only produces a warning per session.
    rtvi = RTVIProcessor()

    async def on_reply(reply: dict, utterance: str = "") -> None:
        """Push one turn's worth of state down the data channel.

        Everything the browser cannot get from RTVI's own events IN A USABLE FORM:
        what the caller was heard to say, what the agent said, the answers
        collected so far, and - on the last turn - where the record landed in
        Salesforce. RTVI does emit its own transcript and bot-text events, but
        none of them carry the turn index that makes a line placeable (see the
        module docstring), so the surface ignores them.

        Both halves of the turn travel together so the transcript cannot render
        them out of order. See AgentCoreBridge._notify.
        """
        form_state = reply.get("formState")
        message = {
            "type": "hvi-turn",
            "callId": reply.get("callId"),
            "turnIndex": reply.get("turnIndex"),
            "userUtterance": utterance or None,
            "agentUtterance": reply.get("agentUtterance"),
            "language": reply.get("language"),
            "askedRef": reply.get("askedRef"),
            "latencyMs": reply.get("latencyMs"),
            "isComplete": bool(reply.get("isComplete")),
            "formState": form_state,
            "progress": _progress(form_state),
        }
        record = reply.get("underwritingRecord")
        if isinstance(record, dict) and (record.get("recordUrl")
                                         or record.get("recordId")):
            message["underwritingRecord"] = record

        # WRITTEN STRAIGHT TO THE DATA CHANNEL, not through rtvi.send_server_message.
        #
        # That method does not send anything directly - it pushes a frame from the
        # RTVIProcessor, which sits at the FRONT of the pipeline, so the frame has
        # to travel transport.input() -> vad -> barge_in -> stt -> agent -> tts ->
        # transport.output() before it reaches the browser. Every one of those is an
        # async processor with its own queue, and this bridge blocks for about a
        # second per turn on the agent call, so a message pushed during one turn can
        # arrive behind the next turn's. Observed exactly that: the identity question
        # rendered after the consent question that followed it.
        #
        # send_app_message writes to the channel in call order, and SCTP delivers it
        # ordered, so the transcript cannot disagree with the conversation. It also
        # queues correctly before the channel opens, which the opening turn needs.
        connection.send_app_message(message)

    agent = AgentCoreBridge(
        call_id=session_id,
        stt_service=stt,
        tts_service=tts,
        caller_phone=caller_phone or None,
        # The two things that make this surface different from the phone.
        on_reply=on_reply,
        include_form_state=True,
    )

    vad = VADProcessor(
        vad_analyzer=SileroVADAnalyzer(sample_rate=WEB_IN_RATE, params=VAD_PARAMS),
    )
    # Still required in Pipecat 1.7: nothing in this pipeline emits an
    # InterruptionFrame on its own, so without this the agent talks over anyone
    # who interrupts. See media/bargein.py.
    barge_in = BargeInProcessor()

    # RTVIProcessor is DELIBERATELY NOT LISTED HERE, even though it belongs at the
    # front of the pipeline and ends up there.
    #
    # PipelineTask inspects the pipeline for one. If it finds a processor but no
    # matching RTVIObserver in `observers`, it logs an error and builds NEITHER -
    # which is a quiet failure worth naming, because send_server_message keeps
    # working (we hold the reference) while every observer-derived message
    # silently disappears: no transcripts, no speaking events, no audio levels.
    # The form still filled in; the UI just went deaf and still.
    #
    # Passing rtvi_processor= to PipelineTask instead makes it prepend this
    # instance AND create the observer from our params. See
    # pipecat/pipeline/worker.py, the external_rtvi branch.
    # LAST IN THE PIPELINE, which is the only position that sees both voices.
    # Caller audio enters at transport.input() and travels downstream through
    # every processor; agent audio is produced by tts just before
    # transport.output(). Only a processor after BOTH observes the pair, and a
    # recording missing either half cannot settle the question it exists for -
    # whether what we transcribed as the caller's answer was actually the agent's
    # own voice returning on the inbound leg.
    #
    # buffer_size=0 accumulates rather than emitting chunks mid-call, so
    # on_audio_data fires exactly once, on stop_recording, with the whole call.
    recorder = AudioBufferProcessor(
        sample_rate=WEB_IN_RATE, num_channels=2, buffer_size=0)

    @recorder.event_handler("on_audio_data")
    async def _on_audio(_buffer, audio, sample_rate, num_channels):
        # agent.call_id, NOT session_id: by here the bridge has adopted the
        # orchestrator's business callId, which is the id the transcript is
        # already archived under.
        uri = await asyncio.to_thread(hvi_recording.upload_recording,
                                      agent.call_id, audio, sample_rate,
                                      num_channels)
        # The upload is the easy half. Nothing outside S3 knows this object exists
        # until the agent is told: this host has no DynamoDB access, and the
        # agent's own archive ran while the audio was still in this buffer. Without
        # this the WAV sits in the bucket unlinked and Recording_URI__c stays blank.
        if uri:
            await agent.notify_recording_stored()

    pipeline = Pipeline([
        transport.input(),
        vad,
        barge_in,
        stt,
        agent,
        tts,
        transport.output(),
        recorder,
    ])

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=WEB_IN_RATE,
            enable_metrics=True,
        ),
        conversation_id=session_id,
        rtvi_processor=rtvi,
        # Audio levels are off by default and are what a volume-reactive
        # visualiser needs; the speaking flags alone can only drive an on/off
        # animation.
        rtvi_observer_params=RTVIObserverParams(
            user_audio_level_enabled=True,
            bot_audio_level_enabled=True,
        ),
        idle_timeout_secs=float(os.environ.get("WEB_IDLE_TIMEOUT_SECS", "300")),
    )

    @transport.event_handler("on_app_message")
    async def _on_app_message(_transport, message, _sender):
        """Inbound data-channel messages from the page.

        Only one is ours. The browser confirms it has RENDERED the questionnaire
        schema, which is the only way this side can know a 31KB message survived:
        pipecat discards queued app messages once the data-channel open timeout
        has passed, silently, and the schema travels on the opening turn - the one
        message most likely to be written before the channel is open. Without the
        ack the bridge assumed the first send landed, and a call that lost it drew
        answers into a form with no labels for its whole duration.

        RTVI's own client messages arrive here too (client-ready and the rest);
        they are the transport's business, not ours, so anything unrecognised is
        left alone rather than logged per turn.

        The ack also reaches RTVIProcessor, which logs "Ignoring not RTVI message"
        at WARNING for it. That is expected and harmless - it is not an RTVI
        message - and it appears once per call because the browser acks once.
        Named here so nobody spends time chasing it.
        """
        if isinstance(message, dict) and message.get("type") == "hvi-schema-ack":
            agent.note_form_schema_delivered()

    @transport.event_handler("on_client_connected")
    async def _on_connected(_transport, _client):
        # Speaking is primed on CONNECT rather than at pipeline start. The phone
        # path can sleep a fixed OPENING_DELAY_SECS because an answered call has
        # a settled RTP path; here ICE completes at its own pace and a fixed
        # sleep would either clip the consent statement or add dead air.
        log.info("web session %s connected; speaking opening", session_id)
        if hvi_recording.enabled():
            await recorder.start_recording()
        await agent.speak_opening()

    runner = PipelineRunner(handle_sigint=False)
    try:
        await runner.run(task)
    except asyncio.CancelledError:
        log.info("web session %s cancelled", session_id)
        raise
    except Exception:
        log.exception("web session %s failed", session_id)
    finally:
        # Stop BEFORE the stats line and inside finally, because this is what
        # flushes the buffer to on_audio_data - a call that ended by cancellation
        # or exception is exactly the call whose audio is most worth keeping, and
        # returning without stopping would discard it. Guarded: a failure to store
        # audio must not replace the real reason the session ended.
        try:
            if recorder.has_audio():
                await recorder.stop_recording()
        except Exception:  # noqa: BLE001 - never mask the session's own outcome
            log.exception("could not finalise the recording for %s", session_id)
        log.info("web session %s finished: stt=%s agent=%s",
                 session_id, stt.stats, agent.stats)


@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "agentConfigured": bool(os.environ.get("AGENT_RUNTIME_ARN")),
        "iceServers": len(_ice_servers()),
        "inSampleRate": WEB_IN_RATE,
        "sessions": len(_sessions),
        # Whether, not what. A health endpoint that echoed the token or any pool
        # detail would undo the point of having auth.
        "authMode": WEB_AUTH,
        "tokenRequired": WEB_AUTH != "none",
    }


@app.get("/api/ice")
async def ice(token: str = "") -> JSONResponse:
    """STUN/TURN for the browser's RTCPeerConnection.

    The browser cannot reach the server across a NAT without the same in-region
    relay the server uses, so it fetches the config here rather than shipping it
    baked into the page. Token-gated in token mode: the TURN credential, though
    time-boxed, is a working relay key, so it is not handed to anyone who has the
    URL minus its ?token=. In none/loopback mode the list is empty anyway.
    """
    if WEB_AUTH == "token" and WEB_TOKEN:
        if not hmac.compare_digest(str(token or ""), WEB_TOKEN):
            return JSONResponse({"error": "forbidden"}, status_code=403)
    return JSONResponse({"iceServers": _ice_servers_json()})


@app.post("/api/offer")
async def offer(request: SmallWebRTCRequest) -> JSONResponse:
    """WebRTC signalling: SDP in, SDP answer out.

    The pipeline is started in a background task rather than awaited, because
    this request has to return the answer before the browser can finish
    connecting - awaiting the call here would deadlock the handshake.
    """
    data = getattr(request, "request_data", None)
    data = data if isinstance(data, dict) else {}

    # Authenticate and resolve WHO this session is for. In cognito mode the phone
    # comes from a verified claim and any client-supplied phone is ignored; in
    # token/none mode the page still supplies it (demo behaviour).
    try:
        caller_phone = _authenticate(data)
    except _Forbidden:
        # 403 and nothing else. Saying which check failed helps an attacker more
        # than it helps a user, who has valid credentials.
        return JSONResponse({"error": "forbidden"}, status_code=403)

    async def on_connection(connection) -> None:
        task = asyncio.create_task(_run_pipeline(connection, caller_phone))
        _sessions.add(task)
        task.add_done_callback(_sessions.discard)

    answer = await _handler.handle_web_request(request, on_connection)
    return JSONResponse(answer or {})


class _Forbidden(Exception):
    """Internal: any auth failure, mapped to an opaque 403 by the caller."""


def _authenticate(data: dict) -> str:
    """Return the caller phone for this session, or raise _Forbidden.

    The one place all three modes converge, so the offer handler stays simple and
    the identity-binding rule (cognito => phone from a verified claim, never from
    the client) lives in exactly one spot.
    """
    if WEB_AUTH == "cognito":
        from common.hvi_web_auth import AuthError
        token = str(data.get("idToken") or data.get("token") or "")
        try:
            verifier = _get_verifier()
            claims = verifier.verify(token)
            return verifier.caller_phone(claims)
        except AuthError as exc:
            log.warning("cognito auth rejected an offer: %s", exc)
            raise _Forbidden from exc

    if WEB_AUTH == "token":
        if WEB_TOKEN:
            # compare_digest rather than == so the check does not leak the token's
            # length or matching prefix through timing.
            supplied = str(data.get("token") or "")
            if not hmac.compare_digest(supplied, WEB_TOKEN):
                log.warning("rejected an offer with a bad or missing token")
                raise _Forbidden
        return _client_phone(data)

    # none: open. Dev only - _log_auth_posture() has already warned at startup.
    return _client_phone(data)


def _client_phone(data: dict) -> str:
    """The phone the page supplied, or the configured default. Only used in
    token/none mode; cognito mode never trusts this."""
    if data.get("phone"):
        return str(data["phone"]).strip()
    return DEFAULT_PHONE


# Mounted last so the API routes above win. index.html is served explicitly
# because a bare "/" must not depend on directory-index behaviour.
if os.path.isdir(WEB_DIR):
    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(os.path.join(WEB_DIR, "index.html"))

    app.mount("/", StaticFiles(directory=WEB_DIR), name="web")
else:
    log.warning("no web directory at %s; serving the API only", WEB_DIR)


def main() -> int:
    parser = argparse.ArgumentParser(description="HVI browser voice surface")
    parser.add_argument("--host", default=WEB_HOST)
    parser.add_argument("--port", type=int, default=WEB_PORT)
    args = parser.parse_args()

    if not os.environ.get("AGENT_RUNTIME_ARN"):
        log.error("AGENT_RUNTIME_ARN is not set; the bridge has no agent to call")
        return 1

    import uvicorn

    if WEB_AUTH == "none":
        log.warning("HVI_WEB_AUTH=none, so any page in any tab can start an "
                    "intake session against this server. Set HVI_WEB_AUTH=token "
                    "(scripts/78-web-local.sh does) or =cognito for a real deploy.")
    elif WEB_AUTH == "cognito":
        # Fail fast on a misconfigured pool at startup rather than 403 every offer.
        try:
            _get_verifier()
            log.info("HVI_WEB_AUTH=cognito; id-tokens verified against the pool.")
        except Exception as exc:  # noqa: BLE001 - surface any config error clearly
            log.error("HVI_WEB_AUTH=cognito but the pool is not configured: %s", exc)
            return 1
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        log.warning("binding %s rather than loopback. Every device that can "
                    "reach this address can start a medical intake session.",
                    args.host)

    log.info("hvi web surface on %s:%d (in=%dHz, ice=%d, auth=%s)",
             args.host, args.port, WEB_IN_RATE, len(_ice_servers()), WEB_AUTH)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
