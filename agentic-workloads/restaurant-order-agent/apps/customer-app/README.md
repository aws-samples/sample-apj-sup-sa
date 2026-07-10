# Tasty Bites — Customer App

Swiggy-inspired food delivery UI with an integrated voice chatbot powered by the AgentCore Deepgram voice agent.

## Features

- Browse restaurant menu with dietary filters (veg/vegan/non-veg)
- Add items to cart, place orders
- Track order status
- Voice chatbot (mic button) connects to AgentCore WebSocket for real-time Deepgram STT/TTS
- Text chat fallback via /api/chat (Claude + tool calling)
- OTP-based phone authentication via AWS SNS

## Setup

```bash
cd apps/customer-app
npm install
```

## Configure

Edit `.env`:
```
VITE_VOICE_AGENT_WS_URL=wss://your-agentcore-endpoint/ws
```

## Run

```bash
npm run dev
# Runs on http://localhost:5173
# Proxies /api to backend at http://localhost:8000
```

## Voice Architecture

```
Browser Mic (16kHz) → WebSocket → AgentCore Runtime
                                    ├─ Deepgram STT (Nova-2)
                                    ├─ Claude Sonnet (Bedrock)
                                    ├─ Restaurant Tool Calls
                                    └─ Deepgram TTS (Aura) → WebSocket → Browser Speakers (24kHz)
```

## Image Credits

Menu item photos are sourced from [Unsplash](https://unsplash.com) and hotlinked
via the Unsplash CDN for demo purposes. They are used under the
[Unsplash License](https://unsplash.com/license), which permits free use without
attribution. For a production deployment, host your own licensed images rather
than hotlinking. See `services/backend/app/store.py` for the image URLs.
