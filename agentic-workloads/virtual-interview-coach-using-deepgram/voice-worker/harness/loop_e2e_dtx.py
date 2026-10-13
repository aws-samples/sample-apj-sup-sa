"""DTX-halt loop validation — exercises the endpoint watchdog (D-fix#4).

This is a variant of harness/loop_e2e.py. The original streams TRAILING SILENCE after each
utterance, which lets Deepgram endpoint normally (it endpoints on in-stream silence) — so it never
exercises the server-side endpoint watchdog. A real browser does NOT do that: Opus DTX / silence
suppression makes it STOP sending RTP when the student goes quiet. When the stream simply HALTS,
Deepgram never fires speech_final/UtteranceEnd, and only the worker's watchdog can drive the turn.

This harness reproduces that: it SPEAKS one synthesized student utterance, then HALTS the outbound
track entirely (recv() blocks forever — no more RTP, like a DTX browser). If the coach still replies,
the watchdog works. We time the gap from end-of-speech to first coach audio (expect it to include
the ENDPOINT_WATCHDOG_MS window, ~1.2s, plus reply lead-in).

Run (signaling via the ALB; media flows direct UDP to the worker's public IP):
    VOICE_TOKEN_SECRET=... python -m harness.loop_e2e_dtx \
        --url http://<alb-dns> --turns 1
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

import av
import jwt
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamTrack

_SAMPLE_RATE = 16000
_FRAME_MS = 20
_SAMPLES = _SAMPLE_RATE * _FRAME_MS // 1000
_BYTES = _SAMPLES * 2

_STUDENT_TURNS = [
    "Hi, my name is Jinje and thank you for arranging this interview today.",
    "I think my greatest strength is that I stay calm under pressure and break problems down.",
]


class DtxPlaybackTrack(MediaStreamTrack):
    """Emits a queued PCM utterance as 20ms frames, then HALTS (recv blocks) — simulating DTX.

    Unlike PlaybackAudioTrack (which pads silence), once the buffer empties and `halt()` has been
    called this track stops producing frames. The RTP sender then sends nothing — exactly what a
    browser does under Opus DTX when the user goes quiet.
    """

    kind = "audio"

    def __init__(self) -> None:
        super().__init__()
        self._buf = bytearray()
        self._lock = asyncio.Lock()
        self._ts = 0
        self._tb = fractions.Fraction(1, _SAMPLE_RATE)
        self._halt = False
        self._never = asyncio.Event()  # intentionally never set

    async def enqueue(self, pcm: bytes) -> None:
        async with self._lock:
            self._buf.extend(pcm)

    def halt(self) -> None:
        self._halt = True

    async def recv(self) -> av.AudioFrame:
        await asyncio.sleep(_FRAME_MS / 1000)
        async with self._lock:
            have = len(self._buf)
            if have >= _BYTES:
                chunk = bytes(self._buf[:_BYTES])
                del self._buf[:_BYTES]
            elif have > 0:
                chunk = bytes(self._buf) + b"\x00" * (_BYTES - have)
                self._buf.clear()
            else:
                chunk = None
        if chunk is None:
            if self._halt:
                # DTX: stop sending RTP entirely. Block forever — the sender idles, like a quiet
                # browser. (The peer is torn down by the harness when the test ends.)
                await self._never.wait()
            chunk = b"\x00" * _BYTES  # pre-speech padding before the first utterance is enqueued
        frame = av.AudioFrame(format="s16", layout="mono", samples=_SAMPLES)
        frame.sample_rate = _SAMPLE_RATE
        frame.planes[0].update(chunk)
        frame.pts = self._ts
        frame.time_base = self._tb
        self._ts += _SAMPLES
        return frame


async def _synth_student_pcm(text: str) -> bytes:
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
        {"sub": "dtx-user", "sid": session_id, "iat": now, "exp": now + 300, "scope": "media-join"},
        secret,
        algorithm="HS256",
    )


def _peak(frame: av.AudioFrame) -> int:
    pcm = bytes(frame.planes[0])[: frame.samples * 2]
    n = len(pcm) // 2
    if not n:
        return 0
    return max(abs(s) for s in struct.unpack(f"<{n}h", pcm[: n * 2]))


async def run(url: str, turns: int, secret: str) -> int:
    session_id = "dtx-" + str(int(time.time()))
    token = _mint_token(secret, session_id)

    n = min(turns, len(_STUDENT_TURNS))
    print(f"synthesizing {n} student utterance(s) via Aura...")
    utterances = [await _synth_student_pcm(_STUDENT_TURNS[i]) for i in range(n)]
    for i, u in enumerate(utterances):
        print(f"  turn {i}: {len(u)} bytes (~{len(u)/2/_SAMPLE_RATE:.1f}s)")

    pc = RTCPeerConnection()
    track = DtxPlaybackTrack()
    pc.addTrack(track)

    coach_audio = {"voiced_frames": 0, "first_voiced_t": None, "last_voiced_t": None}
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
                    if _peak(frame) > 500:
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
        await asyncio.wait_for(connected.wait(), timeout=25)
    except asyncio.TimeoutError:
        print("ICE did NOT connect within 25s (state=%s) — direct UDP to the worker may be blocked"
              % pc.connectionState)
        await pc.close()
        return 2
    print("ICE connected over real WebRTC.")

    print("waiting for the opening question to finish playing...")
    await _wait_quiet(coach_audio, quiet_s=1.0, max_s=15.0)

    # Speak exactly ONE utterance, then HALT (no trailing silence) — pure DTX.
    coach_audio["voiced_frames"] = 0
    coach_audio["first_voiced_t"] = None
    coach_audio["last_voiced_t"] = None
    print(f"\n--- speaking student turn 0 ({_STUDENT_TURNS[0][:48]}...) then HALTING (DTX) ---")
    await track.enqueue(utterances[0])
    play_s = len(utterances[0]) / 2 / _SAMPLE_RATE
    await asyncio.sleep(play_s)
    track.halt()
    t_end_speech = time.monotonic()
    print(f"    utterance done (~{play_s:.1f}s); RTP HALTED. Deepgram cannot endpoint now — only the "
          f"watchdog can drive the turn. Waiting up to 20s for coach audio...")

    deadline = t_end_speech + 20.0
    while time.monotonic() < deadline and coach_audio["first_voiced_t"] is None:
        await asyncio.sleep(0.02)

    rc = 3
    if coach_audio["first_voiced_t"] is not None:
        gap = (coach_audio["first_voiced_t"] - t_end_speech) * 1000
        print(f"    COACH REPLIED after DTX halt: first voiced audio +{gap:.0f}ms after end-of-speech "
              f"(includes the ~1.2s ENDPOINT_WATCHDOG_MS window + reply lead-in). "
              f"==> THE WATCHDOG WORKS.")
        await _wait_quiet(coach_audio, quiet_s=1.0, max_s=15.0)
        rc = 0
    else:
        print("    NO coach audio within 20s after DTX halt — the watchdog did NOT drive the turn.")

    print("closing peer.")
    try:
        await asyncio.wait_for(pc.close(), timeout=5.0)
    except asyncio.TimeoutError:
        print("pc.close() did not return within 5s — exiting anyway.")
    return rc


async def _wait_quiet(coach_audio: dict, quiet_s: float, max_s: float) -> None:
    start = time.monotonic()
    while time.monotonic() - start < max_s:
        last = coach_audio["last_voiced_t"]
        if last is not None and (time.monotonic() - last) >= quiet_s:
            return
        await asyncio.sleep(0.05)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="DTX-halt loop validation (exercises the endpoint watchdog)")
    p.add_argument("--url", default="http://127.0.0.1:18080")
    p.add_argument("--turns", type=int, default=1)
    args = p.parse_args(argv)
    secret = os.environ.get("VOICE_TOKEN_SECRET")
    if not secret:
        print("VOICE_TOKEN_SECRET must be set (same value the server uses)")
        return 1
    return asyncio.run(run(args.url, args.turns, secret))


if __name__ == "__main__":
    raise SystemExit(main())
