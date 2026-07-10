# Phase 3: DR Plan Generation

This steering file guides the agent through generating the `dr-plan.md` document. Output is written to the user's working directory.

**Sources:**
- [AWS DR Whitepaper](https://docs.aws.amazon.com/whitepapers/latest/disaster-recovery-workloads-on-aws/)
- [AWS DRS Best Practices](https://docs.aws.amazon.com/drs/latest/userguide/best_practices_drs.html)
- [AWS Well-Architected Reliability Pillar](https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/welcome.html)
- [AWS Resilience Hub](https://docs.aws.amazon.com/resilience-hub/latest/userguide/arh-mgmt.html)

---

## Phase Gate Check

Before proceeding, read `dr-state/core.json` and verify all of the following:

1. `metadata.completed_phases` contains both `"scan"` and `"analyze"`.
2. `dr-state/resources.json` exists — verify `resources.route53.hosted_zones` is present.
3. `dr-state/resources.json` — verify `resources.rds` is present.
4. `dr-state/resources.json` — verify `resources.ec2.vpcs` is present (required for network readiness step).
5. `dr-state/tiers.json` exists and `workload_tiers` is non-empty.
6. `dr-state/gaps.json` exists (may have empty arrays).

**If any check fails:** Immediately halt all DR plan generation. Report the specific missing data:
> "Cannot generate DR plan. The following required data is missing:
> - `[list each missing file or field]`
> Please re-run the relevant phase(s) before retrying."

Do NOT generate any partial plan content before this check passes.

---

## DR Plan Document Generation

Generate `dr-plan.md` with the following sections, populated from the state file.

### Section 1: Executive Summary

Populate from:
- Total workload count and tier distribution (from `analyze.workload_tiers`)
- Top 5 gaps by severity (from `analyze.gaps`)
- Selected DR strategies per tier (from `analyze.strategy_selections`)

Template:
```
# Disaster Recovery Plan
**Account:** {account_id}
**Primary Region:** {primary_region}
**Generated:** {scan_timestamp}
**Power Version:** aws-dr-power v1.0.0

## 1. Executive Summary

This Disaster Recovery Plan covers {n} workloads across {n} tiers in AWS account {account_id}.
The assessment identified {n} DR gaps across {n} categories.

**Tier Distribution:**
- Tier 1 Critical: {n} workloads
- Tier 2 Important: {n} workloads
- Tier 3 Standard: {n} workloads
- Tier 4 Non-Critical: {n} workloads

**Top Gaps Identified:**
{list top 5 gaps with category and affected resource}

This plan is grounded in the [AWS Disaster Recovery Whitepaper](https://docs.aws.amazon.com/whitepapers/latest/disaster-recovery-workloads-on-aws/)
and [AWS Elastic Disaster Recovery best practices](https://docs.aws.amazon.com/drs/latest/userguide/best_practices_drs.html).
```

### Section 2: Scope and Objectives

List all workload groups in scope, the primary and recovery regions, and the objectives of this DR plan.

### Section 3: Business Impact Analysis Summary

Populate from tier classification questionnaire answers stored in `analyze.workload_tiers`. For each workload, include the Q1 (downtime) and Q2 (data loss) answers that drove the tier assignment.

### Section 4: Workload Inventory and Tier Classification

Generate a table from `scan.resources` **and** `scan.custom_resources`:

```
| Resource ID | Resource Type | Service | Region | Workload Group | Tier | RTO Target | RPO Target |
|-------------|--------------|---------|--------|----------------|------|-----------|-----------|
| ...         | ...          | ...     | ...    | ...            | ...  | ...       | ...       |
```

Custom service resources are included in the same table, with their service name in the "Service" column. Mark custom service rows with `[custom]` in the Service column if they were added via BoM or explicit callout.

### Section 5: RTO/RPO Targets per Tier

Populate from `analyze.workload_tiers`:

| Tier | Workloads | RTO Target | RPO Target | DR Strategy |
|------|-----------|-----------|-----------|-------------|
| Tier 1 Critical | {list} | < 1 hour | < 15 min | {strategy} |
| Tier 2 Important | {list} | < 4 hours | < 1 hour | {strategy} |
| Tier 3 Standard | {list} | < 24 hours | < 4 hours | {strategy} |
| Tier 4 Non-Critical | {list} | < 72 hours | < 24 hours | {strategy} |

### Section 6: DR Strategy per Tier

Populate from `analyze.strategy_selections`. Include **only** the selected strategy details for each tier — do NOT include configuration details for strategies that were not selected.

For each tier, include:
- Strategy name and rationale
- Cost-complexity tradeoff summary (reference `cached-dr-patterns.md#dr-strategy-cost-complexity-reference-table`)
- Key infrastructure components required

### Section 7: Recovery Architecture

Provide a text description of the recovery architecture for each tier. Include:
- Primary region components
- Recovery region components
- Data replication mechanism
- Traffic routing mechanism

### Section 8: Failover Procedures per Tier

For each tier, generate the following 5 required steps. **HALT plan generation if any step cannot be populated from state file data** — report the specific missing fields.

**Step 1: Detection and Declaration**
- Monitoring: CloudWatch alarms for {key metrics from scan.resources.cloudwatch.alarms}
- Health checks: Route53 health checks for {hosted_zones from scan.resources.route53.hosted_zones}
- Declaration criteria: {tier-specific thresholds}
- Stakeholder notification: See Section 11 (Communication Plan)

**Step 2: Network Readiness Verification** *(before any compute or DNS changes)*

Verify the recovery region network is fully operational before launching instances or switching traffic. This step prevents launching compute into a broken network.

- **VPC:** Confirm recovery VPC exists (`aws ec2 describe-vpcs --region <recovery-region>`)
- **Subnets:** Verify public and private subnets span ≥ 2 AZs
- **Route tables:** Confirm public subnets route `0.0.0.0/0 → IGW`; private subnets route `0.0.0.0/0 → NAT GW`
- **NAT Gateway:** Verify NAT Gateway is in `available` state
- **Security groups:** Confirm all required security groups exist with correct inbound/outbound rules (application ports, database ports, DRS TCP 1500, HTTPS 443)
- **NACLs:** Verify NACLs allow required traffic flows
- **Transit Gateway / VPC Peering:** If used, verify attachments are active and route tables include cross-VPC routes
- **Direct Connect / VPN:** Verify on-premises connectivity to recovery region is operational; if not, confirm fallback path via public internet
- **Private DNS:** Confirm Route53 private hosted zones are associated with recovery VPC
- **Load balancers:** Verify ALB/NLB target groups exist and health checks are configured correctly

**If any network check fails:** Resolve before proceeding. Do NOT launch compute into a misconfigured network.

**Step 3: DNS Failover via Route53 / Route53 ARC**
- Hosted zones: {from scan.resources.route53.hosted_zones}
- **Preferred (Tier 1):** Use Route53 ARC routing control toggle (data-plane, more resilient):
  ```bash
  aws route53-recovery-cluster update-routing-control-state \
    --routing-control-arn <primary-control-arn> \
    --routing-control-state Off
  aws route53-recovery-cluster update-routing-control-state \
    --routing-control-arn <recovery-control-arn> \
    --routing-control-state On
  ```
- **Alternative:** Update Route53 failover routing records to point to recovery region
- TTL: Ensure TTL ≤ 60 seconds (should have been pre-reduced before drill)
- Verify DNS propagation: `dig <domain> @8.8.8.8`

**Step 4: Database Promotion/Activation**

Use the appropriate pattern based on the database engine and selected DR strategy (see `cached-dr-patterns.md#database-live-replication-patterns`):

- **Aurora (Pilot Light / Warm Standby):**
  - **Planned failover (drill):** Use Aurora Global Database **switchover** — RPO=0, zero data loss
    ```bash
    aws rds switchover-global-cluster \
      --global-cluster-identifier <global-cluster-id> \
      --target-db-cluster-identifier <secondary-cluster-arn>
    ```
  - **Unplanned failover:** Use Aurora Global Database **failover** — RPO=seconds
    ```bash
    aws rds failover-global-cluster \
      --global-cluster-identifier <global-cluster-id> \
      --target-db-cluster-identifier <secondary-cluster-arn>
    ```
  - **Preferred:** Automate via Route53 ARC Aurora Global Database execution block

- **RDS MySQL / PostgreSQL / MariaDB (Pilot Light / Warm Standby):**
  - Promote cross-region read replica to standalone instance:
    ```bash
    aws rds promote-read-replica \
      --db-instance-identifier <replica-id> \
      --region <recovery-region>
    ```
  - After promotion: replica becomes standalone, replication is broken
  - **Preferred:** Automate via Route53 ARC "Promote Read Replica" execution block
  - Post-failback: re-create cross-region replica in original direction

- **RDS Oracle SE2 / SQL Server SE (Backup & Restore only):**
  - Restore from AWS Backup cross-region snapshot in recovery region
  - No live replication available for these editions

- **DynamoDB Global Tables:**
  - No promotion needed — Global Tables are multi-active
  - Traffic routing (Route53/Global Accelerator) handles failover automatically
  - Verify PITR is enabled on recovery region replica

- **ElastiCache Redis (Global Datastore):**
  - Promote secondary cluster to primary:
    ```bash
    aws elasticache failover-global-replication-group \
      --global-replication-group-id <global-id> \
      --primary-region <recovery-region> \
      --primary-replication-group-id <recovery-replication-group-id>
    ```

- **Redshift:**
  - Restore from cross-region snapshot in recovery region

**Step 5: Application Launch Sequence**

- **EC2 workloads:** Launch EC2 instances from AMIs in recovery region (or DRS recovery instances). Configure Auto Scaling Groups to desired capacity.

- **ECS / Fargate workloads:**
  1. Verify ECR images exist in recovery region ECR repositories
  2. Verify task definitions are registered in recovery region
  3. Scale ECS services to production desired count:
     ```bash
     aws ecs update-service \
       --cluster <cluster-name> \
       --service <service-name> \
       --desired-count <production-count> \
       --region <recovery-region>
     ```
  4. Wait for tasks to reach RUNNING state:
     ```bash
     aws ecs wait services-stable \
       --cluster <cluster-name> \
       --services <service-name> \
       --region <recovery-region>
     ```

- **EKS workloads:**
  1. Verify ECR images exist in recovery region
  2. If Pilot Light (0 nodes): add node group to recovery cluster first
  3. If Warm Standby (reduced nodes): scale node group to production capacity
  4. Apply Kubernetes manifests or Helm charts from S3/Git to recovery cluster:
     ```bash
     kubectl apply -f s3://<manifests-bucket-recovery>/manifests/ \
       --context <recovery-cluster-context>
     ```
  5. If restoring from AWS Backup: initiate restore job for EKS cluster backup
  6. Wait for all pods to reach Running state:
     ```bash
     kubectl get pods --all-namespaces --context <recovery-cluster-context>
     ```

- **Lambda functions:** Deploy Lambda functions (if not already deployed via IaC)

- **App Runner / Elastic Beanstalk:** Deploy from saved configuration in recovery region

**Step 6: Validation**
- Verify application health endpoints return HTTP 200
- Verify database connectivity from application tier
- Verify Route53 DNS resolution points to recovery region
- Verify load balancer target group health checks are passing
- Verify CloudFront is serving from recovery origin (if applicable)
- Run smoke tests for critical user journeys
- Confirm CloudWatch alarms are active in recovery region
- **For custom services:** Verify each custom service listed in `metadata.custom_services` is operational in the recovery region. Include service-specific validation steps based on the service type.

### Section 9: Failback Procedures per Tier

For each tier, include these 4 steps:

**Step 1: Primary Environment Restoration Validation**
- Verify primary region infrastructure is healthy
- Verify data replication has caught up (RPO check)
- Confirm all CloudFormation stacks are in CREATE_COMPLETE or UPDATE_COMPLETE state

**Step 2: Data Resynchronization to Primary**
- RDS: Verify replication lag is within RPO target before failback
- S3: Verify CRR has replicated all objects written during DR period
- DynamoDB: Global Tables automatically sync; verify replication lag
- Redshift: Create snapshot in DR region and restore to primary

**Step 3: Traffic Re-routing Back to Primary**
- Update Route53 failover records to point PRIMARY back to primary region
- Verify DNS propagation (TTL-dependent)
- Monitor traffic shift via CloudWatch metrics

**Step 4: Recovery Environment Deactivation**
- Scale down recovery region Auto Scaling Groups to minimum capacity
- Stop non-essential EC2 instances in recovery region
- Retain DRS replication (do NOT disconnect — see DRS WARNING below)
- Document lessons learned (see Step 5)

**Step 5: Post-Event Lessons Learned**
- Document timeline of events
- Identify gaps in the DR plan that were discovered during the event
- Update this DR plan with findings
- Schedule follow-up DR drill to validate fixes

### Section 10: AWS DRS Integration

**Generate this section ONLY if EC2 instances are present in Tier 1 or Tier 2 workloads.**

#### Agent Installation Prerequisites
- Install the AWS Replication Agent on each source EC2 instance
- Required IAM role: `AWSElasticDisasterRecoveryAgentInstallationRole` (see `cfn-templates/drs-iam-roles.yaml`)
- Network: Source servers must reach DRS endpoints on TCP 443 and TCP 1500

#### Staging Area Network Requirements
- One replication server is provisioned per source server in the staging area
- Staging subnet sizing: See `cached-dr-patterns.md#drs-staging-area-network-pattern` for CIDR sizing table
- Security group: Allow TCP 1500 inbound for replication data transfer

#### 300-Server Limit Guidance
- DRS supports up to 300 source servers per AWS account per region
- Current EC2 instance count: {count from scan.resources.ec2.instances}
- If count ≥ 270: Configure multi-account DRS to distribute replication load
- If count ≥ 300: Multi-account DRS is required

#### ⚠️ WARNING: Disconnect from AWS
> **CRITICAL:** Do NOT use the "Disconnect from AWS" option in the DRS console for any source server that has active Recovery instances. Disconnecting removes the server from DRS management and deletes Point-In-Time snapshots, which cannot be recovered. Only disconnect after confirming all Recovery instances have been terminated and data has been fully synchronized back to the primary environment.

#### EC2 Termination Protection
- Enable EC2 termination protection on all Recovery instances **BEFORE** re-routing traffic to them
- This prevents accidental termination during the active DR period
- Command: `aws ec2 modify-instance-attribute --instance-id <id> --disable-api-termination`

#### Cost Estimation Guidance
DRS costs during active replication:
- DRS service charge: per source server per hour
- Replication EC2 instances: t3.small per source server (staging area)
- EBS snapshot storage: incremental snapshots of source volumes
- Recovery EC2 instances: charged only during actual failover/drill (instance type matches source)

Estimate monthly cost: (source server count × DRS hourly rate × 730) + (EBS snapshot storage × GB rate)

**Source:** [AWS DRS Best Practices](https://docs.aws.amazon.com/drs/latest/userguide/best_practices_drs.html)

### Section 11: Roles and Responsibilities

Check `intake.contacts` in the state file. If contacts were provided in `dr-intake.json`, populate this table directly. Otherwise use `{TBD}` placeholders.

| Role | Name | Contact | Responsibility |
|------|------|---------|---------------|
| DR Coordinator | {from intake or TBD} | {from intake or TBD} | Declares disaster, coordinates response |
| Infrastructure Lead | {from intake or TBD} | {from intake or TBD} | Executes failover procedures |
| Database Lead | {from intake or TBD} | {from intake or TBD} | Manages database promotion/failback |
| Application Lead | {from intake or TBD} | {from intake or TBD} | Validates application health post-failover |
| Security Lead | {from intake or TBD} | {from intake or TBD} | Verifies access controls in recovery environment |
| Communications Lead | {from intake or TBD} | {from intake or TBD} | Manages stakeholder notifications |
| Executive Sponsor | {from intake or TBD} | {from intake or TBD} | Authorizes disaster declaration |

### Section 12: Communication Plan

Check `intake.contacts` for communication channel preferences. If provided in `dr-intake.json`, use them directly.

**Internal Notification:**
- Immediate: DR Coordinator notifies Infrastructure Lead, Database Lead, Application Lead
- Within 15 minutes: Status update to executive team
- Every 30 minutes: Progress updates until recovery complete

**External Notification:**
- Customer-facing status page update within 30 minutes of declaration: {from intake or TBD}
- SLA breach notification per contractual obligations

**Communication Channels:**
- Primary: {from intake.contacts.primary_channel or TBD}
- Backup: {from intake.contacts.backup_channel or TBD}

### Section 13: References

1. [AWS Disaster Recovery Whitepaper](https://docs.aws.amazon.com/whitepapers/latest/disaster-recovery-workloads-on-aws/)
2. [AWS Elastic Disaster Recovery User Guide](https://docs.aws.amazon.com/drs/latest/userguide/what-is-drs.html)
3. [AWS DRS Best Practices](https://docs.aws.amazon.com/drs/latest/userguide/best_practices_drs.html)
4. [AWS Well-Architected Reliability Pillar](https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/welcome.html)
5. [AWS Resilience Hub](https://docs.aws.amazon.com/resilience-hub/latest/userguide/arh-mgmt.html)
6. **Custom service references:** For each service in `metadata.custom_services`, include a link to that service's DR or resilience documentation (e.g., `https://docs.aws.amazon.com/<service>/latest/<guide>/disaster-recovery.html`). Research and include the most relevant DR documentation URL for each custom service.

### Section 14: Document Revision History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | {generation_date} | aws-dr-power v1.0.0 | Initial generation |

---

## DRS Integration Section Instructions

When generating Section 10 (DRS Integration), include all of the following:

1. **Agent installation prerequisites** — IAM role requirements, network connectivity (TCP 443 + TCP 1500)
2. **Staging area network requirements** — one replication server per source server, CIDR sizing from `cached-dr-patterns.md`
3. **300-server limit guidance** — check discovered EC2 count, recommend multi-account if ≥ 270
4. **⚠️ WARNING: Do NOT use "Disconnect from AWS"** for servers with active Recovery instances — this deletes PIT snapshots
5. **EC2 termination protection** — enable BEFORE traffic re-routing
6. **Cost estimation guidance** — DRS charges per server, replication EC2, EBS snapshot storage, recovery EC2 during failover

**Source:** [DRS Best Practices](https://docs.aws.amazon.com/drs/latest/userguide/best_practices_drs.html)

---

## File Write Protocol

1. Calculate the total line count of the generated `dr-plan.md` content.
2. If content ≤ 50 lines: Write in a single operation.
3. If content > 50 lines: Write in chunks of ≤ 50 lines each. After each chunk, verify the last line written matches the expected content before proceeding to the next chunk.
4. After writing is complete, read back the first 10 lines and last 10 lines of `dr-plan.md` to verify the file was written correctly.

---

## Phase Completion

1. Update `dr-state/core.json`:
```json
{
  "phases": {
    "plan": {
      "status": "completed",
      "output_file": "dr-plan.md"
    }
  }
}
```
2. Append `"plan"` to `metadata.completed_phases`.
3. Write `core.json` **in the same operation** as confirming plan generation is complete.

> **Next step:** Run the Templates phase to generate CloudFormation templates for DR infrastructure.
