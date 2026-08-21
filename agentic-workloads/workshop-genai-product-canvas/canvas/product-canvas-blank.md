# GenAI Product Canvas (blank)

Fill this in for the biodiversity anomaly detection use case. This is a thinking
tool, not a spec. You will validate every decision against a working agent in the
build phase. Keep answers short and specific.

```
┌───────────────────────────────────────────────────────────────────────┐
│ GenAI PRODUCT CANVAS                                                    │
│                                                                         │
│ Product: ______________________________________________________        │
│ Shape:   [ ] Pipeline    [ ] Agentic Loop    [ ] Hybrid                 │
├───────────────────────────────────────────────────────────────────────┤
│ 1. OUTCOME (a task, not a conversation)                                 │
│    What artifact does the agent produce? ______________________         │
│    Output format / schema: _____________________________________        │
│    Why not a chatbot? __________________________________________        │
├───────────────────────────────────────────────────────────────────────┤
│ 2. DEFINITION OF DONE (when does the loop stop?)                        │
│    Completion signals: _________________________________________        │
│    Max iterations: ____   Confidence threshold: ____                    │
│    If unresolved: ______________________________________________        │
├───────────────────────────────────────────────────────────────────────┤
│ 3. WHERE'S THE HUMAN?                                                    │
│    Full-auto stages: ___________________________________________        │
│    Human-gated stages: _________________________________________        │
│    Cost of a false positive: ___________________________________        │
│    Cost of a false negative: ___________________________________        │
├───────────────────────────────────────────────────────────────────────┤
│ 4. HOW FAST? (latency budget)                                           │
│    Mode: [ ] Real-time  [ ] Async batch  [ ] Scheduled                  │
│    Latency budget: _____________________________________________        │
│    UX pattern: _________________________________________________        │
├───────────────────────────────────────────────────────────────────────┤
│ 5. WHO'S THE USER?                                                       │
│    Primary persona: ____________________________________________        │
│    What do they need? __________________________________________        │
│    How do they consume the output? _____________________________        │
├───────────────────────────────────────────────────────────────────────┤
│ TOOLS (circle which you will wire up):                                  │
│   query_detections | get_weather_data | check_land_use                  │
│   search_news | get_species_baseline | generate_anomaly_report          │
│                                                                         │
│ COST ESTIMATE: ____ tool calls x ~$____/call = ~$____ / investigation   │
└───────────────────────────────────────────────────────────────────────┘
```

When your team has filled this in, save it (or a photo of it) and hand it to the
AI in the build phase. The next document shows exactly how each box becomes agent
configuration.
