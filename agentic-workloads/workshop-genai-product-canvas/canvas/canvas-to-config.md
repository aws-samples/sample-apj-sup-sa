# From canvas to configuration: translating design requirements into model requirements

This is the heart of Part 2. Your canvas is a set of *design decisions*. An agent
runs on *model requirements* — a system prompt, a tool set, a model choice, and a
few runtime settings. This document is the bridge: every canvas box maps to a
concrete, editable line in this repo.

Hand your filled canvas to the AI assistant along with this table and ask it to
produce your `system_prompt.txt`. Then diff it against the baseline.

## The mapping

| Canvas decision | Becomes this model requirement | Where in the repo |
|-----------------|-------------------------------|-------------------|
| Shape = Agentic Loop | Use an `Agent` with tools (not a fixed pipeline / Step Functions) | `agent/agent.py` -> `Agent(model, system_prompt, tools=...)` |
| Outcome = structured report | A terminal tool with a strict output schema the model must call last | `generate_anomaly_report` in `tool_definition.json` + "Output Format" in `system_prompt.txt` |
| Why not chat | Instruction: "You produce a report, not a conversation." | `system_prompt.txt` -> Role section |
| Definition of Done | Explicit stop rules the model can follow | `system_prompt.txt` -> "Definition of Done" section |
| Max 8 iterations | Prompt hard-stop + (optionally) framework cap | `system_prompt.txt` + Strands `Agent` config |
| Confidence >= 0.6 | Prompt rule + require a confidence field in the report | `system_prompt.txt` + `findings.probable_causes[].confidence` |
| Consider >= 2 causes | Reasoning rule in the prompt | `system_prompt.txt` -> Reasoning Rules |
| Human gate on actions | Prompt constraint + (extension) a Guardrail / pre-tool hook | `system_prompt.txt` -> Human Gate; extension in `content/100-extensions` |
| Async / hours latency | Deploy behind a schedule; not a chat UI | `skills/deploy-to-agentcore` + EventBridge (extension) |
| User = field ecologist | Persona + tone framing; enforcement phrased as "coordinate with Perhilitan" | `system_prompt.txt` -> Role + Human Gate |
| Tools = all six | The tool list wired to the agent (local) and the Gateway target (remote) | `agent/tools_local.py` `LOCAL_TOOLS` / `agent/tool_definition.json` |
| Cost estimate | Validate against real token usage after deploy | `observability/README.md` |

## Worked example: one canvas line -> prompt text

Canvas: **"Definition of Done: confidence >= 0.6 AND >=1 evidence source AND
actions identified. Hard stop 8 calls; else escalate."**

Model requirement (prompt text the model can actually follow):

```
## Definition of Done (loop termination)
- Stop when confidence >= 0.6 AND at least one evidence source supports the
  leading hypothesis AND you have identified concrete recommended actions.
- Hard stop: maximum 8 tool calls per investigation.
- If still unresolved after 8 calls, generate the report with
  escalation_needed = true.
```

Notice what changed: "confidence" became an instruction *and* a required output
field; "8 calls" became an explicit ceiling; "escalate" became a concrete flag the
downstream system can route on. Vague design language became executable model
language. That translation is the skill this workshop teaches.

## Common translation mistakes

1. **Pasting the canvas verbatim.** "Async batch, hours" means nothing to the
   model. It is a *deployment* decision (a schedule), not a prompt line.
2. **Listing tools without a methodology.** Wiring six tools does not tell the
   model *when* to use each. The prompt needs an ordered methodology plus rules
   for when to deviate (e.g. regional vs localized anomalies).
3. **No output contract.** "Produce a report" is not enforceable. Give the model a
   terminal tool with an enum'd schema so the output is machine-checkable.
4. **Stop condition only as a number.** "8 iterations" without "else escalate"
   leaves the model to invent an ending. State the unresolved path explicitly.

## Try it

1. Fill `product-canvas-blank.md` as a team.
2. Give your canvas + this table to the AI and ask for a `system_prompt.txt`.
3. Compare with `agent/system_prompt.txt`. Where do you differ? Is your version
   more or less enforceable?
4. Drop your version into `agent/system_prompt.txt`, run `python agent/agent.py
   tapir`, and see whether the agent behaves the way your canvas predicted.
