# InterviewCoach — Delivery Roadmap (POC → Comprehensive V1)

> **Audience:** Product + engineering, planning the path from the proven G1 voice loop to a
> feature-complete V1.
> **Author stance:** senior product manager.
> **Date:** 2026-06-04
> **Status:** Roadmap. Each numbered feature below becomes a Spec-Kit `specs/00X-*` cycle
> (specify → clarify → plan → tasks → implement) executed in dependency order.

This document is the bridge between the *what/why* (already settled in the Constitution,
Product Spec, Technical Spec, and the Feature-002 brief) and the *in-what-order/how-we-prove-it*.
It does not re-litigate committed decisions; it sequences the remaining work into shippable,
gate-aligned features and states the measurable exit criterion for each.

---

## 1. Where we are (honest baseline)

G1 (Feature 001) is **done and proven**, but it shipped only the live voice loop — not the
product around it. Code-level truth as of this roadmap:

| Layer | Built today | Notes |
|---|---|---|
| Voice loop | STT (Deepgram) → swappable reply seam → Aura TTS over WebRTC/SRTP | turn-taking, barge-in, PTT, endpoint watchdog all working |
| Latency | **SC-001 PASS** via lead-clause (gap p50 ~544ms) | `gate-decision.md`; AgentCore reply TTFT ~3.6s, bedrock_direct ~814ms |
| Data | **3 tables**: `voice_session`, `conversation_turn`, `turn_latency` | a thin latency-proof slice, NOT the product data model |
| Backend API | **4 endpoints**: create / end / read / latency on `/sessions` + Cognito JWT + voice-token mint | no resume, job, report, history, consent endpoints |
| Frontend | **2 screens**: `Session.tsx`, `DeviceCheck.tsx` (+ webrtc/auth/api libs) | live interview + mic pre-flight only |
| Reply seam | `agentcore`, `bedrock_direct` providers behind `interface.py` | the mandatory swappable seam exists and is the integration point for 002+ |
| Deploy | Hosted demo on the full-shaped stack (ECS Fargate, ALB, CloudFront, RDS, Cognito) | demo-scale, single task, no autoscaling |

**Reading:** the riskiest gate (latency, G1) is retired. Everything remaining is product
breadth on a proven real-time foundation — lower technical risk, higher surface area.

---

## 2. Where we're going (definition of "comprehensive V1")

The feature-complete V1 is already fully specified across the Constitution (gates G2–G6), the
Product Spec (MoSCoW MUSTs), and the Technical Spec (FR-1…FR-14, +002 FRs). In one paragraph:

> A signed-in student uploads a resume and a job, picks a difficulty, and holds a realistic
> spoken interview with an HR-grade coach that asks resume/role-grounded questions from a vetted
> bank and probes with STAR follow-ups. On ending they hear a warm score-free wrap-up, then
> receive an evidence-anchored written report (competency scores + transcript quotes + strong
> answers + voice metrics). Sessions, audio playback, and an honest progress trend persist under
> explicit consent with one-click delete — all wrapped in the polished prototype UI.

"Near completion" = gates **G2 through G6 passed** + the UI productization pass, with the
Constitution's three NON-NEGOTIABLEs (latency, honest measurement, privacy-by-architecture)
demonstrably upheld at each gate.

---

## 3. The gap (capability inventory)

| Capability | Spec ref | Built? | Lands in |
|---|---|---|---|
| Real account signup/login UX | A1 / FR-1 | Partial (JWT only) | F005 |
| Resume upload + parse-back + persist | A2 / FR-2 | No | **F002** |
| Job title/description input | A3 / FR-3 | No | **F002** |
| Full product data model (~9 tables) | Tech §4 | No (3-table slice) | **F002** (foundation) |
| Question bank: offline gen + review gate | C0 / FR-2c | No | **F002** |
| Question retrieval: pgvector JD-rank | FR-2d | No | **F002** |
| HR persona: STAR + funnel methodology | C1c / FR-6c | No | **F002** |
| Difficulty profiles (multi-dimensional) | B2 / FR-5 | No | **F002** |
| Dynamic follow-ups wired to bank | C1b / FR-6b | Partial (loop only) | **F002** |
| Async path: SQS + Report Worker | Tech §3 | No | **F003** |
| Scoring rubric + per-Q feedback | D1,D2 / FR-8,9 | No | **F003** |
| Evidence-anchored competency scorecard | D5 / FR-8b | No | **F003** |
| Voice/communication metrics | D3 / FR-10 | No | **F003** |
| Spoken score-free wrap-up | C2 / FR-7 | No | **F004** |
| Session history + progress (ladder+trend) | E1,E2 / FR-11,12 | No | **F005** |
| Consent / retention TTL / delete / export | G1 / FR-14 | No | **F006** |
| Audio recording + per-turn playback | F1 / FR-13 | No | **F006** |
| Polished UI across all 11 screens | Screens S1–S11 | No (2 screens) | **F007** |

---

## 4. Sequencing principles

1. **Gate-aligned, dependency-ordered (Constitution IV).** Each feature retires exactly one
   capability gate with a measurable exit criterion; nothing downstream assumes an unproven
   capability upstream.
2. **Data model is the foundation.** The ~9-table model migration leads Feature 002, because
   personalization, reports, history, and privacy all read/write it. Build it once, correctly.
3. **Protect the NON-NEGOTIABLEs continuously, not at the end.** Latency (`response_gap`),
   scoring consistency (rubric variance), and privacy (consent/delete) are measured/enforced from
   the build that first touches them — they are task types in every feature, not a final gate.
4. **Keep heavy work off the live path.** Bank generation is offline; bank retrieval is a
   session-prep DB query; scoring is async. Only dynamic follow-ups generate live, behind the
   existing seam. Re-measure SC-001 after F002 (the only feature that adds to the live turn).
5. **Functional UI inline, attractive UI last.** Each feature ships the *minimal* screens needed
   to exercise and verify it. Feature 007 is the design-system pass that ports the prototype
   look-and-feel across everything — the explicit final stage.
6. **One feature = one branch = one Spec-Kit cycle.** Branch `00X-name` off `master`, run
   specify→clarify→plan→tasks→implement, pass the gate, merge.

---

## 5. The feature roadmap

Six features take us from G1 to V1. Each is a self-contained Spec-Kit cycle.

### Feature 002 — Personalization & Question Intelligence  → Gate **G2**
**Why first:** it builds the data-model foundation and the personalization wedge that every later
feature depends on. It is also the only remaining feature that touches the live turn, so its
latency re-measurement de-risks the rest.

- **Scope:** full data-model migration (User, InterviewSession w/ job scope, SessionTurn
  expansion, QuestionArchetype, DifficultyProfile + V2 Org/Cohort hooks); resume upload + parse-
  back + profile persistence; job input; offline LLM question-bank generation pipeline + review
  gate; pgvector retrieval at session-prep; HR persona (STAR + funnel) prompt template; difficulty
  profiles wired into prompt; dynamic follow-ups bound to the selected archetype.
- **Key decision to resolve in clarify:** the substantive reply backend. AgentCore is the
  committed primary but its reply TTFT (~3.6s) is poor; bedrock_direct is ~814ms. The lead-clause
  hides first-audio latency, but richer personalized questions raise the stakes. Decide the
  default provider for 002 (the seam keeps it swappable).
- **Exit criterion (G2):** questions verifiably reference resume facts; the bank serves an
  approved, JD-ranked plan from the DB with no live LLM; Easy/Medium/Hard are behaviorally
  distinct; **SC-001 still passes** on the enriched loop.
- **Primary risks:** data migration correctness; bank quality from generation (the review gate
  mitigates); follow-up latency on the live path (F7 fallback).

### Feature 003 — Feedback Report Engine  → Gate **G3**
**Why next:** the report is the product's "money screen" and depends on the data model + a
completed interview (F002). It is fully async, so it cannot regress latency.

- **Scope:** async infra (SQS + Report Worker on Fargate); fixed level-independent scoring rubric
  (+ rubric versioning); per-question feedback (what-worked / improve / strong-answer from the
  student's own material); voice/communication metrics (filler, pace, pauses, conciseness,
  hedging, responsiveness); evidence-anchored competency scorecard (1–5 anchors + verbatim
  transcript quote + STAR coverage); report storage + retrieval API.
- **Exit criterion (G3):** the same answer scored 3× varies < 0.5 points (NFR-8); every
  competency score carries a real, present-in-transcript evidence quote.
- **Primary risks:** rubric consistency (the gate itself); strong-answer quality not drifting into
  generic scripts (Principle V).

### Feature 004 — Spoken Wrap-up Bridge  → Gate **G4**
**Why here:** small, depends on a finished session (F002) and report path (F003), and closes the
async "return moment" loop.

- **Scope:** immediate qualitative, **score-free** spoken wrap-up from warm conversation context;
  the processing/return UX (no dead spinner) that bridges to the async report.
- **Exit criterion (G4):** wrap-up plays < 10s after session end and contains no numeric scores
  (no contradiction with the report, Flag F6).
- **Primary risks:** wrap-up vs. report contradiction (kept qualitative by design).

### Feature 005 — Accounts, History & Progress  → Gate **G5**
**Why here:** needs ≥2 scored sessions to exist (F003) before a trend is meaningful.

> **DELIVERED by Feature 008 (Session Review & Coaching Insights, 2026-06-12)** — implemented as a
> session-history picker + per-session reports with full transcripts + a cross-session **coaching
> dashboard** (prose guidance regenerated after each scored session: recurring strengths/weaknesses,
> an honest trend note, prioritized next actions). Honest-signal rules upheld: no difficulty-blended
> composite exists; trend wording is rubric-version-aware. Real self-serve signup (Cognito sign-up +
> 18+ attestation) remains future work; the demo pool is admin-create-only.

- **Scope (as originally planned):** real signup/login UX (Cognito school-email/Google, 18+
  attestation); session history list; progress dashboard with the **two honest signals** — difficulty
  ladder + within-level absolute trend (never a blended always-up score); empty-state first-run
  experience.
- **Exit criterion (G5):** a returning student with ≥2 scored sessions sees a trend across them;
  no difficulty-blended composite is computed or shown (Principle II / Flag F11).
- **Primary risks:** the temptation of a constructed climbing score (explicitly forbidden).

### Feature 006 — Privacy, Consent & Recording  → Gate **G6**
**Why here:** consent gates the first stored recording, and the heaviest privacy machinery
(retention TTL, delete fan-out, signed-URL playback) is cleanest once the entities it must purge
all exist (F002/F003/F005).

- **Scope:** explicit specific consent at signup + before first recording; per-turn audio
  recording to S3 (SSE-KMS) with consent; per-answer playback via short-lived per-user signed
  URLs; finite retention (30-day default) with a "keep" override + TTL cleanup job; one-click hard
  session delete (bounded blast radius) + full account export/delete.
- **Exit criterion (G6):** recording requires explicit consent; delete purges audio + transcript +
  scores with no residual (incl. no raw PII in AgentCore); playback only via signed URL.
- **Primary risks:** deletion fan-out completeness; consent-state correctness gating recording.

### Feature 007 — UI/UX Productization (prototype port)  → not a Constitution gate; the V1 finish
**Why last (user-directed):** the design pass that makes every screen attractive and cohesive,
applied once the functionality underneath is proven.

- **Scope:** port the `docs/prototype` design system (the "calm, encouraging" visual language)
  across all 11 screens (S1–S11); replace the minimal inline UIs from F002–F006 with the polished
  components; responsive/mobile pass (COULD); WCAG accessibility pass.
- **Exit criterion:** all 11 prototype screens are implemented to the design system; the
  end-to-end flow (Section 6 of the Product Spec) is walkable in the polished UI.
- **Primary risks:** scope creep into new features under the banner of "polish" (hold the line).

---

## 6. Critical path & cross-cutting workstreams

**Critical path (each arrow = hard dependency):**
```
F001 (done) → F002 (data model + personalization) → F003 (report)
                                                        ├→ F004 (wrap-up)
                                                        └→ F005 (history/progress)
F002 + F003 + F005 ─────────────────────────────────→ F006 (privacy/recording)
all of the above ───────────────────────────────────→ F007 (UI polish)
```
F004 and F005 can run in parallel after F003. Everything else is linear.

**Cross-cutting (threaded through every feature, not a separate phase):**
- **Data model migration discipline** — versioned migrations; the 3-table G1 slice is extended,
  not replaced; backward-compatible with the deployed demo.
- **Observability (Constitution: a task type)** — `response_gap`, rubric variance, report-job
  success/duration, cost-per-session in CloudWatch as each feature adds its surface.
- **Privacy (Constitution: a task type)** — even before F006, no feature may write raw PII into
  AgentCore or logs; deletion fan-out is considered as each entity is introduced.
- **Cost** — offline bank generation and the async report worker add spend; bound batch sizes;
  keep demo-scale single tasks until load justifies autoscaling.

---

## 7. Key decisions to resolve (in each feature's clarify phase)

| # | Decision | Where | Default lean |
|---|---|---|---|
| K1 | Substantive reply provider (AgentCore vs bedrock_direct) given the 3.6s vs 0.8s TTFT gap | F002 | bedrock_direct primary for live quality; AgentCore behind the seam; revisit for memory |
| K2 | Archetype bank breadth for V1 (how many domains/role families to seed) | F002 | one strong general family + 2–3 industry variants |
| K3 | Embedding model + pgvector index strategy | F002 | Bedrock Titan embeddings; ivfflat/hnsw per volume |
| K4 | Rubric design + few-shot anchoring to hit < 0.5pt variance | F003 | fixed rubric + temperature 0 + self-consistency check |
| K5 | "Keep" storage cap per student (bound cost) vs unlimited | F006 | a generous per-student cap |
| K6 | Mobile/responsive in V1 (COULD) or deferred | F007 | desktop-first; responsive if cheap |

---

## 8. Definition of done (comprehensive V1)

- Gates **G2–G6** each passed against their measurable exit criteria (Section 5).
- All Product-Spec **MUST** features implemented; all Technical-Spec **FR-1…FR-14 + 002 FRs**
  traceable to a passing acceptance criterion.
- The three NON-NEGOTIABLEs hold: SC-001 latency re-measured and passing on the full loop; rubric
  variance < 0.5pt; consent-gated recording with bounded-blast-radius delete.
- The full new-user journey (Product Spec §6) is walkable end-to-end in the polished prototype UI.
- Honest demo-scale caveats documented (single task, no autoscaling, managed TURN, demo Cognito).

---

## 9. Immediate next step

Kick off **Feature 002** as a fresh Spec-Kit cycle:
```
branch:   002-personalization-question-intelligence  (off master)
commands: /speckit.specify → /speckit.clarify (resolve K1–K3) → /speckit.plan → /speckit.tasks
inputs:   docs/002-interview-intelligence-plan.md, docs/2-Product-Specification.md,
          docs/4-Technical-Specification.md, .specify/memory/constitution.md
```
The existing `docs/002-interview-intelligence-plan.md` is the pre-spec brief for this cycle; the
specify step turns it into `specs/002-*/spec.md`.
