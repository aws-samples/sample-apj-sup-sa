# Well-Architected Framework — Operational Excellence Pillar

**Official docs:** https://docs.aws.amazon.com/wellarchitected/latest/operational-excellence-pillar/welcome.html

## SoW Review Questions

Ask these when reviewing a SoW for operational excellence:

### Organization
- [ ] Is there a defined operating model for the workload?
- [ ] Are team responsibilities and escalation paths documented?
- [ ] Is there a plan for knowledge transfer from partner to customer?

### Prepare
- [ ] Are there runbooks for common operational procedures?
- [ ] Is infrastructure defined as code (CloudFormation, CDK, Terraform)?
- [ ] Is there a deployment pipeline (CI/CD)?
- [ ] Are there pre-deployment testing stages (dev, staging, prod)?

### Operate
- [ ] Is there a monitoring strategy? (CloudWatch, X-Ray, dashboards)
- [ ] Are alerts defined for key metrics?
- [ ] Is there log aggregation and analysis?
- [ ] Are there defined SLAs/SLOs for the workload?

### Evolve
- [ ] Is there a plan for ongoing improvement post-engagement?
- [ ] Are lessons learned captured?
- [ ] Is there a process for incorporating feedback?

## What approvers look for

- **POC:** Minimal — basic monitoring and deployment process sufficient
- **Production:** Full CI/CD, monitoring, alerting, runbooks expected
- **Migration:** Operational parity or improvement vs. on-premises

## Common gaps in SoWs

- No mention of how the customer will operate the workload after handoff
- CI/CD pipeline listed but no detail on environments or testing
- "CloudWatch" mentioned but no specifics on what metrics/alarms
- No incident response or escalation process
