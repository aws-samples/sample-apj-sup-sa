from pathlib import Path

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import RunnerArguments
from pipecat.services.aws.llm import AWSBedrockLLMService
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.transports.base_transport import BaseTransport
from pipecat.workers.runner import WorkerRunner

from voice.config import (
    AWS_REGION,
    BEDROCK_MODEL_ID,
    DEEPGRAM_API_KEY,
    DEEPGRAM_TTS_VOICE,
)
from voice.tools import get_tools_schema, register_tools


def _load_text(name: str) -> str:
    """Load a prompt asset (e.g. prompt.md, greeting.md) from this package."""
    return (Path(__file__).parent / name).read_text(encoding="utf-8").strip()


SYSTEM_PROMPT = _load_text("prompt.md")
GREETING = _load_text("greeting.md")


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments):
    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)

    stt = DeepgramSTTService(
        api_key=DEEPGRAM_API_KEY,
        settings=DeepgramSTTService.Settings(model="nova-3-general"),
    )

    llm = AWSBedrockLLMService(
        model=BEDROCK_MODEL_ID,
        aws_region=AWS_REGION,
        settings=AWSBedrockLLMService.Settings(
            system_instruction=SYSTEM_PROMPT,
            max_tokens=1024,
            temperature=0.3,
        ),
    )

    tts = DeepgramTTSService(
        api_key=DEEPGRAM_API_KEY,
        settings=DeepgramTTSService.Settings(voice=DEEPGRAM_TTS_VOICE),
    )

    # Register debugging tools with the LLM
    tools = get_tools_schema()
    register_tools(llm)

    # Build conversation context with tools
    context = LLMContext(tools=tools)
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    pipeline = Pipeline([
        transport.input(),
        stt,
        user_aggregator,
        llm,
        tts,
        transport.output(),
        assistant_aggregator,
    ])

    agent = PipelineWorker(
        pipeline,
        name="voice",
        params=PipelineParams(enable_metrics=True),
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        # Greet first so the developer is not met with silence.
        await agent.queue_frame(TTSSpeakFrame(text=GREETING, append_to_context=True))

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        await runner.cancel()

    await runner.add_workers(agent)
    await runner.run()
