# Instructor guide

**Duration:** 90 minutes. **Audience:** CXOs, technical leads, senior engineers.
**Room setup:** teams of 2-3; one deployed reference agent for the Part 1 demo.

## Timing

| Segment | Time | Page |
|---------|------|------|
| Part 0 — open the Code Editor, Claude Code on Bedrock | 10 min | `content/020-part-0-environment/` |
| Frame (pipeline vs loop, AgentCore blocks) | 10 min | `content/030-part-1-building-blocks/031-frame.md` |
| Part 1 — see a working agent (live demo) | 10 min | `content/030-part-1-building-blocks/032-see-working-agent.md` |
| Observability of the demo (cost + traces) | 8 min | `content/030-part-1-building-blocks/035-observability.md` |
| Design the canvas | 20 min | `content/040-part-2-design/041-design-canvas.md` |
| Build: translate the canvas, run local + remote, deploy | 25 min | `content/040-part-2-design/050-build.md` |
| Validate + debrief | 7 min | `content/040-part-2-design/080-validate.md` |

## Before the event

1. Deploy the **reference agent** in an instructor account so Part 1 has a live
   agent to invoke:
   - `bash infra/deploy_tools.sh` -> `source tools.env`
   - `python infra/create_gateway.py` -> `source gateway.env`
   - fill gateway.env into `agent/agentcore/agentcore.json` envVars, then `cd agent && agentcore deploy` (the project ships in the repo)
   - `bash observability/enable_observability.sh`, then invoke a few times so the
     dashboards have data to show.
2. Confirm Bedrock model access to Claude Sonnet in the workshop region.
3. Pre-provision participant accounts (Workshop Studio blueprint) with the same
   IAM permissions; optionally pre-deploy the tool backend so teams skip Step 4.
4. Print the blank canvas (`canvas/product-canvas-blank.md`) and keep the
   reference (`canvas/product-canvas-reference.md`) for the reveal.

## Reveal sequence

- Do **not** show `product-canvas-reference.md` until teams have attempted their
  own. The value is in the struggle to make vague design language enforceable.
- After the build, reveal the canvas -> config mapping (`content/040-part-2-design/080-validate.md`).

## Talking points that land

- "Can you draw the call graph before running the code?" is the whole
  pipeline-vs-loop decision in one question.
- AgentCore does not replace Bedrock — the model still does the reasoning.
  AgentCore is where the agent *runs, reaches tools, and is observed*.
- The same `agent.py` runs local, on remote tools, and on the managed runtime.
  Emphasise that nothing about the agent changed — only its environment.
- Token usage in Observability is the cost of the loop. Pipelines are cheaper
  because they do not feed tool output back into a reasoning model repeatedly.

## Common mistakes to watch for

1. **Canvas pasted verbatim into the prompt.** Help teams translate "confidence >=
   0.6" and "async batch" into enforceable model instructions vs deployment
   choices. Point them at `canvas/canvas-to-config.md`.
2. **Six tools, no methodology.** A tool list does not tell the model *when* to use
   each. The prompt needs an ordered methodology and rules for deviating.
3. **Only testing the tapir.** Push teams to run the **pangolin** case — a regional
   decline that a single-station land-use check cannot explain. It exposes weak
   designs.
4. **Gateway auth confusion.** If `TOOL_MODE=gateway` fails, it is almost always
   the bearer token / Cognito scope. See `docs/TROUBLESHOOTING.md`.

## Key takeaways to reinforce

- The canvas boxes are not academic; each maps to a real configuration line.
- "Pipeline or loop" is the highest-leverage decision. Everything follows from it.
- An agent without a Definition of Done runs to timeout or fakes confidence. The
  8-call stop is a *design* decision, not a technical limit.
- Human-gate placement determines your blast radius when the model is wrong.
