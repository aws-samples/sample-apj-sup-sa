"""Voice Analytics Agent — Pipecat pipeline.

Cascade: Deepgram STT → AnalyticsAgentCoreProcessor (Strands agent) → Deepgram TTS

Runs in one of two hosting modes (selected by VOICE_RUNTIME_MODE):
  * "agentcore" (default for the hosted demo) — the bot is its OWN Amazon Bedrock
    AgentCore Runtime. The browser does WebRTC: it POSTs an SDP offer (and trickled
    ICE candidates) to the runtime's /invocations endpoint, JWT-authorized. Media
    relays through Amazon Kinesis Video Streams (KVS) managed TURN. See
    `agentcore_entrypoint` below. The pipeline's "LLM" stage (AnalyticsAgentCoreProcessor)
    in turn invokes the SEPARATE Strands analytics runtime over HTTPS Bearer.
  * unset / "pcc" — the Daily-transport `bot()` entrypoint, used by Pipecat Cloud and
    laptop dev (pipecat.runner.run:main on :7860). Kept as a fallback.
The full pipeline (`run_bot`) is identical across modes; only the transport + how a
session is started differ.
"""

import os

from dotenv import load_dotenv
from loguru import logger

from analytics_processor import AnalyticsAgentCoreProcessor
from auth import get_gateway_token
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import DailyRunnerArguments, RunnerArguments
from pipecat.services.deepgram.flux.sagemaker.stt import DeepgramFluxSageMakerSTTService
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService
from pipecat.services.deepgram.sagemaker.stt import DeepgramSageMakerSTTService
from pipecat.services.deepgram.sagemaker.tts import DeepgramSageMakerTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService, LiveOptions
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
from pipecat.transports.base_transport import BaseTransport
from pipecat.transports.daily.transport import DailyParams, DailyTransport
from pipecat.turns.user_turn_strategies import (
    ExternalUserTurnStrategies,
    TranscriptionUserTurnStartStrategy,
    TurnAnalyzerUserTurnStopStrategy,
    UserTurnStrategies,
    VADUserTurnStartStrategy,
)
from pipecat.utils.text.markdown_text_filter import MarkdownTextFilter

load_dotenv(override=True)


async def run_bot(transport: BaseTransport, user_token: str | None = None,
                  runtime_session_id: str | None = None):
    logger.info("Starting voice analytics bot")

    use_sagemaker = os.getenv("USE_SAGEMAKER", "false").lower() in ("true", "1", "yes")
    use_flux = os.getenv("USE_FLUX", "false").lower() in ("true", "1", "yes")

    # Speech-to-Text
    if use_flux and use_sagemaker:
        stt = DeepgramFluxSageMakerSTTService(
            endpoint_name=os.getenv("SAGEMAKER_STT_ENDPOINT_NAME", ""),
            region=os.getenv("AWS_REGION", "us-west-2"),
            settings=DeepgramFluxSageMakerSTTService.Settings(min_confidence=0.3),
        )
    elif use_flux:
        stt = DeepgramFluxSTTService(
            api_key=os.getenv("DEEPGRAM_API_KEY", ""),
            settings=DeepgramFluxSTTService.Settings(min_confidence=0.3),
        )
    elif use_sagemaker:
        stt = DeepgramSageMakerSTTService(
            endpoint_name=os.getenv("SAGEMAKER_STT_ENDPOINT_NAME", ""),
            language="multi",
            region=os.getenv("AWS_REGION", "us-west-2"),
        )
    else:
        # English nova-3 with smart formatting + endpointing tuned so a normal
        # sentence ("what unicorns are available this weekend") stays ONE turn
        # instead of being chopped into fragments. language="multi" was much less
        # accurate for English and caused garbled transcripts.
        #
        # utterance_end_ms is the silence Deepgram waits before declaring an
        # utterance over. It was 1200ms, which let a mid-sentence pause
        # ("…pie chart. <pause> For that.") finalize the first part as a SEPARATE
        # utterance — the trailing fragment then started a NEW turn that cancelled
        # the real question. Raised to 2000ms so brief between-clause pauses stay
        # inside ONE utterance. endpointing (silence before finalizing an interim
        # chunk) is raised in step so the two are consistent. Cost: the bot waits
        # ~0.8s longer before responding after you stop talking.
        stt = DeepgramSTTService(
            api_key=os.getenv("DEEPGRAM_API_KEY", ""),
            live_options=LiveOptions(
                model="nova-3",
                language="en-US",
                smart_format=True,
                punctuate=True,
                interim_results=True,
                endpointing=500,        # ms of silence before finalizing a chunk
                utterance_end_ms=2000,  # ms of silence before declaring end-of-turn
            ),
        )

    # Text-to-Speech — strip any markdown the LLM emits despite the voice SOP
    # so Aura-2 doesn't read pipes/asterisks aloud.
    md_filter = MarkdownTextFilter(
        params=MarkdownTextFilter.InputParams(filter_code=True, filter_tables=True)
    )
    if use_sagemaker:
        tts = DeepgramSageMakerTTSService(
            endpoint_name=os.getenv("SAGEMAKER_TTS_ENDPOINT_NAME", ""),
            region=os.getenv("AWS_REGION", "us-west-2"),
            voice=os.getenv("DEEPGRAM_VOICE_ID"),
            text_filters=[md_filter],
        )
    else:
        tts = DeepgramTTSService(
            api_key=os.getenv("DEEPGRAM_API_KEY", ""),
            voice=os.getenv("DEEPGRAM_VOICE_ID", "aura-2-apollo-en"),
            text_filters=[md_filter],
        )

    # AgentCore processor — replaces AWSBedrockLLMService.
    # user_token (the speaking user's Cognito access token, forwarded by the
    # JWT-gated start proxy via the session body) makes AgentCore apply RBAC/RLS
    # for the REAL user. runtime_session_id is the SAME id the text chat uses, so
    # voice + text turns share one AgentCore Memory thread (shared context).
    agent = AnalyticsAgentCoreProcessor(
        agent_arn=os.getenv("AWS_AGENT_ARN", ""),
        token_fn=get_gateway_token,
        aws_region=os.getenv("AWS_REGION", "us-west-2"),
        user_token=user_token,
        session_id=runtime_session_id,
        qualifier=os.getenv("AWS_AGENT_QUALIFIER", "agentic_analytics_endpoint"),
    )

    context = LLMContext()
    # Turn detection. The turn STOPS when a *finalized* transcript arrives and the
    # smart-turn model considers the turn complete. The bug: when you paused
    # mid-question ("…pie chart. <pause> For that."), Deepgram finalized the first
    # clause as its OWN utterance, which ended the turn; the trailing "For that."
    # then arrived as a finalized transcript for a NEW turn and cancelled the real
    # question. The primary fix is the longer Deepgram utterance_end_ms above
    # (2000ms) so a brief between-clause pause stays inside ONE utterance and is
    # never finalized separately.
    #
    # Belt-and-suspenders here: bump the smart-turn analyzer's hard silence cap
    # (SmartTurnParams.stop_secs, default 3.0 -> 4.0) so the model also waits a
    # touch longer before force-ending a turn. VAD stop_secs stays at 1.0 (it
    # tolerates brief pauses for turn START; lowering it would make barge-in
    # twitchier without opening the late-transcript grace window).
    user_params = LLMUserAggregatorParams(
        vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=1.0)),
        user_turn_strategies=UserTurnStrategies(
            start=[VADUserTurnStartStrategy(), TranscriptionUserTurnStartStrategy()],
            stop=[TurnAnalyzerUserTurnStopStrategy(
                turn_analyzer=LocalSmartTurnAnalyzerV3(params=SmartTurnParams(stop_secs=4.0))
            )],
        ),
    )
    if use_flux:
        user_params.user_turn_strategies = ExternalUserTurnStrategies()
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=user_params,
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_aggregator,
            agent,
            tts,
            transport.output(),
            assistant_aggregator,
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Client connected")

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected")
        await task.cancel()

    runner = PipelineRunner(handle_sigint=False)
    await runner.run(task)


async def bot(runner_args: RunnerArguments):
    try:
        from pipecat.audio.filters.krisp_viva_filter import KrispVivaFilter

        krisp_filter = KrispVivaFilter()
    except Exception:
        logger.info("Krisp not available, running without noise cancellation")
        krisp_filter = None

    # The start proxy forwards, in the session body:
    #  - gateway_token: the speaking user's Cognito access token (→ AgentCore
    #    RBAC/RLS for the real user)
    #  - runtimeSessionId: the SAME session id the text chat uses, so voice + text
    #    turns share one AgentCore Memory thread (context carries across both).
    body = getattr(runner_args, "body", None) or {}
    user_token = body.get("gateway_token") if isinstance(body, dict) else None
    runtime_session_id = body.get("runtimeSessionId") if isinstance(body, dict) else None
    if user_token:
        logger.info("Using per-session user token from start body for gateway_token")
    if runtime_session_id:
        logger.info(f"Sharing runtimeSessionId with text chat: {runtime_session_id[:24]}...")

    match runner_args:
        case DailyRunnerArguments():
            transport = DailyTransport(
                runner_args.room_url,
                runner_args.token,
                "Analytics Bot",
                params=DailyParams(
                    audio_in_enabled=True,
                    audio_in_filter=krisp_filter,
                    audio_out_enabled=True,
                ),
            )
        case _:
            logger.error(f"Unsupported runner arguments type: {type(runner_args)}")
            return

    await run_bot(transport, user_token=user_token, runtime_session_id=runtime_session_id)


# ── AgentCore Runtime mode (WebRTC + KVS TURN) ───────────────────────────────
# When VOICE_RUNTIME_MODE=agentcore, this file IS an AgentCore Runtime container:
# BedrockAgentCoreApp serves /invocations + /ping on :8080. The browser POSTs an SDP
# offer (and trickled ICE) to /invocations; the runtime's CustomJWTAuthorizer
# validates the Bearer token at the edge before this entrypoint runs. We mirror the
# official pipecat-examples/deployment/aws-agentcore-webrtc-kvs structure, but keep
# all webrtc/agentcore imports INSIDE this branch so the PCC image (no aiortc /
# bedrock-agentcore) still imports bot.py cleanly.
_VOICE_RUNTIME_MODE = os.getenv("VOICE_RUNTIME_MODE", "").lower()

if _VOICE_RUNTIME_MODE == "agentcore":
    import asyncio

    import boto3
    from bedrock_agentcore.runtime import BedrockAgentCoreApp
    from pipecat.runner.types import SmallWebRTCRunnerArguments
    from pipecat.runner.utils import create_transport
    from pipecat.transports.base_transport import TransportParams
    from pipecat.transports.smallwebrtc.connection import IceServer
    from pipecat.transports.smallwebrtc.request_handler import (
        IceCandidate,
        SmallWebRTCPatchRequest,
        SmallWebRTCRequest,
        SmallWebRTCRequestHandler,
    )

    app = BedrockAgentCoreApp()
    # One handler per container; it tracks the live SmallWebRTCConnection so trickled
    # ICE candidates (separate /invocations POSTs) reach the right peer connection.
    _request_handler: "SmallWebRTCRequestHandler | None" = None
    # Strong refs to the in-flight pipeline tasks so they aren't garbage-collected
    # while running in the background (the offer handler returns before they finish).
    _pipeline_tasks: set = set()

    def get_kvs_ice_servers() -> list:
        """Fetch TURN credentials from Amazon Kinesis Video Streams (managed TURN).

        The runtime is in VPC NetworkMode with no public IP behind a symmetric NAT, so
        direct/STUN WebRTC can't work — a TURN relay is required. KVS provides
        temporary, auto-rotating TURN creds via GetIceServerConfig (no vendor, ~$0.03/mo
        per channel). Creds are fetched HERE (agent-side, via the runtime role) so the
        browser never needs AWS credentials.
        """
        region = os.getenv("AWS_REGION", "us-west-2")
        channel = os.getenv("KVS_CHANNEL_NAME", "voice-agent-turn")
        kvs = boto3.client("kinesisvideo", region_name=region)
        try:
            desc = kvs.describe_signaling_channel(ChannelName=channel)
            channel_arn = desc["ChannelInfo"]["ChannelARN"]
        except kvs.exceptions.ResourceNotFoundException:
            created = kvs.create_signaling_channel(
                ChannelName=channel, ChannelType="SINGLE_MASTER"
            )
            channel_arn = created["ChannelARN"]
        ep = kvs.get_signaling_channel_endpoint(
            ChannelARN=channel_arn,
            SingleMasterChannelEndpointConfiguration={
                "Protocols": ["HTTPS"],
                "Role": "MASTER",
            },
        )
        https_ep = next(
            e["ResourceEndpoint"]
            for e in ep["ResourceEndpointList"]
            if e["Protocol"] == "HTTPS"
        )
        signaling = boto3.client(
            "kinesis-video-signaling", region_name=region, endpoint_url=https_ep
        )
        cfg = signaling.get_ice_server_config(ChannelARN=channel_arn, Service="TURN")
        ice_servers = []
        for s in cfg.get("IceServerList", []):
            turn_uris = [u for u in s.get("Uris", []) if u.startswith("turn:")]
            if turn_uris:
                ice_servers.append(
                    IceServer(
                        urls=turn_uris,
                        username=s.get("Username"),
                        credential=s.get("Password"),
                    )
                )
        return ice_servers

    def _bearer_from_headers(context):
        """Extract the user's Bearer token from the runtime request headers.

        The voice runtime's CustomJWTAuthorizer validates the Cognito access token
        at the edge, and RequestHeaderConfiguration.RequestHeaderAllowlist passes
        `Authorization` through to this container — exactly like the Strands
        analytics agent (unicorn_rental_agent.py). This header IS the user identity
        used for RBAC/RLS, so the signaling proxy no longer duplicates the token in
        the offer body.
        """
        headers = getattr(context, "request_headers", None) or {} if context else {}
        auth = headers.get("Authorization") or headers.get("authorization")
        if auth and auth.startswith("Bearer "):
            return auth[len("Bearer "):].strip()
        return auth.strip() if auth else None

    async def _run_webrtc_session(connection, user_token, runtime_session_id):
        """Run the full pipeline over a SmallWebRTC connection, kept alive on /ping."""
        task_id = app.add_async_task("voice_agent")
        try:
            transport = await create_transport(
                SmallWebRTCRunnerArguments(
                    webrtc_connection=connection,
                    session_id=runtime_session_id,
                ),
                {"webrtc": lambda: TransportParams(
                    audio_in_enabled=True, audio_out_enabled=True,
                )},
            )
            await run_bot(
                transport,
                user_token=user_token,
                runtime_session_id=runtime_session_id,
            )
        finally:
            app.complete_async_task(task_id)

    @app.entrypoint
    async def agentcore_entrypoint(payload, context):
        """WebRTC signaling over /invocations: {"type":"offer"|"ice-candidates", "data":...}.

        For an offer we fetch KVS ICE servers, build the SmallWebRTC peer connection,
        kick off the pipeline, and stream the SDP answer back over SSE between
        ANSWER:START / ANSWER:END sentinels (the SPA reads the {"answer":...} line).
        ICE candidates trickle in as separate POSTs and are applied to the live peer.

        The per-user JWT is read from the request headers (context.request_headers
        ['Authorization'], validated by the runtime's JWT authorizer and passed
        through via the header allowlist) — same as the Strands agent. The shared
        runtimeSessionId still travels in the offer body.
        """
        global _request_handler
        rtype = payload.get("type", "unknown")
        data = payload.get("data") or {}

        if rtype == "offer":
            # The user JWT comes ONLY from the Authorization header (validated by the
            # runtime's JWT authorizer, passed through via the header allowlist) — the
            # same single identity channel the Strands analytics agent uses.
            user_token = _bearer_from_headers(context)
            # Pull our own out-of-band fields OUT of the offer data before building the
            # SmallWebRTCRequest — it only accepts the SDP fields (sdp/type/pc_id/
            # restart_pc) and raises TypeError on any extra key (e.g. runtimeSessionId,
            # which the signaling proxy adds so voice+text share one Memory thread; and
            # a legacy gateway_token that an older proxy might still send).
            _OOB_KEYS = {"runtimeSessionId", "gateway_token"}
            sdp_data = {k: v for k, v in data.items() if k not in _OOB_KEYS}
            runtime_session_id = data.get("runtimeSessionId")
            if runtime_session_id:
                logger.info(f"[voice] offer for session {runtime_session_id[:24]}...")
            ice_servers = get_kvs_ice_servers()
            _request_handler = SmallWebRTCRequestHandler(ice_servers=ice_servers)

            async def _on_connection(connection):
                # Launch the pipeline in the BACKGROUND and return immediately. The
                # SmallWebRTCRequestHandler awaits this callback BEFORE it returns the
                # SDP answer (request_handler.py: `await webrtc_connection_callback`
                # then `get_answer()`), so if we awaited the full pipeline here the
                # answer would never reach the browser until the session ended ~30s
                # later — ICE would time out and the offer POST would 502. Scheduling
                # the pipeline as a task lets the answer flush right away so ICE can
                # negotiate; we keep a reference so the task isn't GC'd mid-run.
                task = asyncio.create_task(
                    _run_webrtc_session(connection, user_token, runtime_session_id)
                )
                _pipeline_tasks.add(task)
                task.add_done_callback(_pipeline_tasks.discard)

            request = SmallWebRTCRequest.from_dict(sdp_data)
            answer = await _request_handler.handle_web_request(
                request=request, webrtc_connection_callback=_on_connection
            )
            yield {"status": "ANSWER:START"}
            yield {"answer": answer}
            yield {"status": "ANSWER:END"}
        elif rtype == "ice-candidates":
            if _request_handler is None:
                yield {"error": "no active connection for ice-candidates"}
                return
            # Strip any out-of-band key (runtimeSessionId / gateway_token) —
            # SmallWebRTCPatchRequest is as strict as SmallWebRTCRequest about kwargs.
            patch = {k: v for k, v in data.items() if k not in ("runtimeSessionId", "gateway_token")}
            patch["candidates"] = [IceCandidate(**c) for c in patch.get("candidates", [])]
            await _request_handler.handle_patch_request(SmallWebRTCPatchRequest(**patch))
            yield {"status": "ok"}
        else:
            yield {"error": f"unknown request type: {rtype}"}


if __name__ == "__main__":
    if _VOICE_RUNTIME_MODE == "agentcore":
        # Serve /invocations + /ping on :8080 (AgentCore Runtime contract).
        app.run()
    else:
        # PCC / laptop dev: Pipecat runner's own /start server (Daily transport).
        from pipecat.runner.run import main

        main()
