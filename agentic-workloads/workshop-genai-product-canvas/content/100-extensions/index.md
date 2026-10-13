---
title: "Extension Challenges"
weight: 100
---

For teams that finish early. Each maps back to a canvas decision.

Note: the main workshop is code-free (fill the canvas, run the skill). These
challenges go further and touch the tool backend or scheduling, which is normally
pre-provisioned for you. Some steps assume access to the provisioning scripts and
`tools.env`. Do them in your own AWS account, or ask the facilitator, if your
workshop account restricts infrastructure changes.

## Challenge 1: Add the human gate as a Guardrail

Your canvas said the human gate sits on real-world actions. Enforce it beyond the
prompt.

- Console: create a Bedrock Guardrail with a denied topic for direct law
  enforcement ("conduct a raid", "arrest"), and attach it to the model.
- Code: add a validation hook before `generate_anomaly_report` that rejects
  recommended actions containing enforcement verbs and rewrites them to
  "coordinate with Perhilitan".

Re-run the pangolin scenario and confirm the recommendations stay in-scope.

## Challenge 2: Split into two agents

Design (on paper) a two-agent architecture:

- Detective agent: investigates, gathers evidence.
- Reporter agent: turns findings into tailored reports for different personas
  (ecologist, funder, ranger).

Answer: How do they communicate? What is the hand-off schema? Does the reporter
need the tools? What does the split buy you over a single two-phase prompt? On
AgentCore, each could be its own Runtime, chained via the Gateway (HTTP target).

## Challenge 3: Add a scheduled / real-time path

Your canvas chose async batch. Design the faster path:

| Decision | Your answer |
|----------|-------------|
| What triggers it? (not a monthly batch) | |
| Pipeline or loop? | |
| Latency budget? | |
| Which tools does it need / not need? | |
| Minimum viable alert? | |

The async batch path is already implemented in `infra/scheduled/`: an
EventBridge Scheduler triggers a Lambda nightly that calls `InvokeAgentRuntime`
over each station's latest month. Deploy it after you deploy the agent
(`agentcore deploy`):

```bash
source tools.env                       # DATA_BUCKET, AUDIT_BUCKET
export AGENT_RUNTIME_ARN=<from agentcore status>
bash infra/scheduled/deploy_schedule.sh
```

Study `infra/scheduled/lambda/index.py` and answer: does the real-time path need a
full investigation, or just a "something looks off" nudge that wakes the batch
agent? Then design the faster trigger in the table above.

## Challenge 4: Add a new tool via the Gateway

Add a seventh tool (e.g. `get_patrol_history`) end to end:

1. Add its function to `infra/lambda/tools/index.py` and the `DISPATCH` map.
2. Add its schema to `agent/tool_definition.json`.
3. Re-provision the tool backend (`deploy_tools.sh`) and re-sync the Gateway
   target. This is a provisioning-side step, so run it in your own account or with
   the facilitator.
4. Mention it in `system_prompt.txt` methodology.

Observe: did the agent start using it? If not, was the tool description clear
enough for the model to know when to reach for it?
