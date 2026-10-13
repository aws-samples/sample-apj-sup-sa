# Pipecat: RTVI custom messaging, split spoken/displayed text, fillers, interruptions

Research for the "presenter" voice agent: one agent response is split into (a) spoken voice via
TTS and (b) rich visuals (tables/cards/charts) rendered in the browser, while spoken text is also
partially mirrored as chat text.

**Verified against the Pipecat version actually installed in this repo:**
`server/.venv/lib/python3.12/site-packages/pipecat` — **pipecat-ai 1.3.0**. Symbol names below were
read from that source tree (authoritative), cross-checked with https://docs.pipecat.ai. The
`pipecat-docs` MCP server requires interactive OAuth (user must open the auth URL); could not be
completed autonomously, so findings come from the public docs + the vendored source.

> Important version note: 1.3.0 uses the **new turn-strategy system** (`pipecat.turns.*`). It does
> NOT have the older `StartInterruptionFrame` / `StopInterruptionFrame` / `BotInterruptionFrame` /
> `EmulateUserStartedSpeakingFrame` / `MinWordsInterruptionStrategy` classes that older Pipecat
> tutorials reference. The single frame is `InterruptionFrame`. Don't copy old snippets blindly.

---

## 1. Server → client custom messages (render visuals WITHOUT speaking them)

This is the core mechanism for pushing tables / SQL-approval cards / chart refs to the UI.

### The RTVI processor is auto-created and reachable as `task.rtvi`

`PipelineTask` auto-instantiates an `RTVIProcessor`. The starter `bot.py` already uses it:

```python
@task.rtvi.event_handler("on_client_ready")
async def on_client_ready(rtvi):
    await task.queue_frames([LLMRunFrame()])
```

So `rtvi = task.rtvi`. (`class RTVIProcessor(FrameProcessor)`, in
`pipecat/processors/frameworks/rtvi/processor.py`.)

### Two equivalent ways to send arbitrary structured JSON to the client

**(a) Call the method directly on the processor** (simplest from an event handler / observer):

```python
await task.rtvi.send_server_message({
    "type": "analytics.table",
    "title": "Top yields by region",
    "columns": ["region", "yield", "delta"],
    "rows": [["EMEA", 0.182, "+4%"], ["APAC", 0.131, "-2%"]],
})
```

Source signature:
```python
# pipecat/processors/frameworks/rtvi/processor.py
async def send_server_message(self, data: Any):
    """Send a server message to the client."""
    message = RTVI.ServerMessage(data=data)
    await self._send_server_message(message)
```

**(b) Push an `RTVIServerMessageFrame` from any `FrameProcessor` / observer** — useful when you want
to emit the visual at a precise point in the frame stream:

```python
from pipecat.processors.frameworks.rtvi import RTVIServerMessageFrame

await self.push_frame(RTVIServerMessageFrame(data={
    "type": "analytics.chart",
    "imageUrl": "s3://.../yield_by_region.png",
}))
```

Source (`pipecat/processors/frameworks/rtvi/frames.py`):
```python
@dataclass
class RTVIServerMessageFrame(SystemFrame):
    """A frame for sending server messages to the client."""
    data: Any
```
It is a **`SystemFrame`**, so it is delivered out-of-band and **survives interruptions** — the
visual won't be dropped if the user barges in. Crucially, this frame carries data only; **it never
goes to the TTS service**, so nothing here is spoken. The wire envelope the client sees is
`{ label: "rtvi-ai", type: "server-message", data: <your dict> }`.

### JS / React client receives it

`@pipecat-ai/client-js` — event `RTVIEvent.ServerMessage` / callback `onServerMessage`
("Receives custom messages sent from the server to the client"):

```ts
import { PipecatClient, RTVIEvent } from "@pipecat-ai/client-js";

client.on(RTVIEvent.ServerMessage, (message) => {
  // message.data is the dict you sent from Python
  if (message.data.type === "analytics.table") renderTable(message.data);
  if (message.data.type === "analytics.chart") renderChart(message.data);
});
// or as a constructor callback:
new PipecatClient({ callbacks: { onServerMessage: (m) => render(m) } });
```

React — `useRTVIClientEvent`:
```tsx
import { RTVIEvent } from "@pipecat-ai/client-js";
import { useRTVIClientEvent } from "@pipecat-ai/client-react";

useRTVIClientEvent(RTVIEvent.ServerMessage, useCallback((message) => {
  setVisuals(message.data); // route by message.data.type
}, []));
```

### Richer alternative: the UI-command / job-group family (1.3.0)

1.3.0 adds purpose-built frames for UI orchestration (counterparts of `ui-command` / `ui-job-group`
RTVI messages), pushed downstream and surfaced to the client as `onUICommand` / `onUIJobGroup`:

- `RTVIUICommandFrame(command: str, payload: Any)` — wire shape
  `{label, type: "ui-command", data: {command, payload}}`. Good for "show toast", "navigate",
  "scrollTo", or any app-defined command.
- `RTVIUIJobGroupFrame(data)` — lifecycle envelopes (`UIJobGroupStartedData`, `UIJobUpdateData`,
  `UIJobCompletedData`, `UIJobGroupCompletedData`) — useful to show a "running query…" progress
  card that updates then completes.

For our presenter, plain `send_server_message` / `RTVIServerMessageFrame` with a `type`
discriminator is the simplest and is the recommended pattern; reach for `RTVIUICommandFrame` if you
want the dedicated UI channel.

---

## 2. Client → server custom messages (user clicked "Approve", or typed instead of spoke)

Three patterns; all live in `@pipecat-ai/client-js` client methods.

**(a) Fire-and-forget** — `sendClientMessage(msgType, data?)`:
```ts
client.sendClientMessage("sql.approve", { queryId: "q_42", approved: true });
```
Server handles it via the event handler — note the data shape `msg.type` / `msg.data`:
```python
@task.rtvi.event_handler("on_client_message")
async def on_client_message(rtvi, msg):
    if msg.type == "sql.approve" and msg.data.get("approved"):
        await task.queue_frames([LLMRunFrame()])  # proceed to execute the approved SQL
```
Equivalently, a `FrameProcessor` can match the downstream `RTVIClientMessageFrame`
(`msg_id`, `type`, `data`) that `RTVIProcessor` pushes for every client message.

**(b) Request/response** — `sendClientRequest(msgType, data, timeout=10000): Promise<unknown>`:
```ts
const res = await client.sendClientRequest("get-language", {});
```
Server replies with `await rtvi.send_server_response(msg, {...})` or
`await rtvi.send_error_response(msg, "…")` (or, in a processor, push an `RTVIServerResponseFrame`
with `client_msg=`, plus `data=` or `error=`). Timeout rejects with an `error-response`.

**(c) User typed text instead of speaking** — use the dedicated method `sendText`, NOT a custom
message:
```ts
await client.sendText(content, { run_immediately: true, audio_response: true });
```
- `run_immediately` (default `true`): false → append to context but don't run the bot yet.
- `audio_response` (default `true`): false → **bot bypasses TTS and responds in text only**
  (handy if the typed-chat lane should stay silent).

---

## 3. Splitting spoken (summary) vs displayed (data) text

There are two complementary, idiomatic levers in 1.3.0.

### Lever A — per-frame `skip_tts` on `TextFrame`

`TextFrame` (in `pipecat/frames/frames.py`) carries a `skip_tts` flag:
```python
class TextFrame(DataFrame):
    text: str
    skip_tts: bool | None        # set on the frame → TTS skips this text
    includes_inter_frame_spaces: bool
    append_to_context: bool      # whether it’s added to the LLM context
```
`LLMTextFrame` subclasses `TextFrame`. So a custom `FrameProcessor` placed **between the LLM and the
TTS** can parse the agent's streamed output and route pieces:
- conversational summary → let it flow to TTS normally (spoken + can mirror to chat);
- the table/chart blob → either set `skip_tts=True` so TTS ignores it, or (better) divert it into an
  `RTVIServerMessageFrame` (section 1) and **don't** forward it to TTS at all.

`append_to_context` lets you keep something out of the LLM history too.

### Lever B — `LLMConfigureOutputFrame(skip_tts: bool)` to toggle whole spans

```python
class LLMConfigureOutputFrame(DataFrame):
    """...tell the LLM to generate tokens that should be added to the context
    but not spoken by the TTS service (if one is present in the pipeline)."""
    skip_tts: bool
```
Push `LLMConfigureOutputFrame(skip_tts=True)` before a data-only generation span and
`skip_tts=False` after, to make the LLM emit tokens that hit the context/UI but never the TTS.
(The RTVI processor itself uses this internally to honor the client's `audio_response=false`.)

### Speak text directly without it touching the context — `TTSSpeakFrame`

```python
from pipecat.frames.frames import TTSSpeakFrame   # pipecat/frames/frames.py
await llm.push_frame(TTSSpeakFrame("Here are the top three regions by yield."))
```
`TTSSpeakFrame(text: str, append_to_context: bool | None)` injects text **straight to TTS** to be
spoken, independent of LLM generation, without polluting the LLM context (set/leave
`append_to_context` accordingly).

**Recommended presenter pattern:** have the agent produce a short spoken **summary** (normal LLM
text → TTS, optionally mirrored to chat via `onBotTtsText`/`onBotLlmText`) and emit structured
visuals via `send_server_message` / `RTVIServerMessageFrame` (never to TTS). Use a routing
`FrameProcessor` or have the tool/agent return the two payloads separately so you don't have to
parse a blended stream. To also show the spoken words as chat text, subscribe on the client to
`onBotTtsText` (TTS text chunks) or `onBotLlmText` (LLM text chunks) / `onBotTranscript`.

---

## 4. Filler / "please wait" while a long tool runs

Functions are registered on the LLM service and receive a `FunctionCallParams`:

```python
from pipecat.services.llm_service import FunctionCallParams
from pipecat.frames.frames import TTSSpeakFrame

async def run_analytics(params: FunctionCallParams):
    # speak an interim filler immediately, before the slow work
    await params.llm.push_frame(TTSSpeakFrame("Let me pull that up…"))
    result = await slow_query(params.arguments["question"])   # long backend call
    await params.result_callback(result)                      # real answer → LLM → TTS

llm.register_function(
    "run_analytics", run_analytics,
    cancel_on_interruption=True,   # cancel if user barges in (default True)
    timeout_secs=30.0,
)
```

`FunctionCallParams` fields: `function_name, tool_call_id, arguments, llm, context,
result_callback, app_resources`. The filler is just a `TTSSpeakFrame` pushed through `params.llm`
before the await; the real answer comes back through `params.result_callback(result)`.

**Streaming progress** for very long jobs: call the callback multiple times with
`FunctionCallResultProperties(is_final=False)` for interim updates, then a final call (`is_final`
defaults `True`). Intermediate results are injected into the LLM context as async-tool messages and
keep the call open:
```python
from pipecat.frames.frames import FunctionCallResultProperties
await params.result_callback({"status": "querying"}, properties=FunctionCallResultProperties(is_final=False))
await params.result_callback(final_rows)
```
Set `cancel_on_interruption=False` on registration to make the call **async** (LLM keeps the
conversation moving instead of blocking on the result). Also consider pairing the filler with an
`RTVIUIJobGroupFrame` "running…" card (section 1) for a visual spinner.

Client-side, the RTVIObserver auto-emits function-call lifecycle events you can react to:
`onLLMFunctionCallStarted`, `onLLMFunctionCallInProgress`, `onLLMFunctionCallStopped`. (The old
server-side `handle_function_call` method exists but is **deprecated** in favor of these observer
events.)

---

## 5. Interruption / barge-in (and gating real speech vs noise)

### What fires
- `UserStartedSpeakingFrame` / `UserStoppedSpeakingFrame` (`SystemFrame`s) bound a user turn.
- `InterruptionFrame` (`SystemFrame`) is the single frame that interrupts the pipeline: "discards
  pending DataFrames and ControlFrames", stops the bot speaking, clears queued audio/text.
  (`InterruptionTaskFrame` is the task-level request that gets converted to an `InterruptionFrame`.)
- `RTVIProcessor.interrupt_bot()` → `broadcast_interruption()` lets you trigger one programmatically.
- Client mirrors: `onUserStartedSpeaking` / `onUserStoppedSpeaking` / `onBotStartedSpeaking` /
  `onBotStoppedSpeaking`.

### VAD
`SileroVADAnalyzer` (local, ~1ms/30ms chunk) with `VADParams`:
```python
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
SileroVADAnalyzer(params=VADParams(
    confidence=0.7, start_secs=0.2, stop_secs=0.2, min_volume=0.6))
```
`stop_secs` is the key turn-taking knob (silence before "user stopped").

### Gating whether an interruption is "real" — the turn-START strategy (THIS is the hook)

In 1.3.0 the decision of whether detected speech actually starts a turn / interrupts the bot lives
in **user-turn-start strategies** (`pipecat.turns.user_start`), configured via
`LLMUserAggregatorParams`. Every strategy has `enable_interruptions: bool = True` on its base
(`BaseUserTurnStartStrategy`): "emit an interruption frame when the turn starts."

The most relevant for noise-rejection is **`MinWordsUserTurnStartStrategy`** — require N words before
a barge-in counts, so short noises/affirmations ("uh", "mm") don't kill the bot mid-sentence:
```python
from pipecat.turns.user_start import MinWordsUserTurnStartStrategy
strategy = MinWordsUserTurnStartStrategy(min_words=3, use_interim=True)
```
Source behavior: `min_words = self._min_words if self._bot_speaking else 1` — i.e. **the threshold
only applies while the bot is speaking** (one word starts a turn when the bot is silent), exactly
the "is this a real interruption?" gate. Other strategies in `pipecat.turns.user_start`:
`VADUserTurnStartStrategy`, `TranscriptionUserTurnStartStrategy`, `WakePhraseUserTurnStartStrategy`,
`KrispVivaIPUserTurnStartStrategy`, `ExternalUserTurnStartStrategy`. To disable barge-in entirely:
`VADUserTurnStartStrategy(enable_interruptions=False)`.

To fully suppress user input for a span (e.g. while reading a critical sentence), a **mute strategy**
emits `UserMuteStartedFrame`, after which the user aggregator "drops incoming user frames (audio,
transcription, interruption)".

### Deepgram Flux turn-taking
Flux does endpointing/turn detection itself. The starter wires it via `ExternalUserTurnStrategies`
(`pipecat.turns.user_turn_strategies`) so Pipecat defers turn boundaries to Flux instead of local
VAD timing:
```python
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService
from pipecat.turns.user_turn_strategies import ExternalUserTurnStrategies

stt = DeepgramFluxSTTService(api_key=..., settings=DeepgramFluxSTTService.Settings(min_confidence=0.3))
user_params = LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer())
if use_flux:
    user_params.user_turn_strategies = ExternalUserTurnStrategies()
```
With Flux, lean on `min_confidence` and Flux's own end-of-turn signals for gating rather than
`stop_secs` / Silero timing. (`FilterIncompleteUserTurnStrategies` also exists for filtering
incomplete turns.)

---

## Key symbols (quick index)

| Need | Symbol / import |
|---|---|
| Send visual to UI (method) | `task.rtvi.send_server_message(data)` |
| Send visual to UI (frame) | `RTVIServerMessageFrame(data=...)` — `pipecat.processors.frameworks.rtvi` |
| Dedicated UI command | `RTVIUICommandFrame(command, payload)` / job-group frames |
| Client→server fire-forget | JS `sendClientMessage(type, data)` → `@rtvi.event_handler("on_client_message")` |
| Client→server req/resp | JS `sendClientRequest(...)` → `rtvi.send_server_response/.send_error_response` |
| User typed text | JS `sendText(content, {run_immediately, audio_response})` |
| Client receives visual | JS `RTVIEvent.ServerMessage` / `onServerMessage`; React `useRTVIClientEvent` |
| Skip TTS per text | `TextFrame.skip_tts`; span: `LLMConfigureOutputFrame(skip_tts=True)` |
| Speak directly | `TTSSpeakFrame(text)` (push via `params.llm`/`task`) |
| Function handler | `register_function(name, fn, cancel_on_interruption=, timeout_secs=)`; `FunctionCallParams.result_callback` |
| Interim/async results | `FunctionCallResultProperties(is_final=False)` |
| Interruption frame | `InterruptionFrame` (`SystemFrame`); `rtvi.interrupt_bot()` |
| Gate interruptions | `MinWordsUserTurnStartStrategy(min_words=N)` / `enable_interruptions` on start strategy |
| Flux turn-taking | `ExternalUserTurnStrategies()` on `LLMUserAggregatorParams.user_turn_strategies` |

Installed: pipecat-ai 1.3.0 (`server/.venv`). Starter: `resources/aws-deepgram-sa-hackathon/server/bot.py`.
