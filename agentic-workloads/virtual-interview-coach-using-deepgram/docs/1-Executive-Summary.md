# InterviewCoach — Executive Summary

> **For:** Management / Sponsor review
> **Status:** Proof-of-concept (POC) proposal, pending build approval
> **Prepared by:** CTO Project Review
> **Date:** 2026-06-02

---

## Problem & Solution Overview

Undergraduate students preparing to enter the workforce get **at most one mock-interview session** with a career-coach teacher — and many get none. The constraint is human: career-coaching staff are scarce and their time is rationed. The students who most need repetition (the anxious, the inexperienced, those targeting competitive roles, or those who simply bombed their single session) have nowhere to practice and no professional feedback to learn from.

**InterviewCoach** removes the human bottleneck without removing the human. It is an AI-powered, voice-based mock-interview platform that gives undergraduates **unlimited, on-demand interview practice with personalized, professional-grade feedback**. A student inputs a target job and their resume, conducts a realistic spoken interview with an AI coach at a difficulty level they choose (Easy / Medium / Hard), and afterward receives a detailed written report — scores, per-question critique, a model "strong answer," and communication metrics — plus an immediate encouraging spoken wrap-up. Crucially, the platform **tracks progress across sessions**, so students can *watch themselves improve* over weeks of preparation.

The career-coach teacher is not replaced — they become the **capstone** rather than the only shot. The AI provides the reps; the human provides the final polish.

## Proposed Technical Solution

A **voice-first, AWS-native** architecture with a deliberate split by latency sensitivity:

- **Live conversation (hard real-time):** a streaming pipeline — Deepgram speech-to-text → LLM → Deepgram TTS — orchestrated by **Pipecat** over **WebRTC** (the transport built for real-time voice — it stays smooth on flaky WiFi), targeting a sub-1.2-second response gap so the interview feels natural. The web app and API sit behind a single **CloudFront** front door (WAF-protected); the live audio takes a direct, lower-latency path to the voice service.
- **Interviewer brain & personalization:** **AWS Bedrock AgentCore** holds the resume context, the difficulty persona, and longitudinal memory across a student's sessions.
- **Feedback (asynchronous):** the heavy analysis — scoring, STAR-structure detection, strong-answer generation, voice metrics — runs as a background job *after* the call, where latency is irrelevant and a more thorough model can be used.

**What is deliberately deferred:** video recording and body-language/facial analysis (originally envisioned, explicitly out of V1); scholarship and university-admissions scenarios (the engine is built to generalize, but only the job-interview scenario ships first); mobile (desktop-web first, where browser microphone and real-time audio behavior are predictable); and a younger (under-18) audience, which is excluded specifically to avoid COPPA/FERPA/parental-consent burden.

**POC engineering stance:** Build AgentCore-first across the whole loop to validate the personalized experience on one coherent runtime. Treat a ~1.2s live response gap as a measured release gate; if AgentCore cannot hold it in the live loop, a thin swappable interface lets the per-turn call fall back to direct Bedrock without disturbing personalization or the report layer.

## Resource Requirements

The build will use an **AI-agent SDLC** (a team of AI coding agents), so traditional headcount and calendar estimates are not the planning unit. The roadmap is structured as **dependency-ordered capability gates** rather than staffed weeks. The scarce resource shifts from *coding throughput* to **human verification and integration** at each gate — concentrated on the two genuinely hard parts: the real-time voice loop and the consistency of the scoring rubric.

| Resource | POC need |
|---|---|
| Compute | Small always-on container for the Pipecat voice service (ECS Fargate / EC2 — the WebRTC connection must persist, not Lambda); CloudFront (+ WAF) fronting the web + API; an NLB + managed TURN relay for the direct media path |
| Data | RDS Postgres (users, sessions, session turns, scores, progress); S3 with SSE-KMS (resumes + per-turn audio, paths stored in Postgres) |
| AI services | AWS Bedrock AgentCore; Deepgram STT + Aura TTS |
| Auth & ops | AWS Cognito; CloudWatch logs/metrics/billing alarms |
| Human time | Verification at each capability gate; privacy/consent review before storing recordings |

**Ballpark cost driver:** real-time voice is **per-minute** (STT + LLM + TTS stacked). The dominant financial risk is not heavy usage but a *stuck-open/abandoned session*. A server-side inactivity timeout (safety valve) is included to cap that exposure; otherwise spend is soft-monitored via CloudWatch billing alarms for the POC.

## Proposed Timeline & Key Milestones

Expressed as **capability gates** (AI-SDLC; gates, not dates). Each gate is a measurable checkpoint before the next phase begins.

| Gate | Capability | Exit criterion (measurable) |
|---|---|---|
| **G1 — Voice loop** | Live two-way voice conversation works | Response-gap p50 < 1.0s, p95 < 1.5s over a 10-turn session |
| **G2 — Personalization** | Resume + job drive tailored questions; difficulty changes interviewer behavior | Questions verifiably reference resume facts; E/M/H produce distinct interviewer behavior |
| **G3 — Feedback report** | Async report with scores, per-Q critique, strong answers, voice metrics | Same answer scored 3× varies < 0.5 points on a 10-pt scale (rubric consistency) |
| **G4 — Spoken wrap-up bridge** | Immediate qualitative spoken close + processing/return flow | Wrap-up plays < 10s after session end; no score contradiction with report |
| **G5 — Progress & history** | Session history + progress chart; the "watch yourself improve" loop | A returning student sees prior sessions and a trend across ≥2 sessions |
| **G6 — Privacy & consent** | Consent, encryption, retention, one-click delete | Recording requires explicit consent; delete removes audio+transcript+scores |

## Risks & Mitigations

Includes every CTO flag raised during review.

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| F2 | Real-time voice is the hardest possible MVP core (latency, turn-taking, barge-in) | High | High | Pipecat streaming pipeline; stream first-token→TTS; 1.2s response gap as a release gate |
| F3 | "Progress over time" forces a stateful product (accounts, longitudinal store, consistent scoring) from day one | High | Medium | Accept as the core wedge; invest early in a consistent scoring rubric so improvement is real signal |
| F4 | Resume/job ingestion is a real pipeline with failure modes (bad PDFs, odd formatting) | Medium | Medium | Parse-back confirmation screen; graceful fallback to manual entry |
| F6 | Instant spoken wrap-up may contradict the later scored report, eroding trust | Medium | High | Keep the spoken wrap-up **qualitative and score-free**; all numbers live only in the written report |
| F7 | AgentCore in the live voice loop is an unproven latency bet | Medium | High | POC accepts it deliberately; instrument response-gap; swappable per-turn path falls back to direct Bedrock |
| F8 | Storing raw voice recordings = custodian of sensitive (biometric-adjacent) personal data | High | High | Explicit consent, SSE-KMS encryption, finite retention, one-click delete, private signed-URL access, region pinning |
| F9 | "Soft monitoring only" + per-minute voice + always-on service = runaway-bill risk (stuck-open mic) | Medium | High | Server-side inactivity timeout (auto-end) as a non-intrusive safety valve; CloudWatch billing alarms |
| F10 | AI-agent SDLC shifts risk from throughput to verification/integration | High | Medium | Every phase ends in a concrete measurable acceptance gate; human judgment concentrated at gates |
| F11 | An "always-up" adjusted score would make the north-star metric ("measurable improvement") unfalsifiable and erode trust | Medium | High | Use an absolute level-independent rubric + difficulty ladder + within-level trend — two honest progress signals, no constructed number |
| F12 | A teacher dashboard creates a multi-party consent problem with a chilling effect on private practice | Medium | Medium | Deferred to V2; V1 designs the org/cohort/opt-in-share schema; the eventual view is scores/trends-only with student opt-in, never raw audio |
| F13 | Dynamic follow-up questions can drift sessions apart, eroding the cross-session comparability that makes progress trustworthy | Medium | Medium | Scope follow-ups to depth *within* the current competency; archetype bank anchors main questions; intensity scales with difficulty; watch the live-latency gate (interacts with F7) |
| F14 | Duplicating raw PII (resume text, transcripts, audio) into AgentCore memory would create a second, harder-to-audit custodian of sensitive data and break the one-click-delete guarantee | Medium | High | **RDS + S3 are the sole system of record** for raw PII — resume in S3 (path on `users`), job scope on the session, per-turn transcript + audio in `session_turns` / S3; AgentCore holds only *derived* coaching signals, never durable raw PII, so deletion has a bounded blast radius |
| F15 | CloudFront cannot carry WebRTC media, so fronting everything with the CDN would force a lower-quality audio transport and risk the sub-1.2s voice target | Medium | Medium | Split the planes: CloudFront (+ WAF) fronts web + API; **WebRTC/SRTP audio goes direct** to the voice service for best quality and lowest latency. Provision a **TURN relay** so users on UDP-blocked campus/corporate networks still connect |

*Retired during review:* **F1** — recording minors (COPPA/FERPA/parental consent) — eliminated by scoping the audience to undergraduates aged 18+.

## Strategic Importance

**Market opportunity.** Every graduating undergraduate is a potential user, every year, in perpetuity. The natural buyers are **universities and career-services departments** that today cannot meet demand with human coaches — InterviewCoach lets them offer unlimited practice at a fixed software cost instead of an unscalable human one.

**Competitive wedge.** The status quo splits into two inadequate options: a *human coach* (high quality, but one session, scheduled, rationed) and *ChatGPT* (unlimited, but text-based, stateless, generic). InterviewCoach occupies the gap neither fills: **realistic voice practice + personalized feedback + unlimited reps with visible longitudinal progress.** No competitor today lets a student practice eight times and *watch their filler-words drop and their STAR-structure score climb.* That progress narrative is the defensible core.

**Layered upside.** The engine is "AI mock interviewer + personalized feedback"; "job interview" is merely the first scenario. The same platform extends to **scholarship panels, university and graduate-school admissions**, and — for a later, deliberately compliance-resourced phase — **adult mid-career switchers** and **high-school students**. The POC proves one scenario; success unlocks an adjacent set of high-stakes-interview markets with the hard engineering already paid for.

**The POC's job** is to prove the wedge with one number: *the percentage of students who practice three or more times and measurably improve across sessions.* If that holds, the strategic case writes itself.
