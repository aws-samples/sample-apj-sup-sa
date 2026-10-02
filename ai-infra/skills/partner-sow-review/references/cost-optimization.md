# Well-Architected Framework — Cost Optimization Pillar

**Official docs:** https://docs.aws.amazon.com/wellarchitected/latest/cost-optimization-pillar/welcome.html

## SoW Review Questions

### Practice Cloud Financial Management
- [ ] Is there cost ownership and accountability defined?
- [ ] Is there a tagging strategy for cost allocation?
- [ ] Are budgets and alerts configured?

### Expenditure and Usage Awareness
- [ ] Is there visibility into what's being spent and where?
- [ ] Are unused resources identified and removed?
- [ ] Is there a process for regular cost reviews?

### Cost-Effective Resources
- [ ] Is the pricing model appropriate for the duration?
  - POC (weeks): On-Demand
  - Production (months): Savings Plans or Reserved Instances
  - Batch jobs: Spot Instances
- [ ] Are right-sized instances selected (not over-provisioned)?
- [ ] Is serverless used where appropriate (Lambda, Fargate, S3)?
- [ ] Are managed services preferred over self-managed?

### Manage Demand and Supply
- [ ] Is auto-scaling configured to scale DOWN as well as up?
- [ ] Are there scheduled scaling actions for predictable patterns?
- [ ] Are dev/test environments shut down outside business hours?

### Optimize Over Time
- [ ] Is there a plan to review and optimize costs post-launch?
- [ ] Are newer instance types / services considered?
- [ ] Is there a commitment to regular right-sizing reviews?

## What approvers look for

- **POC:** On-Demand pricing only (RIs are misleading for short-term)
- **Production:** Evidence of right-sizing, auto-scaling down, lifecycle policies
- **Cost estimate:** Should be within 20% of comparable real-world deployments

## Common gaps in SoWs

- Using Reserved Instance pricing in a 3-month POC cost estimate
- No auto-scaling DOWN — resources stay at peak 24/7
- Dev/test environments running full production scale
- No tagging strategy — can't track costs back to workload/team
- Oversized instances "to be safe" with no plan to right-size later
- EBS volumes at provisioned IOPS when gp3 would suffice
- No mention of S3 lifecycle policies for growing data
- NAT Gateway costs ignored (significant for private-subnet architectures)

## Key for SoW cost validation

The AWS Calculator estimate is the funding determinant. Verify:
1. Every service uses On-Demand pricing (unless justified)
2. Region matches the architecture
3. Hours/month assumption is correct (730 hrs = 24/7, 160 hrs = business hours)
4. Data transfer is included and realistic
5. Storage growth over the project duration is modeled
