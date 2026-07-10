---
name: "aws-security-analyzer"
displayName: "AWS Security Analyzer"
description: "Comprehensive multi-region AWS security assessment using the Well-Architected Security MCP Server. Scans all 33+ AWS regions, generates per-region and consolidated security reports with findings, compliance status, encryption audits, and prioritized remediation plans."
keywords: ["security", "aws security", "well-architected", "security assessment", "compliance", "guardduty", "security hub", "inspector", "iam", "encryption", "vulnerability", "security posture", "multi-region", "security audit", "cloud security"]
authors: "Anup Dutta & Writom Guha Roy"
---

# Onboarding

## Step 1: Validate Prerequisites

Before using the AWS Security Analyzer, ensure the following:

- **Python 3.10+**: Required for the MCP server
  - Verify with: `python3 --version`
- **uv (Python package manager)**: Required to run the MCP server
  - Verify with: `uv --version`
  - Install if missing: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **AWS Credentials**: Must have read-only access to security services
  - Verify with: `aws sts get-caller-identity`
  - **CRITICAL**: If AWS credentials are not configured, DO NOT proceed. Ask the user to configure `AWS_PROFILE` or set credentials.

## Step 2: Confirm MCP Server Availability

The power uses the `well-architected-security-mcp-server` MCP server. Confirm it is available by checking the MCP panel or running:

```bash
uvx --from awslabs.well-architected-security-mcp-server well-architected-security-mcp-server --help
```

If not installed, install with:
```bash
uv pip install awslabs.well-architected-security-mcp-server
```

## Step 3: Confirm AWS Account Access

Ask the user:
1. Which AWS account should be assessed? (Get account ID)
2. Which AWS profile to use? (or default)
3. Are all regions opted-in, or should we skip opt-in-only regions that haven't been enabled?

# When to Load Steering Files

- Running a full multi-region security assessment → `multi-region-security-assessment.md`

# Best Practices

## Assessment Workflow

1. **Always scan ALL 33+ regions** — never skip regions without explicit user confirmation
2. **Separate active vs inactive regions** — only generate detailed reports for regions with resources
3. **Generate per-region reports FIRST**, then the consolidated report
4. **Save all reports** in the directory structure defined in the steering file
5. **Never consolidate or skip regions** — each active region gets its own full report

## Security Assessment Principles

- **Read-only operations only** — this power NEVER modifies resources or remediates automatically
- **Complete coverage** — check all security service statuses (GuardDuty, Security Hub, Inspector, IAM Access Analyzer)
- **Prioritized findings** — always categorize by severity (Critical > High > Medium > Low)
- **Actionable remediation** — every finding must include specific remediation steps
- **Cost awareness** — include security service cost analysis and optimization opportunities

## Report Quality Standards

- Use exact resource IDs and names from MCP tool responses
- Include compliance percentages with real numerators/denominators
- Score Well-Architected Security Pillar dimensions on a /10 scale
- Provide timeline-based remediation plans (Immediate/Short/Medium/Long-term)
- Link regional reports from the consolidated report's appendix

## MCP Tools Available

Use these tools from the `well-architected-security-mcp-server`:

| Tool | Purpose |
|------|---------|
| `ListServicesInRegion` | List all AWS services being used in a specific region |
| `CheckSecurityServices` | Verify if selected AWS security services are enabled in the specified region and account |
| `GetSecurityFindings` | Retrieve security findings by severity, resource type, or service |
| `CheckNetworkSecurity` | Check if AWS network resources are configured for secure data-in-transit |
| `GetStoredSecurityContext` | Historical security data for trend analysis |
| `CheckStorageEncryption` | Check if AWS storage resources have encryption enabled |
