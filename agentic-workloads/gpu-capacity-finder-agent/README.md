# GPU Capacity Finder Agent — Short-Term GPU Reservations on AWS

An Amazon Bedrock Inline Agent that helps you find and compare short-term GPU
reservations on AWS using natural language. It searches both
[EC2 Capacity Blocks](https://aws.amazon.com/ec2/capacityblocks/) and
[SageMaker Training Plans](https://docs.aws.amazon.com/sagemaker/latest/dg/reserve-capacity-with-training-plans.html)
across regions, and uses `AMAZON.UserInput` to interactively clarify your
requirements before searching.

> **Related:** This agent wraps the same APIs as the
> [Capacity Finder Streamlit app](https://github.com/aws-samples/sample-capacity-finder-for-ec2-capacity-block-and-sagemaker-training-plan)
> — but provides a conversational interface powered by Amazon Bedrock Agents.

---

## Why short-term GPU reservations?

Training large models or running batch inference often requires guaranteed GPU
access for days or weeks — but you don't want a 1-year commitment. AWS offers
two options:

| | **EC2 Capacity Blocks** | **SageMaker Training Plans** |
|---|---|---|
| What | Reserved GPU instances (p5, p4d, trn) for 1–182 days | Reserved ML training capacity for 1–182 days |
| Access | EC2 instances in your VPC | SageMaker training jobs |
| Pricing | Upfront fee, no hourly charges during block | Upfront fee, covers training job hours |
| Best for | Custom training frameworks, multi-node | SageMaker-native ML workflows |

This agent searches both simultaneously across all supported regions and presents
the best options.

---

## Architecture

```
User ──(natural language)──▶ Bedrock InlineAgent
                                  │
                    ┌──────────────┼──────────────┐
                    │              │              │
              AMAZON.UserInput   Skill:        Skill:
              (ask clarifying    EC2 Capacity   SageMaker
               questions)        Block Search   Training Plan
                                      │              │
                                      ▼              ▼
                              ec2:Describe     sagemaker:Search
                              CapacityBlock    TrainingPlan
                              Offerings        Offerings
```

The agent uses **two skills** (action groups) plus `AMAZON.UserInput`:

1. **`SearchEC2CapacityBlocks`** — Calls `ec2:DescribeCapacityBlockOfferings` across
   regions to find available GPU capacity blocks.

2. **`SearchSageMakerTrainingPlans`** — Calls `sagemaker:SearchTrainingPlanOfferings`
   across regions to find available training plans.

3. **`AMAZON.UserInput`** — Built-in Bedrock capability that lets the agent ask the
   user for clarification (e.g., "Which instance type do you need?" or "How many
   days do you need the GPUs?") before executing the search.

---

## Quick start

```bash
# Install dependencies
pip install -r requirements.txt

# Run the conversational agent
python agent.py

# Or run with a specific query
python agent.py --query "Find me 8x p5.48xlarge GPUs for 7 days starting next week"

# Non-interactive mode (no AMAZON.UserInput, uses defaults)
python agent.py --non-interactive --instance-type p5.48xlarge --days 7
```

## Configuration

| Flag | Default | Description |
|------|---------|-------------|
| `--model-id` | `us.anthropic.claude-sonnet-4-20250514-v1:0` | Foundation model for the agent |
| `--region` | `us-east-1` | AWS region for the agent (searches all regions) |
| `--query` | (interactive) | Initial query to the agent |
| `--non-interactive` | `false` | Skip user input (use defaults/flags) |
| `--instance-type` | `p5.48xlarge` | Default instance type |
| `--instance-count` | `1` | Default instance count |
| `--days` | `7` | Default reservation duration |

## Example conversation

```
You: I need GPUs for a training job next week

Agent: I'd like to help you find GPU capacity. A few questions:
       1. What instance type do you need? (p5.48xlarge, p4d.24xlarge, trn1.32xlarge, etc.)
       2. How many instances?
       3. How many days do you need them?

You: p5.48xlarge, 4 instances, 7 days

Agent: Searching EC2 Capacity Blocks and SageMaker Training Plans across all regions...

       ✅ Found 3 options:

       EC2 Capacity Blocks:
       | Region     | AZ          | Instances | Duration | Start      | Fee      |
       |------------|-------------|-----------|----------|------------|----------|
       | us-east-1  | use1-az1    | 4         | 7 days   | 2026-07-21 | $12,480  |
       | us-west-2  | usw2-az2    | 4         | 7 days   | 2026-07-22 | $12,480  |

       SageMaker Training Plans:
       | Region     | AZ          | Instances | Duration | Start      | Fee      |
       |------------|-------------|-----------|----------|------------|----------|
       | us-east-1  | use1-az1    | 4         | 7 days   | 2026-07-20 | $11,200  |

       The SageMaker Training Plan in us-east-1 is cheapest and starts earliest.
       Would you like me to provide the reservation command?
```

## IAM permissions required

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeCapacityBlockOfferings",
        "sagemaker:SearchTrainingPlanOfferings",
        "bedrock:InvokeModel",
        "bedrock:InvokeInlineAgent"
      ],
      "Resource": "*"
    }
  ]
}
```

## License

MIT-0 — see [LICENSE](../../LICENSE).
