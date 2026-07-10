# Optimized AWS Security Assessment Prompt - All Regions

Conduct a comprehensive multi-region AWS security assessment using the Well-Architected Security MCP server. Generate detailed markdown reports following this workflow:

## Phase 1: Discovery & Comprehensive Region Scanning

1. **Scan ALL AWS Regions Explicitly**
- Use ExploreAwsResources tool to discover resources in EACH of the following regions:

**US Regions:**
- us-east-1 (N. Virginia)
- us-east-2 (Ohio)
- us-west-1 (N. California)
- us-west-2 (Oregon)

**Europe Regions:**
- eu-west-1 (Ireland)
- eu-west-2 (London)
- eu-west-3 (Paris)
- eu-central-1 (Frankfurt)
- eu-central-2 (Zurich)
- eu-north-1 (Stockholm)
- eu-south-1 (Milan)
- eu-south-2 (Spain)

**Asia Pacific Regions:**
- ap-south-1 (Mumbai)
- ap-south-2 (Hyderabad)
- ap-northeast-1 (Tokyo)
- ap-northeast-2 (Seoul)
- ap-northeast-3 (Osaka)
- ap-southeast-1 (Singapore)
- ap-southeast-2 (Sydney)
- ap-southeast-3 (Jakarta)
- ap-southeast-4 (Melbourne)
- ap-east-1 (Hong Kong)

**Canada Region:**
- ca-central-1 (Canada Central)
- ca-west-1 (Calgary)

**South America Region:**
- sa-east-1 (São Paulo)

**Middle East Regions:**
- me-south-1 (Bahrain)
- me-central-1 (UAE)

**Africa Region:**
- af-south-1 (Cape Town)

**Israel Region:**
- il-central-1 (Tel Aviv)

2. **Document Findings for Each Region**
- For each region above, check if it contains ANY resources
- Create two lists:
  - **Active Regions**: Regions with resources (conduct full assessment)
  - **Inactive Regions**: Regions with no resources (document in summary only)
- Document resource count and types per region

3. **Verify Region Coverage**
- Confirm all 33+ AWS regions have been scanned
- List any regions that couldn't be accessed (permissions/opt-in required)

## Phase 2: Per-Region Assessment

For EACH ACTIVE region identified in Phase 1, perform complete analysis:

### Core Security Analysis (Per Region)
1. **Security Services**: Check GuardDuty, Security Hub, Inspector, IAM Access Analyzer status and findings
2. **Resource Inventory**: Catalog ALL resources by service (EC2, RDS, S3, Lambda, ECS, EKS, etc.)
3. **Security Findings**: Retrieve and categorize by severity (Critical/High/Medium/Low)
4. **Compliance**: Check against security standards, document violations
5. **Data Protection**: Verify encryption for S3, EBS, RDS, EFS, DynamoDB, Glacier
6. **Network Security**: Review VPC, security groups, NACLs, Flow Logs, Transit Gateway, public exposure
7. **Compute Security**: Assess EC2, Lambda, ECS/EKS, Batch configurations
8. **Well-Architected Eval**: Score against Security Pillar

**Important**: Do not skip any region with resources. Assess ALL active regions individually.

## Phase 3: Report Generation

### Regional Reports Template (Generate ONE report per ACTIVE region):

---

# Security Assessment - [REGION]
**Date:** [Date] | **Account:** [ID] | **Region:** [Code - Full Name]

## Executive Summary
- Security Rating: [Rating]
- Total Resources: [#]
- Critical Findings: [#]
- High Priority: [#]
- Compliance Rate: [%]
- Overall Risk: [Critical/High/Medium/Low]

## 1. Regional Overview
**Resource Distribution:**
- EC2 Instances: [#]
- RDS Databases: [#]
- S3 Buckets: [#]
- Lambda Functions: [#]
- VPCs: [#]
- Load Balancers: [#]
- ECS Clusters: [#]
- EKS Clusters: [#]
- DynamoDB Tables: [#]
- Other Services: [list with counts]

**Security Services Status:**
| Service | Status | Findings | Notes |
|---------|--------|----------|-------|
| GuardDuty | ✅/❌ | [#] | [details] |
| Security Hub | ✅/❌ | [#] | [details] |
| Inspector | ✅/❌ | [#] | [details] |
| Access Analyzer | ✅/❌ | [#] | [details] |

## 2. Critical Findings
### Critical Issues (Immediate Action)
1. **[Finding Title]**
   - Resource: [ID/Name]
   - Service: [Service]
   - Description: [Details]
   - Risk: [Impact]
   - Remediation: [Steps]

### High Priority Issues
[List all high findings]

## 3. Compliance Assessment
- Total Resources: [#]
- Compliant: [#] ([%])
- Non-Compliant: [#] ([%])

**Violations by Standard:**
- CIS AWS Foundations: [list]
- AWS FSB: [list]
- PCI DSS: [list if applicable]

## 4. Data Protection
**Encryption Status:**
- **S3**: [#] total | [#] encrypted ✅ | [#] unencrypted ❌
  - Unencrypted: [list bucket names]
- **EBS**: [#] total | [#] encrypted ✅ | [#] unencrypted ❌
  - Unencrypted: [list volume IDs]
- **RDS**: [#] total | [#] encrypted ✅ | [#] unencrypted ❌
  - Unencrypted: [list DB identifiers]
- **EFS**: [status]
- **DynamoDB**: [status]
- **Glacier**: [status]

## 5. Network Security
- Total VPCs: [#]
- VPC Flow Logs: [#/total enabled]
- Security Groups: [#] total | [#] with 0.0.0.0/0 ⚠️
- **Overly Permissive SGs**: [list]
- **Public Exposure**:
  - EC2 with public IPs: [#]
  - RDS with public access: [#]
  - Other public resources: [list]

## 6. IAM
- IAM Roles: [#]
- IAM Policies: [#]
- Access Analyzer Findings: [#]
- Issues: [list]

## 7. Logging & Monitoring
- CloudTrail: ✅/❌
- CloudWatch Alarms: [#]
- VPC Flow Logs: [#] enabled
- S3 Access Logging: [#] enabled
- LB Logging: [#] enabled

## 8. Compute Security
**EC2:**
- Total: [#] | IMDSv2: [#] | SSM: [#] | Outdated AMIs: [#]

**Lambda:**
- Total: [#] | In VPC: [#] | With env vars: [#]

**Containers:**
- ECS Clusters: [#] | EKS Clusters: [#] | Issues: [list]

## 9. Well-Architected Scores
- IAM: [/10]
- Detection: [/10]
- Infrastructure: [/10]
- Data Protection: [/10]
- Incident Response: [/10]

## 10. Cost Optimization
- Monthly security service costs: $[amount]
- Optimization opportunities: [list]

## 11. Remediation Plan
- 🔴 **Immediate (0-24h)**: [list actions]
- 🟠 **Short-term (1-7d)**: [list actions]
- 🟡 **Medium-term (1-4w)**: [list actions]
- 🟢 **Long-term (1-3m)**: [list actions]

## 12. Regional Recommendations
[Region-specific strategic guidance]

## Appendices
- A: Detailed Findings (complete list)
- B: Resource Inventory (complete)
- C: Compliance Mappings (detailed)

---

**Filename:** `AWS_Security_Assessment_[REGION-CODE]_[YYYY-MM-DD].md`

**Generate this report for EVERY active region - do not consolidate or skip regions.**

---

### Consolidated Multi-Region Report:

---

# Multi-Region Security Assessment - Consolidated Report
**Date:** [Date] | **Account:** [ID]

## Executive Summary
### Global Overview
- **Total Regions Scanned**: [33+]
- **Active Regions** (with resources): [#]
- **Inactive Regions** (no resources): [#]
- **Total Resources Across All Regions**: [#]
- **Total Critical Findings**: [#]
- **Total High Priority**: [#]
- **Average Compliance Rate**: [%]
- **Highest Risk Region**: [region]
- **Best Secured Region**: [region]

### Complete Region Assessment Status
| Region Code | Region Name | Status | Resources | Critical | High | Medium | Low | Compliance % |
|-------------|-------------|--------|-----------|----------|------|--------|-----|--------------|
| us-east-1 | N. Virginia | ✅ Active | [#] | [#] | [#] | [#] | [#] | [%] |
| us-east-2 | Ohio | ✅ Active | [#] | [#] | [#] | [#] | [#] | [%] |
| us-west-1 | N. California | ✅ Active | [#] | [#] | [#] | [#] | [#] | [%] |
| us-west-2 | Oregon | ✅ Active | [#] | [#] | [#] | [#] | [#] | [%] |
| eu-west-1 | Ireland | ✅ Active | [#] | [#] | [#] | [#] | [#] | [%] |
| eu-west-2 | London | ⚪ Inactive | 0 | - | - | - | - | - |
| eu-west-3 | Paris | ✅ Active | [#] | [#] | [#] | [#] | [#] | [%] |
| eu-central-1 | Frankfurt | ✅ Active | [#] | [#] | [#] | [#] | [#] | [%] |
| eu-central-2 | Zurich | ⚪ Inactive | 0 | - | - | - | - | - |
| eu-north-1 | Stockholm | ⚪ Inactive | 0 | - | - | - | - | - |
| eu-south-1 | Milan | ⚪ Inactive | 0 | - | - | - | - | - |
| eu-south-2 | Spain | ⚪ Inactive | 0 | - | - | - | - | - |
| ap-south-1 | Mumbai | ✅ Active | [#] | [#] | [#] | [#] | [#] | [%] |
| ap-south-2 | Hyderabad | ⚪ Inactive | 0 | - | - | - | - | - |
| ap-northeast-1 | Tokyo | ✅ Active | [#] | [#] | [#] | [#] | [#] | [%] |
| ap-northeast-2 | Seoul | ✅ Active | [#] | [#] | [#] | [#] | [#] | [%] |
| ap-northeast-3 | Osaka | ⚪ Inactive | 0 | - | - | - | - | - |
| ap-southeast-1 | Singapore | ✅ Active | [#] | [#] | [#] | [#] | [#] | [%] |
| ap-southeast-2 | Sydney | ✅ Active | [#] | [#] | [#] | [#] | [#] | [%] |
| ap-southeast-3 | Jakarta | ⚪ Inactive | 0 | - | - | - | - | - |
| ap-southeast-4 | Melbourne | ⚪ Inactive | 0 | - | - | - | - | - |
| ap-east-1 | Hong Kong | ⚪ Inactive | 0 | - | - | - | - | - |
| ca-central-1 | Canada Central | ✅ Active | [#] | [#] | [#] | [#] | [#] | [%] |
| ca-west-1 | Calgary | ⚪ Inactive | 0 | - | - | - | - | - |
| sa-east-1 | São Paulo | ✅ Active | [#] | [#] | [#] | [#] | [#] | [%] |
| me-south-1 | Bahrain | ⚪ Inactive | 0 | - | - | - | - | - |
| me-central-1 | UAE | ⚪ Inactive | 0 | - | - | - | - | - |
| af-south-1 | Cape Town | ⚪ Inactive | 0 | - | - | - | - | - |
| il-central-1 | Tel Aviv | ⚪ Inactive | 0 | - | - | - | - | - |
| **TOTAL ACTIVE** | | | **[sum]** | **[sum]** | **[sum]** | **[sum]** | **[sum]** | **[avg]** |

### Regions Requiring Opt-In (if not accessible)
- [List any regions that require opt-in and couldn't be scanned]

## 1. Global Resource Distribution

### Resources by Region (Active Regions Only)
| Region | EC2 | RDS | S3 | Lambda | VPC | ECS | EKS | Other | Total | % of Global |
|--------|-----|-----|----|----|-----|-----|-----|-------|-------|-------------|
| us-east-1 | [#] | [#] | [#] | [#] | [#] | [#] | [#] | [#] | [#] | [%] |
| us-west-2 | [#] | [#] | [#] | [#] | [#] | [#] | [#] | [#] | [#] | [%] |
| eu-west-1 | [#] | [#] | [#] | [#] | [#] | [#] | [#] | [#] | [#] | [%] |
| [continue for all active regions] |

### Resources by Service (Global)
| Service | Total Count | Active Regions | Region Distribution |
|---------|-------------|----------------|---------------------|
| EC2 | [#] | [#] | [list regions] |
| RDS | [#] | [#] | [list regions] |
| S3 | [#] | [#] | [list regions] |
| Lambda | [#] | [#] | [list regions] |
| VPC | [#] | [#] | [list regions] |
| ECS | [#] | [#] | [list regions] |
| EKS | [#] | [#] | [list regions] |
| DynamoDB | [#] | [#] | [list regions] |
| [continue for all services] |

## 2. Security Services Coverage (All Regions)

### Global Security Services Status
| Service | Enabled in Active Regions | Disabled in Active Regions | Not Applicable (Inactive) | Total Findings |
|---------|---------------------------|----------------------------|---------------------------|----------------|
| GuardDuty | [list regions] | [list regions] | [#] inactive | [#] |
| Security Hub | [list regions] | [list regions] | [#] inactive | [#] |
| Inspector | [list regions] | [list regions] | [#] inactive | [#] |
| Access Analyzer | [list regions] | [list regions] | [#] inactive | [#] |

### Critical Coverage Gaps
**Regions with NO security services enabled:**
- [List regions where none of the 4 services are active]

**Regions with partial coverage:**
- [List regions with some but not all services]

## 3. Critical Findings - Global Cross-Region Analysis

### Top 20 Critical Issues (All Active Regions)
1. **[Issue Type]** - Affects [X] regions, [Y] resources
   - Regions: [complete list]
   - Resource Count: [#]
   - Impact: [description]
   - Priority: 🔴 CRITICAL
   - Global Remediation: [steps]

[Continue for top 20]

### Critical Findings by Region (Ranked)
1. **[Region]**: [#] critical findings
   - Top issue: [description]
2. **[Region]**: [#] critical findings
3. **[Region]**: [#] critical findings
[Continue for all active regions]

### Common Security Patterns Across Regions
**Issue patterns found in 5+ regions:**
- **[Pattern Name]**: [X] regions affected
  - Regions: [list]
  - Resources: [#]
  - Root cause: [analysis]
  - Global fix: [recommendation]

## 4. Global Compliance Assessment

### Overall Compliance Metrics (All Active Regions)
- **Total Resources Assessed**: [#]
- **Globally Compliant**: [#] ([%])
- **Globally Non-Compliant**: [#] ([%])

### Compliance by Region
| Region | Total | Compliant | Non-Compliant | % Compliant | Status |
|--------|-------|-----------|---------------|-------------|--------|
| [each active region] |

### Most Common Compliance Violations (Cross-Region)
1. **[Violation Type]** - [X] regions
   - Standard: [CIS/AWS FSB/PCI]
   - Regions: [list]
   - Resources: [#]
   - Fix: [remediation]

## 5. Data Protection - Global Analysis

### Encryption Status (All Active Regions)

#### S3 Buckets
- **Global Total**: [#]
- **Encrypted**: [#] ([%]) ✅
- **Unencrypted**: [#] ([%]) ❌

**Unencrypted by Region:**
| Region | Total Buckets | Unencrypted | Bucket Names |
|--------|---------------|-------------|--------------|
| [each region with unencrypted buckets] |

#### EBS Volumes
- **Global Total**: [#]
- **Encrypted**: [#] ([%]) ✅
- **Unencrypted**: [#] ([%]) ❌

**Unencrypted by Region:**
| Region | Total Volumes | Unencrypted | Volume IDs (attached to) |
|--------|---------------|-------------|--------------------------|
| [each region with unencrypted volumes] |

#### RDS Databases
- **Global Total**: [#]
- **Encrypted**: [#] ([%]) ✅
- **Unencrypted**: [#] ([%]) ❌

**Unencrypted by Region:**
| Region | Total DBs | Unencrypted | DB Identifiers |
|--------|-----------|-------------|----------------|
| [each region with unencrypted DBs] |

#### Other Storage Services
- **EFS**: [global status]
- **DynamoDB**: [global status]
- **Glacier**: [global status]

## 6. Network Security - Global View

### Multi-Region Network Architecture
- **Total VPCs**: [#] across [X] regions
- **VPC Peering**: [#] connections
- **Transit Gateways**: [#] in regions [list]
- **VPN Connections**: [#]
- **Direct Connect**: [#]

### Security Group Analysis (All Active Regions)
- **Total Security Groups**: [#]
- **Overly Permissive (0.0.0.0/0)**: [#]

**Regions with Most Permissive SGs:**
| Region | Total SGs | Permissive | % |
|--------|-----------|------------|---|
| [ranked list] |

### Public Exposure Summary (Global)
- **EC2 with Public IPs**: [#] across [list regions]
- **RDS with Public Access**: [#] across [list regions]
- **S3 Public Buckets**: [#]
- **Other Public Resources**: [list]

## 7. Regional Security Comparison

### Top 5 Best Secured Regions
1. **[Region]** - Score: [#]/100
   - Compliance: [%]
   - Critical: [#]
   - Strengths: [list]

2. **[Region]** - Score: [#]/100
[Continue for top 5]

### Top 5 Regions Requiring Immediate Attention
1. **[Region]** - Risk Level: CRITICAL
   - Compliance: [%]
   - Critical Findings: [#]
   - Key Issues: [list]
   - Urgent Actions: [list]

2. **[Region]** - Risk Level: HIGH
[Continue for top 5]

### Security Maturity by Region
| Region | Security Score | Maturity Level | Trend |
|--------|----------------|----------------|-------|
| [all active regions ranked] | | Advanced/Intermediate/Basic | ⬆️⬇️➡️ |

## 8. Cost Analysis (All Regions)

### Security Service Costs by Region
| Region | GuardDuty | Security Hub | Inspector | Config | CloudTrail | Total |
|--------|-----------|--------------|-----------|--------|------------|-------|
| us-east-1 | $[amt] | $[amt] | $[amt] | $[amt] | $[amt] | $[amt] |
| [each active region] |
| **GLOBAL TOTAL** | **$[sum]** | **$[sum]** | **$[sum]** | **$[sum]** | **$[sum]** | **$[sum]** |

### Monthly Cost Breakdown
- **Annual Projected**: $[amount]
- **Cost per Region (avg)**: $[amount]
- **Most Expensive Region**: [region] - $[amount]
- **Least Expensive Region**: [region] - $[amount]

### Cost Optimization Opportunities
1. **[Opportunity]**
   - Regions: [list]
   - Current Cost: $[amt]
   - Potential Savings: $[amt] ([%])
   - Action: [steps]

## 9. Well-Architected Framework - Global Assessment

### Security Pillar Scores by Region
| Region | IAM | Detection | Infrastructure | Data Protection | Incident Response | Overall |
|--------|-----|-----------|----------------|-----------------|-------------------|---------|
| us-east-1 | [/10] | [/10] | [/10] | [/10] | [/10] | [/10] |
| [all active regions] |
| **GLOBAL AVG** | **[avg]** | **[avg]** | **[avg]** | **[avg]** | **[avg]** | **[avg]** |

### Global Security Posture Gaps
1. **[Gap Category]**
   - Affected Regions: [#]
   - Impact: [description]
   - Recommendation: [fix]

## 10. Cross-Region Security Patterns

### Positive Patterns (Consistently Applied)
- **[Best Practice]**: Applied in [X] regions
  - Regions: [list]
  - Impact: [description]

### Negative Patterns (Recurring Issues)
- **[Security Issue]**: Found in [X] regions
  - Regions: [list]
  - Root Cause: [analysis]
  - Global Fix: [recommendation]

## 11. Global Remediation Strategy

### Phase 1: Immediate (0-7 days) - CRITICAL
#### Global Actions (All Regions)
1. **[Action]**
   - Scope: ALL active regions
   - Priority: 🔴 CRITICAL
   - Resources: [#]
   - Steps: [detailed]
   - Owner: [team]
   - Deadline: [date]

#### Region-Specific Critical
| Region | Action | Resources | Owner | Deadline |
|--------|--------|-----------|-------|----------|
| [critical actions per region] |

### Phase 2: Short-term (1-4 weeks) - HIGH
[Prioritized actions across regions]

### Phase 3: Medium-term (1-3 months) - MEDIUM
[Strategic improvements]

### Phase 4: Long-term (3-6 months) - STRATEGIC
[Architectural improvements and standardization]

## 12. Strategic Recommendations

### Global Security Initiatives
1. **[Initiative Name]**
   - Scope: All [X] active regions
   - Objective: [goal]
   - Impact: [expected improvement]
   - Effort: [High/Medium/Low]
   - Priority: [Critical/High/Medium]
   - Timeline: [duration]
   - Cost: $[estimate]

### Regional Initiatives
**By Region:**
- **[Region]**: [specific recommendations]
- **[Region]**: [specific recommendations]

### Multi-Region Security Architecture
1. **Centralized Security Monitoring**
   - Deploy Security Hub aggregation
   - Central GuardDuty administration
   - Regions: [all active]

2. **Standardized Security Baselines**
   - Apply consistent security group templates
   - Encryption-by-default policies
   - Regions: [all active]

3. **Cross-Region Incident Response**
   - Unified IR playbooks
   - Cross-region failover procedures

## 13. Governance & Compliance

### Security Service Standardization Plan
**Target State:** All active regions should have:
- ✅ GuardDuty enabled
- ✅ Security Hub enabled with AWS FSB
- ✅ Inspector running continuously
- ✅ IAM Access Analyzer active
- ✅ Config rules for compliance
- ✅ CloudTrail logging

**Current Gaps by Region:**
| Region | GuardDuty | Security Hub | Inspector | Access Analyzer | Gap Count |
|--------|-----------|--------------|-----------|-----------------|-----------|
| [regions with gaps] |

### Compliance Framework Recommendations
- **CIS Benchmark**: Deploy across all [X] active regions
- **AWS FSB**: Currently in [#] regions, expand to all
- **PCI DSS**: Required in [list regions]

### Monitoring & Alerting Strategy
**Cross-Region EventBridge Rules:**
- Critical findings → SNS → PagerDuty
- Compliance violations → SNS → Slack
- Cost anomalies → SNS → Email

## 14. Action Items & Ownership

### By Security Team
**Critical (All Regions):**
1. Enable GuardDuty in: [list regions without it]
2. Enable Security Hub in: [list regions without it]
3. Fix critical findings in: [list regions]

**High Priority (Specific Regions):**
- [Region]: [actions]
- [Region]: [actions]

### By DevOps Team
**Infrastructure (Per Region):**
- [Region]: [list actions]
- [Region]: [list actions]

### By Application Teams
**Application Security (Per Region):**
- [Region]: [list actions]
- [Region]: [list actions]

## 15. Follow-up & Continuous Monitoring

### Next Assessment Schedule
- **Full Multi-Region Assessment**: [Date + 90 days]
- **High-Risk Region Deep Dive**: [Date + 30 days]
  - Regions: [list top 5 risk regions]
- **Compliance Check**: [Date + 60 days]

### Interim Checkpoints
- **Week 2**: Verify critical remediations in [list regions]
- **Week 4**: Review high priority fixes in [list regions]
- **Week 8**: Compliance recheck in [list regions]

### Key Metrics to Track (Per Region)
- Critical findings count
- Compliance percentage
- Encryption coverage
- Security service adoption
- Cost trend

### Dashboards to Create
1. **Global Security Posture Dashboard**
   - All regions at a glance
   - Trend over time
2. **Regional Security Scorecards**
   - Individual region deep-dives
3. **Compliance Tracking Dashboard**
   - Per-region compliance status

## 16. Inactive Regions Considerations

### Inactive Regions List
[List all regions with 0 resources]

### Recommendations for Inactive Regions
- **Preventive Controls**: Apply SCPs to prevent unauthorized resource creation
- **Monitoring**: Set up CloudWatch alarms for any resource creation
- **Security Services**: Consider enabling GuardDuty/Security Hub for detection even if unused

## Appendix A: Regional Report Links

### Active Regions (Full Reports Generated)
- [us-east-1 (N. Virginia)](./regional-reports/AWS_Security_Assessment_us-east-1_[DATE].md)
- [us-east-2 (Ohio)](./regional-reports/AWS_Security_Assessment_us-east-2_[DATE].md)
- [us-west-1 (N. California)](./regional-reports/AWS_Security_Assessment_us-west-1_[DATE].md)
- [us-west-2 (Oregon)](./regional-reports/AWS_Security_Assessment_us-west-2_[DATE].md)
- [eu-west-1 (Ireland)](./regional-reports/AWS_Security_Assessment_eu-west-1_[DATE].md)
- [eu-west-3 (Paris)](./regional-reports/AWS_Security_Assessment_eu-west-3_[DATE].md)
- [eu-central-1 (Frankfurt)](./regional-reports/AWS_Security_Assessment_eu-central-1_[DATE].md)
- [ap-south-1 (Mumbai)](./regional-reports/AWS_Security_Assessment_ap-south-1_[DATE].md)
- [ap-northeast-1 (Tokyo)](./regional-reports/AWS_Security_Assessment_ap-northeast-1_[DATE].md)
- [ap-northeast-2 (Seoul)](./regional-reports/AWS_Security_Assessment_ap-northeast-2_[DATE].md)
- [ap-southeast-1 (Singapore)](./regional-reports/AWS_Security_Assessment_ap-southeast-1_[DATE].md)
- [ap-southeast-2 (Sydney)](./regional-reports/AWS_Security_Assessment_ap-southeast-2_[DATE].md)
- [ca-central-1 (Canada)](./regional-reports/AWS_Security_Assessment_ca-central-1_[DATE].md)
- [sa-east-1 (São Paulo)](./regional-reports/AWS_Security_Assessment_sa-east-1_[DATE].md)
- [List all other active regions...]

### Inactive Regions (No Report Generated)
- eu-west-2 (London) - No resources
- eu-central-2 (Zurich) - No resources
- [List all inactive regions...]

## Appendix B: Complete Findings Export (All Regions)

### Critical Findings (Complete List)
[Comprehensive list from all active regions]

### High Priority Findings (Complete List)
[Comprehensive list from all active regions]

### Medium Priority Findings (Complete List)
[Comprehensive list from all active regions]

## Appendix C: Complete Resource Inventory (All Regions)

### By Region
**us-east-1:**
- [Complete resource list]

**us-west-2:**
- [Complete resource list]

[Continue for all active regions]

### By Service (Cross-Region)
**EC2 Instances:**
- us-east-1: [list]
- us-west-2: [list]
[Continue for all services]

## Appendix D: Compliance Mappings (All Regions)

### CIS AWS Foundations Benchmark
[Detailed mappings per region]

### AWS Foundational Security Best Practices
[Detailed mappings per region]

### PCI DSS
[Detailed mappings per region]

## Appendix E: Methodology

### Assessment Scope
- **Total Regions Scanned**: 33+ AWS regions
- **Active Regions Assessed**: [#]
- **Inactive Regions Documented**: [#]
- **Tools Used**: AWS Well-Architected Security MCP Server
- **Assessment Date**: [Date]
- **Duration**: [hours/days]

### MCP Tools Utilized
1. **ExploreAwsResources**: Scanned all 33+ regions
2. **CheckSecurityServices**: Verified in each active region
3. **GetSecurityFindings**: Retrieved from all active regions
4. **GetResourceComplianceStatus**: Checked in all active regions
5. **AnalyzeSecurityPosture**: Performed for each active region

### Limitations
- Regions requiring opt-in: [list if any]
- Services not assessed: [list if any]
- Permissions constraints: [note if any]

---

**Filename:** `AWS_Security_Assessment_CONSOLIDATED_[YYYY-MM-DD].md`

---

### Index Document:

---

# AWS Security Assessment - Complete Report Index
**Assessment Date:** [Date]
**AWS Account:** [Account ID]
**Assessment ID:** [Unique ID]

## Overview
- **Total Regions Scanned**: 33+
- **Active Regions**: [#]
- **Inactive Regions**: [#]
- **Total Resources**: [#]
- **Total Critical Findings**: [#]
- **Overall Compliance**: [%]

## Generated Reports

### 1. Consolidated Multi-Region Report
📊 **Main Report**: `AWS_Security_Assessment_CONSOLIDATED_[DATE].md`
- Covers all active regions with cross-region analysis
- Global security posture and recommendations
- Cost analysis and prioritized remediation

### 2. Regional Detailed Reports

#### Active Regions (Full Assessment)
✅ **US Regions:**
- `AWS_Security_Assessment_us-east-1_[DATE].md` - N. Virginia ([#] resources)
- `AWS_Security_Assessment_us-east-2_[DATE].md` - Ohio ([#] resources)
- `AWS_Security_Assessment_us-west-1_[DATE].md` - N. California ([#] resources)
- `AWS_Security_Assessment_us-west-2_[DATE].md` - Oregon ([#] resources)

✅ **Europe Regions:**
- `AWS_Security_Assessment_eu-west-1_[DATE].md` - Ireland ([#] resources)
- `AWS_Security_Assessment_eu-west-2_[DATE].md` - London ([#] resources)
- `AWS_Security_Assessment_eu-west-3_[DATE].md` - Paris ([#] resources)
- `AWS_Security_Assessment_eu-central-1_[DATE].md` - Frankfurt ([#] resources)
- `AWS_Security_Assessment_eu-central-2_[DATE].md` - Zurich ([#] resources)
- `AWS_Security_Assessment_eu-north-1_[DATE].md` - Stockholm ([#] resources)
- `AWS_Security_Assessment_eu-south-1_[DATE].md` - Milan ([#] resources)
- `AWS_Security_Assessment_eu-south-2_[DATE].md` - Spain ([#] resources)

✅ **Asia Pacific Regions:**
- `AWS_Security_Assessment_ap-south-1_[DATE].md` - Mumbai ([#] resources)
- `AWS_Security_Assessment_ap-south-2_[DATE].md` - Hyderabad ([#] resources)
- `AWS_Security_Assessment_ap-northeast-1_[DATE].md` - Tokyo ([#] resources)
- `AWS_Security_Assessment_ap-northeast-2_[DATE].md` - Seoul ([#] resources)
- `AWS_Security_Assessment_ap-northeast-3_[DATE].md` - Osaka ([#] resources)
- `AWS_Security_Assessment_ap-southeast-1_[DATE].md` - Singapore ([#] resources)
- `AWS_Security_Assessment_ap-southeast-2_[DATE].md` - Sydney ([#] resources)
- `AWS_Security_Assessment_ap-southeast-3_[DATE].md` - Jakarta ([#] resources)
- `AWS_Security_Assessment_ap-southeast-4_[DATE].md` - Melbourne ([#] resources)
- `AWS_Security_Assessment_ap-east-1_[DATE].md` - Hong Kong ([#] resources)

✅ **Other Regions:**
- `AWS_Security_Assessment_ca-central-1_[DATE].md` - Canada Central ([#] resources)
- `AWS_Security_Assessment_ca-west-1_[DATE].md` - Calgary ([#] resources)
- `AWS_Security_Assessment_sa-east-1_[DATE].md` - São Paulo ([#] resources)
- `AWS_Security_Assessment_me-south-1_[DATE].md` - Bahrain ([#] resources)
- `AWS_Security_Assessment_me-central-1_[DATE].md` - UAE ([#] resources)
- `AWS_Security_Assessment_af-south-1_[DATE].md` - Cape Town ([#] resources)
- `AWS_Security_Assessment_il-central-1_[DATE].md` - Tel Aviv ([#] resources)

#### Inactive Regions (No Resources)
⚪ **Regions with 0 Resources:**
- [List regions with no resources - no detailed reports generated]

## Quick Access

### By Priority
- 🔴 **Critical Issues**: See Consolidated Report Section 3
- 🟠 **High Priority**: See Consolidated Report Section 11
- 📊 **Compliance Summary**: See Consolidated Report Section 4
- 💰 **Cost Analysis**: See Consolidated Report Section 8

### By Region Type
- **Production Regions**: [list]
- **Development Regions**: [list]
- **DR/Backup Regions**: [list]

### Top 5 Regions by Risk
1. [Region] - [risk level] - [link to report]
2. [Region] - [risk level] - [link to report]
3. [Region] - [risk level] - [link to report]
4. [Region] - [risk level] - [link to report]
5. [Region] - [risk level] - [link to report]

## Assessment Statistics

### Resources by Region
| Region | Resources | % of Total | Status |
|--------|-----------|------------|--------|
| [all active regions listed with stats] |

### Findings Summary
- **Total Findings**: [#]
  - Critical: [#]
  - High: [#]
  - Medium: [#]
  - Low: [#]

### Compliance Summary
- **Average Compliance**: [%]
- **Best Region**: [region] ([%])
- **Needs Improvement**: [region] ([%])

## How to Use These Reports

1. **Start with Consolidated Report** for global overview
2. **Review Regional Reports** for detailed findings
3. **Prioritize Actions** using remediation sections
4. **Track Progress** with follow-up checkpoints

## Contact & Questions
- **Assessment Owner**: [Name]
- **Date Generated**: [Date]
- **Next Assessment**: [Date + 90 days]

---

**Filename:** `README.md`

---

## Directory Structure

```
aws-security-assessment-[YYYY-MM-DD]/
│
├── README.md (this index file)
│
├── AWS_Security_Assessment_CONSOLIDATED_[YYYY-MM-DD].md
│
└── regional-reports/
    ├── us-east-1/
    │   └── AWS_Security_Assessment_us-east-1_[YYYY-MM-DD].md
    ├── us-east-2/
    │   └── AWS_Security_Assessment_us-east-2_[YYYY-MM-DD].md
    ├── us-west-1/
    │   └── AWS_Security_Assessment_us-west-1_[YYYY-MM-DD].md
    ├── us-west-2/
    │   └── AWS_Security_Assessment_us-west-2_[YYYY-MM-DD].md
    ├── eu-west-1/
    │   └── AWS_Security_Assessment_eu-west-1_[YYYY-MM-DD].md
    ├── eu-west-2/
    │   └── AWS_Security_Assessment_eu-west-2_[YYYY-MM-DD].md
    ├── eu-west-3/
    │   └── AWS_Security_Assessment_eu-west-3_[YYYY-MM-DD].md
    ├── eu-central-1/
    │   └── AWS_Security_Assessment_eu-central-1_[YYYY-MM-DD].md
    ├── eu-central-2/
    │   └── AWS_Security_Assessment_eu-central-2_[YYYY-MM-DD].md
    ├── eu-north-1/
    │   └── AWS_Security_Assessment_eu-north-1_[YYYY-MM-DD].md
    ├── eu-south-1/
    │   └── AWS_Security_Assessment_eu-south-1_[YYYY-MM-DD].md
    ├── eu-south-2/
    │   └── AWS_Security_Assessment_eu-south-2_[YYYY-MM-DD].md
    ├── ap-south-1/
    │   └── AWS_Security_Assessment_ap-south-1_[YYYY-MM-DD].md
    ├── ap-south-2/
    │   └── AWS_Security_Assessment_ap-south-2_[YYYY-MM-DD].md
    ├── ap-northeast-1/
    │   └── AWS_Security_Assessment_ap-northeast-1_[YYYY-MM-DD].md
    ├── ap-northeast-2/
    │   └── AWS_Security_Assessment_ap-northeast-2_[YYYY-MM-DD].md
    ├── ap-northeast-3/
    │   └── AWS_Security_Assessment_ap-northeast-3_[YYYY-MM-DD].md
    ├── ap-southeast-1/
    │   └── AWS_Security_Assessment_ap-southeast-1_[YYYY-MM-DD].md
    ├── ap-southeast-2/
    │   └── AWS_Security_Assessment_ap-southeast-2_[YYYY-MM-DD].md
    ├── ap-southeast-3/
    │   └── AWS_Security_Assessment_ap-southeast-3_[YYYY-MM-DD].md
    ├── ap-southeast-4/
    │   └── AWS_Security_Assessment_ap-southeast-4_[YYYY-MM-DD].md
    ├── ap-east-1/
    │   └── AWS_Security_Assessment_ap-east-1_[YYYY-MM-DD].md
    ├── ca-central-1/
    │   └── AWS_Security_Assessment_ca-central-1_[YYYY-MM-DD].md
    ├── ca-west-1/
    │   └── AWS_Security_Assessment_ca-west-1_[YYYY-MM-DD].md
    ├── sa-east-1/
    │   └── AWS_Security_Assessment_sa-east-1_[YYYY-MM-DD].md
    ├── me-south-1/
    │   └── AWS_Security_Assessment_me-south-1_[YYYY-MM-DD].md
    ├── me-central-1/
    │   └── AWS_Security_Assessment_me-central-1_[YYYY-MM-DD].md
    ├── af-south-1/
    │   └── AWS_Security_Assessment_af-south-1_[YYYY-MM-DD].md
    └── il-central-1/
        └── AWS_Security_Assessment_il-central-1_[YYYY-MM-DD].md
```

## Execution Instructions

**CRITICAL: Ensure ALL 33+ regions are scanned in Phase 1.**

1. **Phase 1**: Use `ExploreAwsResources` tool explicitly for each region listed above
2. **Phase 2**: Generate individual detailed report for EVERY active region (do not skip any)
3. **Phase 3**: Generate consolidated report with complete cross-region analysis
4. **Final**: Generate README.md index with links to all reports

**Verification Checklist:**
- ✅ All 33+ regions scanned
- ✅ Active vs inactive regions identified
- ✅ Individual report generated for each active region
- ✅ Consolidated report includes all active regions
- ✅ README.md index created
- ✅ All files saved in proper directory structure

Generate comprehensive, complete reports with real data from MCP tools. Do not skip or summarize any active regions.
