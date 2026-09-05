# GenAI Product Canvas (reference answer)

Facilitator reference. Reveal after teams have attempted their own canvas in the
design phase. There is no single "correct" canvas, but this is a defensible one
for the biodiversity anomaly use case.

| Box | Answer | Reasoning |
|-----|--------|-----------|
| **Project Title** | Biodiversity Anomaly Detection Agent | — |
| **Problem** | Six unmanned camera-trap stations across the Desaru corridor generate more detection data every month than ecologists can review by hand. A population collapse (logging, poaching, habitat loss) can go unnoticed until the intervention window has closed. | The user is a field ecologist who can't watch 6 stations x 12 species manually; missing a collapse is expensive, so the problem is worth automating. |
| **Existing alternatives** | Ecologists eyeball monthly spreadsheets, or a static rule fires when a count drops below a fixed threshold. | A threshold can flag "count dropped" but can't tell seasonal noise from nearby logging from regional poaching — it alerts, it doesn't explain. |
| **Inputs** | An anomaly alert (species, station, month) plus six tool-backed data sources: query_detections (history), get_species_baseline (IUCN status, normal ranges), get_weather_data, check_land_use, search_news. All structured JSON returned by the AgentCore Gateway tools, not pasted into the prompt. | The prompt only needs the alert and a description of the tools — the agent pulls the data it needs at run time. |
| **Outputs** | A structured anomaly investigation report (JSON + short narrative): species, anomaly_type, severity, probable_causes[] with confidence + evidence, recommended_actions[]. | The ecologist needs a decision artifact in their inbox, not a chat thread. |
| **Definition of Done** | confidence >= 0.6 AND >=1 supporting evidence source AND actions identified. Hard stop at 8 tool calls; else escalate. | A loop with no stop condition runs to timeout or hallucinates certainty. |
| **UX** | Report delivered to an inbox/dashboard as an async overnight batch — no one watches the loop run. The ecologist validates by reading the report and deciding on field action; the agent never authorizes real-world action itself, only investigates and recommends. | Tasks over conversations. Blast radius: a wrong report wastes a ranger trip; a wrong raid is far worse — so the human stays in the loop for action, not investigation. |
| **LLMX** | All six tools (query_detections, get_weather_data, check_land_use, search_news, get_species_baseline, generate_anomaly_report), called through an ordered methodology in the prompt: context -> confirm -> environment -> anthropogenic -> corroborate -> report, skipping steps irrelevant to the anomaly. | The agent decides which tools to call and when; the methodology gives a default order without hardcoding the path — the tapir case needs land-use + news, the pangolin case needs multi-station detections + news only. |
| **Costs** | ~5 tool calls x ~$0.03 = ~$0.15 / investigation (model tokens dominate). | Estimate up front; validate against real token usage in Observability after deploy. |
| **Pricing** | Bundled into the Conservation Trust's existing monitoring subscription; no per-investigation charge to the ecologist. Margin impact is the token cost above versus analyst-hours saved. | This box shapes budget and packaging — it never turns into a system-prompt rule. |
| **Success metrics** | Accuracy: >=80% of reports agree with an ecologist's post-hoc judgment. Latency: report ready by the next morning. Adoption: % of anomaly alerts investigated by the agent instead of manually. | Ties back to the confidence threshold in Definition of Done and the async latency budget in UX. |
| **Evaluation** | The three ground-truth anomalies below (tapir, elephant, pangolin), each with a known root cause and evidence tools. Replay each against the agent and check the report's probable_cause and evidence against the ground truth. | The confidence and evidence-citation rules in Reasoning Rules are only checkable against a known answer. |
| **Solution** | An agentic loop, not a fixed pipeline: the agent calls tools in an order it chooses (the methodology suggests a default), reasons over the evidence gathered, and terminates by calling a report tool with a strict schema. | The investigation path differs per anomaly (tapir needs land-use + news; elephant needs construction records; pangolin needs multi-station analysis). You know the goal, not the path -> loop, not Step Functions. |

## The three anomalies and their ground truth

| Species | Anomaly | Root cause | Key evidence tools |
|---------|---------|-----------|--------------------|
| Malayan Tapir (EN) | Sudden decline to ~0 at STN-03, from ~May 2026 | Illegal logging 0.8 km away (LUC-001) | check_land_use, search_news (NEWS-001/002) |
| Asian Elephant (EN) | Disappearance from STN-01, from ~Apr 2026 | Resort construction severs corridor (LUC-002) | check_land_use, search_news (NEWS-003/004) |
| Sunda Pangolin (CR) | Gradual decline across ALL 6 stations, from ~Feb 2026 | Poaching syndicate | query_detections (multi-station), search_news (NEWS-005/006/007) |

The pangolin case is the hardest: the signal is landscape-wide, so a single-station
land-use check will NOT explain it. A good agent notices the scope is regional and
pivots to news corroboration rather than local causes.
