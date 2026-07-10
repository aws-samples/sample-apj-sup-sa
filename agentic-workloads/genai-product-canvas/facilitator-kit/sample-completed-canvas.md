# Worked example — a completed GenAI Product Canvas

A fully filled-in canvas for a fictional company, **HelpHive** (a B2B
customer-support SaaS), feature: **agent-assist draft replies**. Use this as
your reference answer while facilitating — when a box stalls, show the room what
"good" looks like here, then come back to *their* feature.

> This is deliberately a **pipeline, not an agent**, and deliberately **not a
> customer-facing chatbot** — it models the "beyond add-a-chatbot" move the
> workshop exists to teach.

**Title:** `HelpHive — Support agent-assist (draft reply + summary)`

---

## Band 1 — Frame the opportunity

### Problem 🔒
Support agents spend ~6 minutes per ticket manually reading the full thread and
the customer's history before they can reply. At ~80 tickets/agent/day this caps
throughput, slows first-response time (currently 4h median, SLA is 2h), and
drives agent burnout. First-response SLA misses are the #1 driver of CSAT
complaints.

### Existing alternatives 🔀
Today agents either read the entire thread manually, or lean on canned macros.
Macros are fast but generic — they miss context and customers can tell. Search
helps find KB articles but doesn't draft anything. No current solution combines
*this customer's context* with *a ready-to-edit reply*.

### Inputs 🌐
- **Ticket thread** (text, from the support DB).
- **Customer's last 5 orders + plan tier** (JSON, from the orders API).
- **Top-k relevant KB articles** (retrieved via vector search over the KB).
- All internal data; no PII beyond name/email, which stays in-tenant. Retrieval
  needed (KB is too large to inline). ~4k input tokens typical.

### Outputs ☁️
Structured JSON, consumed by the agent console:
```
{ "summary": string (≤120 words),
  "suggested_reply": string,
  "sentiment": "positive"|"neutral"|"negative",
  "citations": [ {source_id, type} ] }
```
Constraints: every factual claim must cite a source ticket or KB article;
**must never state a refund/credit amount** (policy-gated, humans only); reply
tone matches HelpHive's voice guide.

---

## Band 2 — Design the solution

### Solution 💡
When an agent opens a ticket, the system retrieves the customer's order history
and the top KB articles, then calls the model once to produce a **summary** and
a **draft reply** with citations. Both appear inline in the agent console for
one-click edit-and-send. The agent is always in the loop — the model drafts, the
human decides.

### LLMX 🧠
**Pipeline, not an agent.** Deterministic order: `get_order_history` →
`search_kb` (vector retrieval) → single model call (Bedrock Converse). Two
tools, fixed sequence, no autonomous looping — so it's cheap, predictable, and
easy to evaluate. Bedrock Knowledge Bases for retrieval; Guardrails to enforce
the "no refund amounts" constraint.

### UX ✨
- **Modality:** inline panel in the existing agent console — **not** a chatbot.
  The draft sits next to the reply box, pre-filled and editable.
- **Augmentation:** the agent edits and sends; never auto-sends.
- **Feedback:** every edit (diff) + a thumbs up/down is logged. This feedback is
  the ongoing evaluation signal (see Evaluation).

---

## Band 3 — Prove it & ship it

### Definition of Done ✅
A response is "done" when: the summary is factually grounded in the thread, the
draft addresses the customer's actual question, all claims are cited, and no
prohibited content (refund amounts) appears. If grounding confidence is low,
return *no draft* and let the agent work unassisted rather than risk a bad draft.

### Success metrics 📋
- **Accuracy:** ≥85% of drafts sent with only minor edits (edit-distance < 20%).
- **Latency:** draft visible < 3s after ticket open.
- **Adoption:** ≥60% of agents use the draft on ≥half their tickets within 1 month.
- **Business:** median first-response time 4h → under the 2h SLA.

### Evaluation 🏃
- **Test set:** 200 historical tickets, each with a human-written "ideal" reply
  as ground truth, sampled across product areas and sentiment.
- **Offline:** LLM-as-judge scores grounding + helpfulness; humans spot-check
  the bottom-scoring 20%.
- **Online:** track edit-distance and thumbs up/down weekly; alert if
  edit-distance trends up (drift signal). Re-run the offline suite on every
  prompt/model change.

### Costs 📊
~4k input + ~400 output tokens × ~5,000 tickets/day. On a mid-tier Bedrock model
≈ **$X/day** (fill in current per-token pricing at delivery). Re-estimate the
summary step on a smaller model; enable prompt caching for the static system
prompt + KB chunks.

### Pricing 💰
**Internal efficiency play, no direct customer price.** At ~$X/day inference vs.
~6 min saved per ticket across N agents, payback is well inside the first month.
Bundled into the existing support seat as a productivity feature; could later
become a paid "AI Assist" tier add-on once adoption is proven.

---

## What makes this a *good* canvas (point these out to the room)
1. **Problem is quantified** (6 min/ticket, 4h vs 2h SLA) — so success is falsifiable.
2. **Input → output is a contract**, not a vibe — it's buildable and testable.
3. **It chose the simplest shape** (pipeline) and justified *not* using an agent.
4. **It's not a chatbot** — modality fits the existing workflow.
5. **Evaluation, cost, and pricing are answered**, not deferred — the difference
   between a product and a demo.
