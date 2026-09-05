# From canvas to configuration: translating design requirements into model requirements

This is the heart of Part 2. Your canvas is a set of *design decisions*. An agent
runs on *model requirements* — a system prompt, a tool set, a model choice, and a
few runtime settings. This document is the bridge: every canvas box maps to a
concrete, editable line in this repo.

Hand your filled canvas to the AI assistant along with this table and ask it to
produce your `system_prompt.txt`. Then diff it against the baseline.

## The mapping

| Canvas box | Canvas decision | Becomes this model requirement | Where in the repo |
|-----------|-----------------|-------------------------------|-------------------|
| Solution | Agentic Loop, not a fixed pipeline / Step Functions | Use an `Agent` with tools | `agent/agent.py` -> `Agent(model, system_prompt, tools=...)` |
| Outputs | Structured anomaly report with an enum'd schema | A terminal tool with a strict output schema the model must call last | `generate_anomaly_report` in `tool_definition.json` + "Output Format" in `system_prompt.txt` |
| UX | Report delivered to an inbox, not a conversation | Instruction: "You produce a report, not a conversation." | `system_prompt.txt` -> Role section |
| UX | Human gate on real-world field action | Prompt constraint + (extension) a Guardrail / pre-tool hook | `system_prompt.txt` -> Human Gate; extension in `content/100-extensions` |
| UX | Async batch, hours latency | Deploy behind a schedule; not a chat UI | `skills/deploy-to-agentcore` + EventBridge (extension) |
| Definition of Done | Completion signals (confidence + evidence + actions) | Explicit stop rules the model can follow | `system_prompt.txt` -> "Definition of Done" section |
| Definition of Done | Max 8 iterations | Prompt hard-stop + (optionally) framework cap | `system_prompt.txt` + Strands `Agent` config |
| Definition of Done | Confidence >= 0.6 | Prompt rule + require a confidence field in the report | `system_prompt.txt` + `findings.probable_causes[].confidence` |
| Definition of Done | Consider >= 2 causes | Reasoning rule in the prompt | `system_prompt.txt` -> Reasoning Rules |
| Problem | User = field ecologist | Persona + tone framing; enforcement phrased as "coordinate with Perhilitan" | `system_prompt.txt` -> Role + Human Gate |
| LLMX | Tools = all six, agent decides order | The tool list wired to the agent (local) and the Gateway target (remote) | `agent/tools_local.py` `LOCAL_TOOLS` / `agent/tool_definition.json` |
| LLMX | An ordered methodology, not just a tool list | A step-by-step "when to use which tool" section, with rules for deviating | `system_prompt.txt` -> Investigation Methodology |
| Inputs | Six data sources (detections, weather, land use, news, baselines) | Tool input/output schemas the model calls at run time, not text pasted into the prompt | `agent/tool_definition.json`, `agent/data/*.json` |
| Existing alternatives | Manual spreadsheet review / static threshold alerts | Not a model requirement itself — the baseline the agent must beat, used to justify why the prompt asks for an *explanation*, not just a flag | motivates Reasoning Rules; no direct artifact |
| Costs | ~5 tool calls x ~$0.03 = ~$0.15 / investigation | Validate against real token usage after deploy | `observability/README.md` |
| Pricing | How the feature is priced or packaged | Budget/packaging decision — does NOT become a prompt line | tracked outside the agent config; informs deploy budget only |
| Success metrics | Accuracy / latency / adoption targets | Already encoded via Definition of Done (confidence, iteration cap) and the deploy schedule; adoption is tracked post-deploy | `system_prompt.txt` Definition of Done + `observability/README.md` |
| Evaluation | Ground-truth test set (tapir, elephant, pangolin) | Replay fixture data against the agent and check the report against the known root cause | `agent/data/*.json` fixtures + `python agent/agent.py <species>` |

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

1. **Pasting the canvas verbatim.** "Async batch, hours" (a UX box decision) means
   nothing to the model. It is a *deployment* decision (a schedule), not a prompt
   line.
2. **Listing tools without a methodology.** Wiring six tools in the LLMX box does
   not tell the model *when* to use each. The prompt needs an ordered methodology
   plus rules for when to deviate (e.g. regional vs localized anomalies).
3. **No output contract.** "Produce a report" is not enforceable. Give the model a
   terminal tool with an enum'd schema so the output is machine-checkable.
4. **Stop condition only as a number.** "8 iterations" without "else escalate"
   leaves the model to invent an ending. State the unresolved path explicitly.
5. **Encoding Costs or Pricing as prompt rules.** These boxes shape scope and
   budget, not model behavior — they belong in deployment config and business
   docs, not `system_prompt.txt`.

## Try it

1. Fill `product-canvas-blank.md` as a team.
2. Give your canvas + this table to the AI and ask for a `system_prompt.txt`.
3. Compare with `agent/system_prompt.txt`. Where do you differ? Is your version
   more or less enforceable?
4. Drop your version into `agent/system_prompt.txt`, run `python agent/agent.py
   tapir`, and see whether the agent behaves the way your canvas predicted.
