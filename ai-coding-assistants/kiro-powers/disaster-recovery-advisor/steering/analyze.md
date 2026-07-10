# Phase 2: DR Gap Analysis

This steering file guides the agent through workload tier classification, gap analysis, and DR strategy selection. Output is written to `aws-dr-state.json` under the `analyze` key.

**Sources:**
- [AWS DR Whitepaper](https://docs.aws.amazon.com/whitepapers/latest/disaster-recovery-workloads-on-aws/)
- [AWS Well-Architected Reliability Pillar](https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/welcome.html)
- [AWS DRS Best Practices](https://docs.aws.amazon.com/drs/latest/userguide/best_practices_drs.html)

---

## Phase Gate Check

Before proceeding, read `dr-state/core.json` and verify:

1. The file exists and contains valid JSON.
2. `metadata.completed_phases` contains `"scan"`.
3. `phases.scan.status` is `"completed"` or `"completed_with_warnings"`.
4. `dr-state/resources.json` exists and contains data for **both** `metadata.primary_region` and `metadata.recovery_region`.

**If the recovery region is missing from resources.json:** Halt and display:
> "The recovery region (`{recovery_region}`) was not scanned. Gap analysis requires both primary and recovery region data. Please re-run Phase 1 (Scan) and ensure the recovery region is included."

**If any other check fails:** Display the specific failure reason and halt.

---

## Context Document Integration

Before running gap evaluation, check if `dr-state/context.json` exists and has `context_flags` or `files_scanned` entries.

If context data is present:
1. Read `context.json` and extract `context_flags` and `architecture_summary`
2. Pre-populate the gap list with any flags from context documents, marked as `"source": "context_document"`:
   ```json
   {
     "gap_id": "GAP-C001",
     "category": "backup_coverage",
     "resource_id": "inferred from context",
     "resource_type": "RDS DBInstance",
     "region": "primary",
     "workload_tier": "pending_classification",
     "finding": "RDS backup retention is 0 (found in Terraform file main.tf)",
     "remediation": "Set backup_retention_period to at least 7 days in Terraform",
     "source": "context_document",
     "context_file": "main.tf"
   }
   ```
3. When the live scan gap rules run, if a context-document gap is confirmed by the live scan, merge them (keep one entry, update `source` to `"both"`)
4. If a context-document gap is NOT confirmed by the live scan (e.g., the IaC was updated since the diagram was made), keep it but add a note: `"note": "Not confirmed by live scan — may be resolved"`
5. Display context flags to the user before starting gap evaluation:
   ```
   Context Document Flags (pre-populated from dr-context/)
   =========================================================
   - RDS single-AZ visible in architecture diagram (architecture-diagram.png)
   - RDS backup retention is 0 in Terraform (main.tf)
   - S3 bucket missing replication in Terraform (main.tf)
   These will be cross-checked against the live AWS scan.
   ```

---

## Cross-Region Comparison Approach

Gap analysis works by comparing what exists in the **primary region** against what exists in the **recovery region**. Every gap rule below follows this pattern:

> **Primary has X → Recovery should also have Y → If Y is absent or misconfigured, it's a gap.**

Read resources from `dr-state/resources.json` using:
- `resources[primary_region]` for primary region state
- `resources[recovery_region]` for recovery region state

This is the only way to produce accurate, actionable gaps rather than theoretical ones.

---

## Custom Service Handling

If `metadata.custom_services` is non-empty, include all resources from `scan.custom_resources` in the analysis alongside built-in services.

For each custom service resource:
- Include it in workload grouping (using the same tag-based grouping rules).
- Apply the **generic gap rules** below — these apply to any AWS service regardless of whether it has a built-in gap rule:

### Generic Gap Rules (applied to all custom services)

| Gap Category | Generic Rule |
|-------------|-------------|
| **Backup Coverage** | Flag any resource with no associated AWS Backup protected resource entry and no service-native snapshot/backup mechanism detected. [All tiers] |
| **Cross-Region Replication** | Flag any stateful resource (databases, storage, queues) with no cross-region replication or copy mechanism detected. [Tier 1, 2] |
| **IaC Coverage** | Flag any resource not associated with a CloudFormation stack. [All tiers] |
| **Health Check** | Flag any internet-facing endpoint resource with no Route53 health check. [Tier 1, 2] |
| **Multi-AZ / High Availability** | Flag any resource that appears to be single-AZ or single-instance with no redundancy configuration. [Tier 1, 2] |

Additionally, apply any **service-specific gap rules** you can infer from the service's nature:
- If the service is a messaging/streaming service (e.g., Amazon MQ, Amazon EventBridge): check for cross-region event bus replication or message retention.
- If the service is a compute service (e.g., AWS Batch, Amazon Lightsail): check for AMI backups and cross-AZ placement.
- If the service is a data/analytics service: check for cross-region data copy or export mechanisms.
- If the service is an AI/ML service: check for model artifact backup and data source replication.

When applying inferred rules, note them in the gap finding as `"inferred_rule": true` so the user knows these are best-effort assessments rather than hardcoded checks.

---

## Workload Grouping

Group all discovered resources into workload groups using application tags. Check tags in this priority order:

1. `aws:cloudformation:stack-name` — highest priority
2. `Application`
3. `app`
4. `Project`

**Grouping rules:**
- Resources sharing the same tag value for the highest-priority matching tag belong to the same workload group.
- Resources with no matching tags from the above list are placed in a default group named `"ungrouped-resources"`.
- Display the workload groups to the user before proceeding to tier classification.

Example output:
```
Workload Groups Identified
==========================
Group Name              | Resource Count | Resource Types
------------------------|----------------|---------------
my-ecommerce-app        | 12             | EC2, RDS, S3, Lambda
data-pipeline           | 5              | Glue, Redshift, S3
ungrouped-resources     | 8              | EC2, EBS, SQS
```

---

## Tier Classification Questionnaire

Check `intake.workload_targets` in the state file first.

- **If a workload has pre-set RTO/RPO from the intake form:** Use those values directly to assign the tier. Display:
  > "Using `dr-intake.json` targets for `[workload]`: RTO `[value]`, RPO `[value]` → **Tier `[n]` [name]**. Confirm or override?"
  Allow the user to override if needed.

- **If no intake data for a workload:** Ask Q1 and Q2 interactively as below.

**Tier Reference Table:**

| Tier | Name | RTO Target | RPO Target |
|------|------|-----------|-----------|
| Tier 1 | Critical | < 1 hour | < 15 minutes |
| Tier 2 | Important | < 4 hours | < 1 hour |
| Tier 3 | Standard | < 24 hours | < 4 hours |
| Tier 4 | Non-Critical | < 72 hours | < 24 hours |

**For each workload group `[name]` without pre-set targets:**

> **Q1: What is the maximum acceptable downtime for `[name]`?**
> - [A] Less than 1 hour → Tier 1 Critical
> - [B] Less than 4 hours → Tier 2 Important
> - [C] Less than 24 hours → Tier 3 Standard
> - [D] Less than 72 hours → Tier 4 Non-Critical

> **Q2: What is the maximum acceptable data loss for `[name]`?**
> - [A] Less than 15 minutes → Tier 1 Critical
> - [B] Less than 1 hour → Tier 2 Important
> - [C] Less than 4 hours → Tier 3 Standard
> - [D] Less than 24 hours → Tier 4 Non-Critical

**Tier assignment:** Use the more restrictive (lower-numbered) tier from Q1 and Q2 answers.

---

## Gap Evaluation

Evaluate each workload group against the following gap categories. All rules compare primary region state against recovery region state from `dr-state/resources.json`.

**Convention:** `primary[service]` = `resources[primary_region][service]`, `recovery[service]` = `resources[recovery_region][service]`

### 1. Backup Coverage — RDS Retention and Live Replication
**Rule:** For each RDS instance in `primary[rds].instances`:
- Flag if `BackupRetentionPeriod < 7` days
- Check if a cross-region read replica exists in `recovery[rds].instances` for this instance
  - **Exception:** RDS Oracle SE2 and SQL Server SE do not support read replicas — check for AWS Backup cross-region copy instead
- If no replica AND no cross-region automated backup in `recovery[rds]` → flag as gap
- If replica exists: check `ReplicaLag` CloudWatch metric — if lag > RPO target → flag as gap

**Applies to:** Tier 1, Tier 2
**Finding template:** "RDS instance `{id}` (`{engine}`) has backup retention of `{n}` days and no live replication to recovery region `{recovery_region}`. For `{engine}`, the recommended pattern is `{recommended_pattern}` (see `cached-dr-patterns.md#database-live-replication-patterns`)."
**Source:** https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_ReadRepl.XRgn.html

### 1a. Aurora Global Database — Switchover/Failover Readiness
**Rule:** For each Aurora cluster in `primary[rds].clusters`:
- Check if an Aurora Global Database exists linking primary and recovery clusters
- If no Global Database: flag as gap (Aurora should use Global Database for Tier 1/2, not just snapshots)
- If Global Database exists: verify a secondary cluster exists in `recovery[rds].clusters`
- Check engine version compatibility between primary and secondary (required for switchover/RPO=0)

**Applies to:** Tier 1, Tier 2
**Finding template:** "Aurora cluster `{id}` has no Aurora Global Database secondary cluster in recovery region `{recovery_region}`. Aurora Global Database enables RPO=0 switchover (planned) and RPO=seconds failover (unplanned)."
**Source:** https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-global-database-disaster-recovery.html

### 2. Backup Coverage — Redshift Cross-Region Snapshot
**Rule:** Flag any Redshift cluster in `primary[redshift].clusters` with no matching entry in `recovery[redshift]` (no cross-region snapshot copy enabled).
**Cross-region check:** Compare `primary[redshift].snapshot_copy_grants` — if empty or destination region ≠ recovery region, flag as gap.
**Applies to:** Tier 1, Tier 2
**Finding template:** "Redshift cluster `{id}` in primary region has no cross-region snapshot copy to recovery region `{recovery_region}`"
**Source:** https://aws.amazon.com/blogs/big-data/implement-disaster-recovery-with-amazon-redshift/

### 3. Cross-AZ Redundancy — RDS Multi-AZ
**Rule:** Flag any RDS instance in `primary[rds].instances` where `MultiAZ == false`.
**Cross-region check:** Also check if a read replica or cluster member exists in `recovery[rds]`.
**Applies to:** Tier 1, Tier 2
**Finding template:** "RDS instance `{id}` is not Multi-AZ in primary region, and no replica found in recovery region"
**Source:** https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Concepts.MultiAZ.html

### 4. Cross-AZ Redundancy — EC2 Single AZ
**Rule:** Flag any workload where all EC2 instances in `primary[ec2].instances` are in the same AZ.
**Cross-region check:** Also flag if no EC2 instances or AMIs exist in `recovery[ec2]` for this workload.
**Applies to:** Tier 1, Tier 2
**Finding template:** "All EC2 instances in workload `{name}` are in a single AZ in primary region, and no recovery region instances or AMIs found"
**Source:** https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/welcome.html

### 5. Cross-Region Replication — S3 CRR
**Rule:** For each S3 bucket in `primary[s3].buckets`:
- Check if `replication_config` is enabled AND destination bucket exists in `recovery[s3].buckets`
- If no matching bucket name found in recovery region, flag as gap
**Applies to:** Tier 1 only
**Finding template:** "S3 bucket `{name}` in primary region has no CRR configured to recovery region `{recovery_region}` (no matching bucket found)"
**Source:** https://docs.aws.amazon.com/AmazonS3/latest/userguide/replication.html

### 6. Cross-Region Replication — OpenSearch
**Rule:** For each OpenSearch domain in `primary[opensearch].domains`:
- Check `recovery[opensearch].domains` for a matching domain name
- Check `primary[opensearch]` inbound/outbound connections for cross-cluster replication
- If neither exists, flag as gap
**Applies to:** Tier 1 only
**Finding template:** "OpenSearch domain `{name}` in primary region has no cross-cluster replication and no matching domain in recovery region"
**Source:** https://aws.amazon.com/blogs/big-data/achieve-data-resilience-using-amazon-opensearch-service-disaster-recovery-with-snapshot-and-restore/

### 7. IaC Coverage — CloudFormation
**Rule:** Flag any resource in `primary[*]` whose ARN/ID does not appear in any `primary[cloudformation].stacks` resource list.
**Cross-region check:** Also flag if the corresponding CloudFormation stack does not exist in `recovery[cloudformation].stacks`.
**Applies to:** All tiers
**Finding template:** "Resource `{id}` is not in any CloudFormation stack in primary region, and no corresponding stack found in recovery region"
**Note:** Resources managed by Terraform/CDK are still flagged — the power can only detect CloudFormation association.
**Source:** https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/best-practices.html

### 8. Health Check Configuration — Load Balancer
**Rule:** For each load balancer in `primary[elb].load_balancers`:
- Check if a Route53 health check in `primary[route53].health_checks` targets this load balancer
- Also check if a corresponding load balancer exists in `recovery[elb].load_balancers`
- If no health check AND no recovery LB, flag as gap
**Applies to:** Tier 1, Tier 2
**Finding template:** "Load balancer `{name}` in primary region has no Route53 health check, and no corresponding load balancer found in recovery region"
**Source:** https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/dns-failover.html

### 9. DNS Failover — Route53 Failover Policy
**Rule:** Check `primary[route53].hosted_zones` for failover routing records (PRIMARY/SECONDARY) or Route53 ARC routing controls in `primary[route53].arc_clusters`.
- If neither exists, flag as gap
**Cross-region check:** Verify the SECONDARY record points to an endpoint in `recovery_region`.
**Applies to:** Tier 1, Tier 2
**Finding template:** "No Route53 failover routing policy or Route53 ARC routing controls found. Recovery region `{recovery_region}` has no DNS failover path configured."
**Source:** https://docs.aws.amazon.com/r53recovery/latest/dg/what-is-route53-recovery.html

### 9a. Detection Automation — EventBridge + CloudWatch
**Rule:** Flag any Tier 1 or Tier 2 workload with no CloudWatch alarms in `primary[cloudwatch].alarms` targeting service API error rates or latency, AND no CloudWatch Synthetics canaries in `primary[cloudwatch].canaries`.
**Cross-region check:** Also flag if no equivalent alarms exist in `recovery[cloudwatch].alarms`.
**Applies to:** Tier 1, Tier 2
**Finding template:** "Workload `{name}` has no automated detection (CloudWatch alarms/canaries) in primary region, and none in recovery region"
**Source:** https://aws.amazon.com/blogs/architecture/disaster-recovery-dr-architecture-on-aws-part-ii-backup-and-restore-with-rapid-recovery/

### 10. Secrets & Key Replication — KMS CMK
**Rule:** For each KMS CMK in `primary[kms].keys` where `KeyManager == "CUSTOMER"`:
- Check if `MultiRegion == true` AND a replica key exists in `recovery[kms].keys`
- If no replica found in recovery region, flag as gap
**Applies to:** Tier 1, Tier 2
**Finding template:** "KMS CMK `{id}` in primary region has no replica in recovery region `{recovery_region}`"
**Source:** https://docs.aws.amazon.com/kms/latest/developerguide/multi-region-keys-overview.html

### 11. Secrets & Key Replication — Secrets Manager
**Rule:** For each secret in `primary[secretsmanager].secrets`:
- Check if `ReplicationStatus` includes recovery region OR a matching secret name exists in `recovery[secretsmanager].secrets`
- If neither, flag as gap
**Applies to:** Tier 1, Tier 2
**Finding template:** "Secrets Manager secret `{name}` in primary region is not replicated to recovery region `{recovery_region}`"
**Source:** https://docs.aws.amazon.com/secretsmanager/latest/userguide/create-manage-multi-region-secrets.html

### 12. AI/ML Model Continuity — SageMaker
**Rule:** For each SageMaker endpoint in `primary[sagemaker].endpoints`:
- Get the model artifact S3 URI from `primary[sagemaker].models`
- Check if the S3 bucket has CRR to recovery region (cross-check with `recovery[s3].buckets`)
- Also check if a corresponding endpoint exists in `recovery[sagemaker].endpoints`
**Applies to:** Tier 1, Tier 2
**Finding template:** "SageMaker endpoint `{name}` model artifact bucket has no CRR to recovery region, and no corresponding endpoint found in recovery region"
**Source:** https://aws.amazon.com/blogs/machine-learning/implement-amazon-sagemaker-domain-cross-region-disaster-recovery-using-custom-amazon-efs-instances/

### 13. AI/ML Data Continuity — Bedrock Knowledge Base
**Rule:** For each Bedrock knowledge base in `primary[bedrock].knowledge_bases`:
- Get the backing S3 bucket ARN from `storageConfiguration`
- Check if that bucket has CRR to recovery region (cross-check with `recovery[s3].buckets`)
**Applies to:** Tier 1 only
**Finding template:** "Bedrock knowledge base `{name}` backing S3 bucket has no CRR to recovery region `{recovery_region}`"
**Source:** https://docs.aws.amazon.com/wellarchitected/latest/generative-ai-lens/genrel05-bp02.html

### 14. Analytics Pipeline Continuity — Glue Catalog
**Rule:** For each Glue database in `primary[glue].databases`:
- Check if a matching database name exists in `recovery[glue].databases`
- If not found, flag as gap
**Applies to:** Tier 1, Tier 2
**Finding template:** "Glue database `{name}` exists in primary region but has no corresponding catalog in recovery region `{recovery_region}`"
**Source:** https://docs.aws.amazon.com/glue/latest/dg/what-is-glue.html

### 15. Network Infrastructure — Recovery VPC
**Rule:** Check `recovery[vpc].vpcs` for at least one VPC with subnets in ≥ 2 AZs.
- If no VPC exists in recovery region, flag as critical gap
- If VPC exists but has only 1 AZ, flag as gap
**Applies to:** Tier 1, Tier 2
**Finding template:** "Recovery region `{recovery_region}` has no VPC (or VPC has only 1 AZ). DR infrastructure cannot be deployed without a recovery VPC."
**Source:** https://docs.aws.amazon.com/vpc/latest/userguide/what-is-amazon-vpc.html

### 16. Network Infrastructure — Security Group Parity
**Rule:** Compare security group names/descriptions between `primary[vpc].security_groups` and `recovery[vpc].security_groups`.
- Flag any security group present in primary but absent in recovery
**Applies to:** Tier 1, Tier 2
**Finding template:** "Security group `{name}` exists in primary region but has no equivalent in recovery region `{recovery_region}`"
**Source:** https://docs.aws.amazon.com/vpc/latest/userguide/vpc-security-groups.html

### 17. Network Infrastructure — Load Balancer in Recovery Region
**Rule:** For each load balancer in `primary[elb].load_balancers` associated with a Tier 1/2 workload:
- Check if a corresponding load balancer exists in `recovery[elb].load_balancers`
- If not found, flag as gap
**Applies to:** Tier 1, Tier 2
**Finding template:** "Load balancer `{name}` exists in primary region but no corresponding load balancer found in recovery region `{recovery_region}`"
**Source:** https://docs.aws.amazon.com/elasticloadbalancing/latest/userguide/what-is-load-balancing.html

### 18. Cache / In-Memory DR — ElastiCache
**Rule:** For each ElastiCache replication group in `primary[elasticache].replication_groups`:
- Check engine: if `Engine == "memcached"` → flag as critical gap (Memcached has no replication or backup — data is ephemeral)
- If `Engine == "redis"` or `Engine == "valkey"`:
  - Check `MultiAZ` — if false, flag as in-region HA gap
  - Check if a Global Datastore secondary exists in `recovery[elasticache].replication_groups`
  - If no Global Datastore and workload is Tier 1/2 → flag as cross-region DR gap
  - Check `SnapshotRetentionLimit` — if 0, flag as backup gap
**Applies to:** Tier 1, Tier 2 (cross-region); All tiers (Multi-AZ, snapshots)
**Finding template:**
- Memcached: "ElastiCache cluster `{id}` uses Memcached engine which has no replication or backup capability. Any data stored is ephemeral and will be lost on node failure."
- No Global Datastore: "ElastiCache replication group `{id}` (Redis/Valkey) has no Global Datastore secondary in recovery region `{recovery_region}`"
- No Multi-AZ: "ElastiCache replication group `{id}` does not have Multi-AZ enabled"
**Source:** https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/ReliabilityPillar.html

### 19. Cache / In-Memory DR — MemoryDB for Redis
**Rule:** For each MemoryDB cluster in `primary[memorydb].clusters` (scan via `aws memorydb describe-clusters`):
- Check if a Multi-Region cluster exists linking primary and recovery regions
- If no Multi-Region cluster for Tier 1/2 → flag as gap
- Check `MultiRegionClusterReplicationLag` CloudWatch metric — if elevated → flag as gap
**Applies to:** Tier 1, Tier 2
**Finding template:** "MemoryDB cluster `{name}` in primary region has no Multi-Region cluster configured for recovery region `{recovery_region}`"
**Source:** https://docs.aws.amazon.com/memorydb/latest/devguide/multi-Region.monitoring.html

### 20. Container Images — ECR Cross-Region Replication
**Rule:** For each ECR repository in `primary[ecr].repositories`:
- Check if ECR replication rules are configured (`aws ecr describe-registry`) with recovery region as a destination
- Check if matching repositories exist in `recovery[ecr].repositories`
- If no replication AND no matching repositories in recovery region → flag as gap
**Applies to:** Tier 1, Tier 2 (any workload using ECS, EKS, or Fargate)
**Finding template:** "ECR repository `{name}` in primary region has no cross-region replication to recovery region `{recovery_region}`. Container images must be available in the recovery region before ECS/EKS services can be launched."
**Source:** https://docs.aws.amazon.com/AmazonECR/latest/userguide/replication.html

### 21. Container Compute — ECS Service in Recovery Region
**Rule:** For each ECS service in `primary[ecs].services`:
- Check if a corresponding ECS cluster exists in `recovery[ecs].clusters`
- Check if the service's task definition family exists in `recovery[ecs].task_definitions`
- If no cluster OR no task definition in recovery region → flag as gap
- If cluster exists but `desiredCount == 0` for all services → note as Pilot Light (acceptable for Tier 2/3, flag for Tier 1)
**Applies to:** Tier 1, Tier 2
**Finding template:** "ECS service `{name}` in primary region has no corresponding cluster or task definition in recovery region `{recovery_region}`"
**Source:** https://docs.aws.amazon.com/AmazonECS/latest/developerguide/

### 22. Container Compute — EKS Cluster in Recovery Region
**Rule:** For each EKS cluster in `primary[eks].clusters`:
- Check if a corresponding EKS cluster exists in `recovery[eks].clusters`
- Check if AWS Backup is protecting the primary EKS cluster (`primary[backup].protected_resources` contains the cluster ARN)
- Check if AWS Backup cross-region copy is configured for EKS backups
- If no recovery cluster AND no backup → flag as critical gap
- If recovery cluster exists but has 0 node groups → note as Pilot Light
**Applies to:** Tier 1, Tier 2
**Finding template:** "EKS cluster `{name}` in primary region has no corresponding cluster in recovery region `{recovery_region}` and no AWS Backup cross-region copy configured"
**Source:** https://docs.aws.amazon.com/eks/latest/userguide/integration-backup.html

### 23. Container Compute — Kubernetes Manifests / Helm Charts Availability
**Rule:** For each EKS cluster in `primary[eks].clusters`:
- Check if an S3 bucket exists in `primary[s3].buckets` with a name pattern suggesting manifest storage (e.g., contains "manifest", "helm", "k8s", "eks")
- If found: check if that bucket has CRR to recovery region
- If no manifest storage bucket found: flag as informational gap (manifests may be in Git — cannot verify via API)
**Applies to:** Tier 1, Tier 2
**Finding template:** "No S3 bucket with CRR found for EKS manifests/Helm charts for cluster `{name}`. Kubernetes manifests must be accessible from the recovery region to redeploy workloads."
**Note:** If manifests are stored in a Git repository (GitHub, CodeCommit, GitLab), this gap may not apply. Include a note in the finding.
**Source:** https://docs.aws.amazon.com/prescriptive-guidance/latest/ha-resiliency-amazon-eks-apps/

---

## DR Strategy Recommendation

For each workload group, present the recommended DR strategy based on the assigned tier.

### Default Strategy Mapping

| Tier | Default Recommendation | Alternate |
|------|----------------------|-----------|
| Tier 1 Critical | Multi-Site Active/Active | Warm Standby |
| Tier 2 Important | Warm Standby | Pilot Light |
| Tier 3 Standard | Pilot Light | Backup & Restore |
| Tier 4 Non-Critical | Backup & Restore | — |

### Cost-Complexity Tradeoff

Present the four-quadrant model from `cached-dr-patterns.md#dr-strategy-cost-complexity-reference-table` for each recommended strategy.

### DRS Applicability Check

If the workload is Tier 1 or Tier 2 and contains EC2 instances:
1. Count total EC2 instances across all Tier 1/2 workloads.
2. If count < 270: Recommend single-account DRS.
3. If count ≥ 270 (within 10% of 300-server limit): Recommend multi-account DRS configuration.
4. If count ≥ 300: Require multi-account or multi-region DRS.

### Strategy-Specific Configuration Details

Present configuration details **only for the selected strategy**:

**Backup & Restore:**
- Backup frequency: aligned to RPO target (e.g., hourly for 4-hour RPO)
- Retention: minimum 7 days for Tier 3, 30 days for Tier 4
- Cross-region copy: AWS Backup cross-region copy rule to DR region
- Infrastructure recovery: CloudFormation/CDK templates deployed in recovery region
- Golden AMIs: use EC2 Image Builder to create and copy AMIs to recovery region (see `cached-dr-patterns.md#golden-ami-pattern`)
- Automation: EventBridge + Lambda + Step Functions to orchestrate detect → restore → integrate → failover (see `cached-dr-patterns.md#disaster-detection-architecture`)
- **Database pattern:** AWS Backup cross-region copy for all database types (snapshots only — no live replication)
- Source: [Part II](https://aws.amazon.com/blogs/architecture/disaster-recovery-dr-architecture-on-aws-part-ii-backup-and-restore-with-rapid-recovery/)

**Pilot Light:**
- Always-on: Aurora Global Database secondary cluster (headless — no instances), RDS cross-region read replica, core VPC networking, DRS replication
- On-failover: deploy EC2 instances from golden AMIs, scale up, redirect traffic
- Use CloudFormation `ActiveOrPassive` parameter pattern with `DesiredCapacity: 0` when passive (see `cached-dr-patterns.md#active-passive-cloudformation-pattern`)
- Failover: update CloudFormation stack parameter to `active` or use Route 53 ARC routing controls
- **Database pattern:** (see `cached-dr-patterns.md#database-live-replication-patterns` decision tree)
  - Aurora: Global Database secondary (headless) → promote via Route53 ARC Aurora execution block
  - RDS MySQL/PostgreSQL/MariaDB: cross-region read replica → promote via Route53 ARC "Promote Read Replica" block
  - DynamoDB: Global Tables (traffic routing handles failover automatically)
- Source: [Part III](https://aws.amazon.com/blogs/architecture/disaster-recovery-dr-architecture-on-aws-part-iii-pilot-light-and-warm-standby/)

**Warm Standby:**
- Minimum standby capacity: at least 1 instance per tier (reduced, not zero)
- Use CloudFormation `ActiveOrPassive` parameter pattern with reduced `DesiredCapacity` when passive
- **Hot Standby** variant: same as Warm Standby but at full production capacity — higher cost, near-zero RTO
- Scale-up trigger: Route 53 health check failure → CloudWatch alarm → Auto Scaling policy
- Failover options:
  - **Automatic**: Route 53 failover routing with health checks (use with caution — false alarms incur downtime)
  - **Manual (recommended)**: Route 53 ARC routing controls (data-plane, more resilient)
- **Database pattern:** (see `cached-dr-patterns.md#database-live-replication-patterns` decision tree)
  - Aurora: Global Database secondary (1 instance running) → **switchover** via Route53 ARC (RPO=0 for planned drills, RPO=seconds for unplanned outage)
  - RDS MySQL/PostgreSQL/MariaDB: cross-region read replica (running) → promote via Route53 ARC
  - DynamoDB: Global Tables MREC (eventual) or MRSC (strong consistency) depending on requirements
  - Monitor `ReplicaLag` CloudWatch metric — alert if lag exceeds RPO target
- Source: [Part III](https://aws.amazon.com/blogs/architecture/disaster-recovery-dr-architecture-on-aws-part-iii-pilot-light-and-warm-standby/)

**Multi-Site Active/Active:**
- Full production capacity in both regions simultaneously
- Traffic routing: Route 53 latency-based (optimize for performance) or geolocation (deterministic, data governance) routing, or Global Accelerator
- **Choose a write pattern based on consistency requirements:**

  | Write Pattern | How It Works | Best For | Services |
  |--------------|-------------|---------|---------|
  | **Read-local/Write-local** | Writes go to local region; replicated to others | Eventual consistency acceptable; write-heavy globally distributed | DynamoDB Global Tables MREC (last-writer-wins) |
  | **Read-local/Write-global** | All writes route to a single global write region | Strong consistency required | Aurora Global Database with write forwarding; ElastiCache Global Datastore; DynamoDB Global Tables MRSC |
  | **Read-local/Write-partitioned** | Each record has a home region based on partition key | Write-heavy with globally distributed users | DynamoDB Global Tables MREC (writes accepted in all regions) |

- Aurora Global Database write forwarding: secondary clusters forward writes to primary over AWS network — see `cached-dr-patterns.md#aurora-global-database-pattern`
- ElastiCache Global Datastore: cross-region session data replication — see `cached-dr-patterns.md#elasticache-global-datastore-pattern`
- DynamoDB Global Tables: always enable PITR on all replicas to protect against accidental deletes that replicate globally
- Failover: re-route traffic away from impacted region; if write-global, promote new global write region via Aurora switchover (RPO=0); if write-partitioned, repartition records to remaining regions
- Source: [Part IV](https://aws.amazon.com/blogs/architecture/disaster-recovery-dr-architecture-on-aws-part-iv-multi-site-active-active/)

### User Override

After presenting the recommendation, ask:
> "Accept the recommended strategy `[strategy]` for workload `[name]`, or choose a different strategy?
> [A] Accept recommendation
> [B] Backup & Restore
> [C] Pilot Light
> [D] Warm Standby
> [E] Multi-Site Active/Active"

Accept any user selection without warnings or blocks. The user's selection is final.

---

## Phase Completion

1. Write tier classifications and strategy selections to `dr-state/tiers.json`:

```json
{
  "workload_tiers": {
    "<workload_name>": {
      "tier": "Tier 1",
      "rto_target": "< 1 hour",
      "rpo_target": "< 15 minutes",
      "resource_count": 12,
      "includes_custom_services": []
    }
  },
  "strategy_selections": {
    "<workload_name>": "Multi-Site Active/Active"
  }
}
```

2. Write gap analysis results to `dr-state/gaps.json`:

```json
{
  "gaps": [
    {
      "gap_id": "GAP-001",
      "category": "backup_coverage",
      "resource_id": "<resource-id>",
      "resource_type": "<type>",
      "region": "<region>",
      "workload_tier": "Tier 1",
      "finding": "<finding text>",
      "remediation": "<remediation text>",
      "source": "<url>",
      "inferred_rule": false
    }
  ],
  "custom_service_gaps": []
}
```

3. Update `dr-state/core.json`:
   - Set `phases.analyze.status = "completed"`
   - Append `"analyze"` to `metadata.completed_phases`
   - Set `phases.analyze.gap_count` to total gap count (lightweight summary only)
   - Write `core.json` **after** both `tiers.json` and `gaps.json` are fully written.

Display a gap summary by category, including custom service gaps separately:

```
Gap Analysis Summary
====================
Category                      | Gap Count
------------------------------|----------
Backup Coverage               | <n>
Cross-AZ Redundancy           | <n>
Cross-Region Replication      | <n>
IaC Coverage                  | <n>
Health Check Configuration    | <n>
DNS Failover                  | <n>
Secrets & Key Replication     | <n>
AI/ML Model Continuity        | <n>
AI/ML Data Continuity         | <n>
Analytics Pipeline Continuity | <n>
Custom Service Gaps           | <n>
Total                         | <n>
```

If custom service gaps were assessed using inferred rules, display a note:
> "⚠️ Custom service gaps marked with `[inferred]` are best-effort assessments based on the service type. Review them carefully and consult the service's DR documentation."

> **Next step:** Run the Plan phase to generate the DR plan document.
