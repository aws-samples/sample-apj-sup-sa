# AWS Disaster Recovery Advisor

<img src="logo.svg" alt="AWS Disaster Recovery Advisor" width="80" />

A [Kiro Power](https://kiro.dev/docs/powers/) that scans your AWS account and generates a complete Disaster Recovery package — gap analysis, DR plan, CloudFormation templates, and operational checklist — grounded in AWS Well-Architected best practices.

---

## What it does

The power guides you through five phases:

| Phase | Name | What happens | Time |
|-------|------|-------------|------|
| 0 | **Intake** | Generates `dr-intake.json` (fillable form) and `dr-context/` folder for architecture docs | 5–10 min user fill time |
| 1 | **Scan** | Discovers all AWS resources in **both** primary and recovery regions | 5–15 min |
| 2 | **Analyze** | Classifies workload tiers, runs cross-region gap analysis, recommends DR strategies | 10–20 min |
| 3 | **Plan** | Generates `dr-plan.md` — a complete written DR plan with failover/failback procedures | 5–10 min |
| 4 | **Templates** | Generates CloudFormation YAML templates for DR infrastructure | 5–10 min |
| 5 | **Checklist** | Generates `dr-checklist.md` — operational checklist for drills, monitoring, failover, failback | 2–5 min |

**Total:** ~30–70 minutes for a complete assessment.

---

## Output files

```
$WORKING_DIR/
├── dr-intake.json          ← Fill this in before scanning
├── dr-context/             ← Drop architecture diagrams, IaC, runbooks here
│   └── README.md
├── dr-plan.md              ← Generated DR plan
├── dr-checklist.md         ← Generated operational checklist
├── cfn-templates/          ← Generated CloudFormation templates
│   ├── recovery-vpc.yaml
│   ├── backup-vault.yaml
│   ├── route53-failover.yaml
│   ├── drs-staging-area.yaml
│   ├── drs-iam-roles.yaml
│   └── multi-site-routing.yaml (if Multi-Site Active/Active selected)
└── dr-state/               ← State directory (git-ignored)
    ├── core.json
    ├── resources.json
    ├── gaps.json
    ├── tiers.json
    ├── templates.json
    ├── intake.json
    └── context.json
```

---

## Prerequisites

- **Kiro** IDE installed
- **`uvx`** installed: `pip install uv`
- **AWS CLI** installed: [Installation guide](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)
- **AWS credentials** — read-only access to your AWS account (the power validates no write permissions before scanning)

---

## Installation

### Option A: Install from GitLab (this repo)

1. Open Kiro → Powers panel → **Add power from URL**
2. Enter: `git@ssh.gitlab.aws.dev:addysri/Kiro-Powers.git`
3. Select the `AWS Disaster Recovery Advisor` power

### Option B: Install from local directory

1. Clone this repo
2. Open Kiro → Powers panel → **Add power from Local Path**
3. Select the `AWS Disaster Recovery Advisor/` directory

---

## Credential setup

Credentials are loaded from a `.secret` file in your working directory — never stored in `mcp.json`.

Create `.secret` in your working directory:

```
AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE
AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
AWS_DEFAULT_REGION=us-east-1
```

Or for profile-based auth:
```
AWS_PROFILE=my-readonly-profile
AWS_DEFAULT_REGION=us-east-1
```

> **Security:** `.secret` is in `.gitignore` and never committed. The power validates that credentials have no write permissions before scanning begins.

---

## What gets analyzed

### DR Gap Categories (19 gap rules)

| # | Category | Applies to |
|---|----------|-----------|
| 1 | RDS backup retention + live replication | Tier 1, 2 |
| 1a | Aurora Global Database readiness | Tier 1, 2 |
| 2 | Redshift cross-region snapshot | Tier 1, 2 |
| 3 | RDS Multi-AZ | Tier 1, 2 |
| 4 | EC2 single-AZ | Tier 1, 2 |
| 5 | S3 Cross-Region Replication | Tier 1 |
| 6 | OpenSearch cross-region | Tier 1 |
| 7 | IaC coverage (CloudFormation) | All tiers |
| 8 | Load balancer health checks | Tier 1, 2 |
| 9 | DNS failover / Route53 ARC | Tier 1, 2 |
| 9a | Detection automation (CloudWatch/EventBridge) | Tier 1, 2 |
| 10 | KMS CMK replication | Tier 1, 2 |
| 11 | Secrets Manager replication | Tier 1, 2 |
| 12 | SageMaker model continuity | Tier 1, 2 |
| 13 | Bedrock knowledge base CRR | Tier 1 |
| 14 | Glue catalog in recovery region | Tier 1, 2 |
| 15–17 | Network infrastructure (VPC, SGs, LBs) | Tier 1, 2 |
| 18–19 | ElastiCache / MemoryDB replication | Tier 1, 2 |
| 20–23 | Container workloads (ECR, ECS, EKS) | Tier 1, 2 |

### DR Strategies supported

- **Backup & Restore** — AWS Backup cross-region copy, EC2 Image Builder golden AMIs
- **Pilot Light** — Aurora Global Database (headless), RDS cross-region read replicas, ECS DesiredCount=0
- **Warm Standby** — Aurora Global Database switchover (RPO=0), RDS read replica promotion, ECS/EKS at reduced capacity
- **Hot Standby** — Warm Standby at full production capacity
- **Multi-Site Active/Active** — DynamoDB Global Tables (MREC/MRSC), Aurora write forwarding, ElastiCache Global Datastore, Route53 latency/geolocation routing, Global Accelerator

### Services covered

Compute, Databases (RDS, Aurora, DynamoDB, ElastiCache, MemoryDB, Redshift, DocumentDB), Storage (S3, EFS, FSx), Networking (VPC, ALB/NLB, Route53, CloudFront, API Gateway, Global Accelerator, Transit Gateway, Direct Connect), Messaging (SQS, SNS, Kinesis, MSK, EventBridge), Security (KMS, Secrets Manager, ACM, WAF), Operations (CloudFormation, CloudWatch, Systems Manager, AWS Backup), Analytics (Glue, Athena, EMR, OpenSearch), AI/ML (SageMaker, Bedrock, Comprehend, Rekognition), Containers (ECS, EKS, Fargate, ECR)

---

## CloudFormation templates generated

| Template | Generated when |
|----------|---------------|
| `recovery-vpc.yaml` | Always |
| `backup-vault.yaml` | Always |
| `route53-failover.yaml` | Route53 hosted zones discovered |
| `drs-staging-area.yaml` | EC2 instances in Tier 1/2 workloads |
| `drs-iam-roles.yaml` | EC2 instances in Tier 1/2 workloads |
| `multi-site-routing.yaml` | Multi-Site Active/Active selected |

---

## Sources

All recommendations are grounded in official AWS documentation:

- [AWS Disaster Recovery Whitepaper](https://docs.aws.amazon.com/whitepapers/latest/disaster-recovery-workloads-on-aws/)
- [AWS DR Architecture Series (Parts I–IV)](https://aws.amazon.com/blogs/architecture/tag/disaster-recovery-series/)
- [AWS DRS Best Practices](https://docs.aws.amazon.com/drs/latest/userguide/best_practices_drs.html)
- [AWS Well-Architected Reliability Pillar](https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/welcome.html)
- [Aurora Global Database DR](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-global-database-disaster-recovery.html)
- [Route53 Application Recovery Controller](https://docs.aws.amazon.com/r53recovery/latest/dg/what-is-route53-recovery.html)
- [ECR Private Image Replication](https://docs.aws.amazon.com/AmazonECR/latest/userguide/replication.html)
- [EKS Backup with AWS Backup](https://docs.aws.amazon.com/eks/latest/userguide/integration-backup.html)

---

## File structure

```
AWS Disaster Recovery Advisor/
├── POWER.md                          ← Orchestrator: state machine, phase transitions
├── mcp.json                          ← MCP server config (awslabs.aws-api-mcp-server)
├── logo.svg                          ← Power logo
├── .gitignore                        ← Excludes dr-state/ and .secret
└── steering/
    ├── intake.md                     ← Phase 0: intake form + context folder scanning
    ├── scan.md                       ← Phase 1: credential setup + resource discovery
    ├── analyze.md                    ← Phase 2: gap analysis + strategy selection
    ├── plan.md                       ← Phase 3: DR plan generation
    ├── templates.md                  ← Phase 4: CloudFormation template generation
    ├── checklist.md                  ← Phase 5: DR checklist generation
    └── cached-dr-patterns.md         ← Reference: CloudFormation snippets + DR patterns
```

---

## License

See [LICENSE](LICENSE) for details.
