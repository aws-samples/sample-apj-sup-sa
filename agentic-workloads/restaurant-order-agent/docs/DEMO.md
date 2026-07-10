# TastyVoice — Demo Guide

## What Is It?

TastyVoice is a voice-first food delivery assistant. Customers speak naturally to browse menus, place orders, and track deliveries — no typing, no navigating through screens. Think of it as calling your favorite restaurant, except an AI agent handles the conversation instantly, remembers your preferences, and never puts you on hold.

## The Problem

Ordering food through apps today is surprisingly high-friction:

| Frustration | What happens today |
|---|---|
| **Too many taps** | Open app → scroll → tap item → customize → cart → checkout → address → payment. A single order takes 15+ steps. |
| **Repetitive every time** | The app forgets you're vegetarian, forgets your usual address, treats you like a stranger each visit. |
| **Accessibility gap** | Elderly users, visually impaired users, or anyone driving can't safely use a touch interface. |
| **Decision paralysis** | 200-item menus with no guidance. You end up ordering the same thing because exploring is exhausting. |
| **"Where's my food?"** | Checking delivery status means opening the app, finding the order, waiting for the map to load. |

## The Solution

One sentence: **"Add two butter chicken and a naan to my cart"** — and it's done.

The voice agent handles:
- **Menu browsing** — "What vegan options do you have?" → filtered list spoken back
- **Ordering** — "Add a large paneer tikka" → added to cart, price confirmed
- **Authentication** — Phone + OTP, spoken naturally ("the code is 4-5-2-1")
- **Order tracking** — "Where's my food?" → instant spoken status
- **Personalization** — Remembers your dietary preferences, past orders, and favorites across sessions

## Architecture at a Glance

```
Customer (voice)
    ↕ WebSocket (real-time audio)
Voice Agent (AWS Bedrock AgentCore)
    ├── Deepgram STT — speech → text (sub-300ms)
    ├── Claude Sonnet — reasoning + tool calls
    ├── Deepgram TTS — text → speech (streaming)
    └── Persistent Memory — preferences across sessions
           ↕ MCP Proxy (tool gateway)
Restaurant Backend (FastAPI)
    ├── Menu, Cart, Orders, Delivery
    ├── OTP Auth (AWS SNS)
    └── Kitchen Dashboard (real-time)
```

**End-to-end latency:** Under 2 seconds from speaking to hearing the response.

## Components

| Component | What it does | Tech |
|---|---|---|
| **Tasty App** | Customer-facing web app with mic button for voice and text chat fallback | React + Vite |
| **Backend** | REST API wrapping restaurant operations + Claude chat endpoint | FastAPI + Python |
| **Voice Agent** | Real-time voice pipeline deployed on AgentCore | Pipecat + Deepgram + Claude |
| **MCP Proxy** | Bridges AgentCore (HTTPS) to backend (HTTP) via JSON-RPC | FastAPI on AgentCore |
| **Kitchen Dashboard** | Staff view for incoming orders and status updates | React + Vite |

## How to Run Locally

### 1. Backend

```bash
cd services/backend
pip install -r requirements.txt
cp .env.example .env   # fill in your keys
uvicorn app.main:app --reload --port 8000
```

### 2. Customer App

```bash
cd apps/customer-app
npm install
npm run dev
# → http://localhost:5173
```

### 3. Kitchen Dashboard

```bash
cd apps/kitchen-dashboard
npm install
npm run dev
# → http://localhost:5174
```

### 4. Voice Agent (AgentCore)

```bash
cd services/voice-agent
agentcore dev   # starts the voice agent locally
```

## Demo Flow (Suggested Script)

1. **Open the customer app** → show the menu UI and traditional ordering flow
2. **Tap the mic button** → start a voice session
3. **Say:** "Hi, I'd like to order some food"
4. Agent asks for authentication → speak your phone number → receive OTP → speak the code
5. **Say:** "What vegetarian options do you have?"
6. Agent reads filtered menu items with prices
7. **Say:** "Add two paneer tikka and one garlic naan"
8. Agent confirms items and total
9. **Say:** "Place the order"
10. **Switch to kitchen dashboard** → show the order appearing in real-time
11. **Update order status** from kitchen → switch back to customer
12. **Say:** "Where's my order?" → agent gives live status
13. **Start a new session later** → agent remembers preferences ("Welcome back! Want your usual?")

## Who Benefits

- **Busy professionals** — order during commute, between meetings
- **Elderly customers** — prefer speaking over navigating apps
- **Visually impaired users** — full accessibility without screen readers
- **Drivers** — safe, hands-free ordering
- **Repeat customers** — fastest path to re-order favorites

## Key Technical Wins

- **Sub-2s voice-to-voice** — Deepgram streaming + Silero VAD for natural turn-taking
- **Persistent memory** — short-term (within session) + long-term (across sessions) via AgentCore Memory
- **Serverless scaling** — AgentCore handles infra, auto-scales to demand
- **Modular tools** — new capabilities (loyalty, promos) added without touching voice code
- **Interruption support** — start speaking mid-response and the agent stops to listen
