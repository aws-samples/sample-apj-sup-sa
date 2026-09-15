# Well-Architected Framework — Security Pillar

**Official docs:** https://docs.aws.amazon.com/wellarchitected/latest/security-pillar/welcome.html

## SoW Review Questions

### Identity and Access Management
- [ ] Are IAM roles defined (not root or shared credentials)?
- [ ] Is least-privilege access mentioned?
- [ ] Is MFA required for console access?
- [ ] Are service-linked roles used where appropriate?

### Detection
- [ ] Is CloudTrail enabled for audit logging?
- [ ] Is GuardDuty or Security Hub mentioned?
- [ ] Are VPC Flow Logs configured?
- [ ] Is there a plan for security event alerting?

### Infrastructure Protection
- [ ] Are workloads in private subnets where appropriate?
- [ ] Are security groups and NACLs defined?
- [ ] Is WAF mentioned for public-facing applications?
- [ ] Is there network segmentation (separate VPCs or subnets for tiers)?

### Data Protection
- [ ] Is encryption at rest configured (KMS, S3 SSE, EBS encryption)?
- [ ] Is encryption in transit enforced (TLS, HTTPS)?
- [ ] Is there a data classification scheme?
- [ ] Are secrets managed (Secrets Manager, Parameter Store)?

### Incident Response
- [ ] Is there an incident response plan?
- [ ] Are automated remediation actions defined?
- [ ] Is there a forensics/investigation capability?

## What approvers look for

- **POC:** Basic IAM, encryption at rest/transit, private subnets. Minimal acceptable.
- **Production:** Full security stack — GuardDuty, CloudTrail, WAF, KMS, least-privilege IAM, incident response
- **Compliance workloads:** Must address specific frameworks (HIPAA, PCI, SOC2)

## Common gaps in SoWs

- "We'll use IAM" with no detail on role design or least-privilege
- No mention of encryption (especially at rest)
- Public subnets for databases or application servers
- No secrets management — credentials hardcoded or in environment variables
- No audit logging strategy
