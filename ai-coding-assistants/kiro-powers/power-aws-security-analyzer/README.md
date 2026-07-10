# AWS Security Analyzer Power

A Kiro Power that performs comprehensive multi-region AWS security assessments using the [Well-Architected Security MCP Server](https://github.com/awslabs/mcp/tree/main/src/well-architected-security-mcp-server/awslabs/well_architected_security_mcp_server). It scans all 33+ AWS regions, evaluates security posture, and generates detailed markdown reports with findings, compliance status, encryption audits, and prioritized remediation plans.

## What It Does

- Scans all AWS regions to discover active resources
- Checks security service enablement (GuardDuty, Security Hub, Inspector, IAM Access Analyzer)
- Retrieves and categorizes security findings by severity (Critical/High/Medium/Low)
- Audits encryption on storage resources (S3, EBS, RDS, EFS, DynamoDB, ElastiCache)
- Assesses network security (VPCs, security groups, load balancers, API Gateway, CloudFront)
- Scores your account against the Well-Architected Security Pillar
- Generates per-region and consolidated reports with actionable remediation plans

## Prerequisites

| Requirement | Minimum Version | Check Command |
|-------------|-----------------|---------------|
| Python | 3.10+ | `python3 --version` |
| uv | Latest | `uv --version` |
| AWS CLI | Configured credentials | `aws sts get-caller-identity` |

**Install uv** (if not already installed):
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**AWS Permissions Required:** Read-only access to security services including GuardDuty, Security Hub, Inspector, IAM Access Analyzer, EC2, S3, RDS, KMS, VPC, ELB, API Gateway, and CloudFront.

## Installation

This power is installed via the Kiro Powers panel. Once installed, it adds the `well-architected-security-mcp-server` MCP server to your configuration.

### MCP Server Configuration

The power configures the following MCP server in `~/.kiro/settings/mcp.json` under the `powers` section:

```json
{
  "powers": {
    "mcpServers": {
      "power-power-aws-security-analyzer-well-architected-security-mcp-server": {
        "command": "uvx",
        "args": [
          "--from",
          "awslabs.well-architected-security-mcp-server",
          "awslabs.well-architected-security-mcp-server"
        ],
        "env": {
          "AWS_REGION": "us-east-1",
          "FASTMCP_LOG_LEVEL": "ERROR"
        }
      }
    }
  }
}
```

> **Note:** The executable name is `awslabs.well-architected-security-mcp-server` (not `well-architected-security-mcp-server`). If the server fails to connect, verify this in your config.

## Available Tools

| Tool | Purpose |
|------|---------|
| `ListServicesInRegion` | Discover all AWS services with resources in a specific region |
| `CheckSecurityServices` | Verify if GuardDuty, Security Hub, Inspector, and Access Analyzer are enabled |
| `GetSecurityFindings` | Retrieve security findings filtered by severity, resource type, or service |
| `CheckNetworkSecurity` | Assess network resources for secure data-in-transit (ELB, VPC, API Gateway) |
| `CheckStorageEncryption` | Audit encryption status of S3, EBS, RDS, DynamoDB, EFS, ElastiCache |
| `GetStoredSecurityContext` | Access historical security data for trend analysis |

## Usage Examples

### Quick Start — Single Region Assessment

Ask Kiro:
```
Run a security assessment for us-east-1
```

This will:
1. List all services and resources in the region
2. Check security service status
3. Audit storage encryption
4. Assess network security
5. Retrieve security findings
6. Generate a report with findings and remediation steps

### Full Multi-Region Assessment

Ask Kiro:
```
Run a full multi-region security assessment for my account
```

This triggers the comprehensive workflow that:
1. Scans all 33+ AWS regions
2. Identifies active vs inactive regions
3. Generates individual reports for each active region
4. Creates a consolidated cross-region report
5. Provides a prioritized global remediation plan

### Targeted Checks

```
Check if my S3 buckets in eu-west-1 are encrypted
```

```
What security findings do I have in us-west-2?
```

```
Are GuardDuty and Security Hub enabled in all my regions?
```

## Report Structure

Reports are generated as markdown files following this structure:

```
docs/
├── AWS_Security_Assessment_CONSOLIDATED_2026-06-25.md
└── regional-reports/
    ├── AWS_Security_Assessment_us-east-1_2026-06-25.md
    ├── AWS_Security_Assessment_ap-south-1_2026-06-25.md
    └── ...
```

Each regional report includes:
- Executive summary with risk rating
- Resource inventory by service
- Security services status
- Critical and high-priority findings with remediation
- Encryption compliance audit
- Network security assessment
- Well-Architected Security Pillar scores (/10)
- Timeline-based remediation plan (Immediate/Short/Medium/Long-term)

## Power Structure

```
power-aws-security-analyzer/
├── POWER.md                 # Power metadata, onboarding, and best practices
├── README.md                # This file
├── LICENSE                  # MIT No Attribution
├── mcp.json                 # MCP server configuration
└── steering/
    └── multi-region-security-assessment.md   # Full assessment workflow guide
```

## Key Principles

- **Read-only** — This power never modifies resources or auto-remediates
- **Complete coverage** — All 33+ regions are scanned (no silent skipping)
- **Actionable output** — Every finding includes specific remediation steps
- **Prioritized** — Findings are ranked Critical > High > Medium > Low
- **Well-Architected aligned** — Scored against the Security Pillar dimensions (IAM, Detection, Infrastructure, Data Protection, Incident Response)

## Troubleshooting

### Server won't connect
- Verify the executable name in your MCP config is `awslabs.well-architected-security-mcp-server`
- Run `uvx --from awslabs.well-architected-security-mcp-server awslabs.well-architected-security-mcp-server --help` to confirm installation
- Check the MCP Server panel in Kiro for connection status

### AWS credential errors
- Ensure `aws sts get-caller-identity` succeeds
- If using `AWS_PROFILE`, set it in your shell environment before launching Kiro
- Remove `"AWS_PROFILE": "${AWS_PROFILE}"` from the MCP config if you use default credentials

### Region access denied
- Some regions require opt-in (e.g., `ap-east-1`, `af-south-1`, `me-south-1`)
- The assessment will note inaccessible regions and continue with accessible ones

## Authors

Anup Dutta & Writom Guha Roy

## License

MIT No Attribution — see [LICENSE](./LICENSE) for details.
