"""The voice pipeline.

    mic ──WebRTC──▶ Deepgram Nova-3 (streaming STT)
                        │
                 playhead tagger  ── adds [watching 2:15] so "here"/"this" resolve
                        │
                 user aggregator (Silero VAD turn taking, barge-in)
                        │
                 Claude Haiku 4.5 on Bedrock  ◀──▶ tools: find_moment · look_closer · web_search
                        │                            (video knowledge pack cached in the prompt)
                 Deepgram Aura-2 (streaming TTS)
                        │
    speaker ◀──WebRTC──┘

Everything that can be precomputed is precomputed at ingest, so a turn is
usually STT → one cached LLM call → TTS with no retrieval hop in between.
"""

from __future__ import annotations

import asyncio

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    EndFrame,
    Frame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.aws.llm import AWSBedrockLLMService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.daily.transport import DailyParams, DailyTransport
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.workers.runner import WorkerRunner

from talk2vid import agentcore, bedrock, config, media, prompt, tools
from talk2vid.index_store import VideoIndex


class TurnSpeechFlag(FrameProcessor):
    """Track whether the model has already said something in the current turn.

    Claude usually narrates its own holding phrase ("Let me check that") in the
    same reply that calls a slow tool, and that text is streamed to TTS
    immediately. Knowing this lets the slow tool speak a fallback phrase *only*
    when the model stayed silent — no dead air, no duplicated sentence.
    """

    def __init__(self, session: tools.Session) -> None:
        super().__init__()
        self._session = session

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMFullResponseStartFrame):
            self._session.llm_spoke_this_turn = False
        elif isinstance(frame, LLMTextFrame) and frame.text.strip():
            self._session.llm_spoke_this_turn = True
        await self.push_frame(frame, direction)


class PlayheadTagger(FrameProcessor):
    """Prefix each final transcription with the viewer's playback position.

    This is what makes "what's on screen right now?" and "explain this bit" work
    without any extra model call: the LLM simply sees where the user is looking.
    """

    def __init__(self, session: tools.Session) -> None:
        super().__init__()
        self._session = session

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame) and frame.text and frame.text.strip():
            frame.text = f"[watching {media.hhmmss(self._session.position)}] {frame.text.strip()}"
        await self.push_frame(frame, direction)


def build_transport(connection: SmallWebRTCConnection) -> SmallWebRTCTransport:
    return SmallWebRTCTransport(
        webrtc_connection=connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_out_sample_rate=config.TTS_SAMPLE_RATE,
        ),
    )


# Bedrock prompt caching only pays off — and is only accepted — under conditions
# worth being explicit about:
#   * Anthropic models: Nova rejects cachePoint markers inside tool definitions.
#   * Prompts above Claude's ~2048-token minimum: below it the markers are
#     ignored and the extra round-trip work makes first-token latency worse.
# A 5-10 minute video's knowledge pack clears the bar comfortably; a 45s test
# clip does not, so the decision is made per session from the real prompt.
CACHE_MIN_PROMPT_CHARS = 10_000


def should_cache_prompt(model_id: str, system_prompt: str) -> bool:
    return "anthropic" in model_id.lower() and len(system_prompt) >= CACHE_MIN_PROMPT_CHARS


def build_services(
    *, cache_prompt: bool = False
) -> tuple[DeepgramSTTService, AWSBedrockLLMService, DeepgramTTSService]:
    stt = DeepgramSTTService(
        api_key=config.DEEPGRAM_API_KEY,
        settings=DeepgramSTTService.Settings(
            model=config.STT_MODEL,
            language="en-US",
            smart_format=True,
            punctuate=True,
            interim_results=True,
            # Snappy turn ends without clipping natural pauses. Note Deepgram
            # rejects the connection outright if utterance_end_ms < 1000.
            endpointing=200,
            utterance_end_ms=1000,
        ),
    )
    llm = AWSBedrockLLMService(
        aws_region=config.AWS_REGION,
        settings=AWSBedrockLLMService.Settings(
            model=config.LLM_MODEL_ID,
            temperature=0.35,
            max_tokens=350,
            # The video knowledge pack is static per session, so caching it lets
            # every later turn skip re-reading thousands of prompt tokens.
            enable_prompt_caching=cache_prompt,
        ),
    )
    tts = DeepgramTTSService(
        api_key=config.DEEPGRAM_API_KEY,
        sample_rate=config.TTS_SAMPLE_RATE,
        settings=DeepgramTTSService.Settings(voice=config.TTS_VOICE),
    )
    return stt, llm, tts


async def run_bot(connection: SmallWebRTCConnection, index: VideoIndex) -> None:
    """Own one browser session over peer-to-peer WebRTC (local demos)."""
    await _run(build_transport(connection), index, ready_on="on_client_connected")


async def run_bot_daily(room_url: str, token: str, index: VideoIndex) -> None:
    """Own one browser session over Daily.

    Used once the server sits behind CloudFront and an ALB, where there is no
    inbound UDP path for peer-to-peer media. The bot dials *out* to Daily, so the
    deployment needs no open ports at all.
    """
    transport = DailyTransport(
        room_url,
        token,
        "Talk2Vid",
        DailyParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_out_sample_rate=config.TTS_SAMPLE_RATE,
        ),
    )
    # Daily has no "client connected" notion; readiness waits for a human instead.
    await _run(transport, index, ready_on="on_first_participant_joined")


async def _run(transport, index: VideoIndex, *, ready_on: str) -> None:
    """The pipeline itself, identical whichever transport carries the audio."""
    session = tools.Session(index=index)
    system_prompt = prompt.system_prompt(index)
    cache_prompt = should_cache_prompt(config.LLM_MODEL_ID, system_prompt)
    logger.info(
        f"session · {len(system_prompt)} prompt chars · prompt caching "
        f"{'on' if cache_prompt else 'off'}"
    )
    stt, llm, tts = build_services(cache_prompt=cache_prompt)

    context = LLMContext(
        messages=[{"role": "system", "content": system_prompt}],
        tools=tools.build_tools(session),
    )
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        # Default VAD timings on purpose: pipecat's smart turn analyzer decides
        # end-of-turn semantically, and its latency model assumes stop_secs=0.2.
        # Stretching stop_secs collapses the STT wait window and *adds* delay.
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
            # A turn the browser truncated at MAX_LISTEN_SECS usually ends
            # mid-sentence, so the analyzer calls it incomplete and this watchdog
            # is what actually finalizes it. Pipecat's 5s default would read as
            # the demo having hung.
            user_turn_stop_timeout=config.TURN_STOP_TIMEOUT_SECS,
        ),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            PlayheadTagger(session),
            user_aggregator,
            llm,
            TurnSpeechFlag(session),
            tts,
            transport.output(),
            assistant_aggregator,
        ]
    )
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        idle_timeout_secs=900,
        conversation_id=f"talk2vid-{index.video_id}",
    )
    session.rtvi = worker.rtvi

    async def speak(text: str) -> None:
        """Say something immediately, ahead of a slow tool result."""
        await worker.queue_frame(TTSSpeakFrame(text))

    session.speak = speak

    @transport.event_handler(ready_on)
    async def on_connected(_transport, _client):
        logger.info(f"client connected · video={index.video_id}")
        # Open the Bedrock connections while the user is still getting settled.
        asyncio.create_task(asyncio.to_thread(bedrock.warmup))
        asyncio.create_task(asyncio.to_thread(agentcore.warmup))
        # The agent never opens its mouth first: pressing the mic means "listen to
        # me", so the opener is delivered as text in the transcript and the floor
        # stays with the user until they actually say something.
        await session.emit(
            {
                "t": "ready",
                "video_id": index.video_id,
                "title": index.title,
                "models": index.meta.get("models", {}),
                "llm": config.LLM_MODEL_ID,
                "opener": prompt.greeting(index),
            }
        )

    @transport.event_handler("on_client_disconnected")
    async def on_disconnected(_transport, _client):
        logger.info("client disconnected")
        await worker.queue_frame(EndFrame())

    if isinstance(transport, DailyTransport):
        # Daily fires participant-left rather than client-disconnected, and the
        # session must end when the human goes away so the task is not left idle.
        @transport.event_handler("on_participant_left")
        async def on_participant_left(_transport, _participant, _reason):
            logger.info("participant left the room")
            await worker.queue_frame(EndFrame())

    @worker.rtvi.event_handler("on_client_message")
    async def on_client_message(_rtvi, message):
        """Playhead updates and typed questions from the browser."""
        data = message.data if isinstance(message.data, dict) else {}
        if message.type == "playhead":
            session.note_position(data.get("position", 0.0), data.get("playing"))
        elif message.type == "say":
            text = str(data.get("text", "")).strip()
            if text:
                # Same entry point as speech, so typed and spoken turns behave
                # identically (tools, playhead tagging, context, interruptions).
                await worker.queue_frame(
                    TranscriptionFrame(text=text, user_id="ui", timestamp="")
                )

    runner = WorkerRunner(handle_sigint=False)
    try:
        await runner.run(worker)
    except asyncio.CancelledError:
        raise
    finally:
        logger.info(f"session ended · video={index.video_id}")
