# Reference: AWS Startup Security Baseline (SSB)

**Official docs:** https://docs.aws.amazon.com/prescriptive-guidance/latest/aws-startup-security-baseline/introduction.html

The AWS SSB is a set of foundational security controls designed for early-stage
startups. Since most partners requesting funding are working with startups, the
SoW should address (or at minimum not contradict) these baseline controls.

## Why check SSB in a SoW review

- Startups are the primary audience for POC/innovation funding
- The SSB represents the **minimum security bar** AWS expects
- A SoW that violates SSB controls signals the partner may not deliver
  a secure foundation — approvers will flag this
- Addressing SSB in the SoW demonstrates security maturity

## Account Controls (ACCT)

Check that the SoW doesn't introduce practices that violate account-level security:

| Control | Description | SoW Review Check |
|---------|-------------|------------------|
| ACCT.01 | Set account contacts to valid email distribution lists | Does the SoW mention account setup? If so, contacts should be DLs not personal emails |
| ACCT.02 | Restrict use of the root user | ❌ Flag if SoW uses root credentials for deployment or operations |
| ACCT.03 | Configure console access for each user | Flag shared credentials or generic "admin" users |
| ACCT.04 | Assign permissions using least-privilege | Flag `*` resource or `Administrator` policies in the SoW |
| ACCT.05 | Require MFA to log in | Should be mentioned for any console access |
| ACCT.06 | Enforce a password policy | Should be configured if IAM users are created |
| ACCT.07 | Deliver CloudTrail logs to a protected S3 bucket | ❌ Flag if SoW disables or ignores CloudTrail |
| ACCT.08 | Prevent public access to private S3 buckets | ❌ Flag if SoW creates buckets without public access blocks |
| ACCT.09 | Delete unused VPCs, subnets, and security groups | SoW should include cleanup procedures |
| ACCT.10 | Configure AWS Budgets | Important for funded projects — should monitor credit burn |
| ACCT.11 | Enable IAM Access Analyzer | Should be enabled for any production workload |
| ACCT.12 | Resolve Trusted Advisor high-risk items | Should be checked as part of the engagement |
| ACCT.13 | Use short-lived credentials | ❌ Flag if SoW uses long-lived access keys for applications |

## Workload Controls (WKLD)

Check that the proposed architecture addresses workload-level security:

| Control | Description | SoW Review Check |
|---------|-------------|------------------|
| WKLD.01 | Use IAM roles for compute | ❌ Flag if EC2/ECS/Lambda use access keys instead of instance roles |
| WKLD.02 | Restrict credential scope with resource-based policies | Check that IAM policies are scoped to specific resources |
| WKLD.03 | Use ephemeral secrets or secrets management | ❌ Flag hardcoded secrets, env vars with credentials |
| WKLD.04 | Prevent application secrets from being exposed | Should mention Secrets Manager or Parameter Store |
| WKLD.05 | Detect and remediate exposed secrets | Should mention secret scanning (CodeGuru, git-secrets) |
| WKLD.06 | Use Systems Manager instead of SSH/RDP | ❌ Flag if SoW opens port 22/3389 to 0.0.0.0/0 |
| WKLD.07 | Enable CloudTrail data events for sensitive S3 | For S3 buckets containing customer data |
| WKLD.08 | Encrypt EBS volumes | ❌ Flag any unencrypted EBS volumes |
| WKLD.09 | Encrypt RDS databases | ❌ Flag any unencrypted RDS instances |
| WKLD.10 | Deploy private resources into private subnets | ❌ Flag databases, app servers in public subnets |
| WKLD.11 | Restrict network access with security groups | Flag overly permissive rules (0.0.0.0/0 on non-HTTP ports) |
| WKLD.12 | Use VPC endpoints for AWS services | Recommended for private-subnet architectures |
| WKLD.13 | Require HTTPS for public web endpoints | ❌ Flag HTTP-only public endpoints |
| WKLD.14 | Use edge protection for public endpoints | CloudFront + WAF for public-facing apps |
| WKLD.15 | Define security controls in templates (IaC + CI/CD) | Infrastructure should be codified, not manual |

## Priority for SoW review

Not all controls are equally important for a SoW review. Prioritize:

### Critical (❌ Flag immediately if violated)

These are deal-breakers that will likely block funding approval:

1. **ACCT.02** — Root user used for anything operational
2. **ACCT.13** — Long-lived access keys for applications
3. **WKLD.01** — Access keys instead of IAM roles on compute
4. **WKLD.03/04** — Hardcoded secrets
5. **WKLD.08/09** — Unencrypted storage (EBS/RDS)
6. **WKLD.10** — Databases in public subnets
7. **WKLD.13** — HTTP-only public endpoints (no TLS)

### Important (⚠️ Flag as recommendation)

Should be addressed but won't block approval for a POC:

8. **ACCT.05** — MFA not mentioned
9. **ACCT.07** — CloudTrail not mentioned
10. **ACCT.10** — No budget monitoring for credit usage
11. **WKLD.06** — SSH/RDP instead of Systems Manager
12. **WKLD.11** — Overly permissive security groups
13. **WKLD.14** — No WAF/CloudFront for public apps
14. **WKLD.15** — Manual deployment instead of IaC

### Good practice (note if missing for production SoWs)

15. **ACCT.11** — IAM Access Analyzer
16. **WKLD.05** — Secret scanning
17. **WKLD.07** — S3 data events for audit
18. **WKLD.12** — VPC endpoints

## How to assess in the SoW

When reviewing, scan for:

1. **Architecture diagram** — Do private resources sit in private subnets?
2. **IAM section** — Are roles defined? Is least-privilege mentioned?
3. **Data handling** — Is encryption at rest/transit specified?
4. **Access patterns** — Are secrets managed? Is SSH avoided?
5. **Monitoring** — Is CloudTrail/CloudWatch mentioned?
6. **Deployment** — Is IaC used? CI/CD pipeline?
7. **Budget** — Is there cost monitoring for the funded credits?

If the SoW has no security section at all, flag it as a significant gap —
especially for production workloads. A POC can get away with lighter coverage
but should still address the "Critical" controls above.

## GenAI-specific SSB considerations

For GenAI workloads, additional security considerations:

| Concern | SSB Alignment | Check |
|---------|---------------|-------|
| Model data in S3 | WKLD.08, ACCT.08 | Training data and model weights should be encrypted, buckets private |
| Bedrock API access | WKLD.01, WKLD.02 | Should use IAM roles scoped to specific models |
| Prompt injection protection | WKLD.14 (edge protection) | User-facing AI should have input validation / Bedrock Guardrails |
| PII in training data | WKLD.07 | Audit logging for access to sensitive data |
| Model endpoints | WKLD.10, WKLD.11 | Inference endpoints should be in private subnets with restricted SGs |
| Fine-tuning data | WKLD.03, WKLD.04 | Training datasets may contain secrets — scan before use |
