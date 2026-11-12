---
title: "Step 10: Evaluation"
weight: 65
---

## Learning Objectives

By the end of this step, you will:
- Understand the difference between on-demand and online evaluation
- Run an on-demand evaluation and interpret the scores
- Set up continuous online evaluation for production monitoring
- Know what to fix when scores are low

## Why Evaluate?

Observability (Step 9) tells you *what happened*. Evaluation tells you *how well it went*.

:link[AgentCore Evaluations]{href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/evaluations.html"} uses **LLM-as-a-Judge**: a separate LLM scores your agent's responses for quality, correctness, and tool usage. This gives you quantitative metrics you can track over time.

| Evaluation Type | When It Runs | Use Case |
|----------------|-------------|----------|
| **On-demand** | You trigger it manually | Test after changes, investigate specific sessions |
| **Online** | Continuously on live traffic | Production monitoring, quality alerts |

::alert[**One CLI command, one template block.** The on-demand run in Step 10.1 is an analysis you start by hand, so it uses the `agentcore eval` CLI. Continuous scoring in Step 10.3 is infrastructure, so it is a CloudFormation resource you uncomment like every other step.]{type="info"}

## Lab Procedures

### Step 10.1: Run an On-Demand Evaluation

On-demand evaluation scores traces that already exist in CloudWatch (from your invocations in Step 9). You pass it two things: the **agent runtime id** (the `AgentRuntimeId` from `make outputs`, e.g. `agentic_analytics_agent-xxxxxxxxxx`) and a **session id** you exercised in Step 9:

::alert[**Use a session you actually exercised, and give traces a few minutes to index.** Evaluation reads spans from CloudWatch Transaction Search, which indexes a short time *after* an invocation. Use a session id from the chat UI (the endpoint's **OTEL sessions** tab lists them) and pass it with `-s`. The agent runtime is built with OpenTelemetry auto-instrumentation (`opentelemetry-instrument` + the ADOT env on the Runtime) and the runtime role has X-Ray permissions, so agent/model/tool spans flow to `aws/spans`: that's what eval scores. If you see `No spans found`, wait ~3–5 minutes and retry, and confirm the agent emitted runtime spans (Step 9, **All spans** tab: look for `AgentCore.Runtime.*` / model spans, not just `AgentCore.Gateway.*`).]{type="warning"}

```bash
agentcore eval run \
  --agent-id "<AgentRuntimeId-from-make-outputs>" \
  -s "<session-Id-in-Step-9>" \
  --evaluator "Builtin.Helpfulness" \
  --evaluator "Builtin.Correctness" \
  --evaluator "Builtin.ToolSelectionAccuracy"
```

::alert[**`--agent-id` is required.** Without it the CLI errors `No agent specified`. Use the `AgentRuntimeId` output (run `make outputs` in `app/agentcore_strands`), not the full ARN.]{type="info"}

This takes 1-2 minutes. The CLI fetches recent traces and sends them to the evaluator LLM.

### Step 10.2: Understand the Evaluators

Each built-in evaluator scores a different dimension:

| Evaluator | Question It Answers | Level |
|-----------|-------------------|-------|
| `Builtin.Helpfulness` | Was the response useful to the user? | Per trace |
| `Builtin.Correctness` | Was the response factually accurate? | Per trace |
| `Builtin.ToolSelectionAccuracy` | Did the agent pick the right tool for the question? | Per tool call |
| `Builtin.GoalSuccessRate` | Did the agent complete the user's task? | Per session |


### Step 10.3: Set Up Online (Continuous) Evaluation

For production you want conversations scored without anyone running a command. That is one more resource in your template. Find the **Step 10** fence in :code[agentcore-topup-stack.yaml]{showCopyAction=true}, uncomment it and deploy:

```bash
cd /workshop/agentic-analytics/app/agentcore_strands
make deploy
```

The fence adds an IAM role that can read the runtime's spans and an `AWS::BedrockAgentCore::OnlineEvaluationConfig` named `unicorn_rental_online_eval`. It:
- samples 10% of finished conversations (raise it while you test, lower it for cost in production)
- scores each with Correctness, Faithfulness, Helpfulness, ToolSelectionAccuracy and GoalSuccessRate
- writes scores to a results log group and as CloudWatch metrics in the `Bedrock-AgentCore/Evaluations` namespace, so you can alarm on quality like you alarm on errors

Have a few conversations in the UI, then give the evaluator a few minutes: it scores a conversation after it has finished.

### Step 10.4: View Results in CloudWatch

1. Open **CloudWatch** → **GenAI Observability** → **AgentCore**
2. Navigate to your agent → **DEFAULT** endpoint
3. Select the **Evaluations** tab
4. You'll see aggregated scores for each evaluator

**What low scores mean:**
- **Low ToolSelectionAccuracy** → your SOP tool mapping needs improvement (agent picks wrong tool)
- **Low Correctness** → the prebaked SQL views may return unexpected data, or the LLM misinterprets results
- **Low Helpfulness** → the response formatting in the SOP needs work (too verbose, missing insights)

### Step 10.5: The Feedback Loop

Evaluation closes the development loop:

```
Build agent → Deploy → Observe traces → Evaluate quality
     ↑                                        ↓
     └──── Improve (SOP, tools, prompts) ←────┘
```

In practice:
1. **Low ToolSelectionAccuracy** → update the SOP tool mapping instruction → redeploy
2. **Low Correctness** → fix the database view or add business context to the KB → redeploy
3. **Low Helpfulness** → adjust response formatting constraints in the SOP → redeploy
4. Re-evaluate to confirm improvement

::alert[**For your SaaS:** Set up online evaluation from day one. Track scores weekly. When you onboard a new tenant with different data patterns, evaluation can flag quality regressions before tenants report them.]{type="success"}

## Summary

You set up both on-demand and continuous evaluation for your agent. With observability from Step 9, you can trace a conversation, score it, and use low scores to find the SOP rule, tool, or view to fix.

Next → [Step 11: Schema changes](../11-schema-changes/)

## Reference Materials

- :link[AgentCore Evaluations]{href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/evaluations.html"}
- :link[Built-in Evaluators]{href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/built-in-evaluators-overview.html"}
- :link[On-Demand Evaluation Guide]{href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/getting-started-on-demand.html"}
- :link[Online Evaluation Guide]{href="https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/create-online-evaluations.html"}
- :link[Strands Agents Evals SDK]{href="https://strandsagents.com/docs/user-guide/evals-sdk/quickstart/" external=true} (for test-dataset evaluation)
- :link[Strands Evals: Output Evaluator]{href="https://strandsagents.com/docs/user-guide/evals-sdk/evaluators/output_evaluator/" external=true}
