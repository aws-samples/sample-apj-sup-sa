---
name: partner-sow-review
description: Review partner Scope of Work (SoW) documents against the AWS Well-Architected Framework before submitting for Partner Funding approvals (AWS credits, POC funds). Catches red flags around cost-architecture misalignment and use-case-architecture misalignment. Use when a partner asks to review a SoW, validate architecture costs, check funding eligibility, or assess alignment between proposed workload and AWS services.
---

# Partner SoW Review

Review a partner's Scope of Work (SoW) against the AWS Well-Architected Framework
to catch red flags before submitting for Partner Funding approvals.

## Why this skill exists

Partners submit Scope of Work documents to obtain AWS funding (credits, POC funds,
Migration Acceleration Program funds, etc.). The AWS Calculator cost breakdown in
the SoW is what determines funding amounts. Common issues that delay or block
approvals:

- Cost estimates that don't match the architecture (e.g., pricing EC2 instances
  that aren't in the architecture diagram, or missing costs for services that are)
- Architecture that doesn't match the stated use case (e.g., proposing a
  data lake architecture for a simple web app, or over-engineering a POC)
- Well-Architected anti-patterns that indicate the partner hasn't thought
  through reliability, security, or cost optimization

This skill helps partners self-service their SoW reviews before submission,
catching these issues early and reducing approval cycle time.

## Prerequisites check

The user should provide:
1. The SoW document (PDF, Word, or pasted text)
2. The AWS Calculator estimate (URL or exported CSV/JSON)
3. (Optional) Architecture diagram

If any of these are missing, ask the user to provide them.

## Review process

### Step 1: Classify Workload Type

First, identify whether the SoW contains **Generative AI** workloads,
**Core** workloads, or both. This affects funding eligibility and review criteria.

**Generative AI workload indicators:**
- Amazon Bedrock (any model invocation, fine-tuning, agents, knowledge bases)
- SageMaker with LLM training/inference (p5, p4d, g5, g6, trn instances)
- GPU instances for ML (any P-series, G-series, or Trainium)
- Foundation model hosting (vLLM, TGI, custom inference endpoints)
- RAG architectures (vector databases + LLM)
- AI agents, chatbots, copilots, document processing with AI
- Model fine-tuning, RLHF, prompt engineering services

**Core workload indicators:**
- Traditional compute (EC2 general purpose, ECS, EKS without GPU)
- Databases (RDS, Aurora, DynamoDB, ElastiCache)
- Storage (S3, EBS, EFS without ML context)
- Networking (VPC, CloudFront, Route 53, Transit Gateway)
- Analytics (Redshift, Athena, Glue, QuickSight — without LLM)
- Security (IAM, GuardDuty, Security Hub, WAF)
- Application services (Lambda, API Gateway, SQS, SNS)

**Classification output:**

```
Workload Classification:
├── Generative AI: [X% of cost estimate]
│   └── Services: [list]
└── Core: [Y% of cost estimate]
    └── Services: [list]
```

This classification matters because:
- Generative AI workloads have different funding programs and approval criteria
- Cost profiles are very different (GPU instances dominate GenAI; diverse services for Core)
- Review criteria differ (GenAI needs model selection justification and VRAM validation;
  Core needs WAF depth)

If GenAI workloads are detected, perform the deeper assessment in
[references/genai-assessment.md](references/genai-assessment.md) which covers:
- GPU vs Bedrock decision validation
- VRAM calculation verification
- Capacity access strategy (P-series needs reservations; G-series is on-demand)
- Training vs inference cost separation

### Step 2: Understand the SoW

Extract and summarize:
- **Customer name and use case** — What problem is being solved?
- **Customer sponsor and team** — Who owns this? What's their AWS maturity?
  See [references/customer-readiness.md](references/customer-readiness.md)
- **Proposed architecture** — What AWS services are involved?
- **Timeline** — How long is the engagement?
- **Funding requested** — How much and what type (credits, POC, MAP)?
- **Cost estimate** — What does the AWS Calculator breakdown show?
- **Success criteria** — How will the project be measured?
- **If MAP:** Is `map-migrated` tagging addressed? See [references/map-tagging.md](references/map-tagging.md)

### Step 3: Cost-Architecture Alignment Check

This is the primary red flag area. Compare the cost estimate against the
architecture and flag mismatches.

**General red flags:**

| Issue | Example | Risk |
|-------|---------|------|
| Service in cost estimate not in architecture | Paying for Redshift but architecture shows only S3 + Athena | Inflated costs → funding rejection |
| Service in architecture not in cost estimate | Architecture shows ALB + ECS but cost estimate only has EC2 | Under-estimated → project failure |
| Instance sizing mismatch | Architecture says "small POC" but costs show p5.48xlarge production cluster | Over-scoped for stated purpose |
| Region mismatch | Architecture says ap-southeast-2 but costs calculated for us-east-1 | Inaccurate pricing |
| Missing data transfer costs | Multi-region or hybrid architecture with no transfer line items | Commonly under-estimated 30-50% |
| Reserved/Savings Plans in POC | Cost estimate uses 1yr RI pricing for a 3-month POC | Misleading cost basis |
| No cost for supporting services | Main compute priced but no CloudWatch, VPC, NAT Gateway, S3 storage | 15-30% cost undercount |

**Generative AI-specific red flags:**

| Issue | Example | Risk |
|-------|---------|------|
| Bedrock token costs missing or unrealistic | Architecture uses Bedrock but cost estimate shows $50/month for Claude | Massively under-estimated at scale |
| GPU instance costs without utilization plan | 24/7 p5.48xlarge for a workload that runs 2 hours/day | 90% waste — should use Capacity Blocks or spot |
| No model inference cost scaling | Fixed cost for Bedrock but architecture shows user-facing chatbot | Costs scale with users — needs usage projection |
| Missing embedding/vector DB costs | RAG architecture but no OpenSearch Serverless or Pinecone line item | Core component uncosted |
| Fine-tuning costs confused with inference | Single line item for "Bedrock" covering both training and inference | Very different cost profiles |
| Knowledge base storage uncosted | Bedrock Knowledge Bases but no S3/OpenSearch for the index | Infrastructure cost missing |

**How to validate:**

```bash
# If the user provides an AWS Calculator URL, fetch and parse it
# Calculator URLs look like: https://calculator.aws/estimate?id=xxxxx
```

Cross-reference every line item in the cost estimate against the architecture
description. Flag any service that appears in one but not the other.

### Step 4: Use-Case-Architecture Alignment Check

Assess whether the proposed architecture is appropriate for the stated use case:

**General red flags:**

| Issue | Example | Risk |
|-------|---------|------|
| Over-engineering | POC for a chatbot using EKS + Kafka + Neptune when Bedrock + Lambda would suffice | Unnecessary complexity, higher cost |
| Under-engineering | Production ML platform with no auto-scaling, single-AZ, no monitoring | Reliability/operational risk |
| Wrong service choice | Using EC2 self-managed for a stateless API when Lambda/Fargate is cheaper | Cost inefficiency |
| Lift-and-shift only | "Modernization" SoW that's just re-hosting VMs with no architectural improvement | Doesn't justify funding |
| Missing Well-Architected pillars | No mention of security (IAM, encryption), reliability (multi-AZ), or operations (monitoring) | Approval likely flagged |
| Scope creep indicators | SoW covers 15 services for a 4-week engagement | Unrealistic timeline |

**Generative AI-specific red flags:**

| Issue | Example | Risk |
|-------|---------|------|
| Self-hosting when managed exists | Fine-tuning open-source LLM on EC2 when Bedrock custom model would suffice | Unnecessary operational burden |
| GPU training for a Bedrock use case | Requesting p5 instances for a RAG chatbot that only needs Bedrock API calls | Massive over-spend |
| No model selection justification | "We'll use Claude" with no explanation of why that model vs alternatives | Weak technical basis |
| RAG without evaluation plan | Building RAG pipeline but no mention of retrieval quality metrics | No way to measure success |
| Agent architecture without guardrails | Bedrock Agents or custom agents with no mention of content filtering, PII handling | Security/compliance risk |
| Training from scratch vs fine-tuning | Proposing full model training when fine-tuning or prompt engineering would work | 10-100x cost difference |

**Well-Architected Framework alignment:**

For each pillar, check if the SoW addresses it. See `references/` for detailed
checklists per pillar:

1. **Operational Excellence** — [references/operational-excellence.md](references/operational-excellence.md)
2. **Security** — [references/security.md](references/security.md) + [references/startup-security-baseline.md](references/startup-security-baseline.md) (SSB)
3. **Reliability** — [references/reliability.md](references/reliability.md)
4. **Performance Efficiency** — [references/performance-efficiency.md](references/performance-efficiency.md)
5. **Cost Optimization** — [references/cost-optimization.md](references/cost-optimization.md)
6. **Sustainability** — [references/sustainability.md](references/sustainability.md)

A POC doesn't need all of these, but a production deployment should address most.
Flag missing pillars relative to the stated environment (POC vs production).

### Step 5: Funding Reasonableness Check

Assess whether the funding request is reasonable:

- **POC funding** (typically $1K–$25K): Should be 1–3 months, focused scope,
  clear success criteria, minimal production-grade requirements
- **Migration funding** (MAP): Should show clear migration path, TCO comparison,
  business case for each workload. **Must include `map-migrated` tagging via IaC** —
  see [references/map-tagging.md](references/map-tagging.md). Without tagging,
  credits will not apply to resources.
- **Credits for production**: Should show customer commitment, architecture
  maturity, operational readiness

**Red flags:**
- Requesting $50K in credits for a 2-week POC
- No clear success criteria (how do you know the POC succeeded?)
- Cost estimate significantly different from requested funding amount
- SoW that's really a training engagement disguised as implementation

### Step 6: Generate report

Present findings as:

```
## SoW Review Summary

**Customer:** [name]
**Use Case:** [one-line summary]
**Funding Requested:** [amount and type]
**Overall Assessment:** ✅ Ready / ⚠️ Needs revision / ❌ Significant concerns

## Workload Classification
- Generative AI: [X% of cost] — [services list]
- Core: [Y% of cost] — [services list]

## Cost-Architecture Alignment
[findings with specific line items]

## Use-Case-Architecture Alignment
[findings with WAF pillar assessment]

## Funding Reasonableness
[assessment]

## Recommendations
1. [specific action to fix issue]
2. [specific action to fix issue]
...
```

### Step 7: Quality gate — Independent review

After generating the initial review, perform a structured quality gate before
delivering it to the user. This ensures the review is thorough, fair, and
actionable.

**Step 7a: Self-check (think step by step)**

Before presenting the review, walk through these validation questions:

1. **Completeness:** Did I check every service in the cost estimate against the
   architecture? Did I miss any line items?
2. **Fairness:** Am I flagging genuine red flags, or am I being overly harsh on
   a reasonable POC-level SoW?
3. **Specificity:** Does every finding cite a specific service, cost, or section
   of the SoW? (No vague "could be improved" statements)
4. **Actionability:** Does every recommendation tell the partner exactly what
   to fix? ("Add CloudWatch alarms for X" not "improve monitoring")
5. **Context-appropriate:** Am I holding a POC to production standards, or
   a production SoW to POC standards?

**Step 7b: Independent reviewer sub-agent**

Spawn a sub-agent to perform an independent review of your assessment. The
sub-agent acts as a "second pair of eyes" — a skeptical reviewer who challenges
the initial assessment.

Sub-agent prompt:
```
You are an independent reviewer of a Partner SoW assessment. Your job is to:

1. Check if the reviewer missed any red flags in the SoW
2. Challenge any findings that seem like false positives
3. Verify the cost-architecture alignment analysis is complete
4. Confirm the overall assessment rating (Ready/Needs revision/Significant concerns) is justified
5. Identify any blind spots

Here is the original SoW:
[paste SoW]

Here is the assessment:
[paste assessment]

Provide:
- Missed issues (if any)
- Findings you disagree with (with reasoning)
- Confidence level in the overall rating (High/Medium/Low)
- Any additional recommendations
```

After the sub-agent responds:
- If it identifies **missed issues** → add them to the report
- If it **disagrees with findings** → re-evaluate and either remove or justify
- If confidence is **Low** → flag to the user that this review may need human
  expert input
- If confidence is **High** and no disagreements → proceed with delivery

**Step 7c: Final iteration**

If the sub-agent triggered changes, regenerate the report incorporating the
feedback. Present both the final report and a brief "Review confidence" note:

```
## Review Confidence

- Initial review: [X findings]
- Independent review: [added Y, removed Z, confirmed W]
- Final confidence: High / Medium / Low
- Recommendation: [Ready to submit / Suggest human expert review for areas X, Y]
```

### Iteration on partner feedback

After delivering the review, the partner may revise their SoW and ask for
re-review. When this happens:

1. Load the previous assessment from `memory/`
2. Diff what changed in the revised SoW
3. Check if previously flagged issues are now addressed
4. Look for new issues introduced by the revisions
5. Update the assessment and save the new version to `memory/`

Naming convention for iterations:
```
memory/
├── 2026-07-13_customer-name_review_v1.md
├── 2026-07-15_customer-name_review_v2.md    ← after revision
└── 2026-07-15_customer-name_changelog.md    ← what changed
```

## Important notes

- This is a **self-service pre-review** — it does not replace the actual
  AWS approval process
- Be specific: cite exact services, costs, and page numbers from the SoW
- Focus on the two primary red flag areas: cost-architecture alignment and
  use-case-architecture alignment
- The AWS Calculator cost breakdown is the key funding determinant — any
  discrepancy between it and the architecture is the #1 blocker
- When in doubt, suggest the partner contact their Partner Development Manager
  (PDM) or Partner Solutions Architect (PSA)
- **Save all assessments** to the `memory/` folder using the naming convention:
  `YYYY-MM-DD_customer-name_review.md`. This builds a local history of reviews
  for reference.
