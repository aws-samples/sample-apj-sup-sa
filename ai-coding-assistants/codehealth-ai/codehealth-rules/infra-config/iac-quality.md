# IaC Quality Analysis - Detailed Steps

## Purpose
Analyze Infrastructure-as-Code for security, maintainability, and best practices.

---

## Step 1: Identify IaC Framework

| Framework | Detection | Files |
|-----------|-----------|-------|
| Terraform | `*.tf` files, `.terraform/` | `main.tf`, `variables.tf`, `outputs.tf` |
| AWS CDK | `cdk.json`, `lib/*.ts` | Stack definitions |
| CloudFormation | `template.yaml/json` | Resource definitions |
| Pulumi | `Pulumi.yaml` | Program files |
| Serverless | `serverless.yml` | Service config |
| AWS SAM | `template.yaml` with Transform | SAM resources |

---

## Step 2: Security Checks

### IAM & Permissions
```bash
# Wildcard permissions (overly permissive)
grep -rn '"*"' --include="*.tf" --include="*.yaml" --include="*.json" . | grep -i "action\|resource\|policy"
grep -rn "Action.*\*\|Resource.*\*" --include="*.{tf,yaml,json}" .
```

| Check | Pattern | Severity |
|-------|---------|----------|
| Wildcard actions | `"Action": "*"` | 🔴 Critical |
| Wildcard resources | `"Resource": "*"` | 🟠 High |
| Admin policy attached | `AdministratorAccess` | 🔴 Critical |
| No condition keys | IAM without conditions | 🟡 Medium |

### Network Security
```bash
# Open security groups
grep -rn "0\.0\.0\.0/0\|::/0" --include="*.{tf,yaml,json}" .
```

| Check | Severity |
|-------|----------|
| SSH (22) open to 0.0.0.0/0 | 🔴 Critical |
| All ports open | 🔴 Critical |
| HTTP on non-standard port open | 🟡 Medium |
| Egress unrestricted | 🔵 Info |

### Encryption
| Check | Detection | Severity |
|-------|-----------|----------|
| S3 bucket encryption | Missing `server_side_encryption` | 🟠 High |
| RDS encryption at rest | `storage_encrypted = false` | 🟠 High |
| EBS encryption | Missing encryption config | 🟡 Medium |
| TLS/SSL on load balancer | HTTP listener without redirect | 🟠 High |

---

## Step 3: Best Practice Checks

### Terraform-Specific
| Check | Issue | Severity |
|-------|-------|----------|
| No remote state | Missing `backend` config | 🟠 High |
| No state locking | Backend without locking (e.g., S3 without DynamoDB) | 🟠 High |
| Hardcoded values | Literals instead of variables | 🟡 Medium |
| No module reuse | Copy-paste resource blocks | 🟡 Medium |
| Missing tags | Resources without standard tags | 🟡 Medium |
| No lifecycle policies | Stateful resources without `prevent_destroy` | 🟡 Medium |
| Pinned provider versions | Missing version constraints | 🟡 Medium |

### CDK/CloudFormation-Specific
| Check | Issue | Severity |
|-------|-------|----------|
| Missing RemovalPolicy | Stateful resources with default DELETE | 🟠 High |
| Hardcoded account/region | `123456789:us-east-1` in template | 🟡 Medium |
| No stack outputs | No way to reference resources cross-stack | 🔵 Info |
| Large monolithic stack | >50 resources in single stack | 🟡 Medium |

---

## Step 4: Generate Report

```markdown
## 🏗️ IaC Quality Report

### Summary
| Category | Critical | High | Medium | Info |
|----------|----------|------|--------|------|
| Security | {n} | {n} | {n} | {n} |
| Best Practices | {n} | {n} | {n} | {n} |
| Maintainability | {n} | {n} | {n} | {n} |

### Security Findings
| # | Issue | File | Resource | Severity | Fix |
|---|-------|------|----------|----------|-----|
| 1 | Wildcard IAM | {file} | {resource} | 🔴 | Scope to specific actions |
| 2 | Open SG | {file} | {resource} | 🔴 | Restrict to known IPs |

### Best Practice Violations
| # | Issue | File | Recommendation |
|---|-------|------|---------------|
| 1 | No remote state | main.tf | Add S3 backend with DynamoDB lock |
| 2 | Missing tags | {file} | Add default_tags block |
```

---

## Completion
Output to `dep-analysis-docs/analysis/iac-quality.md`
