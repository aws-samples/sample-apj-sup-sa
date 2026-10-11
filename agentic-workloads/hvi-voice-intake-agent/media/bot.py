"""The call media pipeline: Asterisk AudioSocket in, agent speech out.

    AudioSocket in
        -> Silero VAD          detects speech boundaries and interruption
        -> hybrid STT          Voxtral, escalating to Whisper on script mismatch
        -> AgentCore bridge    the deterministic orchestrator, over HTTP
        -> TTS                 OmniVoice (the only TTS; no fallback engine)
        -> AudioSocket out

Runs on the Asterisk instance, so call audio never leaves the box - the only
network hop per turn is the agent call, which is request/response rather than
streaming media. That is also what makes the barge-in loop local: detect speech
and stop playback without a cross-region round trip.

Pipecat Flows is deliberately NOT used. Its state is in memory, and the hard
requirement here is that a call dropped mid-protocol resumes on the follow-up
questions it still owes. That state lives in DynamoDB behind the orchestrator, so
the dialogue graph stays there and this file is only plumbing.

Usage
    python -m media.bot                 # serve AudioSocket on 127.0.0.1:8090
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
from pipecat.processors.audio.vad_processor import VADProcessor

from common import hvi_recording
from media.agent_bridge import AgentCoreBridge
from media.audiosocket import SAMPLE_RATE, AudioSocketServer, AudioSocketTransport
from media.bargein import BargeInProcessor
from media.stt import HybridSTTService
from media.tts import build_tts

logging.basicConfig(
    level=os.environ.get("HVI_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("hvi.media.bot")

AUDIOSOCKET_HOST = os.environ.get("AUDIOSOCKET_HOST", "127.0.0.1")
AUDIOSOCKET_PORT = int(os.environ.get("AUDIOSOCKET_PORT", "8090"))

# VAD tuning for a phone line.
#
# stop_secs is the important one. Too short and the agent interrupts a caller
# who paused to think - unacceptable when the question is "when were you
# hospitalised?" and they are recalling a date. Too long and every turn drags.
#
# 1.5s, RAISED FROM 0.8s AFTER A MEASURED FAILURE. A live caller answered their
# date of birth the way people actually recall dates - "2000" ... "5th
# September" ... "5 September 2000" - and 0.8s closed the turn inside the first
# pause. The fragment "2000" was accepted as the whole answer, the questionnaire
# advanced, and the caller's real answer landed on the height question. The same
# call shows them saying "ठीक है" ten times into a turn that had already been
# cut. Mid-answer recall pauses on this population are simply longer than 0.8s.
#
# The cost is ~0.7s more silence after the caller genuinely finishes, which the
# segment cache absorbs: the acknowledgement now starts from disk in
# milliseconds, so total turn latency stays inside the budget. Getting the turn
# BOUNDARY right dominates - a fast wrong answer is still wrong.
#
# start_secs stays short so barge-in feels immediate: this is the delay before
# the bot stops talking once the caller starts.
VAD_PARAMS = VADParams(
    confidence=float(os.environ.get("VAD_CONFIDENCE", "0.6")),
    start_secs=float(os.environ.get("VAD_START_SECS", "0.2")),
    stop_secs=float(os.environ.get("VAD_STOP_SECS", "1.5")),
    min_volume=float(os.environ.get("VAD_MIN_VOLUME", "0.55")),
)


# Where the dialplan leaves the caller's number for us.
#
# AudioSocket carries a UUID and nothing else, so the caller's number has to
# arrive some other way, and the orchestrator needs it to create or resume the
# session. A file in a shared directory is the whole mechanism: Asterisk and this
# process run on the same host by design, so there is no network involved.
#
# NOT /tmp. The systemd unit sets PrivateTmp=true, so this process has its own
# /tmp and would never see what Asterisk wrote - a handoff that fails silently
# and leaves every caller unidentified.
CALL_DIR = os.environ.get("HVI_CALL_DIR", "/var/lib/hvi/calls")


def read_ticket(call_uuid: str) -> tuple[str | None, str | None]:
    """Read what the dialplan left for this media session.

    Two shapes, because there are two directions and they know different things:

      OUTBOUND (the product path) - JSON written by the dialler, carrying the
        callId of a session it has ALREADY created after passing the compliance
        gates. That callId must be used. If this process instead identified the
        customer by phone number, the orchestrator would find that same fresh
        session as resumable and start another one resuming it, so a brand new
        call would open with "we were cut off earlier".

      INBOUND (callbacks, and testing) - a bare phone number. No session exists
        yet, so the orchestrator creates or resumes one from the number.

    Returns (call_id, phone); either may be None.
    """
    path = os.path.join(CALL_DIR, call_uuid)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read().strip()
    except FileNotFoundError:
        log.warning("no ticket at %s; no caller identity, so the orchestrator "
                    "will decline this call", path)
        return None, None
    except OSError as e:
        log.warning("could not read %s: %s", path, e)
        return None, None

    if not raw:
        return None, None

    if raw.startswith("{"):
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("ticket at %s is not valid JSON; treating as a phone number", path)
            return None, raw
        return doc.get("callId"), doc.get("phone")

    # Bare phone number: inbound.
    return None, raw


async def run_call(call_uuid: str, transport: AudioSocketTransport) -> None:
    """Build and run a pipeline for one call."""
    call_id, phone = read_ticket(call_uuid)
    direction = "outbound" if call_id else "inbound"
    log.info("starting %s pipeline: media=%s callId=%s phone=%s",
             direction, call_uuid, call_id or "(to be resolved)", phone or "unknown")

    stt = HybridSTTService()
    tts = build_tts()
    # call_id falls back to the media UUID for inbound, where the orchestrator
    # resolves the real one from the phone number and hands it back.
    agent = AgentCoreBridge(call_id=call_id or call_uuid, stt_service=stt,
                            tts_service=tts, caller_phone=phone)

    vad = VADProcessor(
        vad_analyzer=SileroVADAnalyzer(sample_rate=SAMPLE_RATE, params=VAD_PARAMS),
    )

    # BARGE-IN IS NOT AUTOMATIC IN PIPECAT 1.7. The output transport acts on
    # InterruptionFrame and nothing in this pipeline emitted one, so the agent
    # talked over the caller for the whole of every prompt - up to 16 seconds on
    # the longest. Placed directly after the VAD so it sees speech events at the
    # earliest point, and before the services so its InterruptionFrame travels
    # downstream through them to the output. See media/bargein.py.
    barge_in = BargeInProcessor()

    # LAST, so it sees BOTH voices: the caller's audio enters at
    # transport.input() and travels downstream, the agent's is produced by tts
    # just before transport.output(). Stereo keeps them on separate channels,
    # which is what makes the recurring "was that the caller or our own echo?"
    # question answerable after the fact. buffer_size=0 accumulates the whole
    # call and emits once, on stop_recording. See common/hvi_recording.py.
    recorder = AudioBufferProcessor(
        sample_rate=SAMPLE_RATE, num_channels=2, buffer_size=0)

    @recorder.event_handler("on_audio_data")
    async def _on_audio(_buffer, audio, sample_rate, num_channels):
        # agent.call_id, not call_uuid: the bridge has adopted the orchestrator's
        # business callId by now, and that is what the transcript is filed under.
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
            audio_in_sample_rate=SAMPLE_RATE,
            audio_out_sample_rate=SAMPLE_RATE,
            enable_metrics=True,
        ),
        conversation_id=call_uuid,
        # A live call that goes quiet for five minutes is over. The default is
        # 300s; keep it so an abandoned line does not hold the pipeline open.
        idle_timeout_secs=float(os.environ.get("CALL_IDLE_TIMEOUT_SECS", "300")),
    )

    # The agent speaks first, because this is an outbound call.
    async def prime() -> None:
        # Let the RTP path settle before speaking. Talking into a half-open
        # media path clips the opening words, and the opening words are the
        # consent statement.
        await asyncio.sleep(float(os.environ.get("OPENING_DELAY_SECS", "0.5")))
        if hvi_recording.enabled():
            await recorder.start_recording()
        await agent.speak_opening()

    runner = PipelineRunner(handle_sigint=False)
    try:
        await asyncio.gather(runner.run(task), prime())
    except asyncio.CancelledError:
        log.info("pipeline cancelled for %s", call_uuid)
        raise
    finally:
        # Inside finally, and before the stats line: stopping is what flushes the
        # buffer to on_audio_data, and a call that ended by cancellation is
        # exactly the one whose audio is most worth keeping. Guarded so a failed
        # upload cannot mask why the call actually ended.
        try:
            if recorder.has_audio():
                await recorder.stop_recording()
        except Exception:  # noqa: BLE001 - never mask the call's own outcome
            log.exception("could not finalise the recording for %s", call_uuid)
        log.info("call %s finished: stt=%s agent=%s",
                 call_uuid, stt.stats, agent.stats)


async def main() -> int:
    if not os.environ.get("AGENT_RUNTIME_ARN"):
        log.error("AGENT_RUNTIME_ARN is not set; the bridge has no agent to call")
        return 1

    log.info("hvi media bot starting: tts=%s stt=%s",
             os.environ.get("HVI_TTS", "omnivoice"),
             os.environ.get("STT_ENGINE", "hybrid"))
    server = AudioSocketServer(
        handler=run_call, host=AUDIOSOCKET_HOST, port=AUDIOSOCKET_PORT,
    )
    await server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
