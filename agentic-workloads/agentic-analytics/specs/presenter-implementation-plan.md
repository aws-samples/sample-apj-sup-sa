# Presenter Mode — Implementation Plan (hackathon, ~6h to video)

Derived from `specs/voice-presenter-mode.md`. Triaged for a recorded-video deadline against a **working** demo. Principle: protect the working pipeline; ship camera-visible value first; defer invisible/high-risk work.

## Slices, by value × safety

| # | Slice | Camera value | Risk | Decision |
|---|---|---|---|---|
| 1 | **Presenter split-output** (SOP `<speak>` + bot splitter) | HIGH — agent speaks summary, shows full data | LOW (graceful fallback) | **DO NOW** |
| 2 | Charts via Code Interpreter | HIGH | MED (agent redeploy) | stretch if time |
| 3 | Dashboard voice (option 3) in `client/` | HIGH | HIGH (new UI, WebRTC in CRA) | defer / fast-follow |
| 4 | Pipecat Cloud hosting | NONE on camera | MED | defer |
| 5 | JWT `/start` proxy | NONE on camera | MED | defer (demo uses ROPC) |
| 6 | CFN-pluggable voice restructure | NONE on camera | HIGH | defer (structure for it, don't wire) |

## Slice 1 (this turn)

1. `server/unicorn_rental_voice.sop.md` → presenter contract: one leading `<speak>…</speak>` (1–3 conversational sentences, verbal numbers, no markdown), then full displayed answer; never speak tables/SQL/UUIDs.
2. `server/analytics_processor.py` → stream splitter: speak only `<speak>` inner (incrementally, low latency); push post-`</speak>` content as display-only (skip_tts) so the transcript shows the full answer but Aura-2 doesn't read tables. Fallback: no markers → speak first sentence, display rest (never read whole markdown).
3. Upload SOP to S3 (`sops/unicorn_rental_voice.sop.md`) — no agent redeploy needed.
4. Headless test against the live backend: assert spoken track is short prose, displayed track carries the table, and the table text is NOT in the spoken track.

## Repo decoupling (as requested, structural only this turn)

- All app code already lives at repo surface (`server/`, `scripts/`); `resources/` stays reference/archive.
- The agent-side presenter SOP is OUR artifact in `server/`; it is uploaded to S3 at deploy time. The agent CODE change that loads SOP-by-key already exists in the vendored agent (synced). For the eventual merge back to agentic-analytics, the voice SOP + bot live in `server/` and are additive.
- Voice remains a per-request MODE (`sop_s3_key` + `mode:voice`), so a CFN `EnableVoice` flag later only needs to (a) deploy the bot/proxy and (b) ship the voice SOP — the agent is unchanged when voice is off. Documented; not wired this turn.

## Deferred (fast-follow, post-video)

Charts, dashboard `client/`, Pipecat Cloud + `pcc-deploy.toml`, JWT `/start` proxy (API GW + Lambda), CFN `EnableVoice` parameter gating the voice resources. All specified in `specs/voice-presenter-mode.md`.
