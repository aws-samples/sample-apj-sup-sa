# Well-Architected Framework — Reliability Pillar

**Official docs:** https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/welcome.html

## SoW Review Questions

### Foundations
- [ ] Are service quotas understood and planned for?
- [ ] Is the network topology resilient (redundant paths, multiple AZs)?
- [ ] Are there sufficient IP addresses in subnets?

### Workload Architecture
- [ ] Is the architecture distributed across multiple Availability Zones?
- [ ] Are components loosely coupled (queues, event-driven)?
- [ ] Are there single points of failure?
- [ ] Is there a service dependency map?

### Change Management
- [ ] Is there auto-scaling for variable load?
- [ ] Are changes deployed progressively (canary, blue/green)?
- [ ] Is there rollback capability?
- [ ] Is there monitoring to detect deployment issues?

### Failure Management
- [ ] Is there a backup strategy (frequency, retention, cross-region)?
- [ ] Is there a disaster recovery plan with defined RTO/RPO?
- [ ] Are failure modes identified and mitigated?
- [ ] Is there chaos engineering or game day testing?

## What approvers look for

- **POC:** Single AZ acceptable, but should acknowledge this as a limitation
- **Production:** Multi-AZ mandatory, auto-scaling expected, backup strategy required
- **Mission-critical:** Multi-region DR, defined RTO/RPO, chaos testing

## Common gaps in SoWs

- Single-AZ deployment proposed for production workloads
- No auto-scaling — fixed instance count regardless of load
- No backup/restore strategy or testing plan
- "High availability" claimed but architecture is single-AZ
- No RTO/RPO defined for production workloads
- Load balancer mentioned but no health check configuration
