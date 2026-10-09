# Well-Architected Framework — Performance Efficiency Pillar

**Official docs:** https://docs.aws.amazon.com/wellarchitected/latest/performance-efficiency-pillar/welcome.html

## SoW Review Questions

### Selection
- [ ] Is the compute type appropriate? (EC2 vs Lambda vs Fargate vs EKS)
- [ ] Are instance types right-sized for the workload?
- [ ] Is the database choice appropriate? (relational vs NoSQL vs in-memory)
- [ ] Is storage tiered appropriately? (S3 Standard vs IA vs Glacier)

### Review
- [ ] Is there a plan to benchmark and validate performance?
- [ ] Are there defined performance targets (latency, throughput)?
- [ ] Is there a load testing plan?

### Monitoring
- [ ] Are performance metrics tracked? (CPU, memory, latency, IOPS)
- [ ] Are there alarms for performance degradation?
- [ ] Is there capacity planning for growth?

### Trade-offs
- [ ] Is caching used where appropriate? (ElastiCache, CloudFront, DAX)
- [ ] Is read/write separation used for databases? (read replicas)
- [ ] Is CDN used for static content?
- [ ] Are edge locations leveraged for global users?

## What approvers look for

- **POC:** Instance sizing should match POC scale (not production-grade)
- **Production:** Right-sizing evidence, caching strategy, CDN for web
- **GenAI:** GPU instance justification, model serving optimization (batching, quantization)

## Common gaps in SoWs

- Oversized instances for POC ("future-proofing" that wastes credits)
- No caching layer for read-heavy workloads
- Database choice doesn't match access patterns (relational DB for key-value)
- No CDN for web applications serving static assets
- GPU instances without utilization justification
- No performance targets — can't measure if architecture is adequate
