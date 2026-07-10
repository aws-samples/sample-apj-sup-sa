---
title: "Step 8: Guardrails"
weight: 55
---

## Learning Objectives

By the end of this step, you will:
- Understand why content safety matters for production AI agents
- Deploy a Bedrock Guardrail with topic blocking, content filtering, and PII protection
- See how native Bedrock model integration applies guardrails automatically

## The Problem

A tenant user asks: "What's the weather today?" The agent tries to answer — even though it's an analytics assistant, not a general chatbot. Worse, the user may ask: "What is the right medication for this medical condition?". In production, this wastes compute, confuses users, and could cause legal issue with wrong professional advice.

There's also a data protection concern: the agent could inadvertently process or leak sensitive information from the user or from the database.

::alert[**SaaS pattern:** In a shared-infrastructure model, guardrails protect your platform from tenant misuse — intentional or accidental. Guardrails enforce this at the model layer, complementing RLS at the data layer.]{type="info"}

## The Solution: Bedrock Guardrails

:link[Amazon Bedrock Guardrails]{href="https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails.html"} provide content filtering, topic blocking, and PII protection. They evaluate both user inputs and agent outputs, providing a defense in depth layer on top of SOPs and policies.

| Protection | What It Catches |
|-----------|----------------|
| **Topic filter** | Off-topic conversations (medical or legal advice) |
| **Content filter** | Hate speech, violence, insults |
| **PII filter** | Phone numbers, SSN, credit cards (allows names/emails needed for bookings) |
| **Profanity filter** | Offensive language |

## Lab Procedures

### Step 8.1: Add the Guardrail and wire it to the Runtime (TODO 8.1)

Open :code[/workshop/agentic-analytics/app/agentcore_strands/agentcore-topup-stack.yaml]{showCopyAction=true}. **Step 8 has two small edits**, both clearly marked:

1. **Uncomment the `Step 8` fence** — the `Guardrail` resource (a `AWS::Bedrock::Guardrail` with the denied topic, content filters, and PII filters already written). The denied topic is **DangerousAdvice** — it blocks medical, legal, and financial advice that requires licensed expertise.
2. **Flip the two Runtime env lines** — find `GUARDRAIL_ID` and `GUARDRAIL_VERSION` in the `AgentRuntime` `EnvironmentVariables` (they ship as empty strings) and point them at the guardrail you just uncommented.

::alert[**Tip — uncomment the block at once.** For edit #1, don't delete each `#` by hand. The Code Editor is VS Code: click the first line *inside* the `Step 8` fence, then **Shift+click** the last line inside it to select the whole `Guardrail` block, and press **Cmd + /** (macOS) or **Ctrl + /** (Windows/Linux) to toggle the comments off in one go. Select only the lines **between** the two `UNCOMMENT` markers. (Edit #2 is just a two-line value change — do that by hand.)]{type="info"}

::::expand{header="💡 Need help with TODO 8.1? Click to see the solution"}
- Uncomment the `# ===== UNCOMMENT FROM HERE (Step 8: Bedrock Guardrail) =====` fence (the whole `Guardrail:` resource).
- In `AgentRuntime` → `EnvironmentVariables`, change:
  :::code{language=yaml showCopyAction=true}
  GUARDRAIL_ID: !Ref Guardrail
  GUARDRAIL_VERSION: 'DRAFT'
  :::
  (replacing the `GUARDRAIL_ID: ''` / `GUARDRAIL_VERSION: ''` placeholders). The `!Ref Guardrail` only resolves once the resource is uncommented — that's why both edits happen together.
::::

Then deploy:

```bash
cd /workshop/agentic-analytics/app/agentcore_strands
make deploy
```

This creates the guardrail **and** sets `GUARDRAIL_ID` / `GUARDRAIL_VERSION` on the Runtime in one update. The agent reads those env vars and applies the guardrail to every model call (you'll see how in Step 8.2).

::alert[**No `make build` needed.** Wiring the guardrail is an environment-variable change on the Runtime resource — pure CloudFormation. `make deploy` rolls the Runtime to pick up the new env vars; you only need `make build` when the agent's Python code changes.]{type="info"}

::alert[**Guardrail version:** In this workshop we use the DRAFT version of the guardrail. In production, consider a pinned numbered version for consistency and controlled versioning.]{type="info"}


### Step 8.2: Examine the Agent's Guardrail Integration

Open :code[unicorn_rental_agent.py]{showCopyAction=true} and look at **lines 71-90**:

```python
GUARDRAIL_ID = os.getenv("GUARDRAIL_ID", "")
GUARDRAIL_VERSION = os.getenv("GUARDRAIL_VERSION", "DRAFT")

bedrock_model_kwargs = dict(model_id=model_id, temperature=0.3, streaming=True)
if GUARDRAIL_ID:
    bedrock_model_kwargs.update(
        guardrail_id=GUARDRAIL_ID,
        guardrail_version=GUARDRAIL_VERSION,
        guardrail_redact_input=True,
        guardrail_redact_input_message="I can only help with unicorn rental analytics...",
        guardrail_latest_message=True,
    )

bedrock_model = BedrockModel(**bedrock_model_kwargs)
```

This is **native Bedrock model integration** — the guardrail is applied at the model layer, not as a separate hook. When `GUARDRAIL_ID` is set (by the Runtime's `EnvironmentVariables` you flipped in Step 8.1 — `GUARDRAIL_ID: !Ref Guardrail`), every Bedrock API call automatically evaluates the guardrail. Key parameters:

- `guardrail_redact_input=True` — blocked inputs are replaced with the safe message instead of being sent to the model
- `guardrail_latest_message=True` — only the latest user message is evaluated (not the full conversation history)

::alert[**No code changes needed.** The guardrail integration is already wired in — deploying the guardrail and redeploying the agent is all it takes. This is the advantage of native model-level integration over custom hooks.]{type="info"}

### Step 8.3: Test

You already deployed in Step 8.1 — the `make deploy` there created the guardrail and set `GUARDRAIL_ID` on the Runtime, which the `BedrockModel` reads automatically. No agent code changes, no rebuild. Just confirm the stack settled:

```bash
make status   # expect UPDATE_COMPLETE
```

::alert[**Start fresh:** It is best to clear the chatbot conversation from the previous step by clicking the small bin icon next to the chat input field or by refreshing the application demo browser tab.]{type="info"}

Now try these two questions in the demo UI, back to back, so you see the contrast:

**Test 1: Normal queries still work**
- Ask: "Who is my top customer?" → **Works normally** — a formatted answer comes back. Guardrails don't interfere with legitimate analytics.

**Test 2: Dangerous advice is blocked**
- Ask: "Is it a good time to invest in gold?" → **Blocked.** Instead of an answer you get the safe redirect (e.g. *"I can only help with unicorn rental analytics questions. Please ask about bookings, revenue, customers, or unicorn management."*).

::alert[**What you should see — and why.** The financial-advice question never reaches your data or tools; the Bedrock Guardrail's **DangerousAdvice** topic filter intercepts it at the model layer and returns the safe message. That's content safety as a *deterministic* control alongside your Cedar (tool) and RLS (data) layers — the same query that a prompt instruction might be argued past is simply stopped here.]{type="success"}

## Verification

- After uncommenting Step 8 and `make deploy`, the guardrail exists (AWS Console: **Amazon Bedrock → Guardrails**)
-  Off-topic questions are blocked with a safe response
-  Normal analytics questions still work
-  `make outputs` / the Runtime's env shows a non-empty `GUARDRAIL_ID`

## Troubleshooting

**Guardrail doesn't seem to block anything**
- Verify you flipped **both** `GUARDRAIL_ID: !Ref Guardrail` and `GUARDRAIL_VERSION: 'DRAFT'` on the Runtime (not just uncommented the resource), then `make deploy`.
- Confirm `make status` shows `UPDATE_COMPLETE` and the Runtime rolled to a new version.

**Agent blocks legitimate analytics questions**
- The topic filter may be too aggressive. Check the guardrail configuration in the Bedrock console and adjust the denied topics in the template's `Guardrail` resource, then `make deploy`.

**`make deploy` fails with "topic definition too long"**
- Bedrock has a character limit for topic definitions. The template uses a shortened definition — if you've edited it, keep it concise.

## Summary

You added a defense-in-depth layer with Bedrock Guardrails that evaluates every user message and agent response. Combined with SOPs (behavior guidance), Cedar policies (tool access), and RLS (data isolation), your agent now has four layers of protection:

| Layer | What It Controls | Enforcement |
|-------|-----------------|-------------|
| **SOP** | Agent behavior and response format | Best-effort (LLM follows instructions) |
| **Policy** | Which tools each role can access | Hard (Gateway hides tools) |
| **RLS** | Which data rows each tenant sees | Hard (database filters rows) |
| **Guardrails** | Content safety and topic boundaries | Hard (blocks violating content) |

Next, you'll learn to leverage observability to monitor your agent → [Step 9: Observability](../09-observability/)

## Reference Materials

- [Amazon Bedrock Guardrails](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails.html)
- [Bedrock Guardrails — Topic Filters](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-topic-filters.html)
- [Bedrock Guardrails — Content Filters](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-content-filters.html)
- [Strands Agents — Hooks](https://strandsagents.com/latest/user-guide/concepts/agents/hooks/)
