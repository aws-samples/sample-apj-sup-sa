# ClaimPilot

ClaimPilot is a real-time voice AI insurance claims companion. It turns a customer call into a structured auto-claim workflow: the agent listens to the incident, updates a companion mobile-style app, requests evidence, stores claim artifacts, analyzes uploaded photos, and produces an adjuster-ready claim packet before the call ends.

## Repository Layout

```
claimpilot/
├── client/   # React + Vite browser companion app (Pipecat voice UI)
├── server/   # Pipecat voice agent (Python, uv-managed)
├── infra/    # AWS CDK stack (DynamoDB, S3, Lambda runtime API, Secrets Manager)
└── docs/     # AWS runtime IAM policy reference
```

## Prerequisites

- Node 20+ and `pnpm` (`corepack enable && corepack prepare pnpm@latest --activate`)
- Python 3.12+ and [`uv`](https://docs.astral.sh/uv/)
- An AWS account with Bedrock access in your chosen region
- API keys for [Deepgram](https://console.deepgram.com/) and [Daily](https://dashboard.daily.co/)

## Local Quick Start

### 1. Server (Pipecat voice agent)

```bash
cd claimpilot/server
cp env.example .env   # fill in DEEPGRAM_API_KEY, DAILY_API_KEY, AWS_* values
uv sync
uv run bot.py --transport daily
```

### 2. Client (browser companion app)

```bash
cd claimpilot/client
cp env.example .env.local
pnpm install
pnpm run dev
```

Open `http://localhost:5173` and connect to the voice agent.

### 3. Infrastructure (optional, for AWS-backed persistence)

```bash
cd claimpilot/infra
pnpm install
pnpm exec cdk bootstrap    # first time only, per account/region
pnpm exec cdk deploy
```

The stack provisions a DynamoDB claims table, an S3 evidence bucket, a Secrets Manager API key, and a Lambda-backed runtime API for cloud-hosted bots.

## Configuration

All secrets are loaded from environment variables. See:

- `claimpilot/server/env.example`
- `claimpilot/client/env.example`

Never commit `.env` or `.env.local` files. They are excluded by `.gitignore`.

## Tech Stack

- [Pipecat](https://github.com/pipecat-ai/pipecat) for the voice pipeline
- [Deepgram](https://deepgram.com/) for speech-to-text and text-to-speech
- [AWS Bedrock](https://aws.amazon.com/bedrock/) (Anthropic Claude) for the LLM and multimodal evidence analysis
- [Daily](https://daily.co/) for WebRTC transport
- React 19 + Vite for the companion app
- AWS CDK for infrastructure

## License

[MIT](LICENSE)
