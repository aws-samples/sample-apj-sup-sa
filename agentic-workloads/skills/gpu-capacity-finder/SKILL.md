---
name: gpu-capacity-finder
description: Find short-term GPU reservations on AWS. Searches EC2 Capacity Blocks and SageMaker Training Plans across regions for available GPU instances (p5, p4d, trn, p6). Use when the user asks about reserving GPUs, finding GPU capacity, training plan availability, capacity blocks, or short-term compute for ML training.
---

# GPU Capacity Finder

Find available short-term GPU reservations on AWS by searching EC2 Capacity Blocks
and SageMaker Training Plans across all supported regions.

## When to use

Trigger this skill when the user mentions:
- Reserving GPUs, finding GPU capacity, or GPU availability
- EC2 Capacity Blocks or SageMaker Training Plans
- Short-term compute for ML training (days to weeks)
- Instance types like p5, p4d, p6, trn1, trn2
- GPU pricing or scheduling for training jobs

## Conversation flow

Before searching, ensure you have these parameters. Ask the user for any that
are missing:

1. **Instance type** — Which GPU instance? (see REFERENCE.md for supported types)
2. **Instance count** — How many instances? (1–256)
3. **Duration** — How many days? (1–14, then weekly increments up to 182)
4. **Start date** — When do they need it? (defaults to today if not specified)
5. **Regions** — Specific region or all regions? (default: search all)

Example conversation:
```
User: I need GPUs for a training job next week

You: I can help find available GPU capacity. A few questions:
     - What instance type? (p5.48xlarge, p4d.24xlarge, trn1.32xlarge, etc.)
     - How many instances?
     - How many days do you need them?

User: 4x p5.48xlarge for 7 days

You: Searching all regions for 4× p5.48xlarge, 7 days, starting next week...
     [runs scripts]
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
