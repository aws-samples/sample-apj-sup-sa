# Reference: Customer Readiness & Platform Maturity

Assess whether the customer has the sponsorship, expertise, and operational
maturity to sustain the workload after the partner engagement ends.

**Official training resources:**
- AWS Skill Builder: https://skillbuilder.aws/
- AWS Training & Certification: https://aws.amazon.com/training/

## 1. Executive Sponsorship

| Signal | ✅ Green | ⚠️ Yellow | ❌ Red |
|--------|---------|----------|-------|
| Named exec sponsor | CTO/VP Eng named, actively involved | "TBD" or only technical IC listed | No sponsor mentioned |
| Business outcome | Clear revenue/cost/efficiency metric | Vague "innovation" or "explore AI" | No business case |
| Budget ownership | Customer owns ongoing costs post-funding | Unclear who pays after credits expire | No mention of post-funding cost plan |
| Post-engagement ownership | Internal team assigned to maintain | "Partner will provide ongoing support" | No plan for after handoff |

**Why this matters for funding approval:**
- No exec sponsor = no commitment = credits may be wasted
- No post-engagement plan = risk of workload being abandoned after funding runs out

## 2. Technical Team Maturity

### Core workload maturity levels

| Level | Description | Indicators | Appropriate SoW complexity |
|-------|-------------|------------|---------------------------|
| 1 — Beginner | New to AWS, no prior experience | No certs, no existing account, no IaC | Simple architectures, heavy partner guidance, training included |
| 2 — Developing | Basic AWS usage, some services | 1-2 team members with AWS experience, existing dev account | Moderate architectures, partner builds + knowledge transfer |
| 3 — Competent | Running production workloads | Team has certs, IaC in use, CI/CD exists | Complex architectures viable, partner advises + co-builds |
| 4 — Advanced | Multi-account, mature operations | DevOps/SRE team, monitoring, incident response | Partner adds specialized expertise, customer self-sufficient |

### GenAI maturity levels

| Level | Description | Indicators | Appropriate GenAI scope |
|-------|-------------|------------|------------------------|
| 1 — Exploring | No ML/AI experience | No data science team, no model experience | Bedrock API calls only, no custom models |
| 2 — Experimenting | Basic ML understanding | Some ML experimentation, Jupyter usage | Bedrock + fine-tuning with partner guidance |
| 3 — Building | Active ML development | Data science team, model training experience | Custom training, self-hosted inference, RAG |
| 4 — Scaling | Production ML systems | MLOps pipeline, model monitoring, A/B testing | Multi-model, complex agent architectures |

### Red flags: complexity vs maturity mismatch

| SoW proposes... | But customer is... | Risk |
|---|---|---|
| EKS + microservices + service mesh | Level 1 (beginner) | Cannot operate post-handoff |
| Custom model training on P-series | GenAI Level 1 (exploring) | No capability to retrain or evaluate |
| Multi-region active-active | Level 2 (developing) | Operational burden exceeds capability |
| Bedrock Agents + Knowledge Bases + Guardrails | GenAI Level 1 | No ability to maintain/debug |
| 20+ AWS services in architecture | 3-person engineering team | Cannot manage this many services |

## 3. Training & Enablement Gap

Check if the SoW addresses capability gaps:

| Gap identified | SoW should include |
|---|---|
| No AWS experience | AWS Cloud Practitioner training, hands-on labs |
| No IaC experience | CDK/CloudFormation workshop, starter templates |
| No GenAI experience | Bedrock workshop, prompt engineering training |
| No operations experience | Monitoring/alerting setup + runbook creation |
| No security experience | SSB implementation + security training |

**What good knowledge transfer looks like in a SoW:**
- Specific training sessions with hours allocated
- Documentation deliverables (runbooks, architecture decision records)
- Hypercare period (2-4 weeks post go-live, partner available)
- Paired programming / shadowing during build phase
- Admin handoff with credentials and access walkthrough

## 4. Post-Engagement Sustainability

| Question | ✅ Good answer | ❌ Bad answer |
|----------|---------------|-------------|
| Who operates day-to-day? | Named internal team/person | "Partner" or "TBD" |
| Who handles incidents? | Internal team with escalation path | No plan |
| Who pays after credits expire? | Customer budget allocated | Not discussed |
| Who retrains models? (GenAI) | Internal data science team | "We'll figure it out" |
| Who updates infrastructure? | Internal DevOps/SRE | "Partner retainer" with no internal plan |

## Assessment output

Include in the review report:

```
## Customer Readiness

**Executive Sponsorship:** ✅ Strong / ⚠️ Weak / ❌ Missing
**Core Platform Maturity:** Level [1-4]
**GenAI Maturity:** Level [1-4] (if applicable)
**Post-Engagement Sustainability:** ✅ Viable / ⚠️ At risk / ❌ Not addressed

Complexity vs Maturity: [Match / Gap identified]

Gaps:
1. [specific gap]
2. [specific gap]

Recommended mitigations:
1. [e.g., Add AWS training component to SoW]
2. [e.g., Include 4-week hypercare period]
3. [e.g., Reduce architecture complexity to match team capability]
```
