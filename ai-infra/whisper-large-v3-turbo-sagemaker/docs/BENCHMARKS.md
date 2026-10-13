# Benchmarks: Whisper Large V3 Turbo on SageMaker

Head-to-head of the two serving options in this repo, measured on the live
endpoints with `common/benchmark.py`.

## Environment

| | |
|---|---|
| Model | `openai/whisper-large-v3-turbo` (fp16) |
| Instance | `ml.g5.xlarge` — 1× NVIDIA A10G (24 GB), 4 vCPU, 16 GB RAM |
| Region | `ap-south-1` (Mumbai) |
| Instances | 1 (no autoscaling during the test) |
| HF option | AWS HF DLC, Transformers ASR pipeline, 1 model-server worker |
| vLLM option | `vllm/vllm-openai:v0.8.5.post1` (CUDA 12.4), continuous batching |

**Method:** `benchmark.py` fires a fixed audio clip at increasing concurrency
(simultaneous in-flight requests), `requests-per-level` times each, and reports
throughput (rps), latency percentiles, and real-time factor (RTF = audio-seconds
transcribed per wall-second). Warm instances. Numbers vary run to run; treat as
indicative, not contractual.

`conc` = number of requests in flight at the same instant.
`RTF`  = higher is better (e.g. 50× = transcribes 50s of audio per wall-second).

---

## Hugging Face pipeline (single worker)

**5s clip (4.59s):**
| conc | rps | p50 | p90 | p99 | RTF |
|--|--|--|--|--|--|
| 1 | 3.53 | 0.276 | 0.312 | 0.374 | 16.2× |
| 2 | 4.20 | 0.465 | 0.474 | 0.601 | 19.3× |
| 4 | 4.23 | 0.921 | 0.936 | 1.227 | 19.4× |
| 8 | 4.29 | 1.830 | 1.885 | 2.121 | 19.7× |

**24s clip:**
| conc | rps | p50 | p90 | p99 | RTF |
|--|--|--|--|--|--|
| 1 | 1.36 | 0.700 | 1.007 | 1.176 | 32.7× |
| 2 | 2.01 | 0.929 | 1.276 | 1.480 | 48.3× |
| 4 | 2.10 | 1.789 | 2.470 | 2.640 | 50.3× |
| 8 | 2.04 | 3.527 | 4.329 | 5.102 | 48.9× |

**Observation:** throughput flatlines past concurrency ~2 because the model server
runs a single worker — extra concurrent requests just queue, so latency grows while
rps stays put. Ceiling ≈ **4.2 rps** (5s) / **2.1 rps** (24s) per instance.

---

## vLLM (continuous batching)

**5s clip (4.59s):**
| conc | rps | p50 | p90 | p99 | RTF |
|--|--|--|--|--|--|
| 1 | 4.91 | 0.201 | 0.212 | 0.240 | 22.6× |
| 2 | 7.04 | 0.284 | 0.299 | 0.325 | 32.3× |
| 4 | 9.47 | 0.415 | 0.503 | 0.506 | 43.5× |
| 8 | 12.86 | 0.619 | 0.686 | 0.688 | 59.0× |
| 16 | 14.98 | 0.903 | 1.550 | 1.563 | 68.8× |

**24s clip:**
| conc | rps | p50 | p90 | p99 | RTF |
|--|--|--|--|--|--|
| 1 | 1.84 | 0.545 | 0.580 | 0.596 | 44.1× |
| 2 | 2.87 | 0.713 | 0.740 | 0.802 | 68.8× |
| 4 | 4.54 | 0.882 | 0.998 | 1.129 | 108.9× |
| 8 | 6.09 | 1.301 | 1.601 | 2.079 | 146.1× |
| 16 | 6.43 | 1.908 | 3.553 | 3.556 | 154.2× |

**Observation:** throughput keeps scaling with concurrency (continuous batching
merges requests into batched GPU passes). Ceiling ≈ **~15 rps** (5s) / **~6.4 rps**
(24s) per instance — roughly **3× the HF pipeline** — with lower latency too.

### Realistic voice-agent turn lengths (vLLM)

**3.5s turn:**
| conc | rps | p50 | p90 |
|--|--|--|--|
| 1 | 4.34 | 0.216 | 0.265 |
| 4 | 8.96 | 0.460 | 0.506 |
| 8 | 10.34 | 0.740 | 1.082 |

**6.0s turn:**
| conc | rps | p50 | p90 |
|--|--|--|--|
| 1 | 3.45 | 0.291 | 0.307 |
| 4 | 5.98 | 0.683 | 0.756 |
| 8 | 6.81 | 1.087 | 1.606 |

Per-turn trailing latency (delay after the caller stops speaking): **~0.2–0.3s**
lightly loaded, **~0.5–1.1s** near the instance's capacity knee.

---

## HF vs vLLM (24s clip, side by side)

| conc | HF rps | vLLM rps | HF p50 | vLLM p50 |
|--|--|--|--|--|
| 1 | 1.36 | 1.84 | 0.70s | 0.55s |
| 2 | 2.01 | 2.87 | 0.93s | 0.71s |
| 4 | 2.10 | 4.54 | 1.79s | 0.88s |
| 8 | 2.04 | **6.09** | 3.53s | 1.30s |
| 16 | — | **6.43** | — | 1.91s |

---

## From throughput to concurrent voice calls

For a turn-based voice agent, STT fires only once per user turn — not continuously.
So concurrent **calls** ≫ concurrent **transcriptions**:

```
fraction of a call spent transcribing = STT latency ÷ seconds between turns
concurrent calls ≈ sustainable rps × seconds between turns
```

Example (vLLM, ~5s turns, caller speaks ~once per 15s):
- sustainable ~10 rps near the latency-safe knee
- concurrent calls ≈ 10 × 15 ≈ **~150 calls / instance**

Equivalently, via talk fraction: one instance sustains ~33× real-time at a
good-latency operating point; if a caller talks ~30–50% of a call, that's
**~65–110 concurrent calls / instance**, i.e. **~300–440 across 4 instances**.

For 3-minute calls measured as volume: each concurrent slot recycles a call every
3 min (20/hour), so `calls/hour ≈ concurrent calls × 20`.

### Calls per hour — 3-minute average call (vLLM, per `ml.g5.xlarge`)

Assumptions: one instance sustains ~33× real-time at a good-latency operating point
(from the turn-length benchmarks); only the caller's speech is transcribed; calls are
evenly distributed (no thundering herd). The swing factor is the **talk fraction** —
what share of the call the caller is actually speaking (the rest is the bot talking
and pauses).

```
concurrent calls/instance ≈ 33 ÷ talk_fraction
calls/hour/instance       ≈ concurrent calls × (60 / 3)   # 3-min calls -> ×20
```

| Caller talk fraction | Concurrent calls / instance | Calls/hour / instance | Calls/hour @ 4 instances |
|---|---|---|---|
| 30% (bot-heavy IVR/assistant) | ~110 | ~2,200 | ~8,800 |
| 40% (typical) | ~82 | ~1,650 | ~6,600 |
| 50% (balanced back-and-forth) | ~66 | ~1,320 | ~5,300 |

Sanity check (40% talk): a 3-min call has ~72s of caller audio; at 33× that's ~2.2s
of GPU time spread over 180s, so each call occupies the GPU ~1.2% of the time →
~82 concurrent calls. ✔

**Rule of thumb:** plan for **~1,300–2,200 three-minute calls per hour per
`g5.xlarge`** (STT side), scaling roughly linearly with instance count.

### Choosing the autoscaling target

`SageMakerVariantInvocationsPerInstance` is measured **per instance per minute**. At
saturation a vLLM `g5.xlarge` does ~15 rps (5s clips) ≈ 900 invocations/min, or ~6 rps
(longer turns) ≈ 360/min. To keep latency safe you want to scale out *before*
saturation (around the concurrency-4 knee), so a target of **~250 invocations/
instance/min** leaves headroom for mixed turn lengths and triggers a new instance
while existing ones still have spare capacity. Raise it for higher utilization (more
latency risk), lower it to scale out earlier. The HF endpoint's single-worker ceiling
is far lower (~125/min), so its target should be correspondingly smaller.

**Caveats:** these are steady-state ceilings for the STT stage only — the LLM and TTS
stages have their own limits and are often the real bottleneck. The talk fraction and
turn cadence dominate the result, so measure them on real calls. And "calls/hour"
assumes the endpoint runs near full concurrent capacity continuously; for latency
safety you actually size to your peak *concurrent* calls with headroom, not raw
hourly volume. Confirm with a load test using realistic, bursty traffic.

---

## Reproduce

```bash
python common/benchmark.py \
  --endpoint-name <endpoint> --region <region> \
  --audio samples/sample.wav --audio-duration 4.59 \
  --concurrency 1 2 4 8 16 --requests-per-level 32
```
