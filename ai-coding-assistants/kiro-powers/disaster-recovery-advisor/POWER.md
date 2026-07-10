---
name: "aws-dr-power"
displayName: "AWS Disaster Recovery Advisor"
description: "Scan your AWS account and generate a complete Disaster Recovery package: gap analysis, DR plan, CloudFormation templates, and operational checklist — grounded in AWS Well-Architected best practices and AWS Elastic Disaster Recovery guidance."
keywords: ["disaster-recovery", "aws", "dr-plan", "cloudformation", "rto", "rpo", "resilience", "drs", "backup", "failover"]
author: "AWS"
---

<img src="logo.svg" alt="AWS Disaster Recovery Advisor" width="80" />

# AWS Disaster Recovery Advisor

Welcome to the **AWS Disaster Recovery Advisor** — a five-phase workflow that scans your AWS account and produces a complete, source-cited DR package.

## Overview

This power guides you through five phases to build a comprehensive Disaster Recovery strategy:

| Phase | Name | Description | Estimated Time |
|-------|------|-------------|----------------|
| 0 | **Intake** | Generate a fillable `dr-intake.md` form; user provides business context, known RTO/RPO targets, contacts, and services before scanning begins | 5–10 min (user fill time) |
| 1 | **Scan** | Discover all AWS resources across compute, databases, storage, networking, analytics, and AI/ML | 5–15 min (depends on account size) |
| 2 | **Analyze** | Classify workload tiers, evaluate DR gaps against AWS Well-Architected best practices, select DR strategies | 10–20 min |
| 3 | **Plan** | Generate `dr-plan.md` — a complete written DR plan with failover/failback procedures | 5–10 min |
| 4 | **Templates** | Generate CloudFormation YAML templates for DR infrastructure (VPC, DRS staging, Route53, Backup vault, IAM roles) | 5–10 min |
| 5 | **Checklist** | Generate `dr-checklist.md` — an operational checklist for drills, monitoring, failover, and failback | 2–5 min |

**Total estimated time:** 32–70 minutes for a complete assessment (excluding user fill time for intake form).

### Prerequisites

- `uvx` installed (for running the AWS CLI MCP server): `pip install uv`
- AWS credentials available (existing profile or guided IAM user creation in Phase 1)
- Working directory where output files will be written

---

## Definitions

- `$DR_DIR` — The user's working directory where all output files are written:
  - `.secret` — User-created credentials file (git-ignored, never committed)
  - `dr-intake.json` — User-fillable intake form (Phase 0)
  - `dr-context/` — Optional folder for user-provided architecture documents, diagrams, runbooks, and IaC files (read during Phase 0)
  - `dr-state/` — State directory persisting data between phases (git-ignored)
    - `dr-state/core.json` — Metadata, phase completion status, write-permission flag
    - `dr-state/resources.json` — Full resource inventory from Phase 1 (Scan)
    - `dr-state/gaps.json` — Gap analysis results from Phase 2 (Analyze)
    - `dr-state/tiers.json` — Workload tier classifications and strategy selections from Phase 2
    - `dr-state/templates.json` — Generated template manifest from Phase 4
    - `dr-state/intake.json` — Parsed intake form data from Phase 0
    - `dr-state/context.json` — Parsed context from `dr-context/` folder (Phase 0)
  - `dr-plan.md` — Generated DR plan document
  - `dr-checklist.md` — Generated DR operational checklist
  - `cfn-templates/` — Directory containing generated CloudFormation templates

---

## Service Extensibility

The power is designed to adapt to any AWS service — not just the built-in catalog. You can extend the scope in two ways:

### Option A: Explicit Service Callout
At any point before or during Phase 1, tell the agent which additional services to include:
> "Also scan Amazon Connect, AWS IoT Core, and AWS AppSync."

The agent will:
1. Add those services to the scan scope for Phase 1.
2. Research the appropriate read-only CLI commands (`list-*` / `describe-*`) for each service.
3. Apply generic DR gap rules (backup coverage, cross-region replication, IaC coverage) during Phase 2.
4. Include those services in the DR plan, templates (where applicable), and checklist.

### Option B: Bill of Materials (BoM)
Provide a structured list of services — as a message, a pasted table, or an attached document — before starting Phase 1:

```
Service             | Resource Types
--------------------|----------------------------------
Amazon Connect      | Instances, contact flows, queues
AWS IoT Core        | Thing registry, rules, certificates
AWS AppSync         | GraphQL APIs, data sources
AWS Elemental       | MediaLive channels, MediaStore containers
```

The agent will parse the BoM, add all listed services to the scan scope, and adapt the full workflow accordingly.

### How Adaptation Works

| Phase | Adaptation |
|-------|-----------|
| Scan | Discovers resources for all BoM/callout services using CLI commands |
| Analyze | Applies generic gap rules; flags missing backup, CRR, and IaC coverage |
| Plan | Includes BoM services in workload inventory, failover/failback procedures |
| Templates | Generates additional CloudFormation snippets where standard patterns apply |
| Checklist | Adds service-specific checklist items with tier annotations |

**Unknown services:** If the agent cannot determine the correct CLI commands for a service, it will ask the user for clarification rather than silently skipping it.

---

## State Machine

The power maintains state in the `dr-state/` directory. The core file `dr-state/core.json` is read on every invocation to determine the current phase. Bulk data lives in separate files to keep reads fast.

| Current State | Condition | Next Action |
|---------------|-----------|-------------|
| `start` | Always | Load `steering/intake.md` (generate intake form) |
| `intake_done` | `intake` in `completed_phases` | Load `steering/scan.md` (credential setup embedded) |
| `scan_done` | `scan` in `completed_phases` | Load `steering/analyze.md` |
| `analyze_done` | `analyze` in `completed_phases` | Load `steering/plan.md` |
| `plan_done` | `plan` in `completed_phases` | Load `steering/templates.md` |
| `templates_done` | `templates` in `completed_phases` | Load `steering/checklist.md` |
| `checklist_done` | `checklist` in `completed_phases` | Workflow complete — display summary |

**Intake is optional but recommended.** If the user skips intake (e.g., says "skip intake" or "go straight to scan"), proceed directly to Phase 1. Any fields left blank in the intake form are filled in during the interactive phases.

---

## State Validation

On every invocation, before loading any steering file:

1. **Read `dr-state/core.json`** from the working directory.

2. **If the `dr-state/` directory or `core.json` is missing** and a non-Scan phase is requested:
   > "No state directory found. Please run Phase 1 (Scan) first to initialize the assessment."
   > Halt execution.

3. **If `core.json` exists but is empty, corrupted, or missing the `scan` key with valid data:**
   > "The core state file is missing or contains invalid scan data. Please re-run Phase 1 (Scan)."
   > Treat as missing. Halt execution.

4. **If multiple sessions exist** (e.g., multiple `dr-state/` directories in different subdirectories):
   > List each session with its phase completion status. Ask the user to choose which session to continue.

---

## Phase Status Update Protocol

- Update `dr-state/core.json` **in the same turn** as phase completion output.
- Write bulk data (resources, gaps, tiers, templates) to their respective files before updating `core.json`.
- Do NOT read state files before writing during active phase execution.
- Each phase appends its key to `core.json → completed_phases` atomically with setting the phase status.

---

## File Writing Protocol

- Files ≤ 50 lines → single write operation.
- Files > 50 lines → chunked write in blocks of ≤ 50 lines. After each block, verify the last line written matches expected content before proceeding to the next block.
- After any file write, read back the first 5 lines to confirm the file was created correctly.

---

## Re-run Protocol

Any phase may be re-run individually without re-running preceding phases, provided:
- The state file contains valid outputs from all prerequisite phases.
- The user explicitly confirms the re-run (re-running overwrites existing output for that phase).

> "Phase `[name]` has already completed. Re-running will overwrite the existing output. Confirm? [Y/N]"

---

## Error Conditions

| Error Type | Behavior |
|-----------|----------|
| `AccessDenied` / `UnauthorizedOperation` | Log to `skipped_services`, continue scan |
| `ThrottlingException` / `RequestLimitExceeded` | Exponential backoff (2s → 4s → 8s, max 3 retries), then log and continue |
| Region not enabled | Log to `skipped_regions`, continue |
| Invalid JSON response from AWS CLI | Log raw response, continue |
| MCP server unavailable | Display start instructions, **halt phase** |
| State directory missing (non-Scan phase) | `dr-state/` not found | Prompt to run Scan first, halt |
| `core.json` missing or corrupted | Invalid JSON or missing file | Treat as missing, prompt to re-run Scan, halt |
| Missing plan inputs (Failover Procedures) | Identify specific missing fields, halt plan generation |
| Template placeholder found | Fail Templates Phase, report which templates failed |
| Logging failure | Allow operation to proceed without blocking workflow |

---

## Available Steering Files

| File | Phase | Description |
|------|-------|-------------|
| `steering/intake.md` | Phase 0 | Generates `dr-intake.md` — a fillable form for business context, RTO/RPO targets, contacts, and services |
| `steering/scan.md` | Phase 1 | Credential setup and full resource discovery across all AWS service categories |
| `steering/analyze.md` | Phase 2 | Workload tier classification, 14-category gap analysis, and DR strategy selection |
| `steering/plan.md` | Phase 3 | DR plan document generation with failover/failback procedures |
| `steering/templates.md` | Phase 4 | CloudFormation template generation for DR infrastructure |
| `steering/checklist.md` | Phase 5 | DR operational checklist generation with tier annotations |
| `steering/cached-dr-patterns.md` | Reference | Pre-authored CloudFormation YAML snippets and DR pattern references |

---

## Prerequisites Check

Before starting any phase, verify the following prerequisites are met. Run this check on every activation.

### 1. Check for `uvx`

```bash
uvx --version
```

- **If found:** Proceed.
- **If not found:** Display:
  > "`uvx` is required to run the AWS CLI MCP server. Install it with:
  > ```bash
  > pip install uv
  > ```
  > Or follow the full installation guide: https://docs.astral.sh/uv/getting-started/installation/
  > Tell me when `uvx` is installed."
  > **Halt until confirmed.**

### 2. Check for the AWS API MCP server

Attempt to verify the MCP server is registered in Kiro by checking `~/.kiro/settings/mcp.json` for an entry named `awslabs.aws-api-mcp-server`.

**If the entry exists and `disabled` is not `true`:** Proceed.

**If the entry is missing or the user says they don't have it installed:**

Ask the user:
> "The `awslabs.aws-api-mcp-server` MCP server is not configured in Kiro. Would you like me to install it automatically?
> - **[A] Yes, install it** — I'll add it to `~/.kiro/settings/mcp.json`
> - **[B] No, I'll do it manually** — I'll show you the config to add"

**If [A] — Auto-install:**

1. Read `~/.kiro/settings/mcp.json`. If the file does not exist, start with `{ "mcpServers": {} }`.
2. Add the following entry under `mcpServers` (merge, do not overwrite existing entries):
   ```json
   "awslabs.aws-api-mcp-server": {
     "command": "uvx",
     "args": ["awslabs.aws-api-mcp-server@latest"],
     "disabled": false,
     "autoApprove": []
   }
   ```
3. Write the updated file back to `~/.kiro/settings/mcp.json`.
4. Display:
   > "✅ `awslabs.aws-api-mcp-server` added to `~/.kiro/settings/mcp.json`.
   > **Restart Kiro** (or reload MCP servers from the MCP Server view) for the change to take effect.
   > Tell me when Kiro has restarted."
5. **Wait for user confirmation** before proceeding to Phase 0.

**If [B] — Manual install:**

Display:
> "Add the following to `~/.kiro/settings/mcp.json` under `mcpServers`:
> ```json
> "awslabs.aws-api-mcp-server": {
>   "command": "uvx",
>   "args": ["awslabs.aws-api-mcp-server@latest"],
>   "disabled": false,
>   "autoApprove": []
> }
> ```
> Then restart Kiro or reload MCP servers. Tell me when done."
> **Wait for user confirmation** before proceeding.

### 3. Check for AWS CLI

```bash
aws --version
```

- **If found:** Proceed.
- **If not found:** Display:
  > "The AWS CLI is required. Install it from: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html
  > Tell me when it's installed."
  > **Halt until confirmed.**

---

## Credential Setup

Credentials are loaded from a `.secret` file in the user's working directory — never from `mcp.json`. The `mcp.json` contains no credential fields at all.

**`.secret` file format:**
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

**Security rules:**
- `.secret` is listed in `.gitignore` — never committed
- Credential values are never logged, echoed, or written to any state file or output document
- The power validates that credentials have **no write permissions** before scanning begins (using `iam:SimulatePrincipalPolicy`)
- If write permissions are detected, the user is warned and asked to provide read-only credentials

See `steering/scan.md` Phase 1 for the full credential setup and validation flow.

## Sources

All recommendations in this power are grounded in the following official AWS documentation:

| Source | URL |
|--------|-----|
| AWS Disaster Recovery Whitepaper | https://docs.aws.amazon.com/whitepapers/latest/disaster-recovery-workloads-on-aws/ |
| AWS Elastic Disaster Recovery User Guide | https://docs.aws.amazon.com/drs/latest/userguide/what-is-drs.html |
| AWS DRS Best Practices | https://docs.aws.amazon.com/drs/latest/userguide/best_practices_drs.html |
| AWS Well-Architected Reliability Pillar | https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/welcome.html |
| AWS Resilience Hub | https://docs.aws.amazon.com/resilience-hub/latest/userguide/arh-mgmt.html |
| **DR Series Part I: Strategies for Recovery** | https://aws.amazon.com/blogs/architecture/disaster-recovery-dr-architecture-on-aws-part-i-strategies-for-recovery-in-the-cloud/ |
| **DR Series Part II: Backup and Restore** | https://aws.amazon.com/blogs/architecture/disaster-recovery-dr-architecture-on-aws-part-ii-backup-and-restore-with-rapid-recovery/ |
| **DR Series Part III: Pilot Light and Warm Standby** | https://aws.amazon.com/blogs/architecture/disaster-recovery-dr-architecture-on-aws-part-iii-pilot-light-and-warm-standby/ |
| **DR Series Part IV: Multi-site Active/Active** | https://aws.amazon.com/blogs/architecture/disaster-recovery-dr-architecture-on-aws-part-iv-multi-site-active-active/ |
| Cross-Region DR with AWS DRS Guidance | https://docs.aws.amazon.com/guidance/latest/deploying-cross-region-disaster-recovery-with-aws-elastic-disaster-recovery/getting-started.html |
| Cross-AZ DR with AWS DRS Blog | https://aws.amazon.com/blogs/storage/enhance-business-continuity-within-an-availability-zone-using-aws-elastic-disaster-recovery/ |
| Cross-Region DR Blog | https://aws.amazon.com/blogs/storage/cross-region-disaster-recovery-using-aws-elastic-disaster-recovery/ |
| DR Strategy for Databases | https://docs.aws.amazon.com/prescriptive-guidance/latest/strategy-database-disaster-recovery/welcome.html |
| CloudFormation Best Practices | https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/best-practices.html |
| AWS Backup Cross-Region | https://docs.aws.amazon.com/aws-backup/latest/devguide/cross-region-backup.html |
| Route 53 ARC | https://docs.aws.amazon.com/r53recovery/latest/dg/what-is-route53-recovery.html |
| Aurora Global Database | https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-global-database-disaster-recovery.html |
| Aurora Global Database Switchover/Failover | https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-global-database-disaster-recovery.html |
| RDS Cross-Region Read Replicas | https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_ReadRepl.XRgn.html |
| Route53 ARC Aurora execution block | https://docs.aws.amazon.com/r53recovery/latest/dg/aurora-global-database-block.html |
| Route53 ARC RDS Promote Read Replica block | https://docs.aws.amazon.com/r53recovery/latest/dg/rds-promote-read-replica-block.html |
| DynamoDB Global Tables design guide | https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/bp-global-table-design.html |
| RDS DR for standard editions | https://docs.aws.amazon.com/prescriptive-guidance/latest/dr-standard-edition-amazon-rds/design-cross-region-dr.html |
| ElastiCache Global Datastore | https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/Redis-Global-Datastore.html |
| ElastiCache Well-Architected Reliability Pillar | https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/ReliabilityPillar.html |
| MemoryDB Multi-Region | https://docs.aws.amazon.com/memorydb/latest/devguide/multi-Region.monitoring.html |
| ECR Private Image Replication | https://docs.aws.amazon.com/AmazonECR/latest/userguide/replication.html |
| EKS Backup with AWS Backup | https://docs.aws.amazon.com/eks/latest/userguide/integration-backup.html |
| EKS HA and Resiliency | https://docs.aws.amazon.com/prescriptive-guidance/latest/ha-resiliency-amazon-eks-apps/ha-resilience-design.html |
| EC2 Image Builder | https://docs.aws.amazon.com/imagebuilder/latest/userguide/what-is-image-builder.html |
| CloudWatch Synthetics | https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Synthetics_Canaries.html |
| AWS Health Events | https://docs.aws.amazon.com/health/latest/ug/what-is-aws-health.html |
| Redshift DR Blog | https://aws.amazon.com/blogs/big-data/implement-disaster-recovery-with-amazon-redshift/ |
| OpenSearch DR Blog | https://aws.amazon.com/blogs/big-data/achieve-data-resilience-using-amazon-opensearch-service-disaster-recovery-with-snapshot-and-restore/ |
| SageMaker Cross-Region DR | https://aws.amazon.com/blogs/machine-learning/implement-amazon-sagemaker-domain-cross-region-disaster-recovery-using-custom-amazon-efs-instances/ |
| Well-Architected GenAI Lens | https://docs.aws.amazon.com/wellarchitected/latest/generative-ai-lens/genrel05-bp02.html |
| Well-Architected Labs (reference templates) | https://www.wellarchitectedlabs.com/reliability/ |
