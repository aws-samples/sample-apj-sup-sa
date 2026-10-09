# GPU Capacity Finder Skill

An [Agent Skill](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)
that helps find short-term GPU reservations on AWS — self-service, no long-term
commitment, no sales calls required.

Many startups don't know AWS offers this because the capability is buried within
EC2 ([Capacity Blocks](https://aws.amazon.com/ec2/capacityblocks/)) and SageMaker
([Training Plans](https://docs.aws.amazon.com/sagemaker/latest/dg/reserve-capacity-with-training-plans.html))
rather than being a standalone product.

> **Related:** [Capacity Finder Streamlit App](https://github.com/aws-samples/sample-capacity-finder-for-ec2-capacity-block-and-sagemaker-training-plan)
> — web UI for the same APIs. This skill provides a conversational alternative
> compatible with Claude Code, Kiro, and other SKILL.md-compatible agents.

---

## Example prompts

Try any of these with your agent:

### Basic availability search
> "Find me available H100 GPUs for 7 days"

> "I need 4 instances of p5.48xlarge starting next Monday"

> "Are there any A100s available in us-west-2 for 2 weeks?"

### NVIDIA GPU / VRAM translation
> "I need 640GB of VRAM for fine-tuning a 70B model — what's available?"

> "Find Blackwell GPUs for 14 days"

> "I want the cheapest option with at least 320GB total VRAM"

### Comparing options
> "Compare EC2 Capacity Blocks vs SageMaker Training Plans for 8x H100 for 3 days"

> "Which region has the cheapest p5.48xlarge capacity block right now?"

> "Find GPU capacity across all regions and show me the earliest available slot"

### Specific use cases
> "I'm training a LLaMA model and need 8 H100 GPUs for 10 days — what are my options?"

> "We have a fine-tuning job that needs 2x A100 80GB for 48 hours. What's the cheapest way?"

> "I need Trainium instances for a week — what's available and how much does it cost?"

### Budget-constrained
> "What GPU reservations can I get for under $5,000?"

> "Find the cheapest 7-day GPU reservation available right now in any region"

### Multi-step / exploratory
> "What GPU options does AWS have for short-term reservations?"

> "Help me figure out what I need — I'm training a 13B parameter model and don't know which instance type to pick"

> "I've been using on-demand p4d.24xlarge instances but keep getting interrupted. What are my reservation options?"

---

## How it works

This is a **pure knowledge skill** — no scripts, no framework dependencies. It
teaches the agent:

1. **Verify AWS CLI auth** (`aws sts get-caller-identity`)
2. **Translate** NVIDIA GPU names / VRAM / architectures → AWS instance types
3. **Call APIs directly** via AWS CLI commands
4. **Loop through regions** dynamically, adapting based on results
5. **Present** a comparison table with recommendations

## Compatibility

Works with any agent that supports the [SKILL.md format](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview):

- ✅ Claude Code
- ✅ Kiro
- ✅ OpenClaw
- ✅ Any SKILL.md-compatible agent

## Installation

Copy the skill directory into your project or agent workspace:

```bash
# Clone just this skill
git clone --depth 1 --filter=blob:none --sparse \
  https://github.com/aws-samples/sample-apj-sup-sa.git
cd sample-apj-sup-sa
git sparse-checkout set ai-infra/gpu-capacity-finder-skill
```

Or simply copy `SKILL.md` and `REFERENCE.md` into your agent's skills directory.

## Prerequisites

- AWS CLI configured and authenticated
- IAM permissions: `ec2:DescribeCapacityBlockOfferings`, `sagemaker:SearchTrainingPlanOfferings`

## Files

| File | Purpose |
|------|---------|
| `SKILL.md` | Agent instructions: when to trigger, conversation flow, API commands |
| `REFERENCE.md` | Instance types, GPU specs, VRAM, regions, CLI syntax, IAM policy |

## License

MIT-0 — see [LICENSE](../../LICENSE).
