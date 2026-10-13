# Frontend (Vite + React + TS) — "Aisle" voice grocery assistant

Voice-first SPA on **S3 + CloudFront**. Voice-orb hero with tabs for the data panels.

> **Targets the DEPLOYED agent.** The deployed Aisle agent runtime speaks a
> contract over Daily/WebRTC (not AgentCore `/ws`). See
> `src/types/contracts.live.ts` for the shapes the UI consumes. A thin adapter
> (`lib/event-adapter.ts`) isolates the transport so a future `/ws` agent can be
> swapped in without touching components.

## How it connects
1. `POST {VITE_SESSION_API_URL}` → `{ room_url, status }` (deployed start endpoint).
2. Join that **Daily** room with `@daily-co/daily-js`. Daily handles mic capture +
   agent audio playback (no hand-rolled PCM).
3. Daily `app-message` events → `lib/event-adapter.ts` → Zustand store → panels.

## Live vs preview panels
- **Live (voice-driven):** Cart (`add_to_cart`/`get_cart`/`create_order`),
  Products (`search_products`/`get_product_variants`), Transcript, Voice orb.
- **Preview (seeded mocks):** Recipes, Offers, Grocery list, Profile — the deployed
  agent has no tools for these (UC1/UC4/UC5/UC3b). Seeded in `mocks/seed.ts` to show
  the full vision; marked with a "preview" pill in the UI.

`agent_state` (orb animation) is **derived locally** from Daily audio levels +
transcript flow — the live agent doesn't emit it.

## Develop
```bash
npm install
npm run dev          # http://localhost:5173 — connects to the configured session endpoint
npm run build        # → dist/
npm run typecheck
```
Set the deployed endpoint in `src/config.ts` or via `VITE_SESSION_API_URL`.
The deployed agent joins a Daily room you configure (a single shared room is
fine for one presenter; concurrent users would collide — use a per-session room
for multi-user).

## Layout
```
src/
  types/contracts.live.ts   # shapes the UI consumes from the deployed agent
  config.ts                 # VITE_SESSION_API_URL
  lib/session-client.ts     # POST start → { room_url }
  lib/daily-client.ts       # join room, mic, audio playback, audio levels → orb
  lib/event-adapter.ts      # app-message → store (transport isolation)
  store/conversation.ts     # Zustand
  mocks/seed.ts             # preview data for non-live panels
  components/  VoiceOrb · TranscriptPanel · MicControl · TabBar · ProductCard
    panels/  CartPanel · ProductsPanel · RecipePanel · OffersPanel
             GroceryListPanel · ProfilePanel
```

## Deploy (WebStack)
`backend/infra/lib/web-stack.ts` (registered in `bin/aisle.ts` as `AisleWebStack`):
private S3 + CloudFront (OAC), SPA 403/404 → `/index.html`, deploys `frontend/dist`,
exports `/aisle/web/url`. Build `frontend/dist` first, then:
```bash
cd ../backend/infra && npm install
npx cdk deploy AisleWebStack
```
