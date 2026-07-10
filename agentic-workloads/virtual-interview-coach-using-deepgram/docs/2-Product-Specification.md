# InterviewCoach — Product Specification (PRD)

> **Audience:** Undergraduate students (18+) preparing to enter the workforce
> **V1 scenario:** Job interviews only (engine generalizes to other scenarios later)
> **Platform:** Web, desktop-first
> **Date:** 2026-06-02

---

## 1. Product Vision & Principles

**Vision.** Give every student the unlimited, personalized interview practice that today only a scarce human career coach can provide — so that no one walks into a real interview having practiced just once, or not at all.

**Design principles:**

1. **Practice should be infinite, not rationed.** The entire reason to exist is that human coaching doesn't scale. Every design choice favors "do it again" over gating. *(Addresses the core problem: one-session-per-student.)*
2. **Lower the cortisol.** The audience is anxious-but-motivated. Calm visuals, supportive copy, default-to-Easy, and a mic check before the real thing all exist to make practicing feel safe. *(Addresses: students avoid practicing out loud.)*
3. **Specific beats generic.** Feedback must cite *their* resume and *the* role, name the exact moment ("you rambled on Q2"), and show a model answer. Generic advice is what ChatGPT already fails at. *(Competitor gap: ChatGPT is stateless and generic.)*
4. **Show the improvement.** The wedge is longitudinal. Progress must be visible and motivating — the difficulty ladder and the score trend *are* the product's story. *(Competitor gap: neither human coach nor ChatGPT shows you improving over time.)*
5. **Encourage, then critique.** Students should leave more ready, not crushed. The emotional close (spoken wrap-up) is warm; the precise judgment (written report) comes after. *(Addresses: confidence-building.)*
6. **Earn trust with the data.** Voice is sensitive. Consent is explicit, retention is finite, deletion is one click. Trust is a feature.

---

## 2. Personas

**Primary — "Maya," the anxious final-year undergrad.**
- *Who:* 21, final-year undergraduate, applying for her first full-time graduate role / internship.
- *Need:* Limited interview experience, nervous about being put on the spot, wants to practice many times privately before the real thing — and to feel she's actually getting better.
- *Context:* Practices alone, spread over **weeks** of preparation, on her own laptop, at her own pace. Starts on Easy to build confidence; works up to Hard.

**Secondary — "Mr. Tan," the career-coach teacher.**
- *Who:* Career-services staff member responsible for far more students than he can personally coach.
- *Need:* Wants students to arrive at their single human session already warmed up, so that session is spent on high-value polish rather than basics.
- *Context:* Buyer/champion within the institution; cares about coverage (every student gets practice) and evidence (students measurably improve).

**Tertiary (roadmap) — "Sofia," the scholarship/admissions applicant.** Same engine, different scenario. Not served in V1, but the abstraction is built for her.

---

## 3. Feature Prioritization (MoSCoW for V1)

| Bucket | ID | Feature |
|---|---|---|
| **MUST** | A1 | Account / login (school email or Google via Cognito) |
| **MUST** | A2 | Resume upload + parse-back confirmation (persists to profile, re-confirm on return) |
| **MUST** | C0 | Hybrid question engine: vetted archetype bank (competency × difficulty) + LLM wording personalization |
| **MUST** | A3 | Job title / description input |
| **MUST** | B1 | Mic check + practice line onboarding |
| **MUST** | B2 | Difficulty selection (Easy / Medium / Hard) |
| **MUST** | C1 | Live real-time voice interview |
| **MUST** | C1b | Dynamic follow-up questions (within-archetype; intensity scales with difficulty) |
| **MUST** | C1c | Interviewer methodology — STAR + funnel probing HR persona |
| **MUST** | C2 | Immediate **qualitative, score-free** spoken wrap-up |
| **MUST** | D1 | Async written feedback report (overall + sub-scores) |
| **MUST** | D2 | Per-question breakdown (transcript · what worked · improve · strong-answer example) |
| **MUST** | D3 | Voice/communication metrics (filler words, pace, long pauses) |
| **MUST** | D5 | Evidence-anchored competency scorecard (1–5 anchors + transcript quote) |
| **MUST** | E1 | Session history |
| **MUST** | E2 | Progress chart across sessions |
| **MUST** | F1 | Audio recording storage + playback |
| **MUST** | G1 | Privacy: explicit consent, encryption, retention, one-click delete |
| **SHOULD** | H1 | Notify-on-ready (email / on-screen) when report finishes |
| **SHOULD** | D4 | Richer voice metrics (sentiment/energy cues) |
| **COULD** | C3 | Full narrated report (all scores read aloud) |
| **COULD** | S1 | Scholarship / admissions scenarios |
| **COULD** | M1 | Mobile / responsive |
| **WON'T (V1)** | V1 | Video recording + facial / body-language analysis |

---

## 4. Feature Specifications

### A — Onboarding & Setup
- **A1 Account (FR-1).** Sign up / log in via school email or Google. *Story:* "As Maya, I want a quick, low-friction sign-in so I can get to practicing." *Rationale:* school email seeds future campus/district distribution.
- **A2 Resume upload (FR-2).** Upload a resume (PDF/DOCX); system parses it and **shows the extracted facts back** for confirmation. *Story:* "As Maya, I want to see that it understood my resume so I trust the feedback is about me." *Rationale / competitor gap:* "critique grounded in their resume" is half the personalization wedge ChatGPT lacks; parse-back mitigates ingestion failure (Flag F4).
- **A3 Job input (FR-3).** Enter a job title and/or paste a job description. *Story:* "As Maya, I want questions tailored to the actual role I'm applying for." *Rationale:* role-relevant questions are the other half of personalization.

### B — Pre-Interview
- **B1 Mic check + practice line (FR-4).** Grant mic permission, see the audio level move, say one throwaway line the coach acknowledges ("Got it — I can hear you clearly!"). *Story:* "As Maya, I want to know the tech works and that talking to it is safe before the real thing." *Rationale:* a broken mic on first try is the #1 way to lose a nervous user; doubles as a rehearsal of speaking aloud.
- **B2 Difficulty selection (FR-5).** Choose Easy / Medium / Hard; Easy is pre-selected and gently nudged. *Story:* "As Maya, I want to start gentle and level up at my own pace." *Rationale:* confidence ramp + tangible progress narrative + a concrete behavioral dial for the interviewer agent.

| Level | Tone | Questions | Follow-ups | Encouragement |
|---|---|---|---|---|
| Easy | Warm, friendly | Softball, predictable | None | Heavy, reassuring |
| Medium | Realistic, neutral-friendly | Standard mix | Occasional | Balanced |
| Hard | Professional, neutral | Probing, role-specific | Frequent; challenges vagueness | Sparse, earned |

Difficulty is **multi-dimensional**, not just "harder questions." Each level is a stored
**difficulty profile** the interviewer agent reads, with these levers (the behavioral dial of
FR-5; intensity of C1b follow-ups is one lever among several):

| Lever | Easy | Medium | Hard |
|---|---|---|---|
| Question type | Warm-up, factual, single-part | Behavioral STAR, mild scenarios | Ambiguous, multi-constraint, no clean answer |
| Probing depth | Accept first answer | 1–2 follow-ups for specifics | Relentless drilling; challenge assumptions; ask for failures |
| Curveballs | None | Occasional clarifying twist | Devil's advocate; pushes back; stress hypotheticals |
| Pace & hints | Very patient, hints offered | Normal pace, minimal hints | Brisk, no hints, comfortable with silence |
| Domain depth | Surface concepts | Applied working knowledge | Edge cases, trade-offs, first principles |
| Scoring bar | Lenient anchors | Standard anchors | Strict anchors; vague answers penalized |

The scoring **anchors** themselves stay level-independent for the headline rubric (Principle II /
Q4) — what the scoring-bar lever changes is the *question difficulty and probing rigor* a given
score is earned against, recorded via the session's archetype difficulty so the within-level
trend stays honest.

### C — The Live Interview
- **C1 Real-time voice interview (FR-6).** A spoken, turn-taking mock interview: the coach asks, the student answers aloud, the coach responds and (per difficulty) follows up. Minimal on-screen chrome; a clear "End interview" control. *Story:* "As Maya, I want it to feel like a real online interview so I practice the actual pressure." *Rationale:* realistic spoken pressure is the non-negotiable heart; text chat can't deliver it.
- **C1b Dynamic follow-up questions (FR-6b).** The interviewer listens to the student's actual answer and asks **follow-up questions that dig deeper within the same competency** ("You said you owned the API on that project — what was the hardest technical decision you faced?"). Follow-ups are scoped to the *current archetype* (not new territory) so cross-session comparability is preserved (Flag F13). Follow-up **intensity is the difficulty dial**: Easy = none, Medium = an occasional clarifier, Hard = frequent probing that challenges vague answers. *Story:* "As Maya, I want the coach to react to what I actually said, like a real interviewer — and to push harder as I level up." *Rationale:* dynamic follow-ups are the single biggest realism upgrade; scoping them within the archetype keeps the progress signal trustworthy.

- **C1c Interviewer methodology — the HR persona (FR-6c).** The coach does not read a question list; it runs a **professional competency-based method**: for each archetype it asks an open behavioral question, then **funnels** with probes for the missing **STAR** element — Situation, Task, **Action** (the part candidates skip), Result (ideally quantified). It **probes without leading** (drills "what did *you* do versus the team?" rather than hinting at the answer it wants) and **references back** to the resume and earlier answers so the session feels like one continuous interview rather than a quiz. The session follows a real arc: rapport → resume/experience → behavioral competencies → role/domain knowledge → situational → the student's questions → warm close. *Story:* "As Maya, I want it to feel like a real interviewer who actually listens and digs in — not a form that reads questions at me." *Rationale:* the methodology (STAR + funnel, probe-don't-lead) is what makes the practice transferable to a real interview; it is delivered as a persona prompt template parameterized by job scope, resume highlights, target competencies, difficulty profile, and the current archetype's intent, behind the swappable reply seam (Principle V; Flag F7).
- **C2 Spoken wrap-up (FR-7).** Immediately on ending, a short **qualitative, score-free** spoken summary plays from the still-warm conversation context — directional headlines + encouragement, then "your full report is being prepared." *Story:* "As Maya, I want to feel the session closed warmly and know what's next while my report generates." *Rationale:* bridges the async wait (Flag F5) and stays score-free to avoid contradicting the report (Flag F6).

### D — The Feedback Report (the "money screen")
- **D1 Scores (FR-8).** Overall score + sub-scores: **Content/Relevance, Structure (STAR), Communication/Clarity, Confidence**, on a **fixed, level-independent rubric** (a 7 means the same thing on Easy and Hard). These plot on the progress chart as a *within-level* trend, never as a difficulty-blended composite (Q4). *Story:* "As Maya, I want a clear, honest read on how I did and what to focus on."
- **D2 Per-question breakdown (FR-9).** For each question: their transcript · what worked · what to improve · a model **strong-answer** example built from **the student's own resume material, arranged into the right structure** and framed as "you mentioned the campus app — here's how to frame it in STAR; notice the shape, then make it yours" (Q5). *Story:* "As a fresh grad, I want to see how to shape *my own* experience into a strong answer." *Rationale:* uses their real material so it's a learning tool, not a script; the rubric rewards their concrete details, mitigating parroting.
- **D3 Voice metrics (FR-10).** Filler-word count, speaking pace, long pauses — the objective signals voice uniquely unlocks. *Story:* "As Maya, I want the concrete stuff I can't judge about myself."
- **D5 Evidence-anchored competency scorecard (FR-8b).** Beyond the four headline sub-scores (D1), each assessed competency is scored on the **fixed 1–5 anchored scale** and — like a real hiring scorecard — **backed by a verbatim evidence quote** pulled from the student's transcript, with the matching STAR element labelled. The communication read is widened past raw voice metrics to include **conciseness vs rambling, hedging/confidence language, and responsiveness under follow-up probes** (did answer quality hold when the coach drilled). *Story:* "As Maya, I want to see *why* I got that score — the exact thing I said — not just a number." *Rationale:* evidence-anchoring is what makes feedback feel professional and actionable rather than opaque; it also makes a score auditable against the transcript. All of this is computed post-session in the report worker (no live-latency cost) and respects the fixed, level-independent rubric (Principle II; NFR-8).

### E — Progress
- **E1 Session history (FR-11).** A list of past sessions with date, role, difficulty, and overall score; each opens its full report. *Rationale:* the longitudinal record that makes improvement real.
- **E2 Progress chart (FR-12).** Two honest signals (Q4): (1) a **difficulty ladder** (Easy ✓ → Medium ✓ → Hard ◐) showing how far the student has climbed, and (2) an **absolute within-level score trend** per sub-score/overall showing how high they score *at a given level*. No blended "always-up" number. *Story:* "As Maya, I want to *see* myself getting better — honestly — so I keep going and trust it." *Rationale:* the core differentiator made visible without faking the number; seeded from session #1 to plant the "come back and beat this" hook; keeps the north-star metric measurable.

### F — Recording
- **F1 Recording + playback (FR-13).** Session audio is stored (with consent) so the student can re-listen to their own answers. *Story:* "As Maya, I want to hear how I actually sounded on Q2." *Rationale:* richer review; groundwork for deferred voice-tone/video analysis. *(Triggers Flag F8 obligations — see G1.)*

### G — Trust & Privacy
- **G1 Consent, retention, deletion (FR-14).** Explicit, specific consent at signup and before the first recording; encryption at rest (SSE-KMS) and in transit; finite default retention with a "keep this session" option; one-click delete of any session (audio + transcript + scores) and full account export/delete. *Story:* "As Maya, I want to know exactly what's stored and be able to delete it instantly." *Rationale:* voice is sensitive/biometric-adjacent data; trust is a precondition for honest practice.

---

## 5. Key User Flows

**The core loop (the habit):**
```
Set up (job + resume + difficulty)
   → Live voice interview
      → Immediate spoken wrap-up
         → (report processes in background)
            → Review written report
               → See progress update
                  → "Try again" (harder, or same role to beat the score)
```

**Flow 2 — Returning student, second+ session:** Dashboard (sees prior scores) → "Start interview" → reuse saved resume/job or change them → pick a higher difficulty → interview → wrap-up → report → progress chart now shows a *trend*.

**Flow 3 — Review a past session:** Dashboard → Session history → open a past report → re-listen to the audio of a specific answer → read the strong-answer example → start a fresh attempt.

**Flow 4 — Delete my data:** Settings/Privacy → select a session → one-click delete (audio + transcript + scores removed) → confirmation. Or account-level export/delete.

---

## 6. New-User Journey (Step-by-Step)

**Mental model (one line):** *"Practice a real interview out loud, as many times as I want, and watch myself get better."*

| # | User action | Screen | Rationale |
|---|---|---|---|
| 1 | Lands; signs up (school email / Google) | Landing → Auth (S1, S2) | Low-friction entry; school email seeds campus distribution |
| 2 | Reads "how it works in 3 steps" + does a mic check & practice line | Onboarding / Mic check (S3) | Sets expectations; kills the #1 voice failure before the emotional interview |
| 3 | Uploads resume + enters job; sees parsed-back summary | Setup (S4) | Personalization fuel; parse-back builds trust (Flag F4) |
| 4 | Picks difficulty (Easy pre-selected) | Difficulty select (S5) | Default-to-Easy protects the anxious first-timer; control stays with them |
| 5 | One click "Start interview"; conducts the live voice session | Live interview (S6) | The core loop; minimal chrome, clear "End" |
| 6 | Interview ends; immediate spoken wrap-up plays | Session-end / wrap-up (S7) | Emotional close + bridges the wait (Flags F5/F6) |
| 7 | Sees "report processing" with context, not a dead spinner | Processing (S8) | Re-engagement moment, not a churn dead-end |
| 8 | **Full report appears:** scores, per-Q, strong answers, voice metrics, audio playback | Report — *money screen* (S9) | The payoff and the differentiator |
| 9 | Sees progress chart seeded with session #1 | Dashboard / Progress (S10) | Plants "come back and beat this" from the first session |

**Empty state (before any interview):** the dashboard shows a single inviting **"Start your first interview"** CTA + one-line promise ("Practice as many times as you want — watch yourself improve"). No empty charts, no clutter.

**Key failure / edge path:** if resume parsing fails or the mic doesn't work, the flow degrades gracefully — manual text entry for resume facts; the mic-check screen blocks progression until audio is detected, with a troubleshooting hint, rather than failing mid-interview.

---

## 7. Engagement & Notification Strategy

| Trigger | Message | Goal it serves |
|---|---|---|
| Report finished processing | "Your feedback report is ready" (on-screen now; email is SHOULD/H1) | Closes the async loop; pulls the student back (Flag F5 re-engagement) |
| First session completed | Progress chart appears, seeded | Plants the longitudinal hook immediately |
| Returning after a session | "Ready for round 2? Try Medium and beat your last score." | Drives the repeat-practice north-star metric |
| Score improved vs last session | Celebratory micro-moment on the chart | Reinforces "I'm getting better," the core motivation |
| Long pauses in attendance (post-V1) | Gentle nudge | Retention over a multi-week prep window |

The async report is intentionally a **return moment**, not a delay to apologize for.

---

## 8. Screens / UI Inventory

Screen numbers are kept consistent with the HTML mockup (Deliverable 3).

| # | Screen | Implements |
|---|---|---|
| S1 | Landing | A1 |
| S2 | Auth (sign up / log in) | A1 |
| S3 | Onboarding + Mic check & practice line | B1 |
| S4 | Setup (resume upload + parse-back, job input) | A2, A3 |
| S5 | Difficulty selection | B2 |
| S6 | Live interview | C1, C1b, C1c |
| S7 | Session-end spoken wrap-up | C2 |
| S8 | Report processing | (bridges D1–D3, D5; Flag F5) |
| S9 | Feedback report (money screen) — scores, per-Q, strong answers, voice metrics, audio playback | D1, D2, D3, D5, F1 |
| S10 | Dashboard / Progress (incl. empty state + session history) | E1, E2 |
| S11 | Settings / Privacy (consent, retention, delete) | G1 |

---

## 9. Feature → Success-Metric Mapping

**North-star metric:** % of students who complete **3+ sessions AND measurably improve** across them.

| Feature | Metric it is meant to move |
|---|---|
| E2 Progress chart, B2 Difficulty ladder | North-star (repeat practice + visible improvement) |
| C1 Live voice interview | Session completion rate; realism/"felt real" rating |
| D1–D3 Report (specific feedback) | Report-usefulness rating; return-to-second-session rate |
| C2 Spoken wrap-up | Drop-off rate at the async wait (lower = working) |
| B1 Mic check | First-session completion (fewer pre-interview failures) |
| A2/A3 Personalization | Perceived relevance of questions/feedback |
| G1 Privacy/consent | Consent-grant rate; trust (qualitative) |
| *(Secondary)* confidence survey | Pre/post self-reported preparedness lift |

---

## 10. Product Roadmap

**V1 (POC) — prove the wedge.** Job-interview scenario; voice interview (E/M/H); personalized async report; spoken wrap-up; progress tracking; recording + playback; consent/retention/delete. AWS-native, desktop-web. Success = the north-star metric.

**Phase 2 — deepen, notify & institutionalize.** Email notify-on-ready; richer voice metrics; full narrated report (scores read aloud); mobile/responsive; and the **teacher/institutional dashboard** (Q6) — read-only, scores/trends-only, student opt-in to share, roster via class join code. Begin instrumenting the AgentCore-vs-direct-Bedrock latency comparison from live data. *(V1 designs the org/cohort/share schema so this needs no migration.)*

**Phase 3 — generalize the engine.** Scholarship and university/grad-admissions scenarios (the abstraction already exists). Optional: video recording + body-language/facial analysis (the original full vision), with its own consent/compliance work.

**Phase 4 — widen the audience (deliberate, compliance-resourced).** Adult mid-career switchers; and — only with COPPA/FERPA/parental-consent work done properly — high-school students.

**Deferred and why:** video/body-language (heaviest build + privacy load; voice proves the thesis first); extra scenarios (protect V1 focus and feedback quality); mobile (desktop-web is the predictable voice environment); under-18 audience (compliance burden out of scope for a POC).

---

## 11. Resolved Product Decisions

These seven decisions were resolved during review. They are interdependent: Q1, Q4, and Q5 together form the **progress-trust engine** — the reason a rising score genuinely reflects a better student rather than an easier test.

| # | Decision | Resolution | Why |
|---|---|---|---|
| Q1 | **Question generation source** | **Hybrid:** a vetted bank of question *archetypes* tagged by competency (teamwork, problem-solving, role-specific, motivation/fit) and difficulty; the LLM selects archetypes by level and personalizes only the *wording* to the resume/role. | Same competencies are assessed every session → scores are comparable across sessions (real progress signal), while phrasing stays personalized and quality-controlled. |
| Q2 | **Retention window** | **30-day default auto-delete**; student can "keep" any session indefinitely (until they delete it). | Useful review window over weeks of prep, while minimizing sensitive voice data held by default. |
| Q3 | **Resume reuse** | **Persist to profile**, reused by default with a one-line "using your saved resume — update?" confirm on return. | Low friction for the repeat-practice loop; personalization stays current. |
| Q4 | **Cross-difficulty scoring** | **Two honest signals:** (1) a difficulty **ladder** (Easy ✓ → Medium ✓ → Hard ◐) and (2) an **absolute, level-independent score trend *within* each level**. No blended/adjusted number. | A constructed "always-up" score would make the north-star metric ("measurable improvement") unfalsifiable and erode trust (Flag F11). Two real wins are more motivating *and* honest. |
| Q5 | **Strong-answer personalization** | **The student's own material arranged into the right structure** ("you mentioned the campus app — here's how to frame it in STAR; notice the shape, then make it yours"). | Strong learning for fresh grads without handing them a script; the rubric rewards their own concrete details, not parroted phrasing. |
| Q6 | **Teacher / institutional dashboard** | **Deferred to V2.** The org/cohort/opt-in-share schema is designed now to avoid a later migration. | Avoids introducing a multi-party consent problem (Flag F12) under POC time pressure; keeps V1 focused on proving the student wedge. A teacher view will be scores/trends-only with student opt-in. |
| Q7 | **Report-ready notification** | **On-screen for V1** (live if they wait; always on the dashboard on return). Email-on-ready remains a SHOULD (H1) fast-follow. | Simplest critical path for the POC without orphaning students who leave during processing. |

### Impact on scoring model (Q1 + Q4 + Q5 combined)
- The four sub-scores (Content, Structure/STAR, Communication, Confidence) are scored on a **fixed, level-independent rubric** (NFR-8 consistency applies).
- The progress dashboard shows the **ladder** plus a **within-level trend** per sub-score and overall — never a difficulty-blended composite.
- Questions get harder by level (via archetype difficulty tags), so leveling up may dip the raw score; the dashboard frames this as "leveled up," not "regressed."

### Remaining genuinely open (decide as you build)
- **Archetype bank breadth:** how many role-specific archetype families to seed for V1 (one general "graduate role" family, or a few industry variants?).
- **"Keep" cap:** is kept-session storage truly unlimited, or capped per student to bound cost?
- **Cohort join mechanism (for V2 schema):** class join-code vs. lightweight SIS integration — only the schema hooks are needed in V1.
