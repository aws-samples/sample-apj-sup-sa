# Phase 5: DR Checklist Generation

This steering file guides the agent through generating `dr-checklist.md`. Output is written to the user's working directory.

**Sources:**
- [AWS DRS Best Practices](https://docs.aws.amazon.com/drs/latest/userguide/best_practices_drs.html)
- [AWS DR Whitepaper](https://docs.aws.amazon.com/whitepapers/latest/disaster-recovery-workloads-on-aws/)
- [AWS Well-Architected Reliability Pillar](https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/welcome.html)

---

## Phase Gate Check

Before proceeding, read `dr-state/core.json` and verify:

1. `metadata.completed_phases` contains all four: `"scan"`, `"analyze"`, `"plan"`, and `"templates"`.
2. `dr-state/tiers.json` exists and `workload_tiers` is non-empty.

**If any check fails:** Halt and prompt the user to complete the missing phase(s).

---

## Custom Service Checklist Items

If `metadata.custom_services` is non-empty, generate an additional section at the end of `dr-checklist.md`:

```markdown
## Custom Service DR Considerations

The following services were added via Bill of Materials or explicit callout. Checklist items are
generated based on the service type and available DR documentation.

<!-- For each custom service, generate 2-4 checklist items covering: -->
<!-- 1. Backup/snapshot verification -->
<!-- 2. Cross-region replication or data copy -->
<!-- 3. Failover procedure (service-specific) -->
<!-- 4. Post-failover validation -->

<!-- Example for Amazon Connect: -->
- [ ] **Amazon Connect — Instance backup** — Verify contact flows, queues, and routing profiles are exported and stored in S3 with CRR. [Tier 1, 2]
  *Source: [Amazon Connect DR Guide](https://docs.aws.amazon.com/connect/latest/adminguide/disaster-recovery.html)*

- [ ] **Amazon Connect — Cross-region instance** — Verify a standby Connect instance exists in the recovery region with matching configuration. [Tier 1, 2]
  *Source: [Amazon Connect DR Guide](https://docs.aws.amazon.com/connect/latest/adminguide/disaster-recovery.html)*
```

For each custom service:
1. Research the service's DR capabilities and documentation URL.
2. Generate 2–4 checklist items covering backup, replication, failover, and validation.
3. Annotate each item with the applicable tier(s) and a source link.
4. If the service has no known DR documentation, note: `[No official DR guide found — review AWS Well-Architected best practices]`

---

## Checklist Generation

Generate `dr-checklist.md` with the following 6 sections. Every checklist item MUST include:
- A tier annotation in brackets: `[All tiers]`, `[Tier 1, 2]`, `[Tier 1]`, etc.
- A source reference link to the AWS documentation where the best practice originates.

---

### Section 1: Pre-Disaster Planning

```markdown
## Pre-Disaster Planning

- [ ] **Workload tier classification review** — Review and validate tier assignments for all workloads against current business requirements. [All tiers]
  *Source: [AWS DR Whitepaper](https://docs.aws.amazon.com/whitepapers/latest/disaster-recovery-workloads-on-aws/)*

- [ ] **RTO/RPO target documentation** — Confirm RTO/RPO targets are documented, approved by stakeholders, and reflected in the DR plan. [All tiers]
  *Source: [AWS Well-Architected Reliability Pillar](https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/welcome.html)*

- [ ] **CloudFormation template validation** — Run `aws cloudformation validate-template` on all templates in `cfn-templates/`. Resolve any validation errors before a DR event. [All tiers]
  *Source: [AWS CloudFormation Best Practices](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/best-practices.html)*

- [ ] **IAM role and permission verification** — Verify DRS agent installation role and replication server role have correct permissions. Test by running `aws sts assume-role` for each role. [Tier 1, 2]
  *Source: [AWS DRS Best Practices](https://docs.aws.amazon.com/drs/latest/userguide/best_practices_drs.html)*

- [ ] **Network connectivity pre-validation** — Verify source servers can reach DRS endpoints on TCP 443 and TCP 1500. Verify recovery VPC routing tables are correct. [Tier 1, 2]
  *Source: [AWS DRS Best Practices](https://docs.aws.amazon.com/drs/latest/userguide/best_practices_drs.html)*

- [ ] **Recovery VPC pre-deployment verification** — Confirm the recovery VPC CloudFormation stack (`recovery-vpc.yaml`) has been deployed and is in `CREATE_COMPLETE` state. Verify subnet CIDRs do not conflict with primary region or on-premises ranges. [Tier 1, 2]
  *Source: [AWS VPC Documentation](https://docs.aws.amazon.com/vpc/latest/userguide/what-is-amazon-vpc.html)*

- [ ] **ECR cross-region replication verification** — Verify ECR replication rules are configured with the recovery region as a destination. Confirm that all production container images exist in the recovery region ECR repositories. Push a test image and verify it replicates. [Tier 1, 2]
  *Command: `aws ecr describe-registry --region <primary-region>` and `aws ecr describe-repositories --region <recovery-region>`*
  *Source: [ECR Private Image Replication](https://docs.aws.amazon.com/AmazonECR/latest/userguide/replication.html)*

- [ ] **ECS task definition availability in recovery region** — Verify all ECS task definition families used by production services are registered in the recovery region. Confirm the image URIs in task definitions point to the recovery region ECR. [Tier 1, 2]
  *Command: `aws ecs list-task-definitions --region <recovery-region>`*
  *Source: [Amazon ECS Developer Guide](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/)*

- [ ] **EKS cluster readiness in recovery region** — Verify the recovery region EKS cluster is in ACTIVE state. Confirm node groups are running (Warm Standby) or can be quickly added (Pilot Light). Verify all required add-ons (VPC CNI, CoreDNS, AWS Load Balancer Controller) are installed. [Tier 1, 2]
  *Command: `aws eks describe-cluster --name <cluster-name> --region <recovery-region>`*
  *Source: [EKS HA and Resiliency](https://docs.aws.amazon.com/prescriptive-guidance/latest/ha-resiliency-amazon-eks-apps/)*

- [ ] **Kubernetes manifests / Helm charts availability** — Verify Kubernetes manifests or Helm charts are accessible from the recovery region (S3 bucket with CRR, or Git repository). Perform a dry-run apply to confirm manifests are valid for the recovery cluster. [Tier 1, 2]
  *Source: [EKS HA and Resiliency](https://docs.aws.amazon.com/prescriptive-guidance/latest/ha-resiliency-amazon-eks-apps/)*

- [ ] **AWS Backup EKS protection verification** — Verify AWS Backup is protecting the primary EKS cluster and that cross-region copy is configured. Confirm the most recent backup completed successfully. [Tier 1, 2]
  *Command: `aws backup list-protected-resources --region <primary-region>`*
  *Source: [EKS Backup with AWS Backup](https://docs.aws.amazon.com/eks/latest/userguide/integration-backup.html)*

- [ ] **Security group pre-parity audit** — Compare security group rules between primary and recovery regions. Document any intentional differences. Automate parity checks using AWS Config rules or a custom script. [Tier 1, 2]
  *Source: [AWS Well-Architected Security Pillar](https://docs.aws.amazon.com/wellarchitected/latest/security-pillar/welcome.html)*

- [ ] **DNS TTL pre-reduction** — At least 24 hours before a planned drill, reduce TTL on all Route53 records to ≤ 60 seconds. This ensures DNS changes propagate quickly during the drill. Restore TTL after the drill. [Tier 1, 2]
  *Source: [AWS Route53 Developer Guide](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/dns-failover.html)*

- [ ] **Route53 ARC readiness check** — If using Route53 ARC, run readiness checks to verify all resources in the recovery region meet readiness criteria. Review and resolve any readiness check failures before the drill. [Tier 1, 2]
  *Command: `aws route53-recovery-readiness list-readiness-checks --region us-west-2`*
  *Source: [AWS Route53 ARC](https://docs.aws.amazon.com/r53recovery/latest/dg/what-is-route53-recovery.html)*

- [ ] **Recovery runbook documentation** — Ensure the DR plan (`dr-plan.md`) is accessible to all team members and stored in a location reachable during a primary region outage (e.g., S3 with CRR, Confluence, or printed copy). [All tiers]
  *Source: [AWS DR Whitepaper](https://docs.aws.amazon.com/whitepapers/latest/disaster-recovery-workloads-on-aws/)*
```

---

### Section 2: Regular DR Drills

```markdown
## Regular DR Drills

- [ ] **Drill scheduling** — Schedule DR drills at minimum quarterly for Tier 1 and Tier 2 workloads; annually for Tier 3 and Tier 4 workloads. Document drill dates in the DR plan revision history. [Tier 1, 2: quarterly] [Tier 3, 4: annually]
  *Source: [AWS DR Whitepaper](https://docs.aws.amazon.com/whitepapers/latest/disaster-recovery-workloads-on-aws/)*

- [ ] **Drill scope definition** — Define the scope of each drill: which workloads, which failure scenario (region failure, AZ failure, data corruption), and which recovery steps will be tested. [All tiers]
  *Source: [AWS Well-Architected Reliability Pillar](https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/welcome.html)*

- [ ] **Drill instance launch and application validation** — Launch DRS recovery instances (or deploy from CloudFormation) in the recovery region. Validate application health endpoints and critical user journeys. [Tier 1, 2]
  *Source: [AWS DRS Best Practices](https://docs.aws.amazon.com/drs/latest/userguide/best_practices_drs.html)*

- [ ] **ElastiCache Global Datastore failover drill** — During DR drills, test ElastiCache failover by promoting the secondary Global Datastore cluster to primary. Verify application reconnects to the new primary endpoint. Verify `ReplicationLag` was within RPO target at time of failover. [Tier 1, 2]
  *Command: `aws elasticache failover-global-replication-group --global-replication-group-id <id> --primary-region <recovery-region> --primary-replication-group-id <secondary-id>`*
  *Source: [ElastiCache Well-Architected Reliability Pillar](https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/ReliabilityPillar.html)*

- [ ] **Drill cost estimation and budget approval** — Estimate the cost of running recovery instances during the drill (EC2, RDS, data transfer). Obtain budget approval before scheduling. [All tiers]
  *Source: [AWS DR Whitepaper](https://docs.aws.amazon.com/whitepapers/latest/disaster-recovery-workloads-on-aws/)*

- [ ] **Drill results documentation** — Document actual RTO and RPO achieved during the drill. Compare against targets. Record any issues encountered. [All tiers]
  *Source: [AWS Well-Architected Reliability Pillar](https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/welcome.html)*

- [ ] **Recovery plan update based on drill findings** — Update `dr-plan.md` with any procedure changes identified during the drill. Increment the document revision history. [All tiers]
  *Source: [AWS DR Whitepaper](https://docs.aws.amazon.com/whitepapers/latest/disaster-recovery-workloads-on-aws/)*
```

---

### Section 3: Ongoing Monitoring

```markdown
## Ongoing Monitoring

- [ ] **DRS replication health monitoring** — Monitor DRS replication health status for all source servers. Healthy = replication current; Stalled = replication stopped; Lag = replication behind. Alert on Stalled or Lag states. [Tier 1, 2]
  *Source: [AWS DRS Best Practices](https://docs.aws.amazon.com/drs/latest/userguide/best_practices_drs.html)*

- [ ] **CloudWatch alarm for DRS replication lag** — Create a CloudWatch alarm on the `ReplicationLag` metric for each DRS source server. Set threshold based on RPO target (e.g., alert if lag > 15 minutes for Tier 1). [Tier 1, 2]
  *Source: [AWS DRS Best Practices](https://docs.aws.amazon.com/drs/latest/userguide/best_practices_drs.html)*

- [ ] **Route53 health check status review** — Review Route53 health check status weekly. Investigate any health checks in UNHEALTHY state that are not during a known maintenance window. [Tier 1, 2]
  *Source: [AWS Route53 Developer Guide](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/dns-failover.html)*

- [ ] **AWS Backup job success rate monitoring** — Monitor AWS Backup job completion status. Alert on any failed backup jobs. Verify cross-region copy jobs are completing successfully. [All tiers]
  *Source: [AWS Backup Documentation](https://docs.aws.amazon.com/aws-backup/latest/devguide/whatisbackup.html)*

- [ ] **RTO/RPO drift detection via AWS Resilience Hub** — Periodically assess workloads in AWS Resilience Hub to detect configuration drift that may impact RTO/RPO targets. [All tiers]
  *Source: [AWS Resilience Hub](https://docs.aws.amazon.com/resilience-hub/latest/userguide/arh-mgmt.html)*

- [ ] **ElastiCache Global Datastore replication lag monitoring** — Monitor the `ReplicationLag` CloudWatch metric for ElastiCache secondary replication groups. Alert if lag exceeds RPO target. A sustained high lag means the recovery region cache is stale. [Tier 1, 2]
  *Command: `aws cloudwatch get-metric-statistics --namespace AWS/ElastiCache --metric-name ReplicationLag --dimensions Name=ReplicationGroupId,Value=<secondary-id>`*
  *Source: [ElastiCache Well-Architected Reliability Pillar](https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/ReliabilityPillar.html)*

- [ ] **MemoryDB Multi-Region replication lag monitoring** — Monitor the `MultiRegionClusterReplicationLag` CloudWatch metric for MemoryDB Multi-Region clusters. Alert if lag exceeds 5 seconds (adjust threshold to RPO target). [Tier 1, 2]
  *Source: [MemoryDB Multi-Region Monitoring](https://docs.aws.amazon.com/memorydb/latest/devguide/multi-Region.monitoring.html)*

- [ ] **ElastiCache TestFailover pre-drill validation** — Before each DR drill, run `aws elasticache test-failover` against the primary replication group to validate that automatic failover works correctly. [Tier 1, 2]
  *Source: [ElastiCache Well-Architected Reliability Pillar](https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/ReliabilityPillar.html)*

- [ ] **Recovery VPC network drift monitoring** — Use AWS Config rules to detect configuration drift in the recovery region network (security group changes, route table modifications, NACL changes). Alert on any unauthorized changes. [Tier 1, 2]
  *Source: [AWS Config Documentation](https://docs.aws.amazon.com/config/latest/developerguide/evaluate-config.html)*

- [ ] **Direct Connect / VPN health monitoring** — Monitor Direct Connect virtual interface state and VPN tunnel status. Alert if the connection to the recovery region becomes unavailable. [Tier 1, 2]
  *Commands: `aws directconnect describe-virtual-interfaces`, `aws ec2 describe-vpn-connections`*
  *Source: [AWS Direct Connect Resiliency](https://docs.aws.amazon.com/directconnect/latest/UserGuide/resiliency_toolkit.html)*
```

---

### Section 4: Failover Execution

```markdown
## Failover Execution

- [ ] **Disaster declaration criteria** — Confirm the disaster meets declaration criteria (e.g., primary region unavailable for > 15 minutes, CloudWatch alarms in ALARM state, Route53 health checks failing). Obtain authorization from DR Coordinator before declaring. [All tiers]
  *Source: [AWS DR Whitepaper](https://docs.aws.amazon.com/whitepapers/latest/disaster-recovery-workloads-on-aws/)*

- [ ] **Stakeholder notification** — Notify all stakeholders per the Communication Plan in `dr-plan.md`. Update the customer-facing status page within 30 minutes of declaration. [All tiers]
  *Source: [AWS DR Whitepaper](https://docs.aws.amazon.com/whitepapers/latest/disaster-recovery-workloads-on-aws/)*

- [ ] **Recovery VPC network readiness** — Before launching any compute, verify the recovery region network is fully operational:
  - Recovery VPC exists with correct CIDR block
  - Public and private subnets span at least 2 AZs
  - Internet Gateway attached and route tables updated (`0.0.0.0/0 → IGW` for public subnets)
  - NAT Gateway operational and private subnet routes updated (`0.0.0.0/0 → NAT GW`)
  - VPC Flow Logs enabled for post-drill audit
  *Command: `aws ec2 describe-vpcs --region <recovery-region>`*
  *Source: [AWS VPC Documentation](https://docs.aws.amazon.com/vpc/latest/userguide/what-is-amazon-vpc.html)* [Tier 1, 2]

- [ ] **Security group parity verification** — Verify all security groups in the recovery region match the primary region configuration. Check inbound/outbound rules for application, database, and DRS replication tiers. Flag any missing rules before launching instances.
  *Command: `aws ec2 describe-security-groups --region <recovery-region>`* [Tier 1, 2]
  *Source: [AWS DR Whitepaper](https://docs.aws.amazon.com/whitepapers/latest/disaster-recovery-workloads-on-aws/)*

- [ ] **Network ACL verification** — Confirm NACLs in the recovery region allow required traffic flows (application ports, database ports, DRS replication TCP 1500, HTTPS 443). [Tier 1, 2]
  *Command: `aws ec2 describe-network-acls --region <recovery-region>`*
  *Source: [AWS VPC Documentation](https://docs.aws.amazon.com/vpc/latest/userguide/vpc-network-acls.html)*

- [ ] **Transit Gateway / VPC Peering connectivity** — If workloads use Transit Gateway or VPC peering for cross-VPC communication, verify attachments and route table entries are active in the recovery region. Test connectivity between VPCs before launching application tier. [Tier 1, 2]
  *Commands: `aws ec2 describe-transit-gateway-attachments`, `aws ec2 describe-vpc-peering-connections`*
  *Source: [AWS Transit Gateway Documentation](https://docs.aws.amazon.com/vpc/latest/tgw/what-is-transit-gateway.html)*

- [ ] **Direct Connect / VPN failover** — If workloads use Direct Connect or Site-to-Site VPN for on-premises connectivity, verify the recovery region has an equivalent connection or that traffic can route via the public internet as a fallback. Test on-premises to recovery region connectivity. [Tier 1, 2]
  *Commands: `aws directconnect describe-connections`, `aws ec2 describe-vpn-connections --region <recovery-region>`*
  *Source: [AWS Direct Connect Resiliency Recommendations](https://docs.aws.amazon.com/directconnect/latest/UserGuide/resiliency_toolkit.html)*

- [ ] **Load balancer health check configuration** — Verify ALB/NLB in the recovery region has correct target groups, health check paths, and thresholds. Confirm health checks are passing before routing production traffic. [Tier 1, 2]
  *Command: `aws elbv2 describe-target-health --target-group-arn <arn> --region <recovery-region>`*
  *Source: [AWS ELB Documentation](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/target-group-health-checks.html)*

- [ ] **Private DNS / Route53 Resolver** — Verify Route53 private hosted zones are associated with the recovery VPC. Confirm Route53 Resolver inbound/outbound endpoints are operational if used for hybrid DNS resolution. [Tier 1, 2]
  *Command: `aws route53 list-hosted-zones-by-vpc --vpc-id <recovery-vpc-id> --vpc-region <recovery-region>`*
  *Source: [AWS Route53 Resolver Documentation](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resolver.html)*

- [ ] **DNS failover via Route53 / Route53 ARC** — Execute DNS failover using one of:
  - **Automatic**: Route53 failover routing (health check triggers automatically)
  - **Manual (preferred for Tier 1)**: Route53 ARC routing control toggle via data-plane API
  Verify DNS propagation by querying from multiple locations and checking TTL expiry. [Tier 1, 2]
  *Source: [AWS Route53 ARC](https://docs.aws.amazon.com/r53recovery/latest/dg/what-is-route53-recovery.html)*

- [ ] **CloudFront origin failover** — If CloudFront is used, verify origin failover group is configured with the recovery region ALB/S3 as the secondary origin. Confirm CloudFront is serving content from the recovery origin. [Tier 1, 2]
  *Command: `aws cloudfront get-distribution --id <distribution-id>`*
  *Source: [CloudFront Origin Failover](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/high_availability_origin_failover.html)*

- [ ] **API Gateway regional endpoint** — Verify API Gateway REST/HTTP APIs are deployed in the recovery region with correct stage configurations, custom domain mappings, and VPC link configurations (if applicable). [Tier 1, 2]
  *Command: `aws apigateway get-rest-apis --region <recovery-region>`*
  *Source: [AWS API Gateway Documentation](https://docs.aws.amazon.com/apigateway/latest/developerguide/disaster-recovery-resiliency.html)*

- [ ] **Global Accelerator endpoint health** — If Global Accelerator is used, verify endpoint group health in the recovery region. Confirm traffic dial is set correctly for the recovery region. [Tier 1, 2]
  *Command: `aws globalaccelerator list-accelerators --region us-west-2`*
  *Source: [AWS Global Accelerator Documentation](https://docs.aws.amazon.com/global-accelerator/latest/dg/what-is-global-accelerator.html)*

- [ ] **ECS / Fargate service scale-up** — Scale ECS services to production desired count in recovery region. Wait for all tasks to reach RUNNING state before routing traffic. [Tier 1, 2]
  *Command: `aws ecs update-service --desired-count <n> --region <recovery-region>` then `aws ecs wait services-stable`*
  *Source: [Amazon ECS Developer Guide](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/)*

- [ ] **EKS workload restoration** — Apply Kubernetes manifests or Helm charts to recovery cluster. If using AWS Backup, initiate restore job. Wait for all pods to reach Running state across all namespaces. [Tier 1, 2]
  *Command: `kubectl get pods --all-namespaces --context <recovery-cluster-context>`*
  *Source: [EKS Backup with AWS Backup](https://docs.aws.amazon.com/eks/latest/userguide/integration-backup.html)*

- [ ] **DRS recovery instance launch sequence** — In the DRS console, select source servers and initiate recovery. Monitor instance launch progress. Verify instances reach "Running" state before proceeding. [Tier 1, 2]
  *Source: [AWS DRS Best Practices](https://docs.aws.amazon.com/drs/latest/userguide/best_practices_drs.html)*

- [ ] **EC2 termination protection enablement BEFORE traffic re-routing** — Enable termination protection on all Recovery instances BEFORE re-routing traffic to them. Command: `aws ec2 modify-instance-attribute --instance-id <id> --disable-api-termination`. [Tier 1, 2]
  *Source: [AWS DRS Best Practices](https://docs.aws.amazon.com/drs/latest/userguide/best_practices_drs.html)*

- [ ] **Application validation** — Run smoke tests and health checks against the recovery environment. Verify all critical user journeys are functional before declaring recovery complete. [All tiers]
  *Source: [AWS Well-Architected Reliability Pillar](https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/welcome.html)*

- [ ] **Traffic re-routing confirmation** — Confirm traffic is flowing to the recovery region via CloudWatch metrics (request count, error rate, latency). Verify no traffic is still reaching the primary region. [All tiers]
  *Source: [AWS DR Whitepaper](https://docs.aws.amazon.com/whitepapers/latest/disaster-recovery-workloads-on-aws/)*
```

---

### Section 5: Failback Execution

```markdown
## Failback Execution

- [ ] **Primary environment restoration validation** — Verify the primary region infrastructure is healthy. Confirm all CloudFormation stacks are in CREATE_COMPLETE or UPDATE_COMPLETE state. Run health checks against primary endpoints. [All tiers]
  *Source: [AWS DR Whitepaper](https://docs.aws.amazon.com/whitepapers/latest/disaster-recovery-workloads-on-aws/)*

- [ ] **Data resynchronization from recovery to primary** — Verify data written during the DR period has been replicated back to the primary region. Check RDS replication lag, S3 CRR status, and DynamoDB Global Tables replication lag. Do NOT failback until data is synchronized. [Tier 1, 2]
  *Source: [AWS DRS Best Practices](https://docs.aws.amazon.com/drs/latest/userguide/best_practices_drs.html)*

- [ ] **Traffic re-routing back to primary** — Update Route53 failover records to restore PRIMARY routing to the primary region. Monitor traffic shift via CloudWatch. [All tiers]
  *Source: [AWS Route53 Developer Guide](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/dns-failover.html)*

- [ ] **DNS TTL restoration** — After traffic has fully shifted back to primary, restore Route53 record TTLs to their normal values (e.g., 300 seconds). [Tier 1, 2]
  *Source: [AWS Route53 Developer Guide](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/dns-failover.html)*

- [ ] **Recovery network cleanup** — After failback, review recovery region network resources for any temporary changes made during the DR event (e.g., security group rule additions, route table modifications). Revert to baseline configuration. [Tier 1, 2]
  *Source: [AWS Well-Architected Security Pillar](https://docs.aws.amazon.com/wellarchitected/latest/security-pillar/welcome.html)*

- [ ] **Recovery environment decommission** — Scale down recovery region Auto Scaling Groups. Stop non-essential EC2 instances. Retain DRS replication — do NOT disconnect source servers (see Security Considerations). [All tiers]
  *Source: [AWS DRS Best Practices](https://docs.aws.amazon.com/drs/latest/userguide/best_practices_drs.html)*

- [ ] **Post-event lessons-learned documentation** — Document the timeline of events, actual RTO/RPO achieved, issues encountered, and improvements identified. Update `dr-plan.md` revision history. Schedule a follow-up DR drill to validate fixes. [All tiers]
  *Source: [AWS Well-Architected Reliability Pillar](https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/welcome.html)*
```

---

### Section 6: Security Considerations

```markdown
## Security Considerations

- [ ] **⚠️ DRS PIT snapshot protection — WARNING: Do NOT use "Disconnect from AWS"** — Never use the "Disconnect from AWS" option in the DRS console for source servers that have active Recovery instances. This action deletes all Point-In-Time snapshots and cannot be undone. Only disconnect after all Recovery instances are terminated and data is fully synchronized. [Tier 1, 2]
  *Source: [AWS DRS Best Practices](https://docs.aws.amazon.com/drs/latest/userguide/best_practices_drs.html)*

- [ ] **IAM agent installation permission review** — Periodically review the `AWSElasticDisasterRecoveryAgentInstallationRole` permissions. Ensure the role follows least-privilege principles and has not accumulated unnecessary permissions. [Tier 1, 2]
  *Source: [AWS DRS Best Practices](https://docs.aws.amazon.com/drs/latest/userguide/best_practices_drs.html)*

- [ ] **Recovery environment access control audit** — After failover, audit IAM roles and security groups in the recovery environment. Ensure only authorized personnel have access. Remove any temporary access granted during the DR event. [All tiers]
  *Source: [AWS Well-Architected Security Pillar](https://docs.aws.amazon.com/wellarchitected/latest/security-pillar/welcome.html)*

- [ ] **Encryption-at-rest verification for replicated data** — Verify that all replicated data (EBS snapshots, S3 objects, RDS snapshots, DRS recovery volumes) is encrypted at rest using KMS keys that are accessible in the recovery region. [All tiers]
  *Source: [AWS DRS Best Practices](https://docs.aws.amazon.com/drs/latest/userguide/best_practices_drs.html)*
```

---

## Attribution Footer Instructions

Include the following footer at the end of `dr-checklist.md`:

```markdown
---

## Attribution

This checklist is grounded in the following AWS documentation:

- [AWS Elastic Disaster Recovery Best Practices](https://docs.aws.amazon.com/drs/latest/userguide/best_practices_drs.html)
- [AWS Disaster Recovery Workloads on AWS Whitepaper](https://docs.aws.amazon.com/whitepapers/latest/disaster-recovery-workloads-on-aws/)
- [AWS Well-Architected Reliability Pillar](https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/welcome.html)
- [AWS Resilience Hub](https://docs.aws.amazon.com/resilience-hub/latest/userguide/arh-mgmt.html)

*Generated by aws-dr-power v1.0.0*
```

---

## File Write Protocol

1. Calculate the total line count of the generated `dr-checklist.md` content.
2. If content ≤ 50 lines: Write in a single operation.
3. If content > 50 lines: Write in chunks of ≤ 50 lines. After each chunk, verify the last line written matches expected content before proceeding.
4. After writing is complete, read back the first 5 lines and last 5 lines to verify the file was written correctly.

---

## Phase Completion

1. Update `dr-state/core.json`:
```json
{
  "phases": {
    "checklist": {
      "status": "completed",
      "output_file": "dr-checklist.md"
    }
  }
}
```
2. Append `"checklist"` to `metadata.completed_phases`.
3. Write `core.json`.

Display the workflow completion summary:

```
🎉 AWS DR Advisor — Workflow Complete!
======================================
All 5 phases completed successfully.

Output Files:
  dr-plan.md          — Disaster Recovery Plan
  dr-checklist.md     — DR Operational Checklist
  cfn-templates/      — CloudFormation Templates
    recovery-vpc.yaml
    backup-vault.yaml
    [additional templates based on your configuration]

State File:
  aws-dr-state.json   — Full assessment data

Next Steps:
  1. Review dr-plan.md with your team and fill in TBD sections (contacts, communication channels)
  2. Deploy cfn-templates/ to your recovery region
  3. Schedule your first DR drill using dr-checklist.md
  4. Import workloads into AWS Resilience Hub for ongoing RTO/RPO monitoring

Thank you for using the AWS Disaster Recovery Advisor!
```
