#!/usr/bin/env python
"""End-to-end voice probe: pretends to be the browser.

Synthesises a spoken question with Deepgram TTS, dials the running server over
real WebRTC, streams the audio in, and prints every RTVI event plus the reply
audio it receives. This exercises STT -> LLM -> tools -> TTS with no browser.

    uv run python scripts/voice_probe.py --video smoke-test \
        --ask "What is the magic number and where is it mentioned?"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import aiohttp
import requests
from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaPlayer, MediaRecorder

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from talk2vid import config  # noqa: E402


def synth_question(text: str, dest: Path, lead: float = 1.2, tail: float = 22.0) -> Path:
    """Deepgram TTS, padded with silence so the peer stays up for the answer."""
    raw = dest.with_suffix(".raw.wav")
    resp = requests.post(
        "https://api.deepgram.com/v1/speak",
        params={"model": "aura-2-orion-en", "encoding": "linear16", "sample_rate": "16000"},
        headers={"Authorization": f"Token {config.DEEPGRAM_API_KEY}"},
        json={"text": text},
        timeout=60,
    )
    resp.raise_for_status()
    raw.write_bytes(resp.content)
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-t", str(lead), "-i", "anullsrc=r=16000:cl=mono",
            "-i", str(raw),
            "-f", "lavfi", "-t", str(tail), "-i", "anullsrc=r=16000:cl=mono",
            "-filter_complex", "[0:a][1:a][2:a]concat=n=3:v=0:a=1[a]",
            "-map", "[a]", "-ar", "48000", "-ac", "1", str(dest),
        ],
        check=True,
    )
    raw.unlink(missing_ok=True)
    return dest


async def probe(base: str, video_id: str, question: str, position: float, listen_secs: float) -> int:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        wav = synth_question(question, tmp / "ask.wav")
        reply = tmp / "reply.wav"

        pc = RTCPeerConnection(
            RTCConfiguration([RTCIceServer(urls="stun:stun.l.google.com:19302")])
        )
        events: list[str] = []
        transcript: dict[str, list[str]] = {"user": [], "bot": []}
        server_messages: list[dict] = []
        timings: dict[str, float] = {}
        turn: dict[str, float] = {}
        t0 = time.time()

        dc = pc.createDataChannel("chat")

        @dc.on("open")
        def on_open() -> None:
            dc.send(
                json.dumps(
                    {
                        "label": "rtvi-ai",
                        "type": "client-ready",
                        "id": "cr1",
                        "data": {"version": "1.0.0", "about": {"library": "voice-probe"}},
                    }
                )
            )
            # Same channel the browser uses: an RTVI client-message, not a bare
            # app message, so it reaches the bot's on_client_message handler.
            dc.send(
                json.dumps(
                    {
                        "label": "rtvi-ai",
                        "type": "client-message",
                        "id": "ph1",
                        "data": {"t": "playhead", "d": {"position": position, "playing": False}},
                    }
                )
            )

        @dc.on("message")
        def on_message(raw) -> None:
            if isinstance(raw, str) and raw.startswith("ping"):
                return
            try:
                msg = json.loads(raw)
            except Exception:
                return
            if msg.get("label") != "rtvi-ai":
                return
            kind, data = msg.get("type"), msg.get("data") or {}
            events.append(kind)
            now = time.time() - t0
            timings.setdefault(kind, now)
            # Latency is per turn: the *last* end-of-speech and the first bot
            # audio that follows it (the greeting must not be counted).
            if kind == "user-stopped-speaking":
                turn["asked_at"] = now
                turn.pop("answered_at", None)
            elif kind == "bot-started-speaking" and "asked_at" in turn and "answered_at" not in turn:
                turn["answered_at"] = now
            if kind == "user-transcription" and data.get("final"):
                transcript["user"].append(data.get("text", ""))
            elif kind == "bot-transcription":
                transcript["bot"].append(data.get("text", ""))
            elif kind == "bot-tts-text":
                # What was actually sent to the voice, after text normalisation.
                transcript.setdefault("spoken", []).append(data.get("text", ""))
            elif kind == "server-message":
                server_messages.append(data)
                print(f"  [server-message] {json.dumps(data)[:300]}")
            elif kind in ("error", "error-response"):
                print(f"  [ERROR] {data}")

        player = MediaPlayer(str(wav))
        pc.addTrack(player.audio)
        recorder = MediaRecorder(str(reply))

        @pc.on("track")
        def on_track(track) -> None:
            print(f"  receiving {track.kind} track")
            recorder.addTrack(track)

        await pc.setLocalDescription(await pc.createOffer())
        while pc.iceGatheringState != "complete":
            await asyncio.sleep(0.1)

        async with aiohttp.ClientSession() as http:
            async with http.post(
                f"{base}/api/offer",
                params={"video_id": video_id},
                json={"sdp": pc.localDescription.sdp, "type": pc.localDescription.type},
            ) as resp:
                if resp.status != 200:
                    print(f"signalling failed: {resp.status} {await resp.text()}")
                    return 1
                answer = await resp.json()

        await pc.setRemoteDescription(
            RTCSessionDescription(sdp=answer["sdp"], type=answer["type"])
        )
        await recorder.start()
        print(f"  connected (pc_id={answer['pc_id']}), asking: {question!r}")

        await asyncio.sleep(listen_secs)
        await recorder.stop()
        await pc.close()

        size = reply.stat().st_size if reply.exists() else 0
        dur = 0.0
        if size:
            dur = float(
                subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                     "-of", "csv=p=0", str(reply)],
                    capture_output=True, text=True,
                ).stdout.strip() or 0
            )

        print("\n=== probe result ===")
        print(f"events: {len(events)} · unique: {sorted(set(events))}")
        print(f"heard from user: {transcript['user']}")
        print(f"bot said: {transcript['bot']}")
        print(f"sent to voice: {transcript.get('spoken', [])}")
        print(f"tool/ui messages: {[m.get('t') for m in server_messages]}")
        if "asked_at" in turn and "answered_at" in turn:
            print(f"voice-to-voice: {turn['answered_at'] - turn['asked_at']:.2f}s")
        else:
            print("voice-to-voice: not measured (no answered turn)")
        print(f"reply audio: {dur:.1f}s recorded")

        ok = bool(transcript["bot"]) and dur > 0.5
        print("VERDICT:", "PASS" if ok else "FAIL")
        return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default="smoke-test")
    ap.add_argument("--ask", default="What is this video about?")
    ap.add_argument("--position", type=float, default=0.0, help="pretend playhead position")
    ap.add_argument("--listen", type=float, default=22.0, help="seconds to stay on the call")
    ap.add_argument("--base", default=f"http://{config.HOST}:{config.PORT}")
    args = ap.parse_args()
    return asyncio.run(probe(args.base, args.video, args.ask, args.position, args.listen))


if __name__ == "__main__":
    raise SystemExit(main())
