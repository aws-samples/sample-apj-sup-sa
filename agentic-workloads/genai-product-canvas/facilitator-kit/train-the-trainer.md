# Train-the-trainer — scaling the canvas across APJ

How a new Solutions Architect goes from *"I found this repo"* to *"I confidently
ran a canvas workshop for my customer."* This is the path that actually scales
the workshop across APJ — the artifacts are necessary but not sufficient; SAs
need a route to competence and a way to feed improvements back.

---

## The onboarding path (for a new facilitator)

Four steps, ~one delivery cycle:

### Step 1 — Self-study *(~1 hour)*
- [ ] Read the [project README](../README.md) — the 12 boxes and the philosophy.
- [ ] Read the [worked example](./sample-completed-canvas.md) end to end.
- [ ] Skim the [run-sheet](./run-sheet.md) and [FAQ](./faq-and-objections.md).
- [ ] Open the [canvas template](../genai-product-canvas-template.pptx) and read
      every box's prompt on the slide itself.

### Step 2 — Observe *(1 workshop)*
- [ ] Sit in on a workshop run by an experienced facilitator — ideally as the
      **scribe**, so you're hands-on with the canvas without owning the room.
- [ ] Note where the energy dips and which objections came up.

### Step 3 — Co-facilitate *(1 workshop)*
- [ ] Run a workshop **paired** with an experienced facilitator. Take the band
      you're most comfortable with (usually Band 1 with the AM, or Band 2 if
      you're technical). The experienced SA covers the rest and backstops you.

### Step 4 — Lead *(ongoing)*
- [ ] Run one as the **lead facilitator**, with a co-pilot scribing/timekeeping.
- [ ] Afterwards, do a 15-min retro and **log the delivery** (see metrics below).
- [ ] You're now able to onboard the next SA — the loop is self-replicating.

> A new SA can usually reach Step 4 within **2–3 observed/co-led sessions**. The
> worked example + run-sheet compress this a lot — they're why a facilitator
> doesn't need to have *invented* the canvas to run it well.

---

## What a "train-the-trainer" session covers *(~90 min, run by an experienced facilitator for a cohort of new SAs)*

1. **Why this exists** (10 min) — the "beyond add-a-chatbot" thesis; what good
   outcomes look like.
2. **Walk the canvas** (20 min) — all 12 boxes, using the worked example.
3. **Facilitation mechanics** (20 min) — the AM/SA split, timeboxing, the
   pacing rules, reading a room.
4. **Objection drills** (20 min) — role-play the top objections from the
   [FAQ](./faq-and-objections.md); new SAs practise the redirect.
5. **Logistics & next steps** (20 min) — how to book a session with a customer,
   prep checklist, where to get help, how to log a delivery.

A reusable **facilitator deck** for this session is a recommended next artifact
(see "Assets to build" below) — until then, run it off the worked example +
run-sheet.

---

## Community & ownership (so it doesn't go stale)

- **Workshop owner:** _[name / alias — fill in]_ — owns the canvas, reviews PRs,
  runs the train-the-trainer cohorts.
- **Per-country champions:** _[fill in per APJ geo]_ — own local delivery, adapt
  customer examples, and are the first point of contact for SAs in that country.
- **Facilitator channel:** _[Slack/Chime channel — fill in]_ — share what
  worked, completed-canvas examples (anonymised), and objection patterns.
- **Improve the workshop:** changes go back via PR to this repo (see the repo
  [CONTRIBUTING](../../../CONTRIBUTING.md)). Found a better prompt for a box, or
  a new objection + answer? PR it so every APJ SA gets it.

---

## Measuring scale (prove it's working)

Track these so the workshop stays funded and you can show impact across APJ:

| Metric | Why it matters |
|--------|----------------|
| # workshops delivered (by country) | Reach / adoption across the geo |
| # SAs trained to lead | The actual scaling lever — facilitator supply |
| # customers / features canvassed | Pipeline of grounded GenAI opportunities |
| Follow-on PoCs / Bedrock workloads started | Did the canvas convert to building? |
| Facilitator confidence (post-session survey) | Quality of the enablement itself |

A lightweight shared tracker (one row per delivery) is enough to start. Country
champions roll their numbers up to the workshop owner.

---

## Localization notes (APJ)

- The **facilitator guide can stay in English** (the SA audience reads it), but
  **customer-facing canvas prompts** may need translation (e.g. JP, KO, TH, ID,
  VN) for non-English-speaking PM teams.
- **Swap in regional customer examples** — the worked example is a generic SaaS;
  country champions should keep a local example that resonates with their market.
- Keep any **internal-only guidance** (deal qualification, internal talking
  points, pipeline) **out of this public repo** — it belongs on the internal
  wiki. This kit is intentionally customer-safe.

---

## Assets to build next *(backlog for the owner)*
- [ ] A **facilitator deck** for the train-the-trainer session.
- [ ] A **recording** of one real delivery (with customer consent / anonymised).
- [ ] A **one-page printable canvas** (A0/A1) export for in-person sessions.
- [ ] **Translated canvas prompts** for the main APJ languages.
- [ ] An **internal wiki page** linking here, holding the SA-only material.
