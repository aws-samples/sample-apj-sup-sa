---
name: gpu-capacity-finder
description: Find short-term GPU reservations on AWS. Searches EC2 Capacity Blocks (up to 10 instance types per call) and SageMaker Training Plans across regions. Translates NVIDIA GPU names (H100, A100, B200, L4, L40S), VRAM requirements, and architecture names (Hopper, Ampere, Blackwell, Ada Lovelace) into AWS instance types (P-series, G-series, Trainium). Use when the user asks about reserving GPUs, finding GPU capacity, training plan availability, capacity blocks, short-term compute for ML training, or mentions specific NVIDIA hardware.
---

# GPU Capacity Finder

Find available short-term GPU reservations on AWS by searching EC2 Capacity Blocks
and SageMaker Training Plans across regions using the AWS CLI.

## Why this skill exists

AWS offers **self-service, short-term GPU reservations** (days to weeks) with no
long-term commitment — but many startups don't know about them because the
capability is buried within EC2 (Capacity Blocks) and SageMaker (Training Plans)
rather than being a standalone product.

This means you can reserve H100s, A100s, or Trainium chips for exactly the
duration you need (even just a few days), pay a fixed upfront fee, and get
**guaranteed capacity** — no spot interruptions, no waiting for on-demand
availability. It's like booking a hotel room for GPUs.

This skill helps users discover and compare these options across all AWS regions.

## Prerequisites check

Before searching, verify the user has AWS CLI access:

```bash
aws sts get-caller-identity
```

If this fails, help the user authenticate first (`aws configure` or set
`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`).

Check the CLI version too:

```bash
aws --version
```

Multi-instance-type Capacity Block search (`--instance-type-preferences`) needs
AWS CLI **v2.41.0 or later**. Older versions fail with
`Unknown options: --instance-type-preferences`. If the user can't upgrade, fall
back to one `--instance-type` call per instance type.

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

1. **Instance type(s)** — Which GPU instance? Users may say the NVIDIA name (H100,
   A100, B200, L4) or VRAM amount instead of the AWS instance type. Translate
   using the mapping in [REFERENCE.md](REFERENCE.md). If several types fit the
   requirement (e.g. "640GB+ of VRAM", "any Hopper", "H100 or H200"), don't make
   the user pick one: search them together (up to 10) and let the earliest or
   cheapest offering decide.
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

Flexible example (one Capacity Block call per region covers every candidate):
```
User: I need at least 640GB of VRAM for 5 days, whatever starts soonest

You: Candidates: p5.48xlarge (8× H100, 640GB), p5en.48xlarge (8× H200, 1.1TB),
     p6-b200.48xlarge (8× B200, 1.5TB). I'll search all three together in
     each region and rank by earliest start...
     [runs one describe-capacity-block-offerings per region]
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

Search up to 10 instance types in one call per region. Offerings for any listed
type come back ranked by earliest start date:

```bash
aws ec2 describe-capacity-block-offerings \
  --region <region> \
  --instance-type-preferences InstanceType=<type-1> InstanceType=<type-2> \
  --instance-count <count> \
  --capacity-duration-hours <days * 24> \
  --start-date-range <YYYY-MM-DDTHH:MM:SSZ> \
  --end-date-range <YYYY-MM-DDTHH:MM:SSZ> \
  --output json \
  --query 'CapacityBlockOfferings[].{Id:CapacityBlockOfferingId,Fee:UpfrontFee,Currency:CurrencyCode,Segments:CapacityBlockOfferingSegments[].{Type:InstanceType,AZ:AvailabilityZone,Count:InstanceCount,Start:StartDate,End:EndDate,Hours:CapacityBlockDurationHours}}'
```

**Read the segments.** With `--instance-type-preferences`, the top-level
`InstanceType`, `AvailabilityZone`, `StartDate` and `EndDate` of each offering
come back empty. The details are in `CapacityBlockOfferingSegments[]`. The
offering-level `UpfrontFee` and `CapacityBlockOfferingId` are still populated,
and the ID is what you pass to `purchase-capacity-block`.

Single-type form (also the fallback for AWS CLI older than v2.41.0). Here the
details are on the offering itself:

```bash
aws ec2 describe-capacity-block-offerings \
  --region <region> \
  --instance-type <instance-type> \
  --instance-count <count> \
  --capacity-duration-hours <days * 24> \
  --start-date-range <YYYY-MM-DDTHH:MM:SSZ> \
  --output json
```

Rules for the multi-type form:
- Use either `--instance-type` or `--instance-type-preferences`, never both
  (the API rejects the combination).
- At most 10 instance types per call.
- Not available for UltraServers (`--ultraserver-type`); search those one type
  at a time.
- The API covers one Region per call. The EC2 console can also search several
  Regions in one view, if the user prefers clicking to CLI.

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
2. **One Capacity Block call per region, all candidate types together.** Pass
   every instance type that meets the user's need in `--instance-type-preferences`
   instead of looping type by type. SageMaker Training Plans still take one
   instance type per call, so loop types there.
3. **Run both APIs** (EC2 Capacity Blocks and SageMaker Training Plans) for each
   region to give the user the most options.
4. **Handle errors gracefully.** Some regions may not support certain instance
   types — skip those and continue (look for `InvalidParameterValue` or
   `ValidationException` errors).
   - In a multi-type call, **one bad type fails the whole request.** For example,
     `The instance type 'trn2.48xlarge' is not supported for Capacity Blocks in
     'us-east-1'` or `The start date is not valid for p5en.48xlarge`. The
     message names the type: drop it and retry the call with the rest, rather
     than skipping the whole region.
5. **Adapt if no results found.** Try:
   - Fewer instances
   - Shorter duration
   - Adding more equivalent instance types to the same call
   - Later start date
6. **Stop early if sufficient results found.** No need to scan all 13 regions
   if the user already has good options from the first few.

## Presenting results

After gathering results:

1. Present as a comparison table (source, region, instance type, AZ, instances,
   duration, start date, upfront fee)
2. Highlight the **cheapest option** and **earliest availability**
3. Note which regions have capacity vs. which don't
4. If no results, suggest fallback strategies (see step 4 above)
5. Offer to provide the reservation/purchase command if the user wants to proceed
   (`aws ec2 purchase-capacity-block --capacity-block-offering-id <id>
   --instance-platform Linux/UNIX --region <region>`). Capacity Blocks can't be
   cancelled once purchased, so confirm with the user first.

## Key context

- **EC2 Capacity Blocks**: Self-service, short-term reserved GPU instances in
  your VPC. Pay a fixed upfront fee, no hourly charges during the block.
  No long-term commitment — reserve for as few as 1 day.
  Best for custom frameworks, multi-node training, or any GPU workload.
- **SageMaker Training Plans**: Same concept but for SageMaker-native workflows.
  Pay upfront, get guaranteed capacity for your training jobs.
- Both are **self-service** — no sales calls, no capacity requests to file.
  Just call the API and reserve if capacity is available.
- Both offer the same GPU hardware — the difference is the access model.
- Capacity is limited and first-come-first-served — results change frequently.
- Capacity Blocks and Training Plans support **P-series and Trainium only**.
  G-series (L4, L40S, A10G) are on-demand/Savings Plans only.

For full instance specs, GPU-to-instance mapping, and API details, see [REFERENCE.md](REFERENCE.md).
