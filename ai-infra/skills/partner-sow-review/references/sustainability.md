# Well-Architected Framework — Sustainability Pillar

**Official docs:** https://docs.aws.amazon.com/wellarchitected/latest/sustainability-pillar/sustainability-pillar.html

## SoW Review Questions

### Region Selection
- [ ] Is the region chosen considering carbon intensity?
- [ ] Are AWS regions with renewable energy commitments preferred?

### Resource Efficiency
- [ ] Are resources right-sized (not over-provisioned)?
- [ ] Is serverless/managed used where possible (higher utilization)?
- [ ] Are Graviton (ARM) instances considered? (better perf/watt)

### Data Management
- [ ] Are S3 lifecycle policies used to move cold data to cheaper/efficient tiers?
- [ ] Is data retention policy defined (not storing indefinitely)?
- [ ] Is unnecessary data duplication avoided?

### Software and Architecture Patterns
- [ ] Are asynchronous patterns used where possible (reduce idle compute)?
- [ ] Is caching used to reduce redundant computation?
- [ ] Are batch processing patterns used for non-real-time workloads?

## What approvers look for

- **POC:** Minimal sustainability requirements — focus on right-sizing
- **Production:** Graviton consideration, lifecycle policies, auto-scaling down
- **Large-scale:** Sustainability metrics, carbon-aware scheduling

## Common gaps in SoWs

- x86 instances when Graviton equivalent exists (and is cheaper)
- No data lifecycle — S3 buckets grow indefinitely
- 24/7 compute for workloads that only run during business hours
- No mention of resource efficiency or right-sizing plan
