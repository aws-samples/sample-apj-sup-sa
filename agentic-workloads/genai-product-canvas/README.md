# GenAI Product Canvas

A facilitator's guide for running a **half-day GenAI product workshop** with a
customer's product managers. The workshop is built around the **GenAI Product
Canvas** — a single-page template (provided here as
[`genai-product-canvas-template.pptx`](./genai-product-canvas-template.pptx))
that a team fills in **box by box, left to right and top to bottom** to turn a
vague "we should use AI" ambition into a concrete, buildable, measurable product
feature.

It is run by a **Solutions Architect (SA) + Account Manager (AM)** pairing and
is aimed at **product managers** — not engineers. No code is written in the
room. The output is a completed canvas the team can take straight into design
and delivery.

> **Who this is for.** Companies that know GenAI matters to their product but
> are stuck at *"let's add a chatbot."* If a customer keeps proposing a chat
> box bolted onto an existing screen, this workshop is the intervention: it
> forces the conversation back to a real user problem, the data they actually
> have, the output that actually creates value, and how they'll know it works.

---

## Why a canvas (and why this one)

Most GenAI ideas die in one of two ways: they never get past a demo because
nobody defined what "good" looks like, or they ship a generic chatbot that
nobody uses because it wasn't tied to a real workflow. The canvas is a forcing
function against both failure modes. In ~3 hours a PM-led team produces:

- A **problem statement** that justifies AI over the existing alternative.
- A clear **input → output contract** for the model.
- A **solution sketch** that names whether this is a prompt, a pipeline, or an
  agent — and what tools/data it needs.
- **Definition of done, success metrics, and an evaluation plan** so the team
  can tell whether it's working *before* it ships and *after*.
- A first read on **cost and pricing**, so the feature has a viable margin.

The canvas deliberately puts **Evaluation, Cost, and Pricing on the page from
day one**. That is what separates a product feature from a demo.

---

## Outcomes

By the end of the session each participating team will have:

1. A **completed GenAI Product Canvas** for one concrete feature.
2. Shared language across product, design, and engineering for *what* is being
   built and *why*.
3. A prioritised list of **open questions and assumptions** to validate.
4. A rough sense of **technical shape** (prompt vs. pipeline vs. agent) and the
   AWS building blocks involved — enough for the SA to scope a follow-up.

---

## Who runs it, and who attends

| Role | In the room | Responsibility |
|------|-------------|----------------|
| **Solutions Architect** | Required | Facilitates the technical boxes (Inputs, Outputs, Solution, LLMX, Evaluation, Costs). Keeps ideas grounded in what's actually buildable on AWS. |
| **Account Manager** | Required | Owns the room, frames business value, drives the Problem / Success metrics / Pricing boxes, and lines up the follow-up. |
| **Product Manager(s)** | Required | The primary participants. They own the canvas and most of the content. |
| Designer / UX | Recommended | Strongest contributor to the UX box. |
| Eng lead / tech lead | Recommended | Reality-checks Solution, LLMX, and Costs. |
| Data / ML person | Optional | Valuable for Inputs and Evaluation if available. |

**Group size:** 4–8 people per canvas works best. Above that, split into teams
and run canvases in parallel, then read out.

---

## What you need

- **The template:** [`genai-product-canvas-template.pptx`](./genai-product-canvas-template.pptx).
  Run it live in PowerPoint/Keynote/Google Slides, or print it A0/A1 and fill it
  in with sticky notes. One canvas per feature.
- **A real candidate feature.** Ask the customer to come with **1–3 product
  ideas** they're genuinely considering. Pick **one** to work through end to end;
  the others become homework using the same canvas.
- **Room setup:** a screen to project the canvas, a way for everyone to
  contribute (sticky notes, a shared doc, or the slide itself), and a
  timekeeper.
- **Pre-read (optional, ~10 min):** send the canvas image to participants a day
  before so the structure isn't a surprise.

---

## Suggested agenda (≈ 3 hours)

| Time | Segment | Lead |
|------|---------|------|
| 0:00–0:15 | Welcome, goals, "why not just a chatbot" framing | AM |
| 0:15–0:30 | Walk the empty canvas; pick the one feature to work on | SA + AM |
| 0:30–1:15 | **Frame the opportunity** — Problem, Existing alternatives, Inputs, Outputs | AM leads Problem; SA leads Inputs/Outputs |
| 1:15–1:25 | Break | — |
| 1:25–2:10 | **Design the solution** — Solution, LLMX, UX | SA leads; designer on UX |
| 2:10–2:55 | **Prove it & ship it** — Definition of Done, Success metrics, Evaluation, Costs, Pricing | SA leads Eval/Costs; AM leads Metrics/Pricing |
| 2:55–3:00 | Read-back, capture open questions, agree next steps | AM |

Timeboxes are deliberate. **Done beats perfect** — if a box stalls, capture the
open question and move on.

---

## Working the canvas: the 12 boxes

Fill the canvas **left to right, top to bottom**. The boxes build on each other:
you can't define a good output until you know the problem, and you can't pick
metrics until you know the output. The emoji next to each name matches the icon
on the template so you can find the box quickly.

Work in three bands:

```
┌─────────────────────────── FRAME THE OPPORTUNITY ───────────────────────────┐
│  1. Problem 🔒   2. Existing alternatives 🔀   3. Inputs 🌐   4. Outputs ☁️   │
├──────────────────────────── DESIGN THE SOLUTION ────────────────────────────┤
│           5. Solution 💡        6. LLMX 🧠        7. UX ✨                    │
├──────────────────────────── PROVE IT & SHIP IT ─────────────────────────────┤
│  8. Definition of Done ✅   9. Success metrics 📋   10. Evaluation 🏃         │
│                       11. Costs 📊      12. Pricing 💰                        │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Band 1 — Frame the opportunity

#### 1. Problem 🔒
*What problem are you solving? Why does it matter?*

Start here, always. Anchor on a **user and a job-to-be-done**, not on the
technology. If the answer is "we want to use AI," that's not a problem — push
until you get a sentence of the form *"[user] can't [outcome] because [friction],
which costs [impact]."*

- **Facilitator prompts:** Whose problem is this? How often does it happen? What
  does it cost them today — time, money, errors, churn?
- **Weak:** "Customers want a smarter experience."
- **Strong:** "Support agents spend ~6 min per ticket manually summarising the
  customer's history, capping throughput and slowing first response."

#### 2. Existing alternatives 🔀
*How is this problem solved today without AI/LLMs?*

This box is the antidote to "add a chatbot." If the problem is already solved
well enough by a search box, a rule, or a template, AI may not earn its place.
Naming the alternative also gives you the **baseline** you'll measure against
later.

- **Facilitator prompts:** What do users do today? What's the manual workaround?
  Why isn't that good enough? What would "non-AI better" look like?
- **Strong:** "Today: agents read the full ticket thread, or use canned
  responses that often miss context. Macros help but don't adapt."

#### 3. Inputs 🌐
*(Prompt + other data sources)* — *What data goes into the prompt? Where does it
come from? What format?*

Now get concrete about what the model sees. This is where the SA earns their
keep: surface the **real data**, where it lives, its quality, and any
sensitivity. No good input → no good output.

- **Facilitator prompts:** What context does the model need to do the job? Where
  does that data live (DB, docs, API, the user's typing)? Is it structured? Is
  any of it PII/regulated? Does it need retrieval (RAG) or is it small enough to
  pass inline?
- **AWS tie-ins:** Amazon Bedrock Knowledge Bases for retrieval; S3/OpenSearch
  as sources; Bedrock Guardrails for PII handling.
- **Strong:** "Ticket thread (text), customer's last 5 orders (JSON from the
  orders API), and relevant KB articles retrieved via vector search."

#### 4. Outputs ☁️
*(Structured vs unstructured, schema)* — *What does the LLM output look like?
What constraints?*

Define the **contract**. A vague output ("a helpful answer") is unbuildable and
untestable. Decide structured vs. unstructured, the schema, length, tone, and
hard constraints (must cite sources, must never invent a refund amount, etc.).

- **Facilitator prompts:** Does a human or a system consume this? If a system,
  what schema? What must *never* appear? What's the max length/latency budget?
- **AWS tie-ins:** Structured output / tool-use schemas, Bedrock Guardrails for
  content constraints.
- **Strong:** "A JSON object: `{summary: string(≤120 words), suggested_reply:
  string, sentiment: enum, citations: []}` — every claim cites a source ticket
  or KB article."

### Band 2 — Design the solution

#### 5. Solution 💡
*How will your AI/LLM solution work at a high level?*

With problem and the input→output contract defined, sketch the approach in a few
sentences or a simple flow. Resist deep architecture — this is the *what*, not
the *how*.

- **Facilitator prompts:** Walk it through from trigger to result. What happens
  when the user does X? Where does the model sit in the flow?
- **Strong:** "When an agent opens a ticket, we retrieve order history + KB
  articles, prompt the model to produce a summary and a draft reply, and show
  both in the agent console for one-click edit-and-send."

#### 6. LLMX 🧠
*(Which services will an LLM/agent use, and how will it know how to use them)* —
*Is this a pure prompt, or does the LLM use tools/agents? What integrations are
needed?*

This box names the **technical shape**, and it's where teams discover whether
they're building a prompt, a pipeline, or an agent. That decision drives
everything downstream.

- **Decide:** **Pure prompt** (context in, text out) → **Pipeline** (retrieval /
  chained steps, deterministic) → **Agent** (the model chooses tools and loops
  toward a goal). Don't reach for an agent if a prompt will do.
- **Facilitator prompts:** Does the model need to *take actions* or just produce
  text? What tools/APIs would it call? How does it know when and how to call
  them?
- **AWS tie-ins:** Amazon Bedrock for inference; Bedrock AgentCore / Strands for
  agents and tool use; Lambda/API Gateway for tool endpoints.
- **Strong:** "Pipeline, not an agent: deterministic retrieve → summarise →
  draft. Two tools — `get_order_history` and `search_kb` — called in a fixed
  order before a single model call."

#### 7. UX ✨
*(Modality, augmentation, feedback)* — *How will users interact with this
feature? How do they validate or give feedback on outputs?*

The make-or-break box for adoption, and the one most often skipped by teams
fixated on the model. Decide modality (chat? inline? a button? background
automation?), how the AI **augments** rather than replaces the user, and how
users **correct and give feedback** — which doubles as evaluation data.

- **Facilitator prompts:** Where does this live in the existing product? Is chat
  even the right modality, or is an inline suggestion better? How does a user
  know the output might be wrong? How do they fix it, and how do we capture
  that signal?
- **Strong:** "Draft reply appears inline in the agent console, editable, with
  citations shown. Agent edits before sending; every edit + thumbs up/down is
  logged as an eval signal."

### Band 3 — Prove it & ship it

#### 8. Definition of Done ✅
*Rubric/completion criteria* — *How do we know the workflow (pipeline) has
completed, or the goal (agentic) has been achieved?*

Define what a **single successful run** looks like. For a pipeline, that's the
completion criteria; for an agent, it's how it knows the goal is met and when to
stop. This is per-task "done," distinct from product success metrics below.

- **Facilitator prompts:** What does a correct, complete output contain? What's
  the rubric a human would use to grade one response? When should an agent stop
  or escalate to a human?
- **Strong:** "A response is 'done' if the summary is factually grounded in the
  thread, the draft reply addresses the customer's question, and all claims are
  cited. Otherwise, escalate to the agent unassisted."

#### 9. Success metrics 📋
*(e.g. accuracy, adoption)* — *What are your target metrics?*

Move from per-run quality to **product impact**. Set targets, even rough ones —
they make the feature's value falsifiable. The template seeds three:

- **Accuracy:** target % (e.g. "≥90% of drafts sent with minor or no edits").
- **Latency:** target (e.g. "draft visible < 3s after ticket open").
- **Adoption:** target (e.g. "≥60% of agents use the draft within 1 month").
- **Facilitator prompts:** Tie each metric back to the Problem box. If accuracy
  hit target but adoption didn't, what would that tell us?

#### 10. Evaluation 🏃
*How will you measure accuracy and quality? What does your test set look like?
How will you validate against ground truth? How will you monitor quality and
detect drift?*

The box that turns a demo into a product. Decide **how** you'll measure the
metrics above — offline and in production.

- **Facilitator prompts:** What does your test set look like, and where does it
  come from (historical tickets? hand-labelled examples)? What's ground truth?
  Will you use human review, an LLM-as-judge, or both? How will you watch for
  drift after launch?
- **AWS tie-ins:** Curate a golden test set in S3; automated eval runs;
  LLM-as-a-judge; capture the UX feedback signal from box 7 as ongoing eval data.
- **Strong:** "200 historical tickets with human-written 'ideal' replies as
  ground truth. Offline: LLM-judge scores grounding + helpfulness, spot-checked
  by humans. Online: track edit-distance and thumbs up/down weekly to detect
  drift."

#### 11. Costs 📊
*What are the estimated LLM inference costs? (tokens, model, volume)*

A back-of-envelope unit-economics check. The SA can estimate from input/output
token sizes (boxes 3 & 4), the chosen model, and expected volume (from box 9).

- **Facilitator prompts:** Tokens in × tokens out × calls/day × model price?
  Could a smaller/cheaper model meet the bar? Does caching or retrieval reduce
  tokens?
- **AWS tie-ins:** Bedrock per-model pricing; right-sizing model choice;
  prompt caching; batch where latency allows.
- **Strong:** "~4k input + 400 output tokens × ~5k tickets/day on a mid-tier
  model ≈ $X/day. Re-estimate against a smaller model for the summary step."

#### 12. Pricing 💰
*How will this feature be priced or packaged? What is the margin impact?*

Close the loop: given the cost, how does the feature **make or save money**? It
might be a paid add-on, a tier upgrade, or an internal efficiency play with no
direct price — but the margin question must be answered.

- **Facilitator prompts:** Is this a new revenue line, a retention/upsell lever,
  or a cost saving? Does the per-use inference cost fit inside the price/value?
  What's the margin at target volume?
- **Strong:** "Internal efficiency play: at $X/day inference vs. ~6 min saved
  per ticket across N agents, payback is well inside a month. No direct customer
  price; bundled into the existing support seat."

---

## Facilitation tips

- **Start with the problem, not the model.** If the room jumps to "we'll use
  model X," park it and come back to box 1.
- **"Add a chatbot" is a smell, not an answer.** When you hear it, ask: *what
  problem does the chat solve that the current UI doesn't, and would an inline
  suggestion serve the user better?* Usually the answer reshapes boxes 1, 5,
  and 7.
- **Make the input→output contract concrete early.** Vague inputs/outputs are
  the #1 cause of un-buildable, un-testable ideas.
- **Force the pipeline-vs-agent decision (box 6).** Most "agent" ideas are
  actually pipelines. Picking the simplest shape that works saves cost and
  evaluation pain.
- **Don't skip Evaluation, Costs, and Pricing.** Teams want to stop after the
  Solution box. The last band is what makes it real — protect the time for it.
- **Capture assumptions, don't resolve everything.** A box with a clear open
  question is a success. Keep a running "to validate" list.
- **One feature, end to end.** A fully worked single canvas teaches the method
  better than three half-finished ones.

---

## After the workshop

1. **Photograph / export** the completed canvas and share it with the team.
2. **Triage open questions** into "validate with data," "needs a spike," and
   "decided."
3. **SA follow-up:** turn boxes 3–6 and 10–11 into a lightweight architecture
   and a small **proof of concept** on Amazon Bedrock — prove the riskiest
   assumption (usually evaluation/accuracy) first.
4. **AM follow-up:** connect the team to relevant AWS programs and the
   [AWS startup team](https://aws.amazon.com/startups/contact-us); line up the
   next session to review PoC results.
5. **Repeat the canvas** for the other candidate features the team brought.

---

## Files in this directory

| File | Purpose |
|------|---------|
| [`genai-product-canvas-template.pptx`](./genai-product-canvas-template.pptx) | The blank canvas. Fill in live, or print large and use sticky notes. One per feature. |
| `README.md` | This facilitator guide. |
| [`facilitator-kit/`](./facilitator-kit/) | Everything a new SA needs to run and to *teach* this workshop (see below). |

## Facilitator kit — running and scaling it

New to facilitating, or rolling this out to other SAs? Start in
[`facilitator-kit/`](./facilitator-kit/):

| File | Purpose |
|------|---------|
| [`run-sheet.md`](./facilitator-kit/run-sheet.md) | Minute-by-minute script for the SA + AM pair, with the who-drives-what split and pacing rules. |
| [`sample-completed-canvas.md`](./facilitator-kit/sample-completed-canvas.md) | A fully worked canvas (a "HelpHive" support agent-assist feature) — your reference answer for every box. |
| [`faq-and-objections.md`](./facilitator-kit/faq-and-objections.md) | Prep FAQ for facilitators + scripted responses to in-the-room objections (starting with "why not just a chatbot"). |
| [`train-the-trainer.md`](./facilitator-kit/train-the-trainer.md) | The observe → co-facilitate → lead onboarding path, community/ownership model, and how to measure scale across APJ. |

---

*Part of the [AWS APJ Startup Samples](../../README.md) repository. Provided for
reference under the repository's MIT-0 license.*
