# Reference: GPU Capacity Finder

AWS offers self-service, short-term GPU reservations (1–182 days) via EC2 Capacity
Blocks and SageMaker Training Plans. No long-term commitment, no sales process —
just API calls. This reference covers the instance types, regions, and API details
needed to find and reserve capacity.

## Instance type lookup

Users often refer to GPUs by NVIDIA product name, VRAM amount, or architecture
rather than AWS instance type. Use this table to translate.

### P-series (ML training / HPC)

| Instance Type | GPUs | NVIDIA GPU | VRAM per GPU | Total VRAM | Architecture | Use Case |
|---------------|------|-----------|--------------|------------|--------------|----------|
| `p6-b200.48xlarge` | 8 | B200 | 192 GB HBM3e | 1,536 GB | Blackwell | Largest models, next-gen |
| `p6-b300.48xlarge` | 8 | B300 | 288 GB HBM3e | 2,304 GB | Blackwell | Highest perf, frontier models |
| `p5.48xlarge` | 8 | H100 SXM | 80 GB HBM3 | 640 GB | Hopper | Large-scale training |
| `p5e.48xlarge` | 8 | H100 SXM | 80 GB HBM3 | 640 GB | Hopper | Memory-intensive training |
| `p5en.48xlarge` | 8 | H100 SXM | 80 GB HBM3 | 640 GB | Hopper | Multi-node distributed (3200 Gbps EFA) |
| `p4d.24xlarge` | 8 | A100 | 40 GB HBM2e | 320 GB | Ampere | General ML training |
| `p4de.24xlarge` | 8 | A100 | 80 GB HBM2e | 640 GB | Ampere | Large model training |

### G-series (Inference / graphics / smaller training)

| Instance Type | GPUs | NVIDIA GPU | VRAM per GPU | Total VRAM | Architecture | Use Case |
|---------------|------|-----------|--------------|------------|--------------|----------|
| `g6.xlarge` – `g6.48xlarge` | 1–8 | L4 | 24 GB GDDR6 | 24–192 GB | Ada Lovelace | Inference, fine-tuning |
| `g6e.xlarge` – `g6e.48xlarge` | 1–8 | L40S | 48 GB GDDR6 | 48–384 GB | Ada Lovelace | Inference + training hybrid |
| `g7e.xlarge` – `g7e.48xlarge` | 1–8 | RTX Pro 6000 | 96 GB GDDR7 | 96–768 GB | Blackwell | AI inference, spatial computing |
| `g5.xlarge` – `g5.48xlarge` | 1–8 | A10G | 24 GB GDDR6X | 24–192 GB | Ampere | Inference, rendering |

### Trainium (AWS custom silicon)

| Instance Type | Accelerators | Chip | Memory | Use Case |
|---------------|-------------|------|--------|----------|
| `trn1.32xlarge` | 16 | Trainium v1 | 512 GB HBM2e | Cost-effective training |
| `trn2.48xlarge` | 16 | Trainium v2 | 1,536 GB HBM3 | Next-gen, large scale |
| `trn2.3xlarge` | 2 | Trainium v2 | 192 GB HBM3 | Smaller training jobs |

### Common user queries → instance type mapping

| User says... | Suggest |
|---|---|
| "H100", "Hopper", "80GB HBM3" | `p5.48xlarge` / `p5en.48xlarge` |
| "A100 80GB" | `p4de.24xlarge` |
| "A100 40GB", "A100" | `p4d.24xlarge` |
| "B200", "Blackwell" | `p6-b200.48xlarge` |
| "B300" | `p6-b300.48xlarge` |
| "L4", "inference GPU", "24GB" | `g6.*` (not available as capacity blocks) |
| "L40S", "48GB", "Ada Lovelace" | `g6e.*` (not available as capacity blocks) |
| "RTX Pro 6000", "RTX 6000", "96GB GDDR7", "G7e" | `g7e.*` (not available as capacity blocks) |
| "A10G", "rendering" | `g5.*` (not available as capacity blocks) |
| "Trainium", "custom silicon", "cheapest" | `trn1.32xlarge` / `trn2.48xlarge` |
| "640GB VRAM", "need 640GB" | `p5.48xlarge` or `p4de.24xlarge` |
| "1.5TB VRAM", "need 1.5TB" | `p6-b200.48xlarge` or `trn2.48xlarge` |
| "multi-node", "distributed", "EFA" | `p5en.48xlarge` |

> **Note:** EC2 Capacity Blocks and SageMaker Training Plans currently support
> P-series and Trainium instances only. G-series are available on-demand but
> not as capacity block reservations. If a user asks about G-series, inform
> them that on-demand or Savings Plans are the options for those.

## Supported regions

EC2 Capacity Blocks and SageMaker Training Plans are available in:

- `us-east-1` (N. Virginia)
- `us-east-2` (Ohio)
- `us-west-1` (N. California)
- `us-west-2` (Oregon)
- `eu-north-1` (Stockholm)
- `eu-west-2` (London)
- `ap-northeast-1` (Tokyo)
- `ap-northeast-2` (Seoul)
- `ap-south-1` (Mumbai)
- `ap-southeast-2` (Sydney)
- `ap-southeast-3` (Jakarta)
- `ap-southeast-4` (Melbourne)
- `sa-east-1` (São Paulo)

Not all instance types are available in all regions. If a region doesn't support
the requested instance type, the API returns an error — skip and continue.

## Valid durations

- **1–14 days**: Any whole number of days
- **15–182 days**: Weekly increments (21, 28, 35, ... 182)

## APIs

### EC2 Capacity Blocks

**CLI:** `aws ec2 describe-capacity-block-offerings`

**Docs:** https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeCapacityBlockOfferings.html

**Parameters:**
- `--instance-type` — GPU instance type (e.g., `p5.48xlarge`)
- `--instance-count` — Number of instances (1–256)
- `--capacity-duration-hours` — Duration in hours (days × 24)
- `--start-date-range` — Earliest start (ISO 8601, e.g., `2026-07-20T00:00:00Z`)
- `--end-date-range` — (optional) Latest end date
- `--max-results` — Up to 100

**Response key fields:** `CapacityBlockOfferings[].{AvailabilityZone, StartDate, EndDate, UpfrontFee, InstanceCount, CapacityBlockDurationHours}`

### SageMaker Training Plans

**CLI:** `aws sagemaker search-training-plan-offerings`

**Docs:** https://docs.aws.amazon.com/sagemaker/latest/APIReference/API_SearchTrainingPlanOfferings.html

**Parameters:**
- `--target-resources '["training-job"]'` — Always this value
- `--instance-type` — With `ml.` prefix (e.g., `ml.p5.48xlarge`)
- `--instance-count` — Number of instances
- `--duration-hours` — Duration in hours
- `--start-time-after` — Earliest start (ISO 8601)
- `--end-time-before` — (optional) Latest end

**Response key fields:** `TrainingPlanOfferings[].{UpfrontFee, DurationHours, ReservedCapacityOfferings[].{StartTime, EndTime, AvailabilityZone, InstanceType, InstanceCount}}`

## IAM permissions required

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeCapacityBlockOfferings",
        "sagemaker:SearchTrainingPlanOfferings"
      ],
      "Resource": "*"
    }
  ]
}
```

## Pricing notes

- Pricing is **upfront** — you pay the full amount at reservation time
- No additional hourly charges during the capacity block
- Prices vary significantly by region (us-east-1/us-west-2 typically cheapest)
- Shorter durations have higher per-day costs than longer ones
- Check https://aws.amazon.com/ec2/capacityblocks/pricing/ for current rates
