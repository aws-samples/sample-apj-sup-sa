# TastyVoice

A voice-first food delivery assistant. Customers speak naturally to browse menus,
place orders, and track deliveries — no typing, no navigating screens. Built on
Pipecat, Deepgram, and Claude Sonnet, deployed on AWS Bedrock AgentCore.

> Speak: _"Add two butter chicken and a naan to my cart"_ — and it's done.

## Why

Ordering food through apps is high-friction: too many taps, no memory of your
preferences, poor accessibility, and decision paralysis from huge menus.
TastyVoice collapses all of that into a single natural conversation, with
sub-2-second voice-to-voice latency and persistent memory across sessions.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full design and the
customer pain points it addresses.

## Architecture

```
Customer (voice)
    ↕ WebSocket (real-time audio)
Voice Agent (AWS Bedrock AgentCore)
    ├── Deepgram STT — speech → text
    ├── Claude Sonnet — reasoning + tool calls
    ├── Deepgram TTS — text → speech
    └── Persistent Memory — preferences across sessions
           ↕ MCP Proxy (tool gateway)
Restaurant Backend (FastAPI)
    ├── Menu, Cart, Orders, Delivery
    ├── OTP Auth (AWS SNS)
    └── Kitchen Dashboard (real-time)
```

## Repository Structure

```
.
├── apps/
│   ├── customer-app/        # React customer web app (voice + text ordering)
│   └── kitchen-dashboard/   # React staff dashboard for order management
├── services/
│   ├── backend/             # FastAPI REST API (auth, menu, cart, orders, chat)
│   ├── voice-agent/         # Pipecat voice pipeline on AgentCore
│   ├── mcp-proxy/           # MCP tool gateway (HTTPS → HTTP bridge)
│   └── lambda-voice-token/  # Lambda issuing pre-signed WebSocket URLs
├── docs/
│   ├── ARCHITECTURE.md      # System design and pain points
│   ├── DEMO.md              # Demo walkthrough and script
│   └── api/                 # OpenAPI specs for the customer tools
├── README.md
├── LICENSE
├── SECURITY.md
└── CONTRIBUTING.md
```

## Components

| Component | What it does | Tech |
|---|---|---|
| `apps/customer-app` | Customer web app with mic button (voice) and text chat fallback | React + Vite |
| `apps/kitchen-dashboard` | Staff view for incoming orders and status updates | React + Vite |
| `services/backend` | REST API for auth, menu, cart, orders + Claude chat endpoint | FastAPI |
| `services/voice-agent` | Real-time voice pipeline deployed on AgentCore | Pipecat + Deepgram + Claude |
| `services/mcp-proxy` | Bridges AgentCore (HTTPS) to the backend via JSON-RPC | FastAPI on AgentCore |
| `services/lambda-voice-token` | Issues pre-signed WebSocket URLs for the browser | AWS Lambda |

## Quickstart (Local)

You'll need Python 3.12, Node 18+, and AWS credentials configured.

### 1. Backend

```bash
cd services/backend
pip install -r requirements.txt
cp .env.example .env          # set JWT_SECRET, AWS_REGION, etc.
uvicorn app.main:app --reload --port 8000
```

### 2. Customer app

```bash
cd apps/customer-app
npm install
cp .env.example .env          # set VITE_VOICE_AGENT_WS_URL
npm run dev                    # http://localhost:5173
```

### 3. Kitchen dashboard

```bash
cd apps/kitchen-dashboard
npm install
npm run dev                    # http://localhost:5174
```

### 4. Voice agent (optional, requires AgentCore)

```bash
cd services/voice-agent
pip install -r requirements.txt
cp .env.example .env
agentcore dev
```

For a full guided walkthrough, see [docs/DEMO.md](docs/DEMO.md).

## Configuration

Every service ships a `.env.example`. Copy it to `.env` and fill in your own
values. **Never commit secrets** — see [SECURITY.md](SECURITY.md) for the full
policy and production-hardening notes.

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) before
opening a pull request.

## License

Released under the [MIT-0 License](LICENSE).
