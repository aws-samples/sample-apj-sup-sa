"""End-to-end real-WebRTC loop validation (Phase A4).

Unlike harness/run_session.py (which composes the gap from component measurements without a real
media plane), this drives the TRUE loop: a headless aiortc peer connects to the running signaling
server over real WebRTC, SPEAKS synthesized student audio into the connection, and confirms the
coach replies with audio back. This is where real orchestration overhead (0 in the composed
harness) actually appears.

How it works:
  1. Synthesize each scripted student utterance to 16kHz PCM via Deepgram Aura (same TTS the worker
     uses) — a realistic spoken input that Deepgram STT on the server side will transcribe.
  2. A PlaybackAudioTrack emits that PCM as 20ms frames, then trailing silence so the server's
     endpointing fires (end-of-speech -> the turn runs).
  3. The peer POSTs its offer to /offer with a minted media-join token, sets the answer, and waits
     for ICE to connect.
  4. We detect the coach's reply by the first inbound audio frame after speaking, and time the
     real-loop response gap (peer-side end-of-playback -> first coach audio). This is a coarse
     wall-clock check that the loop LIVES on real media; the authoritative per-component gate
     numbers come from the server's turn_latency rows / CloudWatch.

Run (server must be running, sharing VOICE_TOKEN_SECRET):
    VOICE_TOKEN_SECRET=... python -m harness.loop_e2e --url http://127.0.0.1:18080 --turns 2
"""

from __future__ import annotations

import argparse
import asyncio
import fractions
import json
import os
import struct
import time
import urllib.request
import wave

import av
import jwt
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamTrack

_SAMPLE_RATE = 16000
_FRAME_MS = 20
_SAMPLES = _SAMPLE_RATE * _FRAME_MS // 1000
_BYTES = _SAMPLES * 2

_STUDENT_TURNS = [
    "Sure, I am a final year student studying computer science and I love building things.",
    "I think my greatest strength is that I stay calm under pressure and break problems down.",
    "I want this role because it lets me work on real systems that people actually use.",
]


class PlaybackAudioTrack(MediaStreamTrack):
    """Emits a queued PCM utterance as 20ms frames, then silence (so endpointing fires)."""

    kind = "audio"

    def __init__(self) -> None:
        super().__init__()
        self._buf = bytearray()
        self._lock = asyncio.Lock()
        self._ts = 0
        self._tb = fractions.Fraction(1, _SAMPLE_RATE)

    async def enqueue(self, pcm: bytes) -> None:
        async with self._lock:
            self._buf.extend(pcm)

    async def recv(self) -> av.AudioFrame:
        await asyncio.sleep(_FRAME_MS / 1000)
        async with self._lock:
            if len(self._buf) >= _BYTES:
                chunk = bytes(self._buf[:_BYTES])
                del self._buf[:_BYTES]
            else:
                chunk = bytes(self._buf) + b"\x00" * (_BYTES - len(self._buf))
                self._buf.clear()
        frame = av.AudioFrame(format="s16", layout="mono", samples=_SAMPLES)
        frame.sample_rate = _SAMPLE_RATE
        frame.planes[0].update(chunk)
        frame.pts = self._ts
        frame.time_base = self._tb
        self._ts += _SAMPLES
        return frame


async def _synth_student_pcm(text: str) -> bytes:
    """Synthesize a student utterance to 16kHz PCM via Deepgram Aura (a realistic spoken input)."""
    from src.config import Config
    from harness.tts_deepgram import DeepgramTTS, TTSConfig

    config = Config.load()
    out = bytearray()

    async def on_audio(frame: bytes) -> None:
        out.extend(frame)

    tts = DeepgramTTS(config.deepgram_api_key, TTSConfig(), on_audio)
    tts.reset()
    await tts.speak_chunk(text)
    await tts.finish()
    return bytes(out)


def _mint_token(secret: str, session_id: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": "e2e-user", "sid": session_id, "iat": now, "exp": now + 300, "scope": "media-join"},
        secret,
        algorithm="HS256",
    )


async def run(url: str, turns: int, secret: str, record_path: str | None = None) -> int:
    session_id = "e2e-" + str(int(time.time()))
    token = _mint_token(secret, session_id)

    # Pre-synthesize student utterances (off the timed path).
    n = min(turns, len(_STUDENT_TURNS))
    print(f"synthesizing {n} student utterance(s) via Aura...")
    utterances = [await _synth_student_pcm(_STUDENT_TURNS[i]) for i in range(n)]
    for i, u in enumerate(utterances):
        print(f"  turn {i}: {len(u)} bytes (~{len(u)/2/_SAMPLE_RATE:.1f}s)")

    pc = RTCPeerConnection()
    track = PlaybackAudioTrack()
    pc.addTrack(track)

    def _peak(frame: av.AudioFrame) -> int:
        pcm = bytes(frame.planes[0])[: frame.samples * 2]
        n = len(pcm) // 2
        if not n:
            return 0
        return max(abs(s) for s in struct.unpack(f"<{n}h", pcm[: n * 2]))

    # "voiced" = inbound coach frames above a silence threshold (the outbound track pads silence
    # continuously, so frame COUNT is meaningless; energy distinguishes real speech from padding).
    coach_audio = {"voiced_frames": 0, "first_voiced_t": None, "last_voiced_t": None}
    # Full inbound coach PCM (incl. silence padding) captured in arrival order — written to a WAV at
    # the end when --record is set, so the audio can be LISTENED to. Frame COUNT / energy alone (the
    # A4 check) cannot detect the choppiness bug; only the rendered audio can.
    recorded = bytearray() if record_path else None
    connected = asyncio.Event()

    @pc.on("connectionstatechange")
    async def _state() -> None:
        print("peer connection state:", pc.connectionState)
        if pc.connectionState == "connected":
            connected.set()

    @pc.on("track")
    def _on_track(t: MediaStreamTrack) -> None:
        if t.kind != "audio":
            return

        async def _drain() -> None:
            try:
                while True:
                    frame = await t.recv()
                    if recorded is not None:
                        # Resample-safe capture: the inbound frame is already 16kHz mono s16le from
                        # the worker; take its valid sample bytes (PyAV pads the plane).
                        recorded.extend(bytes(frame.planes[0])[: frame.samples * 2])
                    if _peak(frame) > 500:  # voiced (not silence padding)
                        now = time.monotonic()
                        coach_audio["voiced_frames"] += 1
                        if coach_audio["first_voiced_t"] is None:
                            coach_audio["first_voiced_t"] = now
                        coach_audio["last_voiced_t"] = now
            except Exception:  # noqa: BLE001 - track ended
                pass

        asyncio.create_task(_drain())

    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    body = json.dumps({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}).encode()
    req = urllib.request.Request(
        f"{url}/offer",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    resp = await asyncio.get_running_loop().run_in_executor(
        None, lambda: urllib.request.urlopen(req, timeout=20)
    )
    ans = json.loads(resp.read())
    await pc.setRemoteDescription(RTCSessionDescription(sdp=ans["sdp"], type=ans["type"]))
    print("answer set; waiting for ICE to connect...")

    try:
        await asyncio.wait_for(connected.wait(), timeout=20)
    except asyncio.TimeoutError:
        print("ICE did NOT connect within 20s (state=%s) — see Risks: NLB/host candidates" % pc.connectionState)
        await pc.close()
        return 2
    print("ICE connected over real WebRTC.")

    # Let the opening question play out fully (wait until the coach track goes quiet for ~1s).
    print("waiting for the opening question to finish playing...")
    await _wait_quiet(coach_audio, quiet_s=1.0, max_s=12.0)

    replied = 0
    for i, u in enumerate(utterances):
        coach_audio["voiced_frames"] = 0
        coach_audio["first_voiced_t"] = None
        coach_audio["last_voiced_t"] = None
        print(f"\n--- speaking student turn {i} ({_STUDENT_TURNS[i][:40]}...) ---")
        await track.enqueue(u)
        play_s = len(u) / 2 / _SAMPLE_RATE
        await asyncio.sleep(play_s)         # finish playing the utterance
        t_end_speech = time.monotonic()
        # endpointing (~600ms) + finalization + reply + TTS — wait generously for the lead-in.
        deadline = t_end_speech + 15.0
        while time.monotonic() < deadline and coach_audio["first_voiced_t"] is None:
            await asyncio.sleep(0.02)
        if coach_audio["first_voiced_t"] is not None:
            replied += 1
            gap = (coach_audio["first_voiced_t"] - t_end_speech) * 1000
            print(f"    COACH REPLIED over real WebRTC: first voiced audio +{gap:.0f}ms after "
                  f"end-of-playback (peer-side wall clock; includes ~600ms server endpointing — "
                  f"the authoritative per-component gate number is the server's turn_latency row)")
            # let the full reply play out before the next turn
            await _wait_quiet(coach_audio, quiet_s=1.0, max_s=15.0)
        else:
            print("    NO coach audio detected within 15s — loop did not reply")

    # Write the WAV BEFORE pc.close(): aiortc's close() can block while tracks drain, and the
    # capture must survive that regardless.
    if recorded is not None and record_path:
        with wave.open(record_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(_SAMPLE_RATE)
            w.writeframes(bytes(recorded))
        dur = len(recorded) / 2 / _SAMPLE_RATE
        print(f"recorded coach audio -> {record_path} ({dur:.1f}s, {len(recorded)} bytes). "
              f"Listen to it to confirm full, smooth sentences (not choppy fragments).")

    print(f"\n{replied}/{n} turns produced a coach reply over the real loop. closing peer.")
    try:
        await asyncio.wait_for(pc.close(), timeout=5.0)
    except asyncio.TimeoutError:
        print("pc.close() did not return within 5s (tracks still draining) — exiting anyway.")
    return 0 if replied == n else 3


async def _wait_quiet(coach_audio: dict, quiet_s: float, max_s: float) -> None:
    """Block until the coach track has been silent for `quiet_s` (or `max_s` elapses)."""
    start = time.monotonic()
    while time.monotonic() - start < max_s:
        last = coach_audio["last_voiced_t"]
        if last is not None and (time.monotonic() - last) >= quiet_s:
            return
        await asyncio.sleep(0.05)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Real-WebRTC end-to-end loop validation (A4)")
    p.add_argument("--url", default="http://127.0.0.1:18080")
    p.add_argument("--turns", type=int, default=2)
    p.add_argument("--record", metavar="WAV", default=None,
                   help="capture inbound coach audio to this WAV file (to listen for choppiness)")
    args = p.parse_args(argv)
    secret = os.environ.get("VOICE_TOKEN_SECRET")
    if not secret:
        print("VOICE_TOKEN_SECRET must be set (same value the server uses)")
        return 1
    return asyncio.run(run(args.url, args.turns, secret, record_path=args.record))


if __name__ == "__main__":
    raise SystemExit(main())
