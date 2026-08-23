# GenAI Product Canvas (reference answer)

Facilitator reference. Reveal after teams have attempted their own canvas in the
design phase. There is no single "correct" canvas, but this is a defensible one
for the biodiversity anomaly use case.

| Box | Answer | Reasoning |
|-----|--------|-----------|
| **Product** | Biodiversity Anomaly Detection Agent | — |
| **Shape** | Agentic Loop | The investigation path differs per anomaly (tapir needs land-use + news; elephant needs construction records; pangolin needs multi-station analysis). You know the goal, not the path -> loop. |
| **1. Outcome** | A structured anomaly investigation report (JSON + short narrative): species, anomaly_type, severity, probable_causes[] with confidence + evidence, recommended_actions[]. | The ecologist needs a decision artifact in their inbox, not a chat thread. |
| **1. Why not chat?** | The consumer acts on a report offline; there is no back-and-forth. | Tasks over conversations. |
| **2. Definition of Done** | confidence >= 0.6 AND >=1 supporting evidence source AND actions identified. Hard stop at 8 tool calls; else escalate. | A loop with no stop condition runs to timeout or hallucinates certainty. |
| **2. Max iterations** | 8 tool calls | Enough for 6 tools + one re-check; caps cost and latency. |
| **3. Human** | Full-auto through detection + investigation + report. Human gate on any real-world field action. | Blast radius: a wrong report wastes a ranger trip; a wrong raid is far worse. |
| **3. False positive cost** | A wasted patrol / analyst review. | Tolerable. |
| **3. False negative cost** | A missed population collapse; intervention window closes. | High -> bias toward flagging. |
| **4. Speed** | Async batch, overnight. Hours budget. Report waiting by morning. | No human is watching the loop run. |
| **4. UX pattern** | Report delivered to inbox / dashboard. | — |
| **5. User** | Field ecologist (AnyCompany Conservation Trust). | Reads the report, decides on action, coordinates with Perhilitan. |
| **Tools** | All six. The agent decides which to call and in what order. | The methodology suggests an order but the agent adapts per anomaly. |
| **Cost** | ~5 tool calls x ~$0.03 = ~$0.15 / investigation (model tokens dominate). | Validate against real token usage in Observability. |

## The three anomalies and their ground truth

| Species | Anomaly | Root cause | Key evidence tools |
|---------|---------|-----------|--------------------|
| Malayan Tapir (EN) | Sudden decline to ~0 at STN-03, from ~May 2026 | Illegal logging 0.8 km away (LUC-001) | check_land_use, search_news (NEWS-001/002) |
| Asian Elephant (EN) | Disappearance from STN-01, from ~Apr 2026 | Resort construction severs corridor (LUC-002) | check_land_use, search_news (NEWS-003/004) |
| Sunda Pangolin (CR) | Gradual decline across ALL 6 stations, from ~Feb 2026 | Poaching syndicate | query_detections (multi-station), search_news (NEWS-005/006/007) |

The pangolin case is the hardest: the signal is landscape-wide, so a single-station
land-use check will NOT explain it. A good agent notices the scope is regional and
pivots to news corroboration rather than local causes.
