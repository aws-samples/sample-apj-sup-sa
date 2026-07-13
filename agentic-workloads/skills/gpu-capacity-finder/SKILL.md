---
name: gpu-capacity-finder
description: Find short-term GPU reservations on AWS. Searches EC2 Capacity Blocks and SageMaker Training Plans across regions. Translates NVIDIA GPU names (H100, A100, B200, L4, L40S), VRAM requirements, and architecture names (Hopper, Ampere, Blackwell, Ada Lovelace) into AWS instance types (P-series, G-series, Trainium). Use when the user asks about reserving GPUs, finding GPU capacity, training plan availability, capacity blocks, short-term compute for ML training, or mentions specific NVIDIA hardware.
---

# GPU Capacity Finder

Find available short-term GPU reservations on AWS by searching EC2 Capacity Blocks
and SageMaker Training Plans across all supported regions.

## When to use

Trigger this skill when the user mentions:
- Reserving GPUs, finding GPU capacity, or GPU availability
- NVIDIA GPU names: H100, A100, B200, B300, L4, L40S, A10G
- GPU architectures: Hopper, Ampere, Blackwell, Ada Lovelace
- VRAM requirements (e.g., "I need 640GB VRAM", "80GB GPUs")
- EC2 Capacity Blocks or SageMaker Training Plans
- Short-term compute for ML training (days to weeks)
- AWS instance types like p5, p4d, p6, g5, g6, trn1, trn2
- GPU pricing or scheduling for training jobs

## Conversation flow

Before searching, ensure you have these parameters. Ask the user for any that
are missing:

1. **Instance type** — Which GPU instance? Users may say the NVIDIA name (H100,
   A100, B200, L4) or VRAM amount instead of the AWS instance type. Translate
   using the mapping in REFERENCE.md. If ambiguous, offer options.
2. **Instance count** — How many instances? (1–256)
3. **Duration** — How many days? (1–14, then weekly increments up to 182)
4. **Start date** — When do they need it? (defaults to today if not specified)
5. **Regions** — Specific region or all regions? (default: search all)

Example conversation:
```
User: I need 640GB of H100 VRAM for a week

You: That's 8× H100 80GB = 1× p5.48xlarge (640GB total VRAM).
     Searching all regions for 1× p5.48xlarge, 7 days, starting today...
     [runs scripts]
```

Another example:
```
User: I need Blackwell GPUs for 2 weeks

You: Blackwell is available as p6-b200.48xlarge (8× B200, 1.5TB VRAM)
     or p6-b300.48xlarge (8× B300, 2.3TB VRAM). Which do you prefer?
     And how many instances do you need?
```

## Executing the search

Once you have the parameters, run both search scripts:

### EC2 Capacity Blocks

```bash
python scripts/search_ec2_capacity_blocks.py \
  --instance-type p5.48xlarge \
  --instance-count 4 \
  --duration-days 7 \
  --start-date 2026-07-20
```

### SageMaker Training Plans

```bash
python scripts/search_sagemaker_training_plans.py \
  --instance-type p5.48xlarge \
  --instance-count 4 \
  --duration-days 7 \
  --start-date 2026-07-20
```

Both scripts output JSON to stdout. Run them in parallel if possible.

## Presenting results

After running both scripts:

1. Combine the results and present as a comparison table
2. Highlight the **cheapest option** and **earliest availability**
3. Note which regions have availability vs. which don't
4. If no results found, suggest:
   - Trying a shorter duration
   - Reducing instance count
   - Trying a different instance type
   - Checking back later (capacity refreshes frequently)

## Key context

- **EC2 Capacity Blocks**: Reserved GPU instances in your VPC. Pay upfront, no hourly charges during the block. Best for custom frameworks, multi-node training.
- **SageMaker Training Plans**: Reserved ML training capacity. Pay upfront, covers training job hours. Best for SageMaker-native workflows.
- Both offer the same GPU hardware — the difference is the access model.
- Pricing varies by region, duration, and instance type.
- Capacity is limited and first-come-first-served.

For full API details, supported instance types, and valid durations, see [REFERENCE.md](REFERENCE.md).
