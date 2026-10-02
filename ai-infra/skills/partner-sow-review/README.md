# Partner SoW Review Skill

An [Agent Skill](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)
that helps AWS partners self-service review their Scope of Work (SoW) documents
before submitting for Partner Funding approvals (AWS credits, POC funds, MAP).

## Why this skill exists

Partners submit SoWs to obtain AWS funding, but approvals frequently get delayed
or rejected due to:

1. **Cost-architecture misalignment** — The AWS Calculator estimate doesn't match
   the proposed architecture (services missing, services extra, wrong sizing)
2. **Use-case-architecture misalignment** — The architecture is over-engineered
   for a POC or under-engineered for production

This skill catches these red flags early, helping partners fix issues before
submission rather than after rejection.

---

## Example prompts

### Full SoW review
> "Review this SoW for a customer migration project — here's the document and the
> AWS Calculator link"

> "I'm submitting this for POC funding. Can you check if the costs align with
> the architecture?"

### Cost alignment check
> "Here's our architecture diagram and AWS Calculator estimate — do the costs
> make sense?"

> "We're proposing EKS + RDS + ElastiCache for a $15K POC. Is this reasonable?"

> "Check if we're missing any cost line items for this serverless architecture"

### Architecture appropriateness
> "Is this architecture appropriate for a 4-week POC with $10K in credits?"

> "We're proposing Kafka + Spark + Redshift for a small analytics dashboard —
> is this over-engineered?"

> "This SoW is for a production deployment but I'm not sure we've covered all
> the Well-Architected pillars"

### Funding reasonableness
> "We're requesting $50K in credits for this project — does the SoW justify it?"

> "Is our success criteria specific enough for a POC funding request?"

### Pre-submission check
> "Give me a final review before I submit this to my PDM"

> "What would an approver flag in this SoW?"

---

## How it works

1. **Agent reads** the SoW document (PDF, text, or pasted content)
2. **Agent asks** for the AWS Calculator estimate if not provided
3. **Agent cross-references** every cost line item against the architecture
4. **Agent assesses** architecture appropriateness against the use case and WAF
5. **Agent generates** a structured review report with specific recommendations

## Compatibility

- ✅ Claude Code
- ✅ Kiro
- ✅ OpenClaw
- ✅ Any SKILL.md-compatible agent

## Files

| File | Purpose |
|------|---------|
| `SKILL.md` | Review process, red flag checklists, report format |
| `REFERENCE.md` | WAF pillars, funding types, common cost gaps, architecture anti-patterns |

## License

MIT-0 — see [LICENSE](../../LICENSE).
