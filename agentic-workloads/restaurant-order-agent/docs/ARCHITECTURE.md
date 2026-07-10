# Voice Agent for Restaurant Delivery — Architecture & Customer Pain Points

## Executive Summary

This project is a real-time conversational voice agent for restaurant food delivery. Customers speak naturally to browse menus, place orders, and track deliveries — no typing, no app navigation. Built on Pipecat 1.3, Deepgram, Claude Sonnet (via Bedrock), and deployed on AWS Bedrock AgentCore Runtime with persistent memory.

---

## System Architecture

### High-Level Flow

```
┌──────────────┐         WebSocket (bidirectional audio)         ┌──────────────────────────┐
│   Customer   │ ◄──────────────────────────────────────────────►│   Voice Agent (AgentCore)│
│  (Phone/Web) │                                                 │   - Pipecat Pipeline     │
└──────────────┘                                                 │   - Silero VAD           │
                                                                 │   - Deepgram STT/TTS     │
                                                                 │   - Claude Sonnet (LLM)  │
                                                                 │   - Memory (STM + LTM)   │
                                                                 └────────────┬─────────────┘
                                                                              │
                                                                   Tool Calls (HTTP/MCP)
                                                                              │
                                                                              ▼
                                                                 ┌──────────────────────────┐
                                                                 │   MCP Proxy (AgentCore)  │
                                                                 │   - JSON-RPC 2.0 / WS    │
                                                                 │   - HTTPS → HTTP bridge  │
                                                                 └────────────┬─────────────┘
                                                                              │
                                                                              ▼
                                                                 ┌──────────────────────────┐
                                                                 │  Restaurant Backend API  │
                                                                 │  (CloudFront + ELB)      │
                                                                 │  - Auth, Menu, Cart,     │
                                                                 │    Orders, Delivery,     │
                                                                 │    Profile               │
                                                                 └──────────────────────────┘
```

### Component Breakdown

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Speech-to-Text | Deepgram Nova-2 | Low-latency transcription of customer speech |
| Text-to-Speech | Deepgram Aura-2 Andromeda | Natural, warm voice synthesis |
| Voice Activity Detection | Silero VAD | Turn-taking — knows when customer stops speaking |
| LLM | Claude Sonnet 4 (Bedrock) | Reasoning, tool selection, response generation |
| Transport | WebSocket (FastAPI) | Bidirectional real-time audio streaming |
| Runtime | AWS Bedrock AgentCore | Managed serverless container hosting |
| Memory | AgentCore Memory (STM + LTM) | Conversation history + long-term preferences |
| Tool Gateway | MCP Proxy (AgentCore) | Bridges HTTPS requirement to HTTP backend |
| Backend API | CloudFront + REST | Auth, menu, cart, orders, delivery, profile |

### Memory Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    AgentCore Memory                          │
│                                                             │
│  ┌─────────────────────┐    ┌────────────────────────────┐ │
│  │  Short-Term Memory  │    │     Long-Term Memory       │ │
│  │  (per session)      │    │     (per customer)         │ │
│  │                     │    │                            │ │
│  │  • User utterances  │    │  /preferences/{actor_id}   │ │
│  │  • Agent responses  │    │  /facts/{actor_id}         │ │
│  │  • Tool results     │    │  /summaries/{actor_id}     │ │
│  │  • 30-day expiry    │    │                            │ │
│  └─────────────────────┘    └────────────────────────────┘ │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  MemoryPrefetchAgent (async, non-blocking)          │   │
│  │  • Queries LTM on every new user prompt             │   │
│  │  • Injects relevant preferences/facts into context  │   │
│  │  • 2-second timeout fallback                        │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### Deployment Topology

| Service | Region | Platform | Memory Mode |
|---------|--------|----------|-------------|
| Voice Agent (voiceagent) | us-west-2 | ARM64 container | STM + LTM |
| MCP Proxy (mcpproxy) | ap-south-1 | ARM64 container | No memory |

### Authentication & Session Flow

```
Customer: "I want to order"
    │
    ▼
Agent: "What's your phone number?"
    │
    ▼
Agent calls: request_otp("+14155552671") → SMS sent
    │
    ▼
Customer: "The code is 123456"
    │
    ▼
Agent calls: verify_otp(phone, "123456") → session_token
    │
    ▼
RestaurantToolClient stores token → all subsequent calls authenticated
    │
    ▼
Agent: "You're all set! Want to see the menu?"
```

---

## Customer Pain Points Addressed

### 1. Friction in Traditional Food Ordering Apps

| Pain Point | Traditional App Experience | Voice Agent Solution |
|---|---|---|
| **Multi-step navigation** | Open app → scroll menu → tap item → customize → add to cart → checkout → enter address → select payment | "Add two butter chicken and a naan to my cart" — done in one sentence |
| **Typing fatigue** | Searching for items, entering address, typing special instructions | Speak naturally, agent handles everything |
| **Accessibility barriers** | Visually impaired or motor-impaired users struggle with touch interfaces | Voice-first interaction requires zero visual/touch input |
| **Language/literacy gaps** | Text-heavy menus intimidate non-native readers | Conversational interaction in natural language |

### 2. Cold Start & Repetitive Actions

| Pain Point | Current Experience | Voice Agent Solution |
|---|---|---|
| **Re-entering preferences every session** | "Veg only" filter resets; past orders forgotten | LTM stores dietary preferences, usual portion sizes, favorite items |
| **No personalization** | Every visit feels like the first time | Agent greets with context: "Welcome back! Last time you ordered butter chicken — want the same?" |
| **Forgotten addresses** | Re-typing delivery address | Profile stored and auto-filled from memory |

### 3. Real-Time Status Anxiety

| Pain Point | Current Experience | Voice Agent Solution |
|---|---|---|
| **"Where's my food?"** | Open app → find order → tap tracking → wait for map to load | "Where's my order?" → instant spoken status update |
| **No proactive updates** | Customer must manually check | Agent can be extended to push status changes as voice notifications |
| **Order confusion** | Multiple past orders, unclear which is active | Agent knows the current active order and responds contextually |

### 4. Authentication Hassle

| Pain Point | Current Experience | Voice Agent Solution |
|---|---|---|
| **Password fatigue** | Forgot password → reset flow → email → set new → login | Phone + OTP — no passwords, no email |
| **Account lockouts** | Too many failed attempts | Simple 6-digit code spoken aloud |
| **Multi-device friction** | Logged out on new device, need credentials | OTP works on any device with the phone number |

### 5. Decision Paralysis from Large Menus

| Pain Point | Current Experience | Voice Agent Solution |
|---|---|---|
| **Menu overload** | 200+ items, endless scrolling | Agent filters by dietary preference, recommends based on past orders |
| **No dietary guidance** | Buried allergy/diet info | "Show me vegan options" → instant filtered list |
| **Price uncertainty** | Need to tap each item to see price | Agent naturally mentions price in conversation |

### 6. Hands-Free Use Cases

| Pain Point | Current Experience | Voice Agent Solution |
|---|---|---|
| **Driving** | Can't safely use touch interface | Fully voice-driven, no screen required |
| **Cooking / busy hands** | Need to put down what you're holding | "Hey, add garlic bread to my order" |
| **Multitasking** | Ordering requires full visual attention | Background voice interaction while doing other things |

---

## Technical Differentiators

### Low Latency Pipeline
- Deepgram Nova-2 delivers sub-300ms STT latency
- Silero VAD enables natural turn-taking (no "press to talk")
- Streaming TTS begins before full LLM response completes
- End-to-end voice-to-voice under 2 seconds

### Persistent Memory Across Sessions
- Short-term memory maintains context within a call
- Long-term memory remembers customer across visits (preferences, facts, summaries)
- Async prefetch avoids adding latency to the voice response path

### Serverless Scaling
- Bedrock AgentCore handles cold starts, scaling, and infrastructure
- No server management, auto-scales to demand
- Pay only for active sessions

### Modular Tool Architecture
- MCP Proxy decouples tool gateway from voice agent
- New tools (loyalty programs, promotions, feedback) added without touching voice code
- OpenAPI spec drives tool definitions — single source of truth

---

## Target Users

| Segment | Why Voice Ordering Fits |
|---------|------------------------|
| Busy professionals | Ordering during commute, between meetings |
| Elderly customers | Prefer speaking over navigating apps |
| Visually impaired users | Full accessibility without screen readers |
| Non-tech-savvy users | "Just call and order" experience, digitized |
| Repeat customers | Fastest path to re-order favorites |
| Drivers / hands-occupied | Safe ordering without touch |

---

## Future Extensions

1. **Multilingual support** — Add language detection and switch STT/TTS models dynamically
2. **Proactive notifications** — Push delivery status updates as voice messages
3. **Group ordering** — Multiple speakers in one session, split billing
4. **Loyalty integration** — "You have 50 points, want to redeem for free delivery?"
5. **Feedback loop** — Post-delivery voice survey: "How was your butter chicken?"
6. **Smart recommendations** — LTM-powered suggestions based on time of day, weather, past behavior
