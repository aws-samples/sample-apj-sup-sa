# Research: Generating charts in AgentCore Code Interpreter and surfacing them to the UI

Question: How can a Strands agent on Bedrock AgentCore Runtime generate **charts** via the
built-in **Code Interpreter** tool, and get the chart image into a **browser UI** (Pipecat/RTVI
voice client)?

Sources: `bedrock-agentcore-mcp-server` docs tools, `aws-knowledge` AWS docs, Strands tools
GitHub source, Pipecat custom-messaging docs, and the vendored example
`resources/pipecat-aws-agentcore-example/agents/code_agent.py`.

---

## 1. Code Interpreter as a Strands tool

The Strands community tools package (`strands-agents-tools`, importable as `strands_tools`) ships
an AgentCore Code Interpreter wrapper. The vendored example uses it exactly like this:

```python
# resources/pipecat-aws-agentcore-example/agents/code_agent.py
from strands import Agent
from strands_tools.code_interpreter import AgentCoreCodeInterpreter

code_interpreter = AgentCoreCodeInterpreter(region=REGION, auto_create=True)

agent = Agent(
    model=MODEL_ID,
    system_prompt="...",
    tools=[code_interpreter.code_interpreter],   # <-- the bound tool method is what's passed
)
```

Key points on the wiring:
- You instantiate `AgentCoreCodeInterpreter(region=..., auto_create=True)`.
- You pass the **bound method** `code_interpreter.code_interpreter` (NOT the instance) into
  `Agent(tools=[...])`. That method is the `@tool`-decorated entrypoint the LLM calls.
- `auto_create=True` lets the wrapper create/reuse the managed `aws.codeinterpreter.v1`
  interpreter resource automatically.
- The AWS quickstart shows the same pattern (without `auto_create`):
  `from strands_tools.code_interpreter import AgentCoreCodeInterpreter` then
  `AgentCoreCodeInterpreter(region="us-west-2")` and `tools=[code_interpreter_tool.code_interpreter]`.

Install: `pip install bedrock-agentcore strands-agents strands-agents-tools`.

### Actions the tool exposes (one `code_interpreter` tool, dispatched by a `type` discriminator)

From the Strands source (`strands_tools/code_interpreter/models.py`,
`agent_core_code_interpreter.py`), the single tool accepts a `CodeInterpreterInput` union with
8 action models (discriminated by `type`):

| `type` value        | Model                     | Key fields |
|---------------------|---------------------------|-----------|
| `initSession`       | `InitSessionAction`       | `description`, `session_name?` |
| `listLocalSessions` | `ListLocalSessionsAction` | — |
| `executeCode`       | `ExecuteCodeAction`       | `code`, `language`(=`PYTHON`/`JAVASCRIPT`/`TYPESCRIPT`), `clear_context`, `session_name` |
| `executeCommand`    | `ExecuteCommandAction`    | `command`, `session_name` |
| `readFiles`         | `ReadFilesAction`         | `paths: list`, `session_name` |
| `listFiles`         | `ListFilesAction`         | `path` (default `"."`), `session_name` |
| `removeFiles`       | `RemoveFilesAction`       | `paths: list`, `session_name` |
| `writeFiles`        | `WriteFilesAction`        | `content: list[FileContent]`, `session_name` |

`FileContent` has `path` + exactly one of `text` or `blob` (validator enforces XOR). The
underlying raw client calls map to the AgentCore data-plane verbs `executeCode`, `executeCommand`,
`readFiles`, `writeFiles`, `listFiles`, `removeFiles` (these are the `name` values on
`invoke_code_interpreter`).

---

## 2. Generating a chart

The managed runtime (`aws.codeinterpreter.v1`) ships with common data/plotting libraries
**pre-installed: numpy, pandas, matplotlib** (and more). So the agent just writes Python that
renders a figure and saves it to a file in the sandbox:

```python
import matplotlib
matplotlib.use("Agg")          # headless backend
import matplotlib.pyplot as plt

plt.bar(["Fund A", "Fund B", "Fund C"], [8.2, 3.1, 6.7])
plt.title("YTD yield by fund")
plt.savefig("chart.png", format="png", dpi=120, bbox_inches="tight")
# optionally also emit base64 to stdout for small images:
import base64
with open("chart.png","rb") as f:
    print("IMG_B64:" + base64.b64encode(f.read()).decode())
```

### What `executeCode` returns

`executeCode` returns a **streamed result** with `stdout`, `stderr`, `exit_code`/`is_error`, and a
`content` array of typed items (`{"type":"text", "text": ...}`). The MCP `execute_code` tool
surfaces this as `ExecutionResult(stdout, stderr, exit_code, is_error, content, message)`.

It does **NOT** automatically return the saved PNG as a file artifact or as image bytes. Anything
written to disk stays in the sandbox filesystem. There are two ways to get the bytes out:

**A. `readFiles` (inline, base64).** Call the tool again with `readFiles paths=["chart.png"]`
(MCP equivalent: `download_file(session_id, path="chart.png")`). Binary file content comes back
base64-encoded. Inline transfer is capped at **100 MB** — far more than any PNG needs.

  Caveat for the Strands `code_interpreter` tool specifically: its `_create_tool_result` coerces
  the result to text via `"content":[{"text": str(result.get("content"))}]`. That is fine when the
  *agent/LLM* reads files, but it means binary bytes round-trip through a stringified blob, which
  is awkward to consume programmatically. For clean bytes, prefer either (a) printing base64 to
  **stdout** from inside `executeCode` and parsing it from `stdout`, or (b) bypassing the Strands
  tool and using the lower-level client `CodeInterpreter.invoke("readFiles", {"paths":[...]})` /
  the MCP `download_file`, which return the base64 blob directly.

**B. S3 via terminal command (large / out-of-band).** A custom Code Interpreter with an execution
role can `aws s3 cp chart.png s3://bucket/...` from `executeCommand`. Supports up to **5 GB**.
Overkill for a PNG but it is the documented "large file" path and gives you a durable object to
presign.

Recommended chart-extraction pattern for this project: render to PNG, then `print` a base64 string
with a sentinel prefix to **stdout**, and read it off `stdout` in the orchestration layer. This
avoids both the Strands stringify issue and an extra `readFiles` round-trip.

---

## 3. Surfacing the image to the UI

Options:

| Option | Mechanism | Pros | Cons |
|--------|-----------|------|------|
| (a) RTVI server message | `RTVIServerMessageFrame` / `rtvi.send_server_message({...})`; client receives via `RTVIEvent.ServerMessage` / `onServerMessage` | Single channel already open for the voice app; no extra infra; React hook `useRTVIClientEvent(RTVIEvent.ServerMessage, ...)` | Travels over the WebRTC **data channel** as JSON — keep payload modest |
| (b) S3 + presigned URL | upload from sandbox (S3 terminal cmd) or from the bot; send the URL in an RTVI message | Tiny message; no data-channel bloat; handles huge images | Needs S3 bucket + IAM + presign; extra latency/round-trip; URL expiry |
| (c) data URI | `data:image/png;base64,...` embedded in the server message; `<img src=...>` | Dead simple in the browser; no storage | Same size concern as (a); duplicates base64 in the message |

### Recommended for a Pipecat / RTVI voice UI

For a hackathon analytics chart, **(a) RTVI server message carrying a base64 PNG as a data URI**
is the cleanest: it reuses the connection the voice app already has, needs zero extra AWS infra,
and the React client just drops the data URI into an `<img>`.

Server side (in the bot, after the agent run / tool completes):
```python
from pipecat.frames.frames import RTVIServerMessageFrame   # or rtvi.send_server_message(...)
await rtvi.send_server_message({
    "type": "analytics-chart",
    "mime": "image/png",
    "data_uri": f"data:image/png;base64,{b64}",
})
```
Client (React):
```tsx
useRTVIClientEvent(RTVIEvent.ServerMessage, (m) => {
  if (m.data.type === "analytics-chart") setChartSrc(m.data.data_uri);
});
```

**Size note / when to switch to (b):** RTVI messages go over the WebRTC data channel. Base64 adds
~33% overhead. A typical 120-dpi matplotlib chart PNG is roughly 30–150 KB (~40–200 KB encoded),
which the data channel handles fine. Keep individual messages well under ~1 MB; large or
multi-megapixel/high-dpi charts should switch to **(b) S3 presigned URL** (send only the short URL
in the RTVI message). The Code Interpreter inline file limit (100 MB) is not the binding
constraint here — the data-channel message size is. To stay small: `dpi<=120`, PNG over SVG for
photos/dense plots (SVG can be smaller for simple line charts and is also fine to inline as text).

Also: per CLAUDE.md, the chart is a *visual side-channel* — the TTS output should still be a short
spoken summary; do not read the chart data aloud.

---

## 4. Latency and session reuse

- **Session start-up:** AgentCore advertises "low-latency session start-up." Empirically a cold
  `start_code_interpreter_session` (`StartCodeInterpreterSession`) is on the order of ~1–3 s; first
  `executeCode` that imports matplotlib and renders a simple figure is typically another ~1–3 s.
  So a cold chart on a fresh session is roughly **2–6 s**; a warm session render is **sub-second to
  ~1 s** for a simple plot. (No exact published number; treat as an estimate to validate.)
- **Session reuse — yes, and you should.** A session persists until `stopCodeInterpreterSession`
  or its `sessionTimeoutSeconds` (default **900 s / 15 min**, max **28,800 s / 8 h**). Execution
  context (imports, variables, files) **persists across `executeCode` calls** in the same session
  unless `clear_context=True`. The Strands `AgentCoreCodeInterpreter` keeps the session in
  `self._sessions[session_name]` and reuses it; you can pin a `session_name` across turns.
- **Implication for the voice agent:** create/reuse one Code Interpreter session for the
  conversation (bind it to the runtime `session_id` like the vendored example binds memory), keep
  matplotlib imported once, so follow-up "now show me Q3 only" re-renders are warm and fast. This
  matters because the CLAUDE.md latency risk is real — voice users feel multi-second gaps.

### IAM (for reference)
`bedrock-agentcore:{Create,Start,Invoke,Stop,Delete,List,Get}CodeInterpreter*` on
`arn:aws:bedrock-agentcore:<region>:<acct>:code-interpreter/*`, plus Bedrock model access. S3
option additionally needs a custom interpreter execution role trusting
`bedrock-agentcore.amazonaws.com` with S3 read/write.

---

## Symbol cheat-sheet
- `from strands_tools.code_interpreter import AgentCoreCodeInterpreter`
- `AgentCoreCodeInterpreter(region=..., auto_create=True)` → `.code_interpreter` (pass to `Agent(tools=[...])`)
- Actions: `initSession`, `executeCode`, `executeCommand`, `readFiles`, `writeFiles`, `listFiles`, `removeFiles`
- Raw client: `from bedrock_agentcore.tools.code_interpreter_client import CodeInterpreter`; `.start()`, `.invoke("executeCode"/"readFiles", params)`, `.stop()`
- boto3: `client.start_code_interpreter_session(codeInterpreterIdentifier="aws.codeinterpreter.v1", sessionTimeoutSeconds=900)`, `client.invoke_code_interpreter(name="executeCode"|"readFiles", arguments=...)`, `client.stop_code_interpreter_session(...)`
- Pipecat: `RTVIServerMessageFrame(data={...})` / `await rtvi.send_server_message({...})`; client `RTVIEvent.ServerMessage` / `onServerMessage`
- Limits: inline file 100 MB; S3 file 5 GB; session default 900 s, max 28,800 s; pre-installed numpy/pandas/matplotlib
