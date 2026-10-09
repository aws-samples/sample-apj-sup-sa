# Reference: GenAI Workload Assessment

When a SoW contains Generative AI workloads, perform this deeper assessment
beyond the general cost-architecture alignment check.

## 1. Classify GenAI delivery model

First, categorize how the SoW proposes to deliver GenAI capabilities:

| Category | Examples | Capacity Model | Cost Model |
|----------|----------|----------------|------------|
| **Bedrock (managed)** | Claude, Titan, Llama on Bedrock, Knowledge Bases, Agents | On-demand, no capacity concern | Per-token (input/output) |
| **3rd-party model APIs** | OpenAI via API, Cohere, etc. (not on Bedrock) | External, no AWS capacity | Pass-through cost |
| **GPU — Training/Fine-tuning** | Pre-training, fine-tuning, RLHF, distillation on P-series | Capacity-constrained. Needs reservation. | Upfront (Capacity Blocks / Training Plans) |
| **GPU — Inference** | Self-hosted LLM serving on G-series or P-series | G-series: on-demand. P-series: reservation needed | On-demand or reserved |
| **Trainium/Inferentia** | Training on trn1/trn2, inference on inf2 | Capacity Blocks available for trn | Reservation or on-demand |

## 2. GPU justification validation

If the SoW requests GPU instances, validate there is **substance** behind the
request — specifically VRAM calculations and workload appropriateness.

### VRAM calculation check

| Factor | How to calculate |
|--------|-----------------|
| Model parameters | e.g., 7B, 13B, 70B, 405B |
| Precision | FP32 = 4 bytes/param, FP16/BF16 = 2, INT8 = 1, INT4 = 0.5 |
| Training overhead | Multiply by 4–6× (optimizer states, gradients, activations) |
| Inference overhead | Multiply by 1.2–1.5× (KV cache, batch size) |

**Formula:**
```
Training VRAM ≈ params × bytes_per_param × 5  (rough)
Inference VRAM ≈ params × bytes_per_param × 1.3
```

**Examples:**
| Model | Precision | Training VRAM | Inference VRAM | Appropriate Instance |
|-------|-----------|---------------|----------------|---------------------|
| 7B | FP16 | ~70 GB | ~18 GB | Training: 1× p4d. Inference: g5.2xlarge |
| 13B | FP16 | ~130 GB | ~34 GB | Training: 1× p4d. Inference: g6e.xlarge (48GB) |
| 70B | FP16 | ~700 GB | ~182 GB | Training: 1× p5 or 2× p4de. Inference: p4de or multi-g6e |
| 405B | FP16 | ~4 TB | ~1 TB | Training: multi-node p5. Inference: multi-p5 |
| 7B | INT4 | N/A (inference only) | ~5 GB | g5.xlarge (24GB) is overkill — g6.xlarge fine |

**Red flags:**
- Requesting p5.48xlarge (640GB) for 7B model inference → ~35× over-provisioned
- Requesting g5.xlarge (24GB) for 70B model → will OOM
- No model name or parameter count in SoW → can't validate sizing at all
- "We need 8× H100s" with no VRAM justification → flag for explanation

### When GPUs are appropriate vs not

| Use Case | GPUs Appropriate? | Notes |
|----------|-------------------|-------|
| **Pre-training a foundation model** | ✅ Yes — P-series | Requires large GPU clusters, high VRAM, fast interconnect |
| **Fine-tuning (full)** | ✅ Yes — P-series | Full parameter fine-tuning needs similar VRAM to training |
| **Fine-tuning (LoRA/QLoRA)** | ✅ Yes — G-series or small P | Reduced VRAM needs, single GPU often sufficient |
| **RLHF / alignment training** | ✅ Yes — P-series | Needs multiple models in memory simultaneously |
| **Distillation** | ✅ Yes — P-series | Teacher + student model, high VRAM |
| **Self-hosted inference (high volume)** | ✅ Maybe — G or P | Justified at >10K RPM or for latency/data sovereignty |
| **Self-hosted inference (low volume)** | ⚠️ Likely over-kill | Bedrock on-demand cheaper at <100 RPM |
| **RAG chatbot** | ❌ Usually not | Bedrock + Knowledge Bases handles this without GPUs |
| **Simple text generation** | ❌ No | Bedrock API call — no infrastructure needed |
| **Image generation** | ✅ Maybe | Bedrock has Titan Image/Stability; GPUs for custom models |
| **Embedding generation** | ⚠️ Depends on scale | Bedrock embeddings for <1M docs; GPUs for massive scale |

## 3. Capacity access strategy validation

For GPU workloads, validate the SoW addresses capacity access appropriately:

| SoW Scenario | Expected Strategy | Flag if missing |
|---|---|---|
| P-series, <1 year duration | SageMaker Training Plans or EC2 Capacity Blocks | ⚠️ On-demand P-series has capacity constraints — may not launch |
| P-series, >1 year duration | Reserved Instances or Savings Plans | 💰 Significant savings opportunity missed |
| G-series, any duration | On-demand (always available) | ✅ No reservation needed |
| Training job (days/weeks) | EC2 Capacity Blocks or SageMaker Training Plans | ⚠️ Must secure capacity ahead of time |
| Inference (ongoing) | On-demand for G-series; RI/SP for P-series long-term | Check pricing model matches duration |
| "We need H100s" but no reservation plan | Flag immediately | ❌ P-series without reservation = likely capacity failure |
| Mixed training + inference | Separate line items with different strategies | Should not be one combined cost line |

**Key insight for partners:**
- P-series instances (p5, p4d, p6) are capacity-constrained and cannot be
  reliably obtained on-demand. The SoW MUST include a reservation strategy
  (Capacity Blocks for EC2, Training Plans for SageMaker).
- G-series instances (g5, g6, g6e, g7e) are generally available on-demand
  without reservation.
- SageMaker Training Plans = "Flexible Training Plans" — short-term reservations
  specifically for training jobs.
- EC2 Capacity Blocks = short-term reserved instances for any workload.

## 4. Bedrock vs GPU decision tree

If the SoW proposes self-hosted GPUs, validate whether Bedrock would be more
appropriate:

| If the use case is... | And they proposed... | Assessment |
|---|---|---|
| RAG chatbot | p5.48xlarge + vLLM | ⚠️ Bedrock + Knowledge Bases is simpler and likely cheaper |
| Fine-tuning a foundation model | Bedrock custom model training | ✅ Appropriate use of managed service |
| Pre-training from scratch | p5.48xlarge cluster | ✅ Appropriate — Bedrock can't do this |
| Full fine-tuning (all params) | P-series GPU cluster | ✅ Appropriate — needs full VRAM |
| LoRA fine-tuning | Single G-series or small P | ✅ Appropriate — reduced VRAM needs |
| RLHF / alignment | P-series multi-GPU | ✅ Appropriate — multiple models in memory |
| Real-time inference, <100 RPM | Self-hosted on G5 | ⚠️ Bedrock on-demand likely cheaper at low volume |
| Real-time inference, >10K RPM | Self-hosted on G5/P5 | ✅ May be cost-effective — verify with Bedrock pricing |
| Batch inference (offline) | GPU instances | ⚠️ Check Bedrock batch inference pricing first |
| Data sovereignty / air-gapped | Self-hosted | ✅ Valid reason for GPUs |
| Custom model architecture | P-series | ✅ Only option for non-standard models |
| Open-source model (Llama, Mistral) | Self-hosted | ⚠️ These are also on Bedrock — check if managed is cheaper |

**Decision framework for the reviewer:**
1. Can this be done on Bedrock? → If yes, why isn't it?
2. Is there a valid reason for self-hosting? (data sovereignty, custom architecture,
   scale economics, latency requirements, model not on Bedrock)
3. If GPUs are justified, is the sizing backed by VRAM calculations?
4. If P-series, is there a capacity reservation strategy?

## 5. GenAI cost validation specifics

| Cost Element | What to check |
|---|---|
| Bedrock tokens | Is the per-token estimate realistic for the expected volume? (e.g., 1M tokens/day for a chatbot with 1000 users is reasonable; $50/month total is not) |
| GPU hours | Is the training time estimate realistic? (7B fine-tune = hours; 70B = days; 405B pre-train = weeks) |
| Storage for models | Model weights on S3/EBS? (70B FP16 = ~140GB; 405B = ~810GB) |
| Data preparation | ETL/preprocessing costs often forgotten |
| Embedding generation | If RAG: cost to embed the corpus (one-time + incremental) |
| Vector database | OpenSearch Serverless, Pinecone, pgvector — ongoing cost |
| Evaluation/testing | Compute for benchmarking model quality |
| Multiple environments | Training needs separate from inference — different instance types |
