# InterviewCoach — Demo Write-up

A voice-first AI mock-interview platform. This document answers the six demo questions, grounded in
what is actually built and deployed (not aspirational).

**Live demo:** https://dukfh8o2yf4fg.cloudfront.net
**Test login:** `demo@interviewcoach.test` / `DemoPass!2026`

---

## 1. What did you build, and what problem does it solve?

**The problem:** Interview practice is the single biggest predictor of interview success, but it doesn't scale. A career-coach teacher can give a student *at most one* mock session, and many students get none. Existing free tools are text chatbots — they don't capture the thing that actually makes interviews hard: **speaking out loud, in real time, under a little pressure, and getting feedback you trust.**

**What:** A real-time, voice-first mock-interview coach. A student signs in, consents, uploads a resume, pastes a target job description, picks a difficulty and length — then has a **spoken** interview with an AI interviewer that asks personalized questions, listens, and probes follow-ups like a real recruiter. At the end it speaks a short qualitative wrap-up, then produces an evidence-anchored written **scored report**.


**What makes it different (the wedge):**
- **It's voice, in real time.** You talk; it talks back within ~1 second. It feels like a conversation,
  not a form.
- **It's personalized.** Every question is grounded in *your* resume and *this* job — and the
  "strong-answer" examples in the report are built from your own background, not generic scripts.
- **The feedback is honest and trustworthy.** Scores are on a fixed, level-independent rubric; every
  competency score is anchored to a **verbatim quote you actually said** (never fabricated); the same
  answer scored repeatedly varies < 0.5 points. There is no "always-goes-up" vanity score.
- **Privacy is structural.** Recording is consent-gated, audio is encrypted, retained 30 days, and a
  one-click delete removes everything (audio + transcript + scores) with no residual.

---

## 2. Who is the end user? Industry / persona.

**Primary persona — the anxious, first-time job seeker.** Undergraduates and recent grads (18+)
preparing to enter the workforce, who:
- have a real interview coming up and no one to practice the *spoken* part with,
- feel nervous talking out loud and want unlimited low-stakes reps,
- want feedback that's specific to their resume and target role, not generic tips.

**Industry / distribution context:**
- **Higher-ed career services & bootcamps** — the natural channel. Career-coach teachers are the
  bottleneck today (one mock per student at best); InterviewCoach removes that bottleneck with
  unlimited on-demand practice, and the schema is designed (V2 hooks) for cohort/teacher dashboards.
- **Adjacent:** any high-volume, coaching-constrained hiring funnel — early-career hiring programs,
  workforce-reentry and upskilling programs.

The product is deliberately scoped **18+** to stay clear of COPPA/FERPA/parental-consent obligations
until a deliberately compliance-resourced phase.

---

## 3. Demo walkthrough — end to end

The deployed flow (each screen is implemented and live):

1. **Sign in.** Cognito email/password. (`demo@interviewcoach.test` / `DemoPass!2026`.)
2. **Consent.** A clear "what we keep and for how long" card, plus a **recording toggle** ("Record my
   answers so I can play them back"). Recording is opt-in; with it off, the interview still runs and
   still produces a transcript + report, just no stored audio.
3. **Resume.** Upload a PDF/DOCX/TXT (or reuse a saved one). The system parses it and shows the
   extracted facts for you to **confirm/correct** — these become authoritative for the interview.
4. **The role + difficulty + length.** Paste the job description, pick **Easy / Moderate / Difficult**
   (gentle-and-encouraging → demanding-panel), and an interview **length** (3 / 5 / 10 / 15 / 30 min;
   3 min is a "quick test drive") which sizes the number of questions (~90s each).
5. **Mic check.** A live level meter confirms the microphone, and you choose turn-taking: hands-free
   (the coach replies when you pause) or hold-to-talk.
6. **The live interview.** The coach asks an opening question and you answer **out loud**. It listens,
   replies within ~1 second, and asks personalized questions + contained follow-ups (STAR: situation /
   task / action / result), staying within one competency before moving on. The interview **wraps up on
   its own** at the chosen length — it won't run over.
7. **Spoken wrap-up.** The coach speaks a warm, **score-free** qualitative debrief — one genuine
   strength and one thing to work on, grounded in what you actually said — and tells you the written
   report is being prepared. (No numbers spoken, so the wrap-up can never contradict the report.)
8. **Processing → scored report.** Scoring runs **asynchronously** (off the live path). When ready, the
   report shows: an overall + four sub-scores (content, structure/STAR, communication, confidence) on
   a 0–10 fixed rubric; a **competency scorecard** where every 1–5 score carries a **verbatim quote**
   from your transcript; voice/communication metrics (filler words, pace, pauses); and per-question
   feedback with a "strong answer built from *your* background." If you recorded, you can **play back**
   each answer via a short-lived private link. The report header labels which practice you are
   viewing (latest by default); any past session's report is one click away from the **Dashboard's**
   session history. Every report includes the **full interview transcript** — every question and
   answer, readable even while scoring is still running.
9. **Coaching dashboard.** After your sessions are scored, the dashboard shows your **coach's
   notes** — prose synthesized across ALL your sessions: what keeps showing up as a strength, what
   keeps recurring to work on, an honest trend note, and 2–3 prioritized next actions, stamped with
   when they were generated. (Refreshed automatically after each scored session.)
10. **Privacy controls.** One-click delete (session or whole account) removes audio + transcript +
   scores + coaching notes with zero residual; recordings auto-delete after 30 days.

**What to watch for in the live demo:** the **sub-second turn-taking** (it feels like talking to a
person), the **personalized** questions referencing your resume, the **honest** report (some
competencies may be marked "not assessed" rather than given an invented score), and that the interview
**ends itself** at the chosen length.

---

## 4. Architecture & services

A 4-component system on AWS (us-west-2), provisioned by one CloudFormation stack.

```
            Browser (React SPA)
              │            │  WebRTC / DTLS-SRTP (direct media,
   HTTPS via  │            └─────────────┐  bypasses CDN)
   CloudFront │                          ▼
        ┌────────────┐ /api* ┌───────┐  ┌──────────────┐
        │ CloudFront │──────▶│  ALB  │─▶│ Voice Worker │ [1]
        │  + S3 SPA  │ /offer└───────┘  │ (ECS Fargate)│
        └────────────┘          │       └──────┬───────┘
                                ▼              │ writes turns
                         ┌────────────┐        ▼
                         │  Backend   │  ┌──────────────────┐
                         │ (Fargate,  │─▶│ RDS Postgres +   │
                         │  FastAPI)  │  │ pgvector (PII)   │
                         └──────┬─────┘  └─────────┬────────┘
                                │ on /end:         ▲ scores
                                ▼  enqueue         │
                         ┌────────────┐  ┌──────────────────┐
                         │    SQS     │─▶│ Report Worker    │ [2]
                         └────────────┘  │ (ECS Fargate)    │
                                         └──────────────────┘

  [1] Voice Worker: Pipecat pipeline · Deepgram STT + Aura TTS ·
      Silero VAD (barge-in) · Bedrock Haiku 4.5 (live reply)
  [2] Report Worker: Bedrock Haiku 4.5 (async scoring + retention
      + cross-session coaching guidance)

  PII at rest: S3 (SSE-KMS) for resumes + per-turn audio ·
  RDS for transcripts/scores · Cognito auth
```

**Services actually used (verified against the code + IaC):**

| Service | Role |
|---|---|
| **Deepgram** | Streaming **STT** (live transcription) + **Aura TTS** (the coach's voice). |
| **WebRTC (Pipecat SmallWebRTCTransport / aiortc)** | Browser ↔ voice-worker **direct media path** (DTLS-SRTP), with STUN for NAT traversal — media does not go through the CDN/ALB, which is what keeps latency low. |
| **AWS Bedrock** | **Claude Haiku 4.5** for the live interviewer reply (streaming first-token → TTS) AND for async scoring (temperature 0, median-of-3 self-consistency). **Titan Embed v2** (1024-dim) embeds the JD + question bank for semantic retrieval. |
| **RDS PostgreSQL + pgvector** | Sole durable store for raw PII (transcripts, scores, resume facts) + **pgvector** IVFFlat for JD-ranked question retrieval at session-prep (off the live path — no LLM in the live selection). |
| **ECS Fargate ×3** | Three always-on services: voice-worker (live loop), backend (FastAPI API + auth + media token), report-worker (SQS-consumed async scorer + retention sweep + coaching-guidance refresh). |
| **SQS** | Decouples scoring from the live path: `POST /sessions/{id}/end` enqueues a job and returns immediately; the report-worker consumes and scores. |
| **Amazon Cognito** | Email/password auth; the SPA sends the ID token, the backend validates `aud`, and the worker verifies a short-lived per-session media-join JWT. |
| **S3 + KMS (SSE-KMS)** | Encrypted PII at rest: resumes and per-turn audio, each under its own customer-managed KMS key; audio is private (signed-URL playback only). |
| **CloudFront + S3** | Serves the React/TypeScript SPA and fronts `/api/*` + `/offer` as a single HTTPS origin. |
| **CloudFormation / CodeBuild** | One-stack IaC; images built in CodeBuild (no local Docker dependency). |

**Services we deliberately did *not* use:**
- **Pipecat Cloud** — the worker runs the **Pipecat library self-hosted** on our own Fargate task
  (G1 was first proven on a hand-written aiortc loop; F007 migrated it onto Pipecat 1.3 and re-proved
  the latency gate). The hosted Pipecat Cloud is deliberately forbidden: it would move PII off our
  RDS+S3-only topology and reopen the latency question.
- **Amazon Connect / SageMaker** — not used. (No telephony need; no custom model training — Bedrock
  foundation models cover both reply and scoring.)
- **Bedrock AgentCore** — the reply generator is behind a **swappable interface** with an AgentCore
  implementation, but the deployed provider is **direct Bedrock** (`bedrock_direct`) because it met the
  sub-second latency gate; AgentCore remains the documented fallback.

**The load-bearing architectural decisions:**
- **Scoring is fully asynchronous and off the live path.** The live turn loop never calls the scorer —
  it only enqueues to SQS on `/end`. This is what protects the < 1s response-gap latency.
- **WebRTC media is direct browser↔worker**, not proxied through the CDN/ALB, for the same reason.
- **Privacy is a property of the topology**, not a feature: RDS + S3 are the only durable PII home,
  encrypted, consent-gated, with a bounded one-click delete.

---

## 5. What surprised us

- **Latency was won by a trick, not a framework.** The gate-passing lever is the **"lead clause"**:
  the instant the student stops, the coach speaks a short bridge phrase while the LLM generates
  concurrently behind it — taking the model's first-token time off the critical path entirely. G1
  proved it on a hand-rolled aiortc loop; when we later migrated onto the **Pipecat library** (F007)
  we kept that lever as a custom processor and A/B-proved it again: lead-clause **p50 290ms** vs the
  native LLM-on-the-clock arm at 1504ms (recent live sessions: p50 ≈ 130ms). The framework changed;
  the trick is what holds the sub-second promise.
- **Honest scoring is an *engineering* problem, not a prompting one.** Getting < 0.5-point variance and
  zero fabricated evidence wasn't about a cleverer prompt — it was **temperature 0 + median-of-3
  self-consistency**, and **post-hoc substring validation**: the model proposes a quote, and we drop it
  unless it's verbatim-present in the transcript. Trust came from mechanics you can test, not from
  trusting the model. (Live: 5 sessions, 0.0-point spread, 100% evidence-present.)
- **The recurring bug taught the real lesson about deadlines.** "The interviewer is too eager / runs
  over the chosen time" came back *three times*. Each fix tuned the same **reactive** check (we only
  looked at the clock when the student happened to speak again). The permanent fix was architectural: an
  **independent deadline timer** that fires wrap-up on its own, regardless of student behavior. The
  insight — *a bound that depends on the user cooperating isn't a bound* — generalized.
- **A multi-agent code review caught a real CRITICAL.** A parallel, adversarially-verified review found
  an **IDOR**: four authenticated endpoints checked *that* you were logged in but not *who* you were, so
  any user could read another's transcript/scores. It also caught a self-inflicted footgun — CloudFront
  was rewriting backend 404s into 200 SPA pages, masking API errors during debugging.
- **The smallest CSS detail nearly shipped broken.** A consent card collapsed into thin slivers because
  a `<label>` defaults to `display: inline`; the styled box fragmented around the inline run. A
  one-line fix — but a reminder that the polish layer has its own sharp edges.

---

## 6. If we had another week

In priority order:

1. **Real self-serve signup.** Cognito sign-up + email verification + 18+ attestation (the pool is
   admin-create-only today). Session history, per-session reports with full transcripts, and the
   cross-session coaching dashboard (Gate G5) ARE now built — what remains of the original G5 vision
   is the optional difficulty-ladder visualization on top of the honest per-rubric signals.
2. **Production-hardening leftovers.** The end-to-end record→playback→delete loop has now been
   driven on real spoken sessions (recording verified audible, deletes verified zero-residual); what
   remains are the code-review's accepted-deferred items: HTTPS on the CloudFront→ALB origin, and
   moving the retention sweep to an independent scheduled job so deletion survives a worker outage.
3. **Deepen the question bank + JIT quality.** Run the offline generation pipeline at full breadth per
   role family, and add a lightweight human-review queue over the auto-screened / JIT-generated
   questions so the "served without human review" relaxation is closed for uncovered roles.
4. **Tune live turn-taking feel.** Voice barge-in shipped with the Pipecat migration (talk over the
   coach and it stops — gated on real transcribed words so noise can't cancel a reply); remaining work
   is feel: pause-tolerance tuning and the deferred Smart-Turn latency follow-ups.
5. **Polish the productized UI across all screens (F007)** and a responsive/mobile + accessibility
   (WCAG) pass — the design system is in place; this is applying it everywhere and tightening
   loading/empty/error states.
6. **Alerting on the evidence loop.** Per-turn latency + per-session product metrics and a periodic
   log/metric health review are in place; what remains is CloudWatch ALARMS (response-gap p95 breach,
   error-rate spikes, scoring failures, retention drift) so the non-negotiables are watched without
   anyone having to run the review.
