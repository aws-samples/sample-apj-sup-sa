# Ambient Voice QA — Hands-Free Manufacturing Inspection Agent

A voice AI agent that walks factory and warehouse workers through quality
inspection checklists conversationally. The worker keeps both hands on the
equipment; the agent reads each step, captures the worker's spoken reading,
validates it against the engineering threshold in real time, and auto-flags
anomalies. The reference checklist is a five-step hydraulic pump inspection,
but the checklist data structure in `server/bot.py` is generic and can be
swapped for any inspection workflow.

## Architecture

```
                     ┌──────────────────────────────────────┐
                     │  React Web Client (Vite + RTVI)      │
                     │  • Live transcript                   │
                     │  • Live checklist panel              │
                     └─────────────────┬────────────────────┘
                                       │ WebRTC (Daily)
                                       ▼
                     ┌──────────────────────────────────────┐
                     │  Pipecat Pipeline                    │
                     │                                      │
   worker speaks ──▶ │  Daily transport in                  │
                     │     │                                │
                     │     ▼                                │
                     │  Krisp noise cancellation (cloud)    │
                     │     │                                │
                     │     ▼                                │
                     │  Silero VAD                          │
                     │     │                                │
                     │     ▼                                │
                     │  Deepgram Nova-3 STT                 │
                     │  (keyterm-boosted QA vocabulary)     │
                     │     │                                │
                     │     ▼                                │
                     │  Amazon Bedrock — Claude Sonnet 4.5  │
                     │  (function-calling: record_reading,  │
                     │   next_step, repeat, go_back, skip,  │
                     │   flag_anomaly)                      │
                     │     │                                │
                     │     ▼                                │
                     │  Deepgram Aura TTS                   │
                     │     │                                │
                     │     ▼                                │
                     │  Daily transport out ──▶ worker hears│
                     └──────────────────────────────────────┘
```

Function-call results are also pushed back to the React client over the RTVI
server-message channel so the checklist panel updates live as the worker
progresses.

## Prerequisites

- **AWS account** with Bedrock access enabled in `us-west-2` for Claude Sonnet 4.5 (or another model id you set in `.env`)
- **Deepgram account** with API key (Nova-3 STT + Aura TTS) — https://deepgram.com
- **Daily / Pipecat Cloud account** for WebRTC transport — https://pipecat.daily.co
- Python 3.10+ and [`uv`](https://docs.astral.sh/uv/) (or `pip`)
- Node.js 18+ and `npm`
- Docker (only required for deploying to Pipecat Cloud)

## Setup

Clone only this sample using sparse checkout:

```bash
git clone https://github.com/aws-samples/sample-apj-sup-sa.git
cd sample-apj-sup-sa
git sparse-checkout init --cone
git sparse-checkout set agentic-workloads/ambient-voice-qa
cd agentic-workloads/ambient-voice-qa
```

Copy the example env files and fill in your keys:

```bash
cp server/.env.example server/.env
cp client/.env.example client/.env.local
```

### Run the server

```bash
cd server
uv sync                       # or: pip install -r requirements.txt
uv run bot.py --transport daily
```

The bot listens on `http://localhost:7860/start` by default.

### Run the client

In another terminal:

```bash
cd client
npm install
npm run dev                   # local dev server on http://localhost:5173
```

For local development against the local server, set
`VITE_BOT_START_URL=http://localhost:7860/start` in `client/.env.local`.
For production, point it at your deployed Pipecat Cloud agent.

Open http://localhost:5173 and click **Connect** to start an inspection.

## Deployment

### Server → Pipecat Cloud

```bash
uv tool install pipecat-ai-cli
pipecat cloud auth login

cd server
pipecat cloud secrets set --file .env ambient-voice-qa-secrets
pipecat cloud deploy
```

The agent name and secret-set name come from `server/pcc-deploy.toml`. Edit
that file if you want to deploy under a different name.

After deployment, mint a public API key and grab your bot start URL:

```bash
pipecat cloud organizations keys create
pipecat cloud agent start ambient-voice-qa --use-daily
```

Put the start URL and public key into `client/.env.production`, then rebuild the client.

### Client → S3 + CloudFront (or any static host)

`npm run build` produces `client/dist/`. Deploy that directory to:

- **S3 + CloudFront** — upload to a bucket, front it with a CloudFront
  distribution, route 403s to `/index.html` for SPA routing
- **Vercel / Netlify / Cloudflare Pages** — point at this repo and set the
  build command to `npm run build` and the output directory to `dist`

## Project Structure

```
.
├── server/
│   ├── bot.py              # Pipecat pipeline + checklist + function tools
│   ├── pcc-deploy.toml     # Pipecat Cloud deployment config
│   ├── Dockerfile          # Used by Pipecat Cloud build
│   ├── pyproject.toml      # Python dependencies (uv/pip)
│   └── .env.example
└── client/
    ├── src/
    │   ├── main.tsx
    │   ├── config.ts       # Reads VITE_BOT_START_URL / VITE_BOT_START_PUBLIC_API_KEY
    │   └── components/
    │       ├── App.tsx
    │       ├── ChecklistPanel.tsx
    │       └── TransportSelect.tsx
    ├── package.json
    └── .env.example
```

## AWS Services Used

| Service | Purpose |
|---------|---------|
| Amazon Bedrock (Claude Sonnet 4.5) | LLM for conversational flow & function calling |
| Amazon S3 + CloudFront | Static hosting for the React client |

## Third-Party Services

| Service | Purpose |
|---------|---------|
| Deepgram Nova-3 | Real-time speech-to-text |
| Deepgram Aura | Text-to-speech |
| Daily / Pipecat Cloud | WebRTC transport & agent hosting |
| Krisp | Noise cancellation for factory environments |
| Silero VAD | Voice Activity Detection |

## Key Concepts

- **Function-calling agent**: The LLM orchestrates inspection flow via tool calls (`record_reading`, `next_step`, `flag_anomaly`, etc.) rather than free-form text generation.
- **Keyterm boosting**: Deepgram STT is configured with domain-specific vocabulary (PSI, RPM, dB, etc.) to improve transcription accuracy in noisy environments.
- **Real-time validation**: Each reading is checked against engineering thresholds immediately, with anomalies flagged before moving to the next step.
- **Hands-free operation**: Workers wearing gloves/PPE interact entirely by voice — no touch input required.

## License

This library is licensed under the MIT-0 License. See the [LICENSE](../../LICENSE) file.
