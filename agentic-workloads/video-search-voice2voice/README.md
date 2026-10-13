# Video Search Voice2Voice — Talk to your Video

Have a real voice conversation with a pre-recorded video. Pick a clip, press the
mic, ask a question out loud, and hear a grounded answer back — with the player
jumping to the moment being discussed.

Every model is Amazon Bedrock, all media and indexes live in one private S3
bucket, and outside knowledge comes from an Amazon Bedrock AgentCore harness.
Nothing is publicly exposed in the default local mode.

| Ask this | What happens |
|---|---|
| *"What is this video about?"* | Answered straight from the video's indexed timeline — no retrieval hop |
| *"Show me where they talk about encryption"* | Semantic + lexical search finds the moment, **the playhead jumps there**, markers appear on the timeline |
| *"What's written on the screen right now?"* | Pulls the actual frames at your playhead and reads them |
| *"Who is that company they mentioned?"* | Hands off to an **AgentCore harness** with live web access |
| *"Which of those did I ask about first?"* | Full conversational memory across the session |

The agent knows where you are in the video. "This bit", "here" and "right now"
resolve to your live playhead position, because every transcription is tagged
with it before the model sees it.

**You own playback.** Nothing ever starts the video for you — opening the mic
does not, and when the agent jumps to a moment it moves the playhead without
touching play/pause, so a paused frame stays paused and stays on screen while you
talk about it. The one thing the app does do is *pause*: opening the mic,
speaking, or typing a question stops the clip so the agent never talks over it.

## Architecture

![Architecture](docs/architecture.png)

```
  ┌──────────────────────────────────────────────┐
  │ Ingest — offline, once per video             │
  │                                              │
  │  mp4 ─▶ ffprobe/ffmpeg: scenes, segments,    │
  │         keyframes                            │
  │           │                                  │
  │           ├─▶ Deepgram Nova-3 (batch STT)    │
  │           ├─▶ TwelveLabs Pegasus 1.2         │
  │           │     overview + chapters          │
  │           ├─▶ Amazon Nova 2 Lite             │
  │           │     visual captions              │
  │           └─▶ Amazon Nova 2 MME              │
  │                 multimodal embeddings        │
  │           │                                  │
  │           ▼                                  │
  │  index.json + .npz vectors ──▶ private S3    │
  └──────────────────────────────────────────────┘

  ┌──────────────────────────────────────────────┐
  │ Runtime — per session, per question          │
  │                                              │
  │  mic ──WebRTC──▶ Deepgram Nova-3 (stream STT)│
  │                        │                     │
  │                 playhead tagger              │
  │                 adds [watching 2:15] so      │
  │                 "here"/"this" resolve        │
  │                        │                     │
  │                 Silero VAD turn taking       │
  │                 (barge-in)                   │
  │                        │                     │
  │                 Amazon Bedrock —             │
  │                 Claude Haiku 4.5             │
  │                 (video knowledge pack        │
  │                  cached in the prompt)       │
  │                        │                     │
  │                 tools: find_moment ·         │
  │                        look_closer ·         │
  │                        ask_the_world         │
  │                        │                     │
  │                 Deepgram Aura-2 (stream TTS) │
  │                        │                     │
  │  speaker ◀──WebRTC─────┘                     │
  └──────────────────────────────────────────────┘
```

Everything that can be precomputed is precomputed at ingest, so a turn is
usually STT → one cached LLM call → TTS with no retrieval hop in between. Tool
results are also pushed to the browser over the RTVI data channel, so the player
seeks and the timeline markers appear as the agent answers.

Full design notes, latency measurements and security posture:
**[docs/architecture.md](docs/architecture.md)**.

## Prerequisites

- **AWS account** with Amazon Bedrock model access enabled in `us-east-1` for
  Claude Haiku 4.5, Amazon Nova 2 Lite, Amazon Nova 2 Multimodal Embeddings and
  TwelveLabs Pegasus 1.2 (all overridable — see `talk2vid/config.py`)
- **Deepgram account** with an API key (Nova-3 STT + Aura-2 TTS) — https://deepgram.com
- **Daily account** with an API key — only for the optional hosted deployment; local mode is peer-to-peer WebRTC and needs none — https://daily.co
- Python 3.12+ and [`uv`](https://docs.astral.sh/uv/)
- `ffmpeg` and `ffprobe` on your `PATH`
- Node.js 18+ and `npm` (restores the vendored Daily browser SDK)
- **Your own MP4s.** No media ships with this sample.

## Setup

Clone only this sample using sparse checkout:

```bash
git clone https://github.com/aws-samples/sample-apj-sup-sa.git
cd sample-apj-sup-sa
git sparse-checkout init --cone
git sparse-checkout set agentic-workloads/video-search-voice2voice
cd agentic-workloads/video-search-voice2voice
```

Install and provision:

```bash
brew install ffmpeg          # or your platform's package manager
uv sync
npm install                  # restores client/vendor/daily-esm.js

cp .env.example .env         # then fill in your Deepgram key
uv run python scripts/provision_aws.py        # private S3 bucket (+ security audit)
uv run python scripts/provision_agentcore.py  # gateway + web search + harness
```

Each script prints the value to put in `.env` (`TALK2VID_S3_BUCKET` and
`TALK2VID_HARNESS_ARN`). Both are idempotent — re-run them any time.

| Command | Does |
|---|---|
| `provision_aws.py --verify` | Re-audits the bucket |
| `provision_agentcore.py --verify` | Runs a live test search (also absorbs the cold start) |
| `provision_agentcore.py --teardown` | Removes the gateway, harness and their IAM roles |

### Add your videos

Drop your own 5–10 minute MP4s in `videos/`, then ingest each one:

```bash
uv run talk2vid-ingest videos/your-keynote.mp4    --scenario cloud      --title "Security keynote"
uv run talk2vid-ingest videos/your-consult.mp4    --scenario healthcare --title "Tele-consultation follow-up"
uv run talk2vid-ingest videos/your-highlights.mp4 --scenario sports     --title "Match highlights"
```

Ingest takes roughly 1.5–3 minutes for a 10-minute video, most of it the async
embedding job. Scenarios (`cloud`, `healthcare`, `sports`, `education`, `tech`,
`general`) set the agent's domain guidance, the card colour and the suggested
questions — see `SCENARIOS` in `talk2vid/config.py`.

Every enrichment step degrades gracefully: if Pegasus, captioning or embeddings
fail, you still get a talkable video from the transcript and timeline.
`--skip-captions` / `--skip-embeddings` / `--skip-overview` reuse the existing
index for the skipped parts instead of discarding them.

Only ingest videos you have the rights to process this way — see
[Caveats](#caveats).

### Run it

```bash
uv run python scripts/preflight.py    # checks keys, models, bucket, indexes, warm latency
uv run talk2vid                       # http://127.0.0.1:7860
```

Pick a video, press **Start talking**, allow the mic, and ask away. There is also
a text box — handy if a room's mic misbehaves, and it exercises the identical
pipeline.

One spoken question runs for at most **5 seconds** (`TALK2VID_MAX_LISTEN_SECS`).
A noisy room keeps the server's VAD hearing a voice, and a turn cannot end while
it does, so without the cap a crowded room can hold the floor indefinitely and
pile everything said near the laptop into one question. At the cap the mic mutes,
the question is answered, and the mic re-opens when the agent stops talking.

Typing is a first-class path, not a fallback: a typed question opens its own
text-only session with no microphone at all, so nothing competes for the floor.
Press the mic afterwards to switch to speaking (that restarts the bot session, so
the agent's memory of the chat restarts with it).

**Run `preflight.py` before every live run.** It also makes the first AgentCore
call, which takes ~40s while the harness environment is provisioned; after that
you never pay it.

## Testing without a microphone

`scripts/voice_probe.py` impersonates the browser: it synthesises a spoken
question with Deepgram, dials the server over real WebRTC, and reports every RTVI
event, the tools used, the reply and the measured latency.

```bash
uv run python scripts/voice_probe.py --video smoke-test \
    --ask "Show me the part about video understanding" --position 31
```

`scripts/make_test_video.py` generates a narrated 45-second synthetic clip
(`videos/smoke-test.mp4`) so the whole stack can be exercised end to end without
any real media.

> **Never open the microphone in an automated browser test.** Stub or deny
> `getUserMedia` so the client takes its text-only path.

## Deployment

The sample runs happily on a laptop. If you need a shareable URL, one command
puts it behind CloudFront:

```bash
uv run python scripts/deploy.py
```

It builds the container **inside AWS** (CodeBuild on ARM, so no local Docker
daemon), pushes it to a private ECR repo, and creates the `talk2vid` stack:

```
browser ──HTTPS──▶ CloudFront ──HTTP + secret header──▶ ALB ──▶ Fargate task (ARM64)
   │                (Basic Auth                                    │
   │                 function)                                     └─▶ Bedrock · AgentCore · S3
   └──────────────WebRTC media──▶ Daily ◀──────────────────────────────┘
```

Voice media never touches this stack. In hosted mode the bot joins a **Daily**
room that the task creates per session, so there is no inbound UDP path to open
and no TURN server to run. `TALK2VID_TRANSPORT=daily` selects it; the local
default stays peer-to-peer WebRTC.

The first run prints the URL and a generated Basic Auth login, and stores them in
`data/deploy-gate.json` (mode 600, gitignored). Assets are not baked into the
image: the container syncs `data/index/` and `videos/` from the private S3 bucket
at start, so re-ingesting a video needs no rebuild — just restart the service.

```bash
uv run python scripts/deploy.py --status       # URL, login, running count, health
uv run python scripts/deploy.py --skip-build   # redeploy the image already in ECR
uv run python scripts/deploy.py --park         # desired count 0 — stops the Fargate cost
uv run python scripts/deploy.py --resume       # back to 1
uv run python scripts/deploy.py --teardown     # stack, ECR repo, secret, build project
```

Park it when idle: one task plus the ALB is roughly $0.05/hour, and `--park` removes
the Fargate share of that. It is not free — the ALB stays up and keeps billing at
roughly $0.025/hour (about $18/month) plus LCUs, so park between rehearsals and
**`--teardown` when you are done for good**. Teardown removes everything this script
created — the S3 bucket and the AgentCore harness belong to the provisioning scripts
and are left alone.

Deploying needs an AWS profile with permission to create these resources and
`AWS_REGION=us-east-1`, with any stale `AWS_*` environment variables unset.

## Project Structure

```
.
├── talk2vid/               # Python package
│   ├── ingest.py           # offline pipeline: probe, segment, transcribe, caption, embed
│   ├── bedrock.py          # Amazon Bedrock access layer (all model calls)
│   ├── index_store.py      # on-disk index + in-memory semantic/lexical retrieval
│   ├── prompt.py           # system prompt / video knowledge pack construction
│   ├── bot.py              # Pipecat voice pipeline (STT → LLM → TTS)
│   ├── tools.py            # find_moment · look_closer · ask_the_world
│   ├── agentcore.py        # Amazon Bedrock AgentCore harness client
│   ├── rooms.py            # Daily room provisioning (hosted mode)
│   ├── server.py           # FastAPI app + WebRTC signalling
│   └── config.py           # every setting, env-overridable
├── client/                 # browser client (RTVI over the data channel)
├── scripts/                # provision_aws · provision_agentcore · preflight · voice_probe · deploy
├── deploy/                 # Dockerfile · buildspec.yml · app.yaml (CloudFormation)
├── docs/                   # architecture.md + diagrams (PNG and diagram-as-code source)
├── videos/                 # your MP4s (gitignored — none ship with this sample)
└── .env.example
```

Per-module detail is in [docs/architecture.md](docs/architecture.md#code-layout).

## AWS Services Used

| Service | Purpose |
|---|---|
| Amazon Bedrock — Claude Haiku 4.5 | Conversational reasoning and tool calling, with prompt caching |
| Amazon Bedrock — Amazon Nova 2 Lite | Visual captions at ingest; `look_closer` frame reading at runtime |
| Amazon Bedrock — Amazon Nova 2 Multimodal Embeddings | Segment and query embeddings for semantic moment search |
| Amazon Bedrock — TwelveLabs Pegasus 1.2 | Whole-video overview and chapter breakdown |
| Amazon Bedrock AgentCore | Harness + web-search gateway behind the `ask_the_world` tool |
| Amazon S3 | One private bucket (SSE-S3) for media, indexes and vectors |
| Amazon ECS on AWS Fargate | Runs the app in the optional hosted mode (ARM64) |
| Amazon CloudFront | HTTPS edge with a Basic Auth function in hosted mode |
| Elastic Load Balancing (ALB) | CloudFront's origin. Internet-facing, but its security group admits only CloudFront's managed prefix list and its listener refuses anything without CloudFront's secret header |
| AWS Secrets Manager | Injects third-party API keys into the task at runtime |
| Amazon ECR + AWS CodeBuild | Builds and stores the ARM64 container image inside AWS |
| Amazon CloudWatch Logs | Application logs in hosted mode |

## Third-Party Services

| Service | Purpose |
|---|---|
| Deepgram Nova-3 | Batch transcription at ingest and streaming STT at runtime |
| Deepgram Aura-2 | Streaming text-to-speech |
| Pipecat | Voice pipeline framework (Python dependency) |
| Daily | WebRTC transport in hosted mode only; the browser SDK is fetched by `npm install`, not committed to this repo |
| Silero VAD | Voice activity detection / turn taking |
| ffmpeg / ffprobe | Local probing, scene detection, segmentation, keyframe extraction |

## Key Concepts

- **The whole video goes in the prompt, not a vector store.** A 5–10 minute
  clip's indexed timeline is a few thousand tokens; Bedrock prompt caching holds
  it, so the common question needs *zero* retrieval calls. Retrieval is reserved
  for what it is actually good at — finding a specific moment to jump to.
- **Playhead-aware turns.** Every transcription is tagged with the live player
  position before the model sees it, so "here" and "this bit" resolve correctly.
- **Precompute everything.** Overview, chapters, captions and embeddings are all
  built once at ingest, keeping a runtime turn to STT → one cached LLM call → TTS.
- **Graceful degradation.** Any enrichment step can fail and you still get a
  talkable video from the transcript and timeline alone.
- **Playback ownership stays with the user.** The agent may seek, never play.
- **An in-memory matmul beats a vector database here.** One video per session and
  ~50 segments means the network round-trip to a managed store costs more than
  the search itself.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Deepgram STT dies immediately with HTTP 400 | `utterance_end_ms` must be ≥ 1000. Deepgram rejects the socket otherwise |
| No videos on the picker | Nothing ingested, or `data/index/` was cleared. `/api/videos` hot-reloads when index files change |
| Button stuck on "Connecting…" | Mic prompt ignored; after 8s it falls back to text-only mode automatically |
| Agent answers about the wrong moment | Check the playhead is reaching the bot — `window.__t2v.playhead` in the console, and look for `playhead ->` in the server log |
| `ask_the_world` declines politely | `TALK2VID_HARNESS_ARN` is unset; the tool tells the model to say so rather than inventing an answer |
| First web question takes ~40s | Harness cold start. Run `scripts/preflight.py` first to absorb it |
| Stale UI behaviour after an edit | Static files are served `no-store`, so a plain reload is enough |
| Hosted URL returns 403 | You hit the ALB directly. Only the CloudFront URL works — `deploy.py --status` prints it |
| Hosted URL returns 502/504 | The task is still starting (S3 asset sync runs first) or parked. `deploy.py --status`, then `--resume` |
| Hosted picker is empty | The task synced an empty bucket prefix — re-run ingest, then restart the service so it syncs again |
| Deploy dies with `EndpointConnectionError` | Local network or expired credentials, not AWS. Re-auth and re-run with `--skip-build` |

## Caveats

This is a **reference implementation to learn from, not a production-ready
deployment**. Before using anything here with real users or real data:

- **No Amazon Bedrock Guardrails are configured.** Model input and output are not
  filtered. Add guardrails appropriate to your use case.
- **Content you ingest is sent to third parties.** Audio goes to Deepgram for
  transcription and speech synthesis; in hosted mode voice media flows through
  Daily. Only ingest videos you have the rights to process this way, and review
  those vendors' terms and data-handling policies first.
- **Logs may contain user-derived content.** Server logs record video ids, tool
  calls and playhead positions. Review and mask log output against your own
  privacy requirements before deploying, and note that hosted mode ships logs to
  Amazon CloudWatch Logs.
- **The hosted gate is a demo gate.** CloudFront Basic Auth with a generated
  password is fine for a short-lived demo, not for production. Put Amazon
  Cognito or an OIDC provider in front of it instead.
- **Session state is in-process only.** Conversation memory lives in the task and
  is lost on restart; there is no isolation beyond the per-session WebRTC
  connection.
- **Third-party API keys sit in `.env`** in local mode (gitignored). Rotate them
  after use, and run `scripts/provision_agentcore.py --teardown` plus
  `scripts/deploy.py --teardown` to remove billable resources when you are done.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](../../LICENSE) file.
