# Run-sheet — GenAI Product Canvas workshop

A minute-by-minute script for the **SA + AM** pair running the session. Print
this, keep it next to you, and assign one person as **timekeeper**. Times assume
a single canvas worked end to end with 4–8 participants in ~3 hours.

> Pair split, at a glance:
> - **AM drives:** open/close, Problem, Existing alternatives, Success metrics, Pricing.
> - **SA drives:** Inputs, Outputs, Solution, LLMX, UX (with designer), Evaluation, Costs.
> - The non-driver **scribes** into the canvas and watches the clock.

---

## T-1 day — prep (do not skip)

- [ ] Confirm the customer is bringing **1–3 concrete feature ideas**. If they
      arrive with nothing, you'll burn 30 min inventing one — pre-seed it.
- [ ] Send the **canvas image** as a pre-read so the structure isn't a surprise.
- [ ] Decide the medium: live slide, or printed A0 + sticky notes.
- [ ] Read the [worked example](./sample-completed-canvas.md) so you have a
      reference answer in your head for every box.
- [ ] Skim the [FAQ](./faq-and-objections.md) — pre-load the top 3 objections.
- [ ] Agree the AM/SA split above and who scribes.

---

## In the room

### 0:00–0:15 — Welcome & framing *(AM leads)*
- 2 min: round-the-room intros; capture each person's role (PM, design, eng).
- 5 min: goal of the session — *"leave with one feature defined well enough to
  build and to know if it worked."*
- 5 min: **the "why not just a chatbot" framing.** Ask the room: *"What's the
  first GenAI feature people reach for?"* — someone says chatbot. Use that to
  set up the canvas as the tool that gets past the reflex.
- 3 min: ground rules — done beats perfect, park rabbit holes, everyone contributes.

### 0:15–0:30 — Walk the canvas & pick the feature *(SA + AM)*
- 7 min: walk the **empty canvas**, naming the three bands (Frame → Design →
  Prove). Don't explain every box yet — just the shape.
- 8 min: if they brought multiple ideas, **pick ONE** to work end to end (vote
  if needed). Park the others as homework. Write `[Company] – [Use case]` in the
  title box.

### 0:30–1:15 — Band 1: Frame the opportunity *(45 min)*
- **0:30–0:45 Problem 🔒** *(AM)* — push until it's "[user] can't [outcome]
  because [friction], costing [impact]." Don't let "we want AI" stand.
- **0:45–0:53 Existing alternatives 🔀** *(AM)* — how is this solved today? This
  is your anti-chatbot baseline.
- **0:53–1:05 Inputs 🌐** *(SA)* — what real data, from where, what format,
  any PII? This is where SA value shows.
- **1:05–1:15 Outputs ☁️** *(SA)* — define the contract: structured vs.
  unstructured, schema, hard constraints.

> **Checkpoint (1:15):** if Problem/Inputs/Outputs aren't concrete, you're
> behind — tighten before the break, the back half depends on them.

### 1:15–1:25 — Break *(10 min)*

### 1:25–2:10 — Band 2: Design the solution *(45 min)*
- **1:25–1:40 Solution 💡** *(SA)* — high-level flow, trigger → result. Resist
  deep architecture.
- **1:40–1:55 LLMX 🧠** *(SA)* — **force the pure-prompt vs pipeline vs agent
  decision.** Name tools/integrations. Most "agents" are pipelines.
- **1:55–2:10 UX ✨** *(SA + designer)* — modality (is chat even right?),
  augmentation, and how users correct/feedback (this becomes eval data).

### 2:10–2:55 — Band 3: Prove it & ship it *(45 min)*
- **2:10–2:18 Definition of Done ✅** *(SA)* — rubric for one good run / when an
  agent stops.
- **2:18–2:28 Success metrics 📋** *(AM)* — accuracy / latency / adoption
  targets, tied back to the Problem.
- **2:28–2:42 Evaluation 🏃** *(SA)* — test set, ground truth, human vs
  LLM-judge, drift monitoring. **Protect this time — teams want to stop early.**
- **2:42–2:50 Costs 📊** *(SA)* — back-of-envelope tokens × volume × model.
- **2:50–2:55 Pricing 💰** *(AM)* — revenue / upsell / efficiency; margin check.

### 2:55–3:00 — Read-back & next steps *(AM)*
- 3 min: read the completed canvas back aloud, box by box. The team should hear
  one coherent story.
- 2 min: capture the **open-questions list** and agree the follow-up (SA PoC on
  Bedrock, next session date).

---

## Pacing rules

- **Each box has a hard cap.** When time's up, capture the open question on the
  parking lot and move on. A box with a sharp open question is a *success*.
- **If you're 10 min behind by the break**, cut Solution and Costs to the bone —
  never cut Problem, Outputs, or Evaluation.
- **If you're ahead**, deepen Evaluation or start a second canvas on a parked idea.

## Failure modes to catch early
- "We'll use [model X]" in the first 15 min → park it, return to Problem.
- The room designing an agent when a prompt would do → press LLMX hard.
- Silence on Inputs → the idea may not be grounded in real data; flag it.
- Everyone wants to skip Evaluation/Pricing → that's exactly what makes it real.
