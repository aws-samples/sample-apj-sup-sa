---
name: gpu-capacity-finder
description: Find short-term GPU reservations on AWS. Searches EC2 Capacity Blocks and SageMaker Training Plans across regions. Translates NVIDIA GPU names (H100, A100, B200, L4, L40S), VRAM requirements, and architecture names (Hopper, Ampere, Blackwell, Ada Lovelace) into AWS instance types (P-series, G-series, Trainium). Use when the user asks about reserving GPUs, finding GPU capacity, training plan availability, capacity blocks, short-term compute for ML training, or mentions specific NVIDIA hardware.
---

# GPU Capacity Finder

Find available short-term GPU reservations on AWS by searching EC2 Capacity Blocks
and SageMaker Training Plans across regions using the AWS CLI.

## Prerequisites check

Before searching, verify the user has AWS CLI access:

```bash
aws sts get-caller-identity
```

If this fails, help the user authenticate first (`aws configure` or set
`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`).

Also verify the required permissions exist (the call will fail with AccessDenied
if not — inform the user they need `ec2:DescribeCapacityBlockOfferings` and
`sagemaker:SearchTrainingPlanOfferings`).

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
   using the mapping in [REFERENCE.md](REFERENCE.md). If ambiguous, offer options.
2. **Instance count** — How many instances? (1–256)
3. **Duration** — How many days? (1–14, then weekly increments up to 182)
4. **Start date** — When do they need it? (defaults to today if not specified)
5. **Regions** — Specific region or all? (default: search all supported regions)

Example:
```
User: I need 640GB of H100 VRAM for a week

You: That's 8× H100 80GB = 1× p5.48xlarge (640GB total VRAM).
     Let me check availability across all regions...
     [runs CLI commands]
```

Another example:
```
User: I need Blackwell GPUs for 2 weeks

You: Blackwell is available as p6-b200.48xlarge (8× B200, 1.5TB VRAM)
     or p6-b300.48xlarge (8× B300, 2.3TB VRAM). Which do you prefer?
     And how many instances do you need?
```

## Searching — API commands

Once you have the parameters, call the AWS APIs directly via CLI. Loop through
regions and adapt dynamically based on results.

### EC2 Capacity Blocks

```bash
aws ec2 describe-capacity-block-offerings \
  --region <region> \
  --instance-type <instance-type> \
  --instance-count <count> \
  --capacity-duration-hours <days * 24> \
  --start-date-range <YYYY-MM-DDTHH:MM:SSZ> \
  --max-results 20 \
  --output json
```

### SageMaker Training Plans

Note: SageMaker uses `ml.` prefix on instance types.

```bash
aws sagemaker search-training-plan-offerings \
  --region <region> \
  --target-resources '["training-job"]' \
  --instance-type ml.<instance-type> \
  --instance-count <count> \
  --duration-hours <days * 24> \
  --start-time-after <YYYY-MM-DDTHH:MM:SSZ> \
  --output json
```

### Search strategy

1. **Loop through regions dynamically.** Start with commonly available regions
   (us-east-1, us-west-2, us-east-2) then expand. See REFERENCE.md for the full
   list of supported regions.
2. **Run both APIs** (EC2 Capacity Blocks and SageMaker Training Plans) for each
   region to give the user the most options.
3. **Handle errors gracefully.** Some regions may not support certain instance
   types — skip those and continue (look for `InvalidParameterValue` or
   `ValidationException` errors).
4. **Adapt if no results found.** Try:
   - Fewer instances
   - Shorter duration
   - Different instance type
   - Later start date
5. **Stop early if sufficient results found.** No need to scan all 13 regions
   if the user already has good options from the first few.

## Presenting results

After gathering results:

1. Present as a comparison table (source, region, AZ, instances, duration, start
   date, upfront fee)
2. Highlight the **cheapest option** and **earliest availability**
3. Note which regions have capacity vs. which don't
4. If no results, suggest fallback strategies (see step 4 above)
5. Offer to provide the reservation/purchase command if the user wants to proceed

## Key context

- **EC2 Capacity Blocks**: Reserved GPU instances in your VPC. Pay upfront, no
  hourly charges during the block. Best for custom frameworks, multi-node.
- **SageMaker Training Plans**: Reserved ML training capacity. Pay upfront, covers
  training job hours. Best for SageMaker-native workflows.
- Both offer the same GPU hardware — the difference is the access model.
- Capacity is limited and first-come-first-served — results change frequently.
- Capacity Blocks and Training Plans support **P-series and Trainium only**.
  G-series (L4, L40S, A10G) are on-demand/Savings Plans only.

For full instance specs, GPU-to-instance mapping, and API details, see [REFERENCE.md](REFERENCE.md).
