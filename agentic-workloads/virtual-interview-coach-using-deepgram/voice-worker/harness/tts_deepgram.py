"""Deepgram Aura streaming TTS + cancellation (T018, T033).

Synthesizes the coach reply to audio, emitting the FIRST audio frame as early as possible
(the loop hands the first clause to TTS while the reply still generates — sentence-level
chunking, R1). Supports prompt cancellation for barge-in (US2/R3): cancel() aborts in-flight
synthesis and the caller flushes the outbound jitter buffer.

Wire protocol: talks Deepgram Aura's documented streaming WEBSOCKET directly via
`websockets` (see stt_deepgram.py for why the SDK event API is bypassed). One persistent
socket per turn is opened lazily; cancel() closes it so synthesis stops promptly.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable
from urllib.parse import urlencode

log = logging.getLogger("voice_worker")

# Invoked with each synthesized PCM frame; the first call is "first audio".
OnAudioFrame = Callable[[bytes], Awaitable[None]]

_DG_SPEAK_WSS = "wss://api.deepgram.com/v1/speak"


@dataclass
class TTSConfig:
    model: str = "aura-asteria-en"
    sample_rate: int = 16000
    encoding: str = "linear16"
    # Max wait for a single Aura frame / send / connect before abandoning the clause and
    # reconnecting (live-safety, not a latency target). First-audio is normally ~260ms, so a
    # few seconds is generous headroom while still failing a genuine stall fast.
    recv_timeout_s: float = 4.0


class DeepgramTTS:
    def __init__(self, api_key: str | None, config: TTSConfig, on_audio: OnAudioFrame) -> None:
        self._api_key = api_key
        self._config = config
        self._on_audio = on_audio
        self._cancelled = asyncio.Event()
        self._first_audio_sent = False
        self._ws = None
        # A fresh socket pre-opened in the background (open_spare) so the NEXT turn's first
        # speak_chunk uses an already-connected socket whose reuse count is zero. Aura's long-lived
        # sockets stop emitting audio after ~9-10 Speak/Flush/Clear cycles (a cumulative server-side
        # limit), so reusing one socket across a session deterministically stalls late turns. Pairing
        # per-turn retirement with background pre-warm keeps both the TLS handshake AND the
        # degradation off the critical path.
        self._spare = None
        self._recv_timeout_s = config.recv_timeout_s

    def _url(self) -> str:
        q = {
            "model": self._config.model,
            "encoding": self._config.encoding,
            "sample_rate": str(self._config.sample_rate),
        }
        return f"{_DG_SPEAK_WSS}?{urlencode(q)}"

    def reset(self) -> None:
        self._cancelled.clear()
        self._first_audio_sent = False

    def cancel(self) -> None:
        """Signal in-flight synthesis to stop ASAP (barge-in). The caller also flushes the
        outbound audio buffer so the cancel is audible quickly (<300ms, SC-004)."""
        self._cancelled.set()
        # Closing the socket aborts any in-flight synthesis stream promptly.
        ws = self._ws
        if ws is not None:
            asyncio.create_task(self._safe_close(ws))
            self._ws = None

    @staticmethod
    async def _safe_close(ws) -> None:
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass

    @property
    def first_audio_sent(self) -> bool:
        return self._first_audio_sent

    async def _connect(self):
        import websockets

        # Bound connect: a hung handshake must not stall the turn (live-safety).
        return await asyncio.wait_for(
            websockets.connect(
                self._url(), additional_headers={"Authorization": f"Token {self._api_key}"}
            ),
            timeout=self._recv_timeout_s,
        )

    async def _ensure_ws(self):
        if self._ws is None:
            # Prefer a spare pre-warmed in the background (zero handshake cost on the critical path).
            if self._spare is not None:
                self._ws, self._spare = self._spare, None
            else:
                self._ws = await self._connect()
        return self._ws

    async def open_spare(self) -> None:
        """Pre-open a replacement socket in the background (call during LLM generation / between
        turns, OFF the critical path). The next turn's first speak_chunk then runs on a freshly
        connected socket with a zero reuse count, avoiding both the TLS handshake latency and the
        ~9-10-cycle Aura degradation that stalls late turns on a reused socket. Best-effort: on
        failure the spare stays None and the next turn connects lazily as before."""
        if self._spare is not None or not self._api_key:
            return
        try:
            self._spare = await self._connect()
        except Exception as exc:  # noqa: BLE001 - non-fatal: lazy connect covers the next turn
            log.warning("TTS spare pre-warm failed (%s); next turn will connect lazily", exc)
            self._spare = None

    async def rotate(self) -> None:
        """Retire the current per-turn socket and swap in the background-warmed spare (if any).
        Called at end of turn instead of clear_and_drain when per-turn rotation is enabled: it
        sidesteps the cumulative-reuse stall entirely rather than trying to reset a tiring socket."""
        await self._drop_ws()
        if self._spare is not None:
            self._ws, self._spare = self._spare, None

    async def _drop_ws(self) -> None:
        """Discard the current socket so the next clause reconnects cleanly. Used when a clause
        stalls or errors — operating further on a half-broken socket is what caused multi-second
        hangs and 'I/O operation on closed file'."""
        ws = self._ws
        self._ws = None
        if ws is not None:
            await self._safe_close(ws)

    async def speak_chunk(self, text: str) -> None:
        """Synthesize one text chunk (a clause/sentence) and stream its audio frames.

        Returns early if cancelled. The first emitted frame across a turn marks first-audio
        (the loop reads first_audio_sent / its own timer to capture tts_first_audio_ms).
        """
        if self._cancelled.is_set() or not text.strip():
            return
        if not self._api_key:
            raise RuntimeError("DEEPGRAM_API_KEY not set")

        try:
            ws = await self._ensure_ws()
            # Ask Aura to synthesize this clause, then Flush so it emits audio immediately
            # (rather than waiting for more text — critical for first-audio latency). Bound the
            # sends too: a stalled send (not just recv) was observed hanging a turn for ~50s.
            await asyncio.wait_for(ws.send(json.dumps({"type": "Speak", "text": text})), timeout=self._recv_timeout_s)
            await asyncio.wait_for(ws.send(json.dumps({"type": "Flush"})), timeout=self._recv_timeout_s)
        except asyncio.TimeoutError:
            log.warning("TTS connect/send timed out after %.1fs; dropping socket", self._recv_timeout_s)
            await self._drop_ws()
            return
        except Exception as exc:  # noqa: BLE001 - reconnect next clause rather than hang
            log.warning("TTS send failed (%s); dropping socket", exc)
            await self._drop_ws()
            return

        while True:
            if self._cancelled.is_set():
                return
            try:
                # Bound the wait: if Aura never sends the terminating control frame for this
                # clause, a bare recv() would hang the whole turn (and the loop) indefinitely.
                # A short ceiling well above first-audio latency keeps the seam live-safe.
                message = await asyncio.wait_for(ws.recv(), timeout=self._recv_timeout_s)
            except asyncio.TimeoutError:
                log.warning("TTS recv timed out after %.1fs; dropping socket", self._recv_timeout_s)
                await self._drop_ws()
                return
            except Exception:  # noqa: BLE001 - socket closed (e.g. by cancel())
                await self._drop_ws()
                return
            if isinstance(message, (bytes, bytearray)):
                await self._on_audio(bytes(message))
                self._first_audio_sent = True
            else:
                # Control frame: Flushed/Cleared/Warning/Metadata signal end of this clause.
                evt = json.loads(message).get("type")
                if evt in ("Flushed", "Cleared", "Close"):
                    return

    async def clear_and_drain(self) -> None:
        """Reset a reused socket between turns WITHOUT reconnecting (keeps it warm).

        After a turn breaks out of a clause early (first-audio captured), the socket still holds
        un-drained audio frames and an unconsumed control frame. Send Aura's Clear to flush the
        server-side buffer, then drain inbound frames until 'Cleared' (or a short timeout). On any
        error, fall back to dropping the socket so the next clause reconnects clean rather than
        operating on dirty state. This avoids both the ~50s stall and the per-turn reconnect cost.
        """
        ws = self._ws
        if ws is None:
            return
        try:
            await asyncio.wait_for(ws.send(json.dumps({"type": "Clear"})), timeout=self._recv_timeout_s)
            deadline_frames = 200  # safety bound on drain loop
            while deadline_frames > 0:
                deadline_frames -= 1
                message = await asyncio.wait_for(ws.recv(), timeout=self._recv_timeout_s)
                if not isinstance(message, (bytes, bytearray)):
                    evt = json.loads(message).get("type")
                    if evt in ("Cleared", "Flushed", "Close"):
                        return
        except Exception:  # noqa: BLE001 - any failure: drop so next clause reconnects clean
            await self._drop_ws()

    async def finish(self) -> None:
        """Close the TTS socket(s) at end of turn/session."""
        ws, spare = self._ws, self._spare
        self._ws = None
        self._spare = None
        if ws is not None:
            await self._safe_close(ws)
        if spare is not None:
            await self._safe_close(spare)
