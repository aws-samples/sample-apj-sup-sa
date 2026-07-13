# Reference: GPU Capacity Finder

## Supported instance types

| Instance Type | GPU | Use Case |
|---------------|-----|----------|
| `p6-b200.48xlarge` | NVIDIA B200 | Latest gen, largest models |
| `p6-b300.48xlarge` | NVIDIA B300 | Latest gen, highest perf |
| `p5.48xlarge` | 8× NVIDIA H100 80GB | Large-scale training |
| `p5e.48xlarge` | 8× NVIDIA H100 80GB (enhanced) | Memory-intensive training |
| `p5en.48xlarge` | 8× NVIDIA H100 80GB (network-enhanced) | Multi-node distributed |
| `p4d.24xlarge` | 8× NVIDIA A100 40GB | General ML training |
| `p4de.24xlarge` | 8× NVIDIA A100 80GB | Large model training |
| `trn1.32xlarge` | 16× AWS Trainium | Cost-effective training |
| `trn2.48xlarge` | 16× AWS Trainium2 | Next-gen Trainium |
| `trn2.3xlarge` | 2× AWS Trainium2 | Smaller Trainium jobs |

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

Not all instance types are available in all regions. The scripts handle this
gracefully — unsupported combinations return empty results rather than errors.

## Valid durations

- **1–14 days**: Any whole number of days
- **15–182 days**: Weekly increments (21, 28, 35, ... 182)

## APIs used

### EC2 Capacity Blocks

**API:** `ec2:DescribeCapacityBlockOfferings`

**Documentation:** https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DescribeCapacityBlockOfferings.html

**Key parameters:**
- `InstanceType` — GPU instance type
- `InstanceCount` — Number of instances (1–256)
- `CapacityDurationHours` — Duration in hours (duration_days × 24)
- `StartDateRange` — Earliest acceptable start date
- `EndDateRange` — (optional) Latest acceptable end date
- `MaxResults` — Up to 100

**Response includes:** AvailabilityZone, StartDate, EndDate, UpfrontFee, CapacityBlockDurationHours

### SageMaker Training Plans

**API:** `sagemaker:SearchTrainingPlanOfferings`

**Documentation:** https://docs.aws.amazon.com/sagemaker/latest/APIReference/API_SearchTrainingPlanOfferings.html

**Key parameters:**
- `TargetResources` — Always `["training-job"]`
- `InstanceType` — GPU instance type with `ml.` prefix (e.g., `ml.p5.48xlarge`)
- `InstanceCount` — Number of instances
- `DurationHours` — Duration in hours
- `StartTimeAfter` — Earliest start date
- `EndTimeBefore` — (optional) Latest end date

**Response includes:** TrainingPlanOfferings with ReservedCapacityOfferings (StartTime, EndTime, AvailabilityZone, InstanceCount), UpfrontFee, DurationHours

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
