# FAQ & objection handling

Quick answers for the facilitator — both the *"how do I run this"* questions
from new SAs and the *"in-the-room"* objections from participants.

---

## Facilitator FAQ (for SAs preparing to run it)

**Q: I've never facilitated before. Can I run this solo?**
Run it as a **pair** the first time (SA + AM, as designed). The
[run-sheet](./run-sheet.md) splits who-drives-what so neither of you carries the
whole room. If you must go solo, recruit a co-pilot to scribe and timekeep.

**Q: How long does it really take?**
~3 hours for one canvas done well. You *can* compress to 90 min for a single
simple feature with an experienced PM team, but cut depth in Solution/Costs —
never in Problem, Outputs, or Evaluation.

**Q: What if the customer brings no idea?**
That's a prep failure — confirm 1–3 ideas the day before. If they still arrive
empty, use the [worked example](./sample-completed-canvas.md) as a guided demo
for the first band, then have them swap in their own feature.

**Q: Do I need to be deeply technical?**
The SA should be comfortable with prompt vs. pipeline vs. agent, RAG/retrieval,
structured outputs, and rough Bedrock token costs. You don't need to write code
— you need to keep ideas grounded in what's buildable.

**Q: Can I change the boxes / order?**
No — the left-to-right, top-to-bottom order is load-bearing (you can't pick
metrics before you know the output). You *can* spend more or less time per box.

**Q: Online or in-person?**
Both work. In-person with a printed A0 + stickies has the best energy. Remote:
use the live slide and a shared doc; assign a dedicated scribe.

---

## In-the-room objections (from participants)

**"Why not just add a chatbot?"** *(the big one)*
Validate, then redirect: *"A chatbot is a UI choice — let's first nail the
problem and the data. Often once we do, an inline suggestion or an automated
step serves the user better than a chat box."* Point them at the Existing
alternatives and UX boxes. The [worked example](./sample-completed-canvas.md) is
deliberately *not* a chatbot — use it.

**"We just want to use [GPT/Claude/model X]."**
*"Picking the model is the last 10% — let's define the job first, then choose the
cheapest model that hits the accuracy bar."* Park the model name; return to
Problem.

**"This needs to be an autonomous agent."**
Press on LLMX: *"Does the model need to take actions and decide its own steps,
or follow a fixed sequence? If it's fixed, a pipeline is cheaper, faster, and far
easier to evaluate."* Most ideas are pipelines wearing an agent costume.

**"We don't have data for this."**
That's a critical finding, not a blocker. Capture it as the #1 open question —
no data usually means *validate feasibility before building*. It may reshape the
Inputs box or the whole idea.

**"Evaluation/metrics feel premature, we just want to ship."**
*"Without a way to measure 'good,' you can't tell a demo from a product, and you
can't improve it after launch."* This is the most-skipped, highest-value part —
hold the line on the time.

**"How accurate will it be?"**
You can't promise a number in the room — that's *why* the Evaluation box exists.
Frame it: *"That's exactly what we'll measure with the test set; the canvas sets
the target, the PoC proves it."*

**"Can't we just do all three ideas now?"**
*"One done well teaches the method better than three half-finished. The others
are homework using the same canvas — and they'll go faster now you know how."*

**The room goes quiet on a box.**
Read the relevant prompt from the README, then offer the worked example's answer
for that box as a starter — people react more easily than they generate.

---

## Escalation / where to get help
- Stuck on a technical box mid-workshop → note it as an open question; follow up
  with the local GenAI/Bedrock specialist SA.
- Questions about the workshop itself → see the
  [train-the-trainer guide](./train-the-trainer.md) for the facilitator
  community channel and the workshop owner.
