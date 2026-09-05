---
title: "Design Your Canvas"
weight: 41
---

## The scenario you are designing for

A conservation NGO, the AnyCompany Conservation Trust, runs the **Desaru Camera
Trap Programme**: six camera-trap
stations across Desaru, Johor, Malaysia, tracking 12 species. The cameras produce
monthly detection counts. Something has gone wrong at some of the stations, and an
ecologist needs to know what and why.

All wildlife data is **synthetic** — generated for this workshop. You do
not need to understand it in depth; the agent's job is to make sense of it. But
knowing what data and tools exist helps you design good decisions around them.

### The data behind the tools

| Dataset | Contents |
|---------|----------|
| `detections.json` | Monthly camera-trap counts, 12 species × 6 stations, Jan–Jul 2026 |
| `weather.json` | Monthly rainfall / temperature / humidity / flood events per station |
| `land_use_changes.json` | Habitat-change events (logging, construction, agriculture) |
| `news_articles.json` | Local news articles (some relevant, some noise) |
| `species_baselines.json` | IUCN status, normal ranges, threats, ecology for 12 species |

### The tools your agent can call

Your agent can leverage on these six tools (they are already provisioned for you). On the
canvas you decide which ones to wire up or create on your own. 

| Tool | Purpose | Input | Output |
|------|---------|-------|--------|
| `query_detections` | Historical camera-trap counts plus trend | species, station_id?, start/end month | counts plus trend summary |
| `get_weather_data` | Rainfall, floods, temperature | station_id, start/end month | weather records plus flood events |
| `check_land_use` | Habitat changes nearby | station_id, radius_km, date range | land-use change records |
| `search_news` | Corroborating local media | keywords[], date range | matching articles |
| `get_species_baseline` | IUCN status plus ecology | species | status, threats, normal ranges |
| `generate_anomaly_report` | Publish the final report (call this last) | species, type, severity, findings | structured report |

## Materials

- This page (the scenario, data, and tools above)
- The A3 canvas at your working desk, or the blank canvas template in the `canvas/`
  folder of the repo
- One canvas per team of 2-3

## The canvas

Fill this in on the A3 canvas at your desk (or copy the headings onto paper). Work
top to bottom. Keep every answer short and specific — this is a thinking tool, not
a spec.

**Project Title:** _______________________________________________

---

### 🔒 Problem
_What problem are you solving? Why does it matter?_

>

### 🔀 Existing alternatives
_How is this problem solved today without AI/LLMs?_

>

### 🌐 Inputs (prompt, other data sources)
_What data goes into the prompt? Where does it come from? What format?_

>

### ☁️ Outputs (structured vs unstructured, schema)
_What does the LLM output look like? Structured or unstructured? What constraints?_

>

### ✅ Definition of Done (rubric / completion criteria)
_How do you know the goal is achieved? Completion signals, max tool calls, what
happens if unresolved?_

>

### ✨ UX (modality, augmentation, feedback)
_How will users interact with this feature? How do they validate or give feedback?_

>

### 🧠 LLMX (which tools/services the agent uses, and how it knows to use them)
_Pure prompt, or does the agent use tools? Which of the six tools do you wire up?_

>

### 📊 Costs
_Estimated LLM inference costs (tokens, model, volume)._

>

### 💰 Pricing
_How would this feature be priced or packaged? Margin impact?_

>

### 📋 Success metrics
- Accuracy: ____%
- Latency: ____
- Adoption: ____

### 🧪 Evaluation
_How will you measure accuracy and quality? What is your test set? How do you
validate against ground truth?_

>

### 💡 Solution
_How does your solution work at a high level?_

>

## Tips

- Be specific. "Produces a report" is too vague for **Outputs**: what fields, what
  schema, who reads it?
- Argue about the human gate in **UX** / **Definition of Done**. It sets your blast
  radius when the model is wrong: weigh the cost of a false positive (a wasted
  patrol) against a false negative (a missed population collapse and species extinction).

## Save your canvas

Save your filled canvas (or photograph the paper version). You will hand it to your desired platform of choice in the next step to translate your decisions into a working agent.

Next: [Build](./050-build.md)
