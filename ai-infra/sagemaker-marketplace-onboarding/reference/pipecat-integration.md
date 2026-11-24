# Pipecat Orchestration Support — Reference

Pipecat is an open-source Python framework for voice AI pipelines (VAD → STT → LLM → TTS →
transport). It has first-class support for consuming models deployed on SageMaker bidirectional
streaming endpoints — the exact container contract this skill scaffolds in Phase 4-6.

Source: AWS ML blog ["Deploy voice agents with Pipecat and Amazon SageMaker AI bidirectional
streaming"](https://d1im9ux8shqxn5.cloudfront.net/blog-ml-21563/) and the real Pipecat source
(`pipecat-ai/pipecat`, `src/pipecat/services/aws/sagemaker/` and
`src/pipecat/services/deepgram/sagemaker/`).

This file covers two things: (1) how Pipecat's client-side architecture maps onto the container
you just built, so you understand what you need to expose for it to be wrappable; and (2) how to
get your model listed in the Pipecat ecosystem once your container works.

## Scope check — is this relevant to you?

Only relevant if:
- Modality is STT or TTS (or an LLM served over bidirectional streaming), **and**
- You selected Bidirectional WebSocket in Phase 2.

Skip this entirely for Real-Time/Batch/Streaming-response-only containers — Pipecat's SageMaker
integration is specifically for the `/invocations-bidirectional-stream` contract.

## How Pipecat wraps a SageMaker bidi endpoint — three layers

### Layer 1: `SageMakerBidiClient` (already built, reusable, nothing for you to do here)

A generic HTTP/2 client Pipecat ships in core. Handles SigV4 auth, session lifecycle, and
binary/text frame handling against `runtime.sagemaker.<region>.amazonaws.com:8443`. Any model on
any SageMaker bidi endpoint uses the *same* client:

```python
from pipecat.services.aws.sagemaker.bidi_client import SageMakerBidiClient

client = SageMakerBidiClient(
    endpoint_name="my-endpoint",
    region="us-east-2",
    model_invocation_path="v1/speak",   # → your container's X-Amzn-SageMaker-Model-Invocation-Path
    model_query_string="voice=my-voice&sample_rate=16000",
)
await client.start_session()
await client.send_json({"type": "Speak", "text": "Hello"})
response = await client.receive_response()
await client.close_session()
```

This is why the routing header matters (see below) — `model_invocation_path` is how one Pipecat
service targets a specific logical API on your container.

### Layer 2: a service wrapper — this is the part you (the model provider) write

A subclass of Pipecat's `STTService` or `TTSService` that translates Pipecat's generic interface
(`run_stt(audio)` / `run_tts(text)`) into **your container's own WebSocket protocol**. This is
the piece that doesn't exist yet for a new model — everything else (transport, VAD, LLM,
pipeline wiring) is already generic in Pipecat.

Real example — `DeepgramSageMakerTTSService` (`pipecat/services/deepgram/sagemaker/tts.py`):

```python
async def run_tts(self, text: str, context_id: str):
    await self._client.send_json({"type": "Speak", "text": text})
    yield None   # audio arrives asynchronously via a background response-processing task

async def on_audio_context_interrupted(self, context_id: str):
    # Barge-in: caller started speaking, discard whatever we were synthesizing
    await self._client.send_json({"type": "Clear"})

async def flush_audio(self, context_id: str | None = None):
    # LLM turn finished — force synthesis of whatever text is buffered
    await self._client.send_json({"type": "Flush"})
```

A background task continuously reads from the BiDi stream, tells JSON control frames
(`Metadata`, `Flushed`, `Cleared`, `Warning`) apart from raw binary audio bytes by attempting a
UTF-8/JSON decode first, and routes each to the right handler.

The STT counterpart (`DeepgramSageMakerSTTService`, `pipecat/services/deepgram/sagemaker/stt.py`)
follows the same shape: `run_stt(audio)` calls `client.send_audio_chunk(audio)`; a background
task parses transcription JSON and pushes `TranscriptionFrame`/`InterimTranscriptionFrame`; a
second background task sends `{"type": "KeepAlive"}` every 5 seconds during silence; and on
`VADUserStoppedSpeakingFrame` it sends `{"type": "Finalize"}` to flush a final transcript.

### Layer 3: pipeline integration (already generic — nothing for you to do)

A factory picks your service class at runtime based on config; the rest of the Pipecat pipeline
(VAD, LLM, transport, barge-in handling) doesn't know or care which vendor's model it's calling.

## What this means for the container you just built

Pipecat's service wrapper is only possible because the container implements a **control-message
vocabulary** on top of the raw `/invocations-bidirectional-stream` contract — not just raw audio
frames. Deepgram's containers accept JSON text frames like `{"type": "Speak", ...}`,
`{"type": "Flush"}`, `{"type": "Clear"}`, `{"type": "Close"}`, `{"type": "KeepAlive"}`, and (STT)
`{"type": "Finalize"}`, interleaved with binary audio frames.

If you want your model to be easily wrappable by Pipecat (or any other voice-orchestration
framework doing the same job), design your container's WebSocket protocol with equivalents for:

| Need | Deepgram's pattern | Why an orchestrator needs it |
|---|---|---|
| Start synthesis / send input | `{"type": "Speak", "text": "..."}` (TTS) or raw binary audio frames (STT) | The one required message — everything else is optional polish |
| Force flush of buffered output | `{"type": "Flush"}` | Called when the LLM turn ends so TTS doesn't wait for more text before speaking |
| Cancel in-flight output (barge-in) | `{"type": "Clear"}` | Called the instant VAD detects the caller interrupting — without this, the orchestrator can't stop your model mid-sentence |
| Keep an idle connection alive | `{"type": "KeepAlive"}` every 5s | Prevents your own container (or any intermediate proxy) from timing out during silence |
| Force a final result on turn-end | `{"type": "Finalize"}` (STT only) | Lets the orchestrator get a clean final transcript exactly when VAD says the caller stopped talking, instead of waiting on your model's own endpointing |
| Graceful session end | `{"type": "Close"}` / `{"type": "CloseStream"}` | Lets the client signal intent before disconnecting, vs. a hard close |

None of this is a hard SageMaker Marketplace requirement — the container contract (Phase 5/6)
doesn't mandate a specific message vocabulary. This is purely an **interoperability** design
choice: it's what makes a Pipecat (or similar) integration one afternoon of work instead of a
custom protocol negotiation. Mention this table if the user asks "how do I make my container
Pipecat-compatible" during Phase 5.

### Routing multiple logical APIs on one container

If your container serves more than one logical model API (e.g. `/v1/listen` for STT and
`/v1/speak` for TTS on the same endpoint, mirroring how `model_invocation_path` is used above),
read `X-Amzn-SageMaker-Model-Invocation-Path` on the WebSocket upgrade request and route to the
right internal handler. See `websocket.md`'s note on this header, and
`templates/websocket_handler.py` for where to add the branch.

## Getting your model into the Pipecat ecosystem

Once your container passes Phase 8's local testing gate, you have two paths to make it usable by
Pipecat users. **Ask the user which one fits** before doing anything — this is a real decision
with different requirements, not a formality.

### Path A — Community Integration (the realistic default for most providers)

Pipecat's own contribution guide is explicit: *"We encourage community-maintained integrations!"*
and *"the Pipecat team does not code review, test, or maintain community integrations."* This is
a **separate repository you own**, listed for discoverability — not a PR into Pipecat's own
codebase. Lower barrier, faster to ship, and the standard route unless your model is significant
enough that the Pipecat maintainers would want it in core (that's their call, not something to
assume).

Requirements (from Pipecat's `COMMUNITY_INTEGRATIONS.md`), your own repo must contain:
- Full source implementation following Pipecat's service patterns (subclass `STTService` or
  `TTSService`; see Layer 2 above)
- A foundational single-file usage example
- `README.md` with: intro, install instructions, Pipecat pipeline usage, how to run the example,
  tested Pipecat version, and **company attribution** if you work for the company providing the
  model (builds confidence the integration will be maintained)
- A permissive `LICENSE` (BSD-2 like Pipecat, or equivalent)
- Docstrings following Pipecat's docstring conventions
- A changelog

Then, separately, **submit docs into `pipecat-ai/docs`**: a row on the Supported Services page
with a `Community` maintainer badge, plus a dedicated service page (installation, prerequisites,
configuration, minimal usage example, compatibility note) — copy an existing community page as a
starting point rather than writing from scratch. Include a ~30-60s demo video link in that PR
description showing core functionality and an interruption/barge-in handling if applicable.
Announce it in the `#community-integrations` Discord channel after submitting: https://discord.gg/pipecat

### Path B — Core PR into `pipecat-ai/pipecat`

Only appropriate if the model is broadly relevant enough that Pipecat's maintainers would want to
maintain it directly (this is their judgment call — flag it as a possibility to the user, don't
promise it'll be accepted). Standard OSS contribution flow per `CONTRIBUTING.md`:

1. Fork `pipecat-ai/pipecat`, clone your fork.
2. Branch: `git checkout -b <feature-branch-name>`.
3. Implement your service wrapper under `src/pipecat/services/<provider>/sagemaker/` (or
   `<provider>/` if not SageMaker-specific), following the patterns in Layer 2 above and the real
   `deepgram/sagemaker/{stt,tts}.py` files as a structural reference.
4. Add a changelog fragment: `changelog/<PR_number>.added.md` (a Markdown bullet describing the
   addition — PR number isn't known until the PR is opened, so this typically gets added/renamed
   in the same PR).
5. Commit, push to your fork, open a PR against `pipecat-ai/pipecat` `main`, with a clear
   description of what the integration does.

### What this skill does — and doesn't — help with

This is documentation only for now — see the illustrative appendix below for what a service
wrapper looks like structurally. This skill does **not** currently scaffold a live
`templates/pipecat_service_stub.py` file into Phase 4's output, and does not scaffold the full
community-integration repo (README, LICENSE, changelog, docs-site PR) or open the PR on your
behalf — those are decisions and content only you can make (attribution, licensing terms, which
repo to host it in, whether you want to commit to maintaining it). Treat the checklist above as
the punch list to walk through with the user, the same way Phase 3's gap analysis is a punch
list, not an auto-fix. If a future revision of this skill promotes the appendix into a real
scaffolded template, it slots into Phase 4's opt-in tree the same way `websocket_handler.py` and
`metering.py` already do.

## Appendix: illustrative service-wrapper example (not a scaffolded template)

The following is a **structural example**, not code this skill generates or copies anywhere. It
shows the shape a Pipecat `TTSService`/`STTService` subclass takes when wrapping a SageMaker
bidi endpoint, adapted from the real `DeepgramSageMakerTTSService`/`STTService` implementations
in `pipecat-ai/pipecat` (`src/pipecat/services/deepgram/sagemaker/{tts,stt}.py`). Every `TODO`
marks a point where the real implementation is Deepgram-specific and yours would differ. Read
this to *decide* whether the pattern is worth adopting for your model — it is not meant to be
copy-pasted as-is.

This code lives in a **separate Pipecat-integration repository or PR**, never in your model
container — the container only ever speaks the WebSocket protocol Phase 5/6 scaffolds; this class
is the *client-side* translator that a Pipecat pipeline loads.

```python
"""
Illustrative only. Requires (in the Pipecat integration's own environment,
NOT your model container's runtime): uv add "pipecat-ai[sagemaker]"
"""
import asyncio
import json
from collections.abc import AsyncGenerator

from loguru import logger
from pipecat.frames.frames import (
    CancelFrame, EndFrame, ErrorFrame, Frame,
    InterimTranscriptionFrame, TranscriptionFrame, TTSAudioRawFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessorSetup
from pipecat.services.aws.sagemaker.bidi_client import SageMakerBidiClient
from pipecat.services.stt_service import STTService
from pipecat.services.tts_service import TTSService
from pipecat.utils.time import time_now_iso8601

# TODO: model_invocation_path resolves to X-Amzn-SageMaker-Model-Invocation-Path
# on your container's WebSocket upgrade request (see the routing note above).
TTS_INVOCATION_PATH = "v1/speak"
STT_INVOCATION_PATH = "v1/listen"


class YourModelSageMakerTTSService(TTSService):
    def __init__(self, *, endpoint_name: str, region: str, voice: str, **kwargs):
        super().__init__(**kwargs)
        self._endpoint_name = endpoint_name
        self._region = region
        self._voice = voice
        self._client: SageMakerBidiClient | None = None
        self._response_task: asyncio.Task | None = None

    async def setup(self, setup: FrameProcessorSetup):
        await super().setup(setup)
        await self._connect()

    async def cleanup(self):
        await super().cleanup()
        await self._disconnect()

    async def stop(self, frame: EndFrame):
        await super().stop(frame)
        await self._disconnect()

    async def cancel(self, frame: CancelFrame):
        await super().cancel(frame)
        await self._disconnect()

    async def _connect(self):
        # TODO: build your query string from voice/sample_rate/encoding etc.
        query_string = f"voice={self._voice}&sample_rate={self.sample_rate}"
        self._client = SageMakerBidiClient(
            endpoint_name=self._endpoint_name,
            region=self._region,
            model_invocation_path=TTS_INVOCATION_PATH,
            model_query_string=query_string,
        )
        try:
            await self._client.start_session()
            self._response_task = self.create_task(self._process_responses())
        except Exception as e:
            await self.push_error(error_msg=f"Connection failed: {e}", exception=e)

    async def _disconnect(self):
        if not (self._client and self._client.is_active):
            return
        try:
            # TODO: your container's graceful-close control message
            await self._client.send_json({"type": "Close"})
        except Exception as e:
            logger.warning(f"close failed: {e}")
        if self._response_task and not self._response_task.done():
            await self.cancel_task(self._response_task)
        await self._client.close_session()

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None, None]:
        # Audio arrives asynchronously via _process_responses() -- this
        # method just kicks off the request.
        if self._client is None:
            yield ErrorFrame(error="client unavailable")
            return
        try:
            # TODO: your container's "synthesize this text" control message
            await self._client.send_json({"type": "Speak", "text": text})
            yield None
        except Exception as e:
            yield ErrorFrame(error=f"run_tts failed: {e}")

    async def flush_audio(self, context_id: str | None = None):
        # Called when the LLM turn ends -- force synthesis of buffered text.
        if self._client and self._client.is_active:
            try:
                await self._client.send_json({"type": "Flush"})  # TODO
            except Exception as e:
                logger.error(f"flush_audio failed: {e}")

    async def on_audio_context_interrupted(self, context_id: str):
        # Barge-in: caller started speaking, cancel in-flight synthesis.
        if self._client and self._client.is_active:
            try:
                await self._client.send_json({"type": "Clear"})  # TODO
            except Exception as e:
                logger.error(f"interruption handling failed: {e}")
        await super().on_audio_context_interrupted(context_id)

    async def _process_responses(self):
        try:
            while self._client and self._client.is_active:
                result = await self._client.receive_response()
                if result is None:
                    break
                payload = getattr(getattr(result, "value", None), "bytes_", None)
                if not payload:
                    continue
                try:
                    parsed = json.loads(payload.decode("utf-8"))
                    logger.trace(f"control message: {parsed}")  # TODO: handle types
                except (UnicodeDecodeError, json.JSONDecodeError):
                    context_id = self.get_active_audio_context_id()
                    frame = TTSAudioRawFrame(payload, self.sample_rate, 1, context_id=context_id)
                    await self.append_to_audio_context(context_id, frame)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            await self.push_error(error_msg=f"response processing failed: {e}", exception=e)


class YourModelSageMakerSTTService(STTService):
    def __init__(self, *, endpoint_name: str, region: str, **kwargs):
        super().__init__(**kwargs)
        self._endpoint_name = endpoint_name
        self._region = region
        self._client: SageMakerBidiClient | None = None
        self._response_task: asyncio.Task | None = None
        self._keepalive_task: asyncio.Task | None = None
        self._user_id = ""

    async def setup(self, setup: FrameProcessorSetup):
        await super().setup(setup)
        await self._connect()

    async def stop(self, frame: EndFrame):
        await super().stop(frame)
        await self._disconnect()

    async def cancel(self, frame: CancelFrame):
        await super().cancel(frame)
        await self._disconnect()

    async def cleanup(self):
        await super().cleanup()
        await self._disconnect()

    async def _connect(self):
        # TODO: build your query string from model/language/sample_rate etc.
        query_string = f"sample_rate={self.sample_rate}"
        self._client = SageMakerBidiClient(
            endpoint_name=self._endpoint_name,
            region=self._region,
            model_invocation_path=STT_INVOCATION_PATH,
            model_query_string=query_string,
        )
        try:
            await self._client.start_session()
            self._response_task = self.create_task(self._process_responses())
            # Only start a keepalive loop if your container's protocol needs
            # one -- check the control-message table above.
            self._keepalive_task = self.create_task(self._send_keepalive())
        except Exception as e:
            await self.push_error(error_msg=f"Connection failed: {e}", exception=e)

    async def _disconnect(self):
        if self._client and self._client.is_active:
            try:
                await self._client.send_json({"type": "CloseStream"})  # TODO
            except Exception as e:
                logger.warning(f"close failed: {e}")
            if self._keepalive_task and not self._keepalive_task.done():
                await self.cancel_task(self._keepalive_task)
            if self._response_task and not self._response_task.done():
                await self.cancel_task(self._response_task)
            await self._client.close_session()

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame | None, None]:
        if self._client and self._client.is_active:
            try:
                await self._client.send_audio_chunk(audio)
            except Exception as e:
                yield ErrorFrame(error=f"run_stt failed: {e}")
        yield None

    async def _send_keepalive(self):
        while self._client and self._client.is_active:
            await asyncio.sleep(5)
            if self._client and self._client.is_active:
                try:
                    await self._client.send_json({"type": "KeepAlive"})  # TODO
                except Exception as e:
                    logger.warning(f"keepalive failed: {e}")

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, VADUserStoppedSpeakingFrame):
            # Caller stopped talking -- ask for a final result now rather
            # than waiting on the model's own endpointing, if supported.
            if self._client and self._client.is_active:
                try:
                    await self._client.send_json({"type": "Finalize"})  # TODO
                except Exception as e:
                    logger.warning(f"finalize failed: {e}")

    async def _process_responses(self):
        try:
            while self._client and self._client.is_active:
                result = await self._client.receive_response()
                if result is None:
                    break
                payload = getattr(getattr(result, "value", None), "bytes_", None)
                if not payload:
                    continue
                try:
                    parsed = json.loads(payload.decode("utf-8"))
                except json.JSONDecodeError:
                    logger.warning(f"non-JSON STT response: {payload!r}")
                    continue
                # TODO: replace with your model's actual response shape.
                # This example assumes {"text": "...", "is_final": bool}.
                transcript = parsed.get("text", "")
                if not transcript.strip():
                    continue
                frame_cls = TranscriptionFrame if parsed.get("is_final") else InterimTranscriptionFrame
                await self.push_frame(
                    frame_cls(transcript, self._user_id, time_now_iso8601(), None, result=parsed)
                )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            await self.push_error(error_msg=f"response processing failed: {e}", exception=e)
```

**Decision point for the user:** if this pattern looks worth adopting, the next step is to
promote it into a real `templates/pipecat_service_stub.py` scaffolded file (wired into Phase 4's
opt-in tree) — that is a separate, explicit follow-up, not something this doc change does
automatically.

## Reference implementation

The blog's companion repository is a complete, deployable example wiring all of this together —
useful to point a user at if they want to see a production-shaped version (CDK infra, ECS
Fargate, session-aware auto-scaling, CloudWatch dashboard, Claude Code deploy skill) rather than
just the Pipecat service-wrapper piece:
https://github.com/aws-solutions-library-samples/sample-voice-agent
