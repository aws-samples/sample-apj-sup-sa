"""Pipecat TTS service for the call pipeline.

OMNIVOICE IS THE ONLY TTS. There is deliberately no alternative engine.

This module used to also ship a Polly implementation as a "clearly-labelled
interim", selectable with HVI_TTS=polly. That has been removed. The requirement is
explicit and was stated twice:

    "you can use Omni voice as a text-to-speech provider and whisper large for a
     speech-to-text provider"

A second engine sitting one environment variable away did not stay an interim. It
became a fallback, then a debugging shortcut, and each time the customer would
have heard a voice they never asked for. If the OmniVoice endpoint is unhealthy
the right outcome is a loud failure, not a quiet substitution.

Two things matter for barge-in:

  Chunked output. run_tts yields audio in small pieces rather than one blob, so
  the transport can start playing almost immediately and, more importantly, so
  there is little committed audio to throw away when the caller interrupts.

  8kHz native. The PSTN leg is narrowband. OmniVoice synthesises at 24kHz and the
  decimation to 8kHz happens on the endpoint, so nothing is resampled here.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import warnings
from collections.abc import AsyncGenerator

import boto3
from botocore.config import Config

# Pipecat 1.7.0's tts_service module internally subclasses its own deprecated
# AudioContextTTSService, which emits a DeprecationWarning on import that is
# not actionable from this codebase (verified: neither of our services has the
# deprecated class in its MRO). Suppress exactly that message so live call
# logs stay clean; delete this when pipecat 2.0 removes the class.
warnings.filterwarnings(
    "ignore", message=r".*AudioContextTTSService.*", category=DeprecationWarning)

from pipecat.frames.frames import (
    ErrorFrame,
    Frame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.services.tts_service import TTSService

log = logging.getLogger("hvi.media.tts")

DATA_REGION = os.environ.get("DATA_REGION", "ap-south-1")
SAMPLE_RATE = 8000

# Emit in ~40ms pieces. Small enough that the caller hears speech promptly and
# that an interruption discards almost nothing; large enough that we are not
# making a syscall per 20ms frame.
CHUNK_BYTES = SAMPLE_RATE * 2 * 40 // 1000  # 640

# Mirrors common/hvi_state.normalise_language.
#
# NOT IMPORTED, and that is deliberate rather than lazy: hvi_state is the DynamoDB
# layer and builds a boto3 dynamodb resource at import time, this host holds no
# DynamoDB permissions, and scripts/72-media-deploy.sh pointedly does not ship it
# for exactly that reason. A six-entry dict is the cheaper of the two couplings.
# If the canonical map gains a spelling, this needs it too.
_LANGUAGE_ALIASES = {
    "en-in": "en", "en_in": "en", "en": "en", "english": "en",
    "hi-in": "hi", "hi_in": "hi", "hi": "hi", "hindi": "hi",
    "hinglish": "hinglish", "hi-en": "hinglish", "en-hi": "hinglish",
}


def speakable_identifiers(text: str) -> str:
    """Rewrite policy numbers and similar IDs into something a listener can follow.

    The implementation lives in common/hvi_prompt_cache.py for the same reason
    split_segments does: tools/build_prompt_cache.py synthesises the fixed prompts
    and this process synthesises the rest, so two copies would be two ways to
    pronounce the same utterance. No cached prompt carries an identifier today, but
    "no caller ever hits that path" is what the previous four divergences in this
    cache looked like beforehand.

    A missing module DEGRADES rather than raising. If it were fatal the caller would
    hear silence, which is strictly worse than hearing a number badly - and the
    warning names the cause, which is what the un-normalised version could not.
    """
    if not text:
        return text
    try:
        from common.hvi_prompt_cache import speakable_identifiers as _impl
    except ImportError:
        global _WARNED_NO_SPEAKABLE
        if not _WARNED_NO_SPEAKABLE:
            _WARNED_NO_SPEAKABLE = True
            log.warning("common/hvi_prompt_cache.py is not importable, so policy "
                        "numbers will be synthesised verbatim and will be hard to "
                        "understand. Check what scripts/72-media-deploy.sh shipped.")
        return text
    return _impl(text)


_WARNED_NO_SPEAKABLE = False


def normalise_language(value: str | None, default: str = "hinglish") -> str:
    """Map any CRM or carrier spelling onto en / hi / hinglish."""
    if not value:
        return default
    return _LANGUAGE_ALIASES.get(str(value).strip().lower(), default)


class OmniVoiceTTSService(TTSService):
    """The product TTS, on a SageMaker GPU endpoint (scripts/52-omnivoice.sh).

    ZERO-SHOT, SO THERE ARE NO VOICES. OmniVoice clones from a reference clip
    plus that clip's transcript, both baked into the endpoint's model.tar.gz. The
    reference IS the agent's voice, chosen in tools/make_reference_voice.py. The
    'voice' argument exists only so logs read consistently; it selects nothing.

    8kHz OVER THE WIRE, DESPITE 24kHz SYNTHESIS. OmniVoice generates at 24kHz and
    the PSTN leg is 8kHz. The downsample happens on the ENDPOINT, not here: this
    box is a 2GB t4g.small already running VAD, STT and the call pipeline, and
    shipping 24kHz would be three times the payload for audio about to be
    discarded. So SAMPLE_RATE stays 8000 and no resampling happens in this
    process - a property of where the work was put, not an assumption about the
    model.

    LICENCE, worth knowing before this ships: the omnivoice code is Apache 2.0
    but the pretrained weights are CC-BY-NC because the training data includes
    Emilia. Fine for a demo; not fine for commercial customer calls.
    """

    def __init__(self, *, endpoint: str | None = None, voice: str = "reference", **kwargs):
        super().__init__(sample_rate=SAMPLE_RATE, **kwargs)
        self._endpoint = endpoint or os.environ.get("OMNIVOICE_ENDPOINT", "")
        self._voice = voice
        self._language = normalise_language(os.environ.get("HVI_DEFAULT_LANGUAGE"))
        self._client = boto3.client(
            "sagemaker-runtime", region_name=DATA_REGION,
            config=Config(read_timeout=15, connect_timeout=5,
                          retries={"max_attempts": 1}),
        )

    def set_language(self, language: str) -> None:
        """Follow the orchestrator's language, because it selects the reference clip.

        Cross-lingual cloning imports the reference language's accent, so the
        endpoint keeps a reference per language and picks by this value. Before this
        existed the request carried no language at all, so every Hindi turn was
        spoken from the English reference and came out anglicised - and nothing in
        the logs said so.
        """
        self._language = normalise_language(language, default=self._language)

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None, None]:
        if not self._endpoint:
            yield ErrorFrame(
                "OMNIVOICE_ENDPOINT is not set. OmniVoice is the product TTS but "
                "is not set. OmniVoice is the only TTS; run "
                "scripts/52-omnivoice.sh and re-deploy the media host."
            )
            return

        await self.start_ttfb_metrics()
        yield TTSStartedFrame()
        try:
            # OFF THE EVENT LOOP. boto3 is synchronous, and synthesis takes
            # 1.5-3s, so calling it directly here stalled the entire pipeline for
            # the duration: no inbound audio was read, so VAD saw a starved and
            # discontinuous stream, and the one-shot rate measurement in
            # audiosocket.py reported 2572Hz against a real 8000Hz purely because
            # its 3-second window landed on a blocked loop.
            #
            # media/agent_bridge.py already does this, with a comment saying that
            # blocking here "would stall the audio path and stutter playback". The
            # same call was simply never wrapped on this side - and this side is
            # the one that blocks for seconds rather than milliseconds.
            resp = await asyncio.to_thread(
                self._client.invoke_endpoint,
                EndpointName=self._endpoint,
                ContentType="application/json",
                Accept="audio/pcm",
                Body=self._request_body(text),
            )
            pcm = await asyncio.to_thread(resp["Body"].read)
        except Exception as exc:  # noqa: BLE001
            log.exception("omnivoice synthesis failed")
            yield ErrorFrame(f"omnivoice failed: {exc}")
            return
        finally:
            await self.stop_ttfb_metrics()

        for i in range(0, len(pcm), CHUNK_BYTES):
            yield TTSAudioRawFrame(
                audio=pcm[i:i + CHUNK_BYTES],
                sample_rate=SAMPLE_RATE,
                num_channels=1,
                context_id=context_id,
            )
        yield TTSStoppedFrame()

    def _request_body(self, text: str) -> str:
        """Match models/omnivoice/code/inference.py's input_fn.

        The text is run through speakable_identifiers() first - see there for the
        measurement. This is the single choke point where text becomes audio, so
        nothing can route around it.

        'language' selects WHICH REFERENCE CLIP the endpoint clones from - it does
        not tell the model what language the text is in, which OmniVoice infers from
        the text itself. The endpoint falls back to the default reference for any
        language that has no clip of its own, so sending this is always safe.

        There is deliberately no voice parameter: OmniVoice is zero-shot and has no
        voice inventory, so asking the caller to name a voice would imply a choice
        that does not exist.

        Nor is a sample rate sent. The endpoint always returns 8kHz because the
        handler downsamples there; see the class docstring for why.
        """
        import json
        return json.dumps({"text": speakable_identifiers(text),
                           "language": self._language})


# A short pause between spoken segments. Utterances are synthesised in pieces (see
# split_segments) and butting two independently-generated clips together sounds
# abrupt; a beat of silence reads as the speaker taking a breath between the
# acknowledgement and the question, which is what a human does there anyway.
SEGMENT_GAP_MS = int(os.environ.get("TTS_SEGMENT_GAP_MS", "120"))
_GAP_PCM = b"\x00" * (SAMPLE_RATE * 2 * SEGMENT_GAP_MS // 1000)

# THIS IS THE FIX FOR THE DEAD AIR, so the reasoning is recorded here where the
# split is used:
#
# The orchestrator glues a personalised acknowledgement to the front of every
# question - "ठीक है, अनिता जी। और आपका वर्तमान वजन किलोग्राम में?" - and the
# prompt cache is content-addressed on the full string. One personalised prefix
# therefore made the ENTIRE utterance uncacheable, and a live call showed the
# result: every single turn logged "prompt cache miss, synthesising live" and paid
# 1-3s of GPU synthesis. The cache worked; nothing qualified.
#
# Split at sentence boundaries and each piece becomes its own cache entry, and the
# economics invert:
#
#   the ack      - a small set, REPEATED WITHIN A CALL ("ठीक है, अनिता जी।" was
#                  spoken nine times in one three-minute call). First use
#                  synthesises, every later use is a disk read.
#   the question - fixed text from the form or the graph, cacheable across ALL
#                  calls and pre-seeded by tools/build_prompt_cache.py.
#
# The splitter itself lives in common/hvi_prompt_cache.py because it is part of
# the cache's ADDRESSING: the builder must seed exactly the segments this looks
# up, and two implementations of the split would be two ways to compute the key.
def _split_segments(text: str) -> list[str]:
    try:
        from common.hvi_prompt_cache import split_segments
        return split_segments(text)
    except Exception:  # noqa: BLE001 - a missing module must not silence a call
        return [text] if (text or "").strip() else []


class CachedTTSService(TTSService):
    """Serves pre-synthesised audio when it can, and never fails a call when it cannot.

    Measured with tools/prompt_inventory.py: 85 of the 92 utterances the agent can
    speak contain no placeholders, so they are byte-identical on every call. Live
    synthesis was ~1712ms of a 2.8s turn budget; a cache hit is a disk read.

    A MISS IS NOT AN ERROR. Health items are addable at runtime without
    redeployment, so the cache will always potentially lag the form schema. On a
    miss this delegates to the real engine and writes the result back, so the
    second call that asks a new question is fast. A cache that could fail a live
    call would be a worse trade than the latency it saves.

    Wrapping rather than building the cache into each engine, so OmniVoice and the
    every engine would get identical behaviour and there is one place to reason about
    correctness.
    """

    def __init__(self, inner: TTSService, cache=None, **kwargs):
        super().__init__(sample_rate=SAMPLE_RATE, **kwargs)
        self._inner = inner
        if cache is None:
            # The class docstring promises "never fails a call", and this
            # constructor broke that promise on the first live call. The import
            # was bare, common/hvi_prompt_cache.py had not been shipped to the
            # media host, and ModuleNotFoundError propagated out of build_tts()
            # during pipeline setup. The caller answered and heard nothing at all.
            #
            # A missing or broken cache is an optimisation that is unavailable. It
            # must never be the reason a patient hears silence, so the failure is
            # contained here and the pipeline continues on live synthesis - slower,
            # and audible, which is the right way round.
            try:
                from common.hvi_prompt_cache import PromptCache
                cache = PromptCache()
            except Exception as e:  # noqa: BLE001
                log.error("PROMPT CACHE UNAVAILABLE (%s: %s) - every prompt will "
                          "be synthesised live, ~2885ms instead of ~0.5ms. The "
                          "call still works. Check that the module shipped.",
                          type(e).__name__, e)
                cache = None
        self._cache = cache
        self._language = os.environ.get("HVI_DEFAULT_LANGUAGE", "en")
        self.hits = 0
        self.misses = 0

    def set_language(self, language: str) -> None:
        """Follow the orchestrator's language, because the cache key includes it.

        Without this a Hindi call would look up English keys, miss every time, and
        quietly degrade to live synthesis for the whole call - fast on the happy
        path and slow exactly where it matters.

        This also has to reach the WRAPPED engine, which selects its reference clip
        by language. Previously it stopped here, so on a miss the live synthesis
        used whatever language the inner service was constructed with.
        """
        self._language = normalise_language(language, default=self._language)
        inner_set = getattr(self._inner, "set_language", None)
        if callable(inner_set):
            inner_set(self._language)

    async def run_tts(self, text: str, context_id: str):
        # PER SEGMENT, NOT PER UTTERANCE. See split_segments for why: caching the
        # whole utterance meant one personalised prefix made everything uncacheable
        # and every turn of a live call paid full GPU synthesis. Splitting means the
        # repeated ack and the fixed question each hit the cache independently, and
        # only genuinely novel text is ever synthesised.
        #
        # Ordering is preserved by awaiting each segment in sequence. A cached
        # segment starts playing IMMEDIATELY while a later segment synthesises,
        # which also front-fills what used to be dead air: the caller hears
        # "ठीक है, अनिता जी।" from disk in ~0.5ms even when the question after it
        # is a first-time synthesis.
        segments = _split_segments(text) or [text]

        yield TTSStartedFrame()
        first = True
        for seg in segments:
            if not first and SEGMENT_GAP_MS > 0:
                yield TTSAudioRawFrame(audio=_GAP_PCM, sample_rate=SAMPLE_RATE,
                                       num_channels=1, context_id=context_id)
            first = False

            # OFF THE EVENT LOOP, like the synthesis call. A disk hit is ~0.5ms,
            # but the first read of any prompt is an S3 GET on the audio path.
            pcm = (await asyncio.to_thread(self._cache.get, seg, self._language)
                   if self._cache else None)
            if pcm:
                self.hits += 1
                log.debug("segment cache hit (%s, %d bytes): %r",
                          self._language, len(pcm), seg[:40])
                for i in range(0, len(pcm), CHUNK_BYTES):
                    yield TTSAudioRawFrame(
                        audio=pcm[i:i + CHUNK_BYTES], sample_rate=SAMPLE_RATE,
                        num_channels=1, context_id=context_id,
                    )
                continue

            self.misses += 1
            log.info("segment cache miss, synthesising live: %r", seg[:60])

            # Pass frames through as they arrive rather than buffering - the
            # caller starts hearing audio as soon as the engine produces it. The
            # copy is only accumulated for the write-back. Inner start/stop frames
            # are swallowed because this method owns the utterance's envelope and
            # a stop mid-utterance would tell the transport speech has ended.
            collected = bytearray()
            async for frame in self._inner.run_tts(seg, context_id):
                if isinstance(frame, TTSAudioRawFrame) and frame.audio:
                    collected.extend(frame.audio)
                    yield frame
                elif isinstance(frame, ErrorFrame):
                    yield frame

            if collected and self._cache:
                try:
                    await asyncio.to_thread(self._cache.put, seg, self._language,
                                            bytes(collected))
                    log.info("cached segment (%d bytes) for next time",
                             len(collected))
                except Exception as e:  # noqa: BLE001
                    # Never let a cache write break a call that already succeeded.
                    log.warning("could not write prompt cache entry: %s", e)
        yield TTSStoppedFrame()

    @property
    def stats(self) -> dict:
        total = self.hits + self.misses
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hitRate": round(self.hits / total, 3) if total else None,
            "tiers": getattr(self._cache, "stats", {}),
        }


def build_tts(**kwargs) -> TTSService:
    """Build the TTS engine, wrapped in the prompt cache unless disabled.

    OMNIVOICE ONLY. Polly used to be selectable here as an "interim voice" and
    that option has been removed, because it was never in the brief:

        "you can use Omni voice as a text-to-speech provider and whisper large
         for a speech-to-text provider"                    - mentor, twice

    Keeping a Polly path available meant it got used - first as a fallback, then
    as a debugging shortcut - and each time the customer would have heard a voice
    they did not ask for. An engine that must not ship should not be one
    environment variable away from shipping.

    If the OmniVoice endpoint is down, the correct behaviour is to fail loudly
    here rather than to quietly substitute a different vendor's voice mid-demo.
    """
    choice = os.environ.get("HVI_TTS", "omnivoice").lower()
    if choice != "omnivoice":
        raise ValueError(
            f"HVI_TTS={choice!r} is not available. OmniVoice is the TTS the "
            f"customer specified; there is deliberately no alternative engine "
            f"configured. Fix the endpoint rather than switching voice."
        )
    inner: TTSService = OmniVoiceTTSService(**kwargs)

    # On by default: it is the difference between a ~1.1s and a ~2.8s gap after
    # the caller stops speaking. PROMPT_CACHE=off exists to measure the
    # difference, not because live synthesis is a reasonable steady state.
    if os.environ.get("PROMPT_CACHE", "on").lower() in ("off", "0", "false"):
        log.warning("prompt cache DISABLED; every utterance will be synthesised live")
        return inner
    return CachedTTSService(inner)
