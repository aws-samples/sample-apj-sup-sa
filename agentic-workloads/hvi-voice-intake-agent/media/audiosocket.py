"""Asterisk AudioSocket protocol and a Pipecat transport built on it.

WHY A CUSTOM TRANSPORT
----------------------
Pipecat 1.7.0 ships transports for Daily, LiveKit, SmallWebRTC, websockets and a
few vendor SDKs, and serializers for Twilio, Telnyx, Plivo, Exotel, Genesys and
Vonage. There is nothing for Asterisk AudioSocket, so it is written here against
BaseInputTransport and BaseOutputTransport.

The reason we are on AudioSocket at all: Chime SDK PSTN Audio is action-based,
where bot speech is a finished S3 file played by PlayAudio and caller speech is a
finished S3 recording. Actions run sequentially on a call leg, so playing and
listening at the same time is impossible and there is no way to detect that the
caller has interrupted. Terminating the SIP leg on our own Asterisk instead means
we hold the RTP, and barge-in becomes ours to implement rather than the carrier's
to withhold.

THE PROTOCOL
------------
Deliberately tiny. Every message is a 3-byte header and a payload:

    +--------+------------------+------------------+
    | type   | length (2, BE)   | payload          |
    +--------+------------------+------------------+

    0x00  TERMINATE  no payload; the call is over
    0x01  UUID       16 raw bytes, the channel UUID from the dialplan
    0x10  AUDIO      signed linear 16-bit little-endian, 8 kHz, mono
    0xff  ERROR      one byte error code

Asterisk is the CLIENT: it connects out to us, so this module listens. The first
message on a connection is the UUID, which is how a media session is tied to call
state in DynamoDB - and therefore what makes resume-after-drop work.

8 kHz is not a choice. It is what the PSTN delivers, and pretending otherwise by
upsampling here would add latency without adding information.
"""
from __future__ import annotations

import asyncio
import logging
import os
import struct
import time
import uuid as uuidlib
from collections.abc import Awaitable, Callable

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
    StartFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.transports.base_input import BaseInputTransport
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import BaseTransport, TransportParams

log = logging.getLogger("hvi.media.audiosocket")

# Message types, per Asterisk res_audiosocket.
TYPE_TERMINATE = 0x00
TYPE_UUID = 0x01
TYPE_AUDIO = 0x10
TYPE_ERROR = 0xFF

HEADER_LEN = 3
SAMPLE_RATE = 8000
CHANNELS = 1
BYTES_PER_SAMPLE = 2

# How many consecutive silent measurement windows before the inbound leg is
# called broken. Silence is the NORMAL state while the agent is speaking, and the
# longest authored prompt runs about 16 seconds, so a small number here turns a
# healthy call into a wall of ERRORs asserting a dialplan fault. See the RMS
# check in _handle_audio.
SILENT_WINDOWS_BEFORE_ALARM = int(
    os.environ.get("SILENT_WINDOWS_BEFORE_ALARM", "4"))

# Asterisk emits 20ms frames: 160 samples, 320 bytes. Matching that on the way
# out keeps the jitter buffer happy; larger writes arrive as audible chunks.
FRAME_MS = 20
FRAME_BYTES = SAMPLE_RATE * BYTES_PER_SAMPLE * FRAME_MS // 1000  # 320

# Asterisk closes the connection if it is starved, and a runaway producer would
# otherwise buffer unboundedly. One second of audio is ample slack.
MAX_QUEUED_FRAMES = 1000 // FRAME_MS

# How far behind the 20ms schedule we tolerate before restarting the clock from
# now rather than trying to catch up. Catching up means bursting, and bursting is
# what Asterisk discards. 200ms is well beyond normal scheduler jitter and well
# below anything a listener would notice as a gap.
RESYNC_AFTER_S = 0.2


class AudioSocketProtocolError(Exception):
    """Malformed AudioSocket framing."""


def encode(msg_type: int, payload: bytes = b"") -> bytes:
    """Build one AudioSocket message."""
    if len(payload) > 0xFFFF:
        raise AudioSocketProtocolError(
            f"payload of {len(payload)} bytes exceeds the 16-bit length field"
        )
    return struct.pack("!BH", msg_type, len(payload)) + payload


def encode_audio(pcm: bytes) -> bytes:
    return encode(TYPE_AUDIO, pcm)


def decode(buffer: bytes) -> tuple[int, bytes, bytes] | None:
    """Pull one message off the front of a buffer.

    Returns (type, payload, remainder) or None when the buffer does not yet hold
    a whole message. Returning the remainder rather than mutating keeps this pure
    and therefore testable without a socket.
    """
    if len(buffer) < HEADER_LEN:
        return None
    msg_type, length = struct.unpack("!BH", buffer[:HEADER_LEN])
    end = HEADER_LEN + length
    if len(buffer) < end:
        return None
    return msg_type, buffer[HEADER_LEN:end], buffer[end:]


def parse_uuid(payload: bytes) -> str:
    """Channel UUID from a TYPE_UUID payload.

    Asterisk sends 16 raw bytes. Some builds send the 36-character text form
    instead, so accept both rather than failing a call over formatting.
    """
    if len(payload) == 16:
        return str(uuidlib.UUID(bytes=payload))
    text = payload.decode("ascii", "ignore").strip()
    try:
        return str(uuidlib.UUID(text))
    except ValueError as exc:
        raise AudioSocketProtocolError(f"unparseable UUID payload: {payload!r}") from exc


# --------------------------------------------------------------------------- #
# Pipecat transport
# --------------------------------------------------------------------------- #
class AudioSocketInputTransport(BaseInputTransport):
    """Feeds AudioSocket audio into the pipeline."""

    def __init__(self, params: TransportParams, **kwargs):
        super().__init__(params, **kwargs)
        self._ready = False
        # Inbound rate measurement, so the negotiated format is a measured fact
        # rather than an assumption. See feed().
        self._rate_started_at: float | None = None
        self._rate_bytes = 0
        self._rate_windows = 0
        # When audio last arrived, so a stream that STOPS is distinguishable from
        # one that never had the rate we expected.
        self._last_feed_at: float | None = None
        self._gap_warned = False
        # Sum of squares and sample count for the current window, to report the
        # LEVEL of the inbound audio and not merely its quantity. See feed().
        self._rate_sumsq = 0.0
        self._rate_samples = 0
        self._peak = 0
        # A caller who is listening is silent, and a long prompt spans several
        # windows, so one quiet window means nothing. Counted so the alarm can
        # wait for silence that PERSISTS - see the RMS check in _handle_audio.
        self._silent_windows = 0
        self._heard_audio = False
        # Optional capture of exactly what Asterisk delivered. See _record().
        self._record_fh = None
        self._record_path: str | None = None
        self._record_failed = False

    async def start(self, frame: StartFrame):
        await super().start(frame)
        # Signals the pipeline that media can flow. Without this the pipeline
        # waits forever and the caller hears nothing at all.
        await self.set_transport_ready(frame)
        self._ready = True
        log.info("audiosocket input ready at %dHz", SAMPLE_RATE)

    def _record(self, pcm: bytes) -> None:
        """Optionally append inbound audio to a file, for offline analysis.

        A log line saying "RMS 4" settles whether audio arrived, but not what it
        was. Having the actual bytes turns a single test call into evidence that
        can be re-examined without dialling again - which matters when each
        reproduction costs a real PSTN leg and a person to speak into it.

        RAW PCM, NOT WAV, DELIBERATELY. A WAV header records the frame count and is
        only correct once the file is closed properly. A dropped call or a killed
        process is exactly when this capture is most wanted and exactly when the
        close would be skipped, leaving a file that claims to contain zero
        samples. Headerless PCM cannot be truncated into a lie - whatever reached
        the disk is readable.

        OFF BY DEFAULT, AND IT MUST STAY THAT WAY. This is a recording of a
        patient answering medical-history questions. It is written unencrypted to
        an instance volume and is not covered by the consent the caller gave for
        the underwriting record. Enable it for a specific diagnostic call, then
        turn it off and delete the file.

        Convert with:
            ffmpeg -f s16le -ar 8000 -ac 1 -i inbound-<uuid>.raw out.wav
        """
        target = os.environ.get("HVI_DEBUG_RECORD_INBOUND")
        if not target or self._record_failed:
            return
        try:
            if self._record_fh is None:
                os.makedirs(target, exist_ok=True)
                self._record_path = os.path.join(
                    target, f"inbound-{int(time.time())}.raw")
                self._record_fh = open(self._record_path, "ab")  # noqa: SIM115
                log.warning("RECORDING INBOUND AUDIO to %s - this contains "
                            "medical PII. Disable HVI_DEBUG_RECORD_INBOUND and "
                            "delete the file once diagnosed.", self._record_path)
            self._record_fh.write(pcm)
            self._record_fh.flush()
        except Exception as exc:  # noqa: BLE001 - never break a call to record it
            # Give up permanently rather than retry. feed() runs 50 times a second,
            # and the first version reset only the handle, so a directory that was
            # read-only produced 50 identical warnings per second for the length of
            # the call - drowning the log this capture exists to help read.
            self._record_failed = True
            log.warning("inbound capture DISABLED after first failure: %s. The "
                        "call continues normally. If this is EROFS, the systemd "
                        "sandbox is missing a ReadWritePaths entry.", exc)
            self._record_fh = None

    async def feed(self, pcm: bytes) -> None:
        """Called by the connection handler for each inbound audio message."""
        if not self._ready:
            return

        self._record(pcm)

        # MEASURE THE REAL SAMPLE RATE, once, a few seconds into the call.
        #
        # The dialplan tried to pin the format with Set(CHANNEL(audiowriteformat))
        # and Asterisk ignored it - those CHANNEL items are read-only and it warned
        # about them on every call. So the format in use has never actually been
        # verified, only assumed, and a caller reporting audio that is "too fast
        # and cracking" is exactly what a 2x rate mismatch sounds like.
        #
        # Bytes arriving per second of wall clock settles it with no guessing:
        # 16-bit mono at 8kHz is 16000 B/s, at 16kHz it is 32000 B/s. Asterisk
        # sends continuously once the call is up, so elapsed time is a fair
        # denominator.
        # MEASURED EVERY WINDOW, NOT ONCE.
        #
        # This was one-shot over the first 3 seconds, and that made its single
        # reading untrustworthy at exactly the wrong moment: TTS synthesis used to
        # run a synchronous boto3 call on the event loop, so on one call the 3-second
        # window landed almost entirely inside a 2.66s stall. It reported 2572Hz
        # against a real 8000Hz and sent us looking for a codec problem that did not
        # exist. A repeating window cannot be fooled by one stall, and a stall now
        # shows up as one bad window followed by good ones - which is diagnostic in
        # itself rather than misleading.
        now = time.monotonic()

        # A stream that stops matters as much as one at the wrong rate, and the two
        # are indistinguishable from a single average. VAD sees nothing either way.
        if self._last_feed_at is not None:
            gap = now - self._last_feed_at
            if gap > 2.0 and not self._gap_warned:
                self._gap_warned = True
                log.error("INBOUND AUDIO STOPPED for %.1fs and then resumed. VAD "
                          "cannot detect speech that never arrives, so a caller "
                          "talking during that window was not heard.", gap)
        self._last_feed_at = now

        if self._rate_started_at is None:
            self._rate_started_at = now
        self._rate_bytes += len(pcm)

        # AND MEASURE THE LEVEL, WHICH IS THE MORE DIAGNOSTIC HALF.
        #
        # Rate alone cannot distinguish the two failures that produce an identical
        # symptom, and the symptom is the one under investigation: a call where
        # feed() was demonstrably running yet the VAD never fired once in 63
        # seconds and STT was never entered.
        #
        #   bytes arriving, and they are SPEECH   -> VAD should have fired;
        #                                            tests/diag_vad.py shows it
        #                                            fires even attenuated 100x,
        #                                            down to RMS ~49
        #   bytes arriving, and they are SILENCE  -> VAD correctly stays quiet, and
        #                                            the fault is upstream in
        #                                            Asterisk or inbound RTP
        #
        # Both look the same in a rate reading, because comfort noise and speech
        # occupy the same number of bytes. RMS tells them apart in one line, on the
        # next call, without installing an echo test or guessing.
        try:
            import numpy as np

            samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
            if samples.size:
                self._rate_sumsq += float(np.dot(samples, samples))
                self._rate_samples += samples.size
                self._peak = max(self._peak, int(np.abs(samples).max()))
        except Exception:  # noqa: BLE001 - diagnostics must never break the call
            pass

        elapsed = now - self._rate_started_at
        if elapsed >= 5.0:
            bps = self._rate_bytes / elapsed
            implied = bps / (BYTES_PER_SAMPLE * CHANNELS)
            self._rate_windows += 1
            ratio = implied / SAMPLE_RATE
            rms = (self._rate_sumsq / self._rate_samples) ** 0.5 if self._rate_samples else 0.0

            if abs(implied - SAMPLE_RATE) > SAMPLE_RATE * 0.15:
                log.error("inbound window %d: %.0f B/s -> %.0f Hz "
                          "(expected %d Hz, ratio %.2f) - UNDER-DELIVERING. If this "
                          "persists across windows it is a real format or RTP "
                          "problem; a single bad window is usually a blocked event "
                          "loop.", self._rate_windows, bps, implied, SAMPLE_RATE,
                          ratio)
            else:
                log.info("inbound window %d: %.0f Hz (ratio %.2f) ok",
                         self._rate_windows, implied, ratio)

            # Thresholds come from the measured VAD floor, not from taste.
            # diag_vad.py fires reliably at RMS 149 and marginally at 49, so
            # anything under ~30 cannot possibly trigger speech detection.
            #
            # The COUNT before alarming is taste, and deliberately generous: the
            # longest authored prompt takes ~16s to speak, and a window is ~5s.
            # ONE QUIET WINDOW IS NOT A FAULT. This used to log a definite
            # diagnosis - "the caller's audio is not reaching this process" -
            # on every window under the floor, which includes every window in
            # which the caller was politely listening. A 15-second prompt
            # produces several, so a healthy call filled the log with ERRORs
            # asserting a dialplan problem that did not exist. It cost real
            # time chasing it during a live-call investigation.
            if rms < 30:
                self._silent_windows += 1
                if self._silent_windows < SILENT_WINDOWS_BEFORE_ALARM:
                    log.info("inbound window %d: RMS %.0f peak %d - quiet "
                             "(%d in a row; expected while the agent is speaking)",
                             self._rate_windows, rms, self._peak,
                             self._silent_windows)
                elif self._heard_audio:
                    log.error("inbound window %d: RMS %.0f peak %d - AUDIO STOPPED. "
                              "This call HAD real audio and has now had none for %d "
                              "consecutive windows, so the caller either went quiet "
                              "for a long time or the inbound leg dropped mid-call. "
                              "Check inbound RTP; the VAD and STT are downstream of "
                              "the problem.", self._rate_windows, rms, self._peak,
                              self._silent_windows)
                else:
                    log.error("inbound window %d: RMS %.0f peak %d - NO AUDIO EVER "
                              "ARRIVED. Bytes are arriving but they carry no audio "
                              "across %d consecutive windows, so no transcript can "
                              "ever be produced. Look at inbound RTP and the "
                              "dialplan, not at the VAD or STT.",
                              self._rate_windows, rms, self._peak,
                              self._silent_windows)
            else:
                self._silent_windows = 0
                self._heard_audio = True
                log.info("inbound window %d: RMS %.0f peak %d - real audio present",
                         self._rate_windows, rms, self._peak)

            self._rate_started_at = now
            self._rate_bytes = 0
            self._rate_sumsq = 0.0
            self._rate_samples = 0
            self._peak = 0

        await self.push_audio_frame(InputAudioRawFrame(
            audio=pcm, sample_rate=SAMPLE_RATE, num_channels=CHANNELS,
        ))


class AudioSocketOutputTransport(BaseOutputTransport):
    """Sends pipeline audio back down the AudioSocket connection."""

    def __init__(self, params: TransportParams, **kwargs):
        super().__init__(params, **kwargs)
        self._writer: asyncio.StreamWriter | None = None
        self._pending = b""
        # Monotonic deadline for releasing the next 20ms frame. None means no
        # utterance is in flight, so the next write starts the clock.
        self._next_send_at: float | None = None

    def attach(self, writer: asyncio.StreamWriter) -> None:
        self._writer = writer

    async def start(self, frame: StartFrame):
        """Start, and then declare the transport ready so audio has somewhere to go.

        THIS IS WHAT MADE THE CALLER HEAR SILENCE, and it is worth being precise
        about because everything upstream looked perfect.

        In Pipecat 1.7.0 BaseOutputTransport.start() only computes the sample rate
        and chunk size. The thing that makes the transport able to EMIT anything is
        set_transport_ready(), which is where _media_senders[None] - the default
        destination - is constructed. Nothing calls it for you.

        Without it, _handle_frame() finds no sender registered for the frame's
        destination and drops the frame with a warning, once per 20ms of audio. So
        a call looked entirely healthy: the pipeline linked, StartFrame reached the
        end, VAD triggered, STT transcribed, the agent answered in 723ms, OmniVoice
        synthesised 182KB of speech and the prompt cache wrote it back for next
        time - and every single audio frame was thrown away between the TTS service
        and the socket. The caller heard nothing at all.

        The input transport already did this, with a comment saying the caller
        hears nothing without it. The same call was simply never added here.
        """
        await super().start(frame)
        await self.set_transport_ready(frame)
        log.info("audiosocket output ready at %dHz, default destination registered",
                 SAMPLE_RATE)

    async def write_audio_frame(self, frame: OutputAudioRawFrame) -> bool:
        """Chunk to 20ms and write IN REAL TIME.

        The pacing is the important part, and its absence is what a caller heard
        as "bup..bup" instead of sentences.

        Pipecat's MediaSender does not pace audio. Its _next_frame() loop pulls
        from the queue and only does `await asyncio.sleep(0)`, which yields to the
        event loop without waiting - the docstring there says it exists to avoid
        100% CPU during cancellation, not to time anything. Pacing is delegated to
        the transport, which works for WebRTC and websocket transports because
        network backpressure supplies it for free.

        An AudioSocket to Asterisk on localhost has no such backpressure. The TCP
        buffer swallows everything, drain() returns immediately, and a measured
        9-11 seconds of speech was handed over in about 25 milliseconds. Asterisk
        writes one 20ms frame per tick to the channel, so the overwhelming
        majority of that burst had nowhere to go and was discarded - the caller
        got a fragment, a gap, a fragment.

        Measured proof the bytes themselves were fine, so that this was not
        mistaken for a TTS fault: tests/diag_tts_audio_shape.py on the exact PCM
        from those calls reports 8000Hz, 16-bit mono, RMS ~2400, peak well short
        of clipping, DC offset ~-5, and 665-899 zero crossings/sec, which is
        ordinary 8kHz speech.

        So each 20ms frame is now released on a 20ms clock. Sleeping here is the
        correct place to block: it back-pressures the audio task, which is exactly
        the signal Pipecat expects a transport to provide.

        Barge-in survives this. interrupt() empties _pending, and the loop
        re-checks it every iteration, so at most one already-popped 20ms frame is
        written after an interruption - which is the bound the class docstring
        promises.
        """
        if self._writer is None or self._writer.is_closing():
            return False

        self._pending += frame.audio

        now = time.monotonic()
        if self._next_send_at is None or now - self._next_send_at > RESYNC_AFTER_S:
            # First frame of an utterance, or we fell far enough behind that
            # catching up would mean bursting. Restart the clock from now instead
            # of trying to make up lost time, because bursting is the failure this
            # method exists to prevent.
            self._next_send_at = now

        try:
            while len(self._pending) >= FRAME_BYTES:
                chunk, self._pending = (self._pending[:FRAME_BYTES],
                                        self._pending[FRAME_BYTES:])
                delay = self._next_send_at - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                self._writer.write(encode_audio(chunk))
                self._next_send_at += FRAME_MS / 1000.0
            await self._writer.drain()
        except (ConnectionError, RuntimeError) as exc:
            log.warning("audiosocket write failed: %s", exc)
            return False
        return True

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Flush buffered audio the instant an interruption arrives.

        InterruptionFrame is a SystemFrame, so it jumps ahead of queued audio and
        reaches us while the bot is still mid-sentence. Handling it before
        delegating upward is what makes barge-in feel immediate: the base class
        stops TTS producing, and this drops what has already been produced.
        """
        if isinstance(frame, InterruptionFrame):
            await self.interrupt()
        await super().process_frame(frame, direction)

    async def interrupt(self) -> None:
        """Drop audio that has been generated but not yet sent.

        This is the barge-in mechanism at the media layer. When the caller starts
        speaking, anything still queued locally has to go, or the bot keeps
        talking over them from a buffer even after the pipeline has stopped
        producing. Bytes already handed to Asterisk cannot be recalled, which is
        why writes are kept to 20ms - it bounds how much is unrecallable.
        """
        dropped = len(self._pending)
        self._pending = b""
        # Drop the pacing clock too. The next utterance is a new one and must
        # start from now; keeping a stale deadline would make its first frames
        # look overdue and release them in a burst - reintroducing the exact
        # stutter the pacing exists to prevent, but only ever after a barge-in,
        # which is a miserable thing to have to reproduce.
        self._next_send_at = None
        if dropped:
            log.debug("barge-in dropped %d buffered bytes (%.0fms)",
                      dropped, dropped / (SAMPLE_RATE * BYTES_PER_SAMPLE) * 1000)

    async def stop(self, frame: EndFrame):
        await super().stop(frame)
        await self._close()

    async def cancel(self, frame: CancelFrame):
        await super().cancel(frame)
        await self._close()

    async def _close(self) -> None:
        if self._writer and not self._writer.is_closing():
            try:
                self._writer.write(encode(TYPE_TERMINATE))
                await self._writer.drain()
            except (ConnectionError, RuntimeError):
                pass


class AudioSocketTransport(BaseTransport):
    """Pipecat transport for one Asterisk AudioSocket connection."""

    def __init__(self, params: TransportParams | None = None, **kwargs):
        super().__init__(**kwargs)
        # 8kHz in and out: the PSTN rate. The STT and TTS services resample as
        # they need to, which keeps the resampling decision with the component
        # that actually cares.
        self._params = params or TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=SAMPLE_RATE,
            audio_out_sample_rate=SAMPLE_RATE,
            audio_in_channels=CHANNELS,
            audio_out_channels=CHANNELS,
        )
        self._input = AudioSocketInputTransport(self._params, name=self._input_name)
        self._output = AudioSocketOutputTransport(self._params, name=self._output_name)

    def input(self) -> FrameProcessor:
        return self._input

    def output(self) -> FrameProcessor:
        return self._output

    @property
    def audio_in(self) -> AudioSocketInputTransport:
        return self._input

    @property
    def audio_out(self) -> AudioSocketOutputTransport:
        return self._output


# --------------------------------------------------------------------------- #
# TCP server
# --------------------------------------------------------------------------- #
class AudioSocketServer:
    """Listens for Asterisk AudioSocket connections, one per call.

    Asterisk dials out to us, so each accepted connection is a live call. The
    handler is given the channel UUID and a transport, and is expected to build
    and run a pipeline for that call.
    """

    def __init__(
        self,
        handler: Callable[[str, AudioSocketTransport], Awaitable[None]],
        host: str = "127.0.0.1",
        port: int = 8090,
    ):
        self._handler = handler
        self._host = host
        self._port = port
        self._server: asyncio.Server | None = None

    async def serve_forever(self) -> None:
        self._server = await asyncio.start_server(
            self._on_connection, self._host, self._port,
        )
        log.info("audiosocket listening on %s:%d", self._host, self._port)
        async with self._server:
            await self._server.serve_forever()

    async def _on_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
    ) -> None:
        peer = writer.get_extra_info("peername")
        log.info("audiosocket connection from %s", peer)

        transport = AudioSocketTransport()
        transport.audio_out.attach(writer)

        call_uuid: str | None = None
        pipeline_task: asyncio.Task | None = None
        buffer = b""

        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                buffer += data

                while True:
                    parsed = decode(buffer)
                    if parsed is None:
                        break
                    msg_type, payload, buffer = parsed

                    if msg_type == TYPE_UUID:
                        call_uuid = parse_uuid(payload)
                        log.info("call uuid %s", call_uuid)
                        # Start the pipeline only once the UUID is known, since
                        # the handler needs it to load call state.
                        pipeline_task = asyncio.create_task(
                            self._handler(call_uuid, transport)
                        )
                    elif msg_type == TYPE_AUDIO:
                        await transport.audio_in.feed(payload)
                    elif msg_type == TYPE_TERMINATE:
                        log.info("asterisk terminated call %s", call_uuid)
                        return
                    elif msg_type == TYPE_ERROR:
                        code = payload[0] if payload else -1
                        log.warning("asterisk reported error %s on %s", code, call_uuid)
                    else:
                        log.warning("unknown audiosocket type 0x%02x", msg_type)
        except (ConnectionError, asyncio.IncompleteReadError) as exc:
            # A caller hanging up mid-sentence lands here. It is the normal
            # path for a dropped call, not an error - the checkpoint in
            # DynamoDB is what makes the next leg resumable.
            log.info("audiosocket connection closed for %s: %s", call_uuid, exc)
        finally:
            if pipeline_task and not pipeline_task.done():
                pipeline_task.cancel()
            if not writer.is_closing():
                writer.close()
