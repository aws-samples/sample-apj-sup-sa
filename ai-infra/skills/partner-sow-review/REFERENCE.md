# Reference: Partner SoW Review

## AWS Well-Architected Framework Pillars

Reference: https://docs.aws.amazon.com/wellarchitected/latest/framework/welcome.html

| Pillar | Key Questions for SoW Review |
|--------|------------------------------|
| Operational Excellence | Is there a monitoring strategy? Alerting? Deployment pipeline? Runbooks? |
| Security | IAM least-privilege? Encryption at rest and in transit? Network segmentation? WAF/Shield? |
| Reliability | Multi-AZ? Auto-scaling? Backup/restore strategy? Disaster recovery plan? |
| Performance Efficiency | Right instance types? Caching layer? CDN for static content? Database choice appropriate? |
| Cost Optimization | Right pricing model for duration? Auto-scaling down? Lifecycle policies? Spot where applicable? |
| Sustainability | Right-sized resources? Serverless where appropriate? Efficient data storage tiers? |

## Funding Types and Typical Ranges

| Funding Type | Typical Amount | Duration | Key Requirements |
|---|---|---|---|
| POC / Proof of Concept | $1K – $25K | 1–3 months | Clear success criteria, focused scope, customer commitment |
| Innovation Sandbox | $5K – $50K | 1–6 months | Novel use case, customer exec sponsor |
| Migration (MAP) | $10K – $500K+ | 3–12 months | TCO analysis, migration plan, business case per workload |
| Well-Architected Review | $5K – $15K | 2–4 weeks | Existing workload, specific improvement targets |
| ISV Workload Migration | $25K – $200K | 3–9 months | SaaS architecture, multi-tenant considerations |

## Common Cost Estimation Gaps

Services frequently missing from partner cost estimates:

| Commonly Missing | Why It Matters | Typical % of Total |
|---|---|---|
| Data transfer (inter-region, internet egress) | Often 10–30% of compute-heavy workloads | 10–30% |
| NAT Gateway | $0.045/hr + data processing — adds up fast | 5–15% |
| CloudWatch (logs, metrics, dashboards) | Defaults accumulate; custom metrics expensive | 3–8% |
| S3 requests (PUT/GET) | High-throughput workloads: millions of requests | 2–10% |
| KMS key usage | $0.03/10K requests; encryption-heavy workloads | 1–5% |
| Elastic IP (when idle) | Charged when not attached — common in dev/test | Minor |
| Secrets Manager | Per-secret per-month + API calls | Minor |
| Load balancer (ALB/NLB) | Hourly + LCU charges; often under-estimated | 3–8% |
| EBS snapshots | Ongoing storage cost for backups | 2–5% |
| CloudTrail (data events) | If enabled on S3/Lambda — can be significant | Variable |

## AWS Pricing Calculator

**URL:** https://calculator.aws/

**Estimate URL format:** `https://calculator.aws/estimate?id=<UUID>`

When reviewing a Calculator estimate:
1. Check that every service in the architecture has a line item
2. Verify region matches the architecture (pricing varies 10–30% by region)
3. Look for pricing model: On-Demand vs Reserved vs Savings Plan vs Spot
4. For POCs: should be On-Demand (not RIs — misleading for short-term)
5. Check instance sizing matches the stated workload scale
6. Verify data transfer assumptions are realistic

## Architecture Pattern Red Flags

### Over-engineering patterns (common in POC SoWs)

| Pattern | What they proposed | What's usually sufficient |
|---|---|---|
| Container orchestration for simple APIs | EKS + service mesh + GitOps | Lambda or ECS Fargate |
| Data platform for small data | Kafka + Spark + Redshift + QuickSight | S3 + Glue + Athena |
| ML pipeline for simple inference | SageMaker Pipelines + Feature Store + Model Monitor | Bedrock API call or single endpoint |
| Multi-region for POC | Active-active multi-region | Single region with backup strategy |
| Custom networking | Transit Gateway + multiple VPCs + PrivateLink | Single VPC with proper subnets |

### Under-engineering patterns (common in production SoWs)

| Pattern | What's missing | Risk |
|---|---|---|
| Single AZ deployment | No failover, no redundancy | Single point of failure |
| No auto-scaling | Fixed capacity | Can't handle traffic spikes, wastes money at low traffic |
| No monitoring | No CloudWatch, no alerting | Blind to issues until customer reports |
| Root account usage | No IAM roles, no MFA | Critical security risk |
| No backup strategy | No snapshots, no cross-region | Data loss risk |
| Public subnets for everything | Databases, app servers exposed | Security vulnerability |

## SoW Quality Indicators

**Good SoW characteristics:**
- Clear problem statement tied to business outcome
- Architecture diagram with labeled services and data flows
- Cost estimate that matches architecture 1:1
- Timeline broken into phases with deliverables
- Success criteria that are measurable
- Security and compliance section
- Assumptions and exclusions clearly stated
- Partner qualifications/certifications relevant to the workload

**Poor SoW characteristics:**
- Vague scope ("implement AI solution")
- No architecture diagram or only a high-level box diagram
- Cost estimate with unexplained large line items
- No timeline or unrealistic timeline (50 services in 2 weeks)
- No success criteria or only "customer is satisfied"
- No mention of security, reliability, or operations
- Copy-pasted from a template with customer-specific details missing
