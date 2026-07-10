# Phase 4: CloudFormation Template Generation

This steering file guides the agent through generating CloudFormation YAML templates for DR infrastructure. Templates are written to `cfn-templates/` in the user's working directory.

**Sources:**
- [AWS CloudFormation Best Practices](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/best-practices.html)
- [AWS DRS Best Practices](https://docs.aws.amazon.com/drs/latest/userguide/best_practices_drs.html)
- [AWS Backup Cross-Region](https://docs.aws.amazon.com/aws-backup/latest/devguide/cross-region-backup.html)

---

## Phase Gate Check

Before proceeding, read `dr-state/core.json` and verify:

1. `metadata.completed_phases` contains `"scan"`, `"analyze"`, and `"plan"`.
2. `dr-state/tiers.json` exists and `strategy_selections` is non-empty.
3. `dr-state/resources.json` exists with valid data.

**If any check fails:** Halt and prompt the user to run the missing phase(s).

---

## Template Generation Matrix

Use this decision table to determine which templates to generate. Evaluate each condition against the state file.

| Template File | Generate When |
|--------------|---------------|
| `recovery-vpc.yaml` | **Always** — generate for all strategies |
| `drs-staging-area.yaml` | EC2 instances present in Tier 1 or Tier 2 workloads (`scan.resources.ec2.instances` non-empty AND tier is 1 or 2) |
| `route53-failover.yaml` | Route53 hosted zones discovered (`scan.resources.route53.hosted_zones` non-empty) |
| `backup-vault.yaml` | **Always** — generate for all strategies |
| `drs-iam-roles.yaml` | EC2 instances present in Tier 1 or Tier 2 workloads |
| `multi-site-routing.yaml` | Multi-Site Active/Active selected for **any** tier — **ALWAYS generate when this strategy is selected**, regardless of whether other templates have failed |

---

## Template Structure Requirements

Every generated template MUST include all of the following sections:

```yaml
AWSTemplateFormatVersion: '2010-09-09'

Description: |
  [Template purpose and DR strategy it supports]
  Source: aws-dr-power v1.0.0
  Reference: [cached-dr-patterns.md pattern name]

Metadata:
  AWS::CloudFormation::Interface:
    ParameterGroups:
      - Label:
          default: "DR Configuration"
        Parameters:
          - PrimaryRegion
          - RecoveryRegion
          - VpcCidr

Parameters:
  PrimaryRegion:
    Type: String
    Description: Primary AWS region (e.g., us-east-1)
  RecoveryRegion:
    Type: String
    Description: DR/recovery AWS region (e.g., us-west-2)
  VpcCidr:
    Type: String
    Default: "10.1.0.0/16"
    Description: CIDR block for the recovery VPC
  # ... additional workload-specific parameters

Resources:
  # ... DR infrastructure resources

Outputs:
  # ... exported ARNs and IDs for cross-stack reference
  # Every output MUST use Export with a unique name
```

---

## recovery-vpc.yaml Generation

Use the pattern from `cached-dr-patterns.md#recovery-vpc-pattern`.

**Instructions:**
1. Copy the Recovery VPC pattern YAML from `cached-dr-patterns.md`.
2. Add the standard `AWSTemplateFormatVersion`, `Description`, and `Metadata` sections.
3. Parameterize all CIDR blocks using the `Parameters` section.
4. Ensure `Outputs` exports: `VpcId`, `PublicSubnet1Id`, `PublicSubnet2Id`, `PrivateSubnet1Id`, `PrivateSubnet2Id`.
5. Set `Description` to reference `cached-dr-patterns.md#recovery-vpc-pattern`.

**Output file:** `cfn-templates/recovery-vpc.yaml`

---

## drs-staging-area.yaml Generation

Use the pattern from `cached-dr-patterns.md#drs-staging-area-network-pattern`.

**Instructions:**
1. Copy the DRS Staging Area pattern YAML from `cached-dr-patterns.md`.
2. Calculate the staging subnet CIDR from the discovered EC2 instance count in `scan.resources.ec2.instances`:

| EC2 Instance Count | Staging Subnet CIDR |
|--------------------|---------------------|
| ≤ 14 | /28 |
| ≤ 30 | /27 |
| ≤ 62 | /26 |
| ≤ 126 | /25 |
| ≤ 254 | /24 |
| > 254 | /23 (+ add multi-account guidance comment) |

3. Set the `StagingSubnetCidr` parameter default to the calculated CIDR.
4. Configure the security group to allow TCP 1500 inbound for DRS replication traffic.
5. Ensure `Outputs` exports: `DRSStagingSubnetId`, `DRSReplicationSecurityGroupId`.

**Output file:** `cfn-templates/drs-staging-area.yaml`

---

## route53-failover.yaml Generation

Use the pattern from `cached-dr-patterns.md#route53-health-check-and-failover-pattern`.

**Instructions:**
1. Copy the Route53 health check and failover pattern YAML from `cached-dr-patterns.md`.
2. Expose the following as CloudFormation parameters:
   - `HealthCheckFailureThreshold` — default: `3`
   - `HealthCheckRequestInterval` — default: `30` (allowed values: 10, 30)
3. Populate `HostedZoneId` parameter description with the discovered hosted zone IDs from `scan.resources.route53.hosted_zones`.
4. Ensure `Outputs` exports: `HealthCheckId`.

**Output file:** `cfn-templates/route53-failover.yaml`

---

## backup-vault.yaml Generation

Use the pattern from `cached-dr-patterns.md#aws-backup-vault-and-cross-region-copy-pattern`.

**Instructions:**
1. Copy the AWS Backup vault and cross-region copy pattern YAML from `cached-dr-patterns.md`.
2. Parameterize:
   - `BackupSchedule` — default: `cron(0 5 ? * * *)` (daily at 05:00 UTC)
   - `BackupRetentionDays` — default based on tier: Tier 1/2 = 30, Tier 3 = 7, Tier 4 = 30
   - `CrossRegionRetentionDays` — same defaults as above
   - `DestinationRegion` — populate from `metadata.additional_regions[0]` or prompt user
3. Ensure `Outputs` exports: `BackupVaultArn`, `BackupPlanId`.

**Output file:** `cfn-templates/backup-vault.yaml`

---

## drs-iam-roles.yaml Generation

Use the pattern from `cached-dr-patterns.md#drs-iam-roles-pattern`.

**Instructions:**
1. Copy the DRS IAM roles pattern YAML from `cached-dr-patterns.md`.
2. Include both roles:
   - `AWSElasticDisasterRecoveryAgentInstallationRole` — for agent installation on source servers
   - `AWSElasticDisasterRecoveryReplicationServerRole` — for replication server operation
3. Include both instance profiles for EC2 attachment.
4. Ensure `Outputs` exports: `DRSAgentInstallationRoleArn`, `DRSReplicationServerRoleArn`, `DRSAgentInstallationInstanceProfileArn`.

**Output file:** `cfn-templates/drs-iam-roles.yaml`

---

## multi-site-routing.yaml Generation

Use the pattern from `cached-dr-patterns.md#multi-site-activactive-routing-pattern`.

**Instructions:**
1. Copy the Multi-Site Active/Active routing pattern YAML from `cached-dr-patterns.md`.
2. Include **both** routing options as separate resources with CloudFormation Conditions:
   - Route53 latency-based routing records (Condition: `UseRoute53LatencyRouting`)
   - Global Accelerator listener + endpoint groups (Condition: `UseGlobalAccelerator`)
3. Add a `RoutingMethod` parameter with allowed values `["Route53Latency", "GlobalAccelerator"]` and default `"Route53Latency"`.
4. Define conditions:
   ```yaml
   Conditions:
     UseRoute53LatencyRouting: !Equals [!Ref RoutingMethod, "Route53Latency"]
     UseGlobalAccelerator: !Equals [!Ref RoutingMethod, "GlobalAccelerator"]
   ```
5. Ensure `Outputs` exports: `GlobalAcceleratorArn` (conditional), `GlobalAcceleratorDnsName` (conditional).

**⚠️ IMPORTANT:** This template MUST always be generated when Multi-Site Active/Active is selected for any tier, regardless of whether other templates have failed.

**Output file:** `cfn-templates/multi-site-routing.yaml`

---

## Template Validation

Before writing each template file, perform the following validation:

1. Scan the generated YAML content for placeholder patterns:
   - `<[A-Z_]+>` (e.g., `<REPLACE_ME>`, `<YOUR_VALUE>`)
   - `REPLACE_ME`
   - `TODO`
   - `YOUR_VALUE_HERE`

2. Check for these patterns in **required fields** only:
   - `Resources` section values
   - `Parameters` default values (if marked as required)
   - `Outputs` values

3. **If any placeholder is found in a required field:**
   - Do NOT write the file
   - Record the template as failed: `{ "template": "<filename>", "reason": "placeholder_found", "location": "<field path>" }`
   - After processing all templates, **fail the entire Templates Phase** and report:
     > "Templates Phase failed. The following templates could not be generated due to placeholder values:
     > - `<filename>`: placeholder found at `<field path>`"

4. **Exception:** `multi-site-routing.yaml` is always attempted and written even if other templates fail (per REQ-7.8).

---

## File Write Protocol

1. Create the `cfn-templates/` subdirectory in the working directory if it does not exist.
2. For each template:
   a. Validate the content (see Template Validation above).
   b. If content ≤ 50 lines: Write in a single operation.
   c. If content > 50 lines: Write in chunks of ≤ 50 lines. After each chunk, verify the last line written matches expected content before proceeding.
   d. After writing, read back the first 5 lines to verify the file was created correctly.
3. Track all successfully written templates in a list.

---

## Phase Completion

1. Write the template manifest to `dr-state/templates.json`:
```json
{
  "output_dir": "cfn-templates/",
  "generated_templates": ["recovery-vpc.yaml", "backup-vault.yaml"],
  "custom_service_notes": []
}
```
2. Update `dr-state/core.json`:
   - Set `phases.templates.status = "completed"`
   - Append `"templates"` to `metadata.completed_phases`
   - Write `core.json` **after** `templates.json` is fully written.

Display a summary:
```
Templates Phase Complete
========================
Template                  | Status
--------------------------|--------
recovery-vpc.yaml         | Generated
drs-staging-area.yaml     | Generated
route53-failover.yaml     | Generated
backup-vault.yaml         | Generated
drs-iam-roles.yaml        | Generated
multi-site-routing.yaml   | Generated (if applicable)
```

**Custom service template notes** (if `metadata.custom_services` is non-empty):

For each custom service, determine whether a CloudFormation template is applicable:
- If the service supports CloudFormation resources (`AWS::<Service>::<Resource>`): generate a minimal template with the key DR-relevant resources (backup configuration, cross-region replication, IAM roles).
- If the service does not support CloudFormation: add a note to `custom_service_notes` explaining the manual configuration required, and include a reference to the service's DR documentation.
- Write any generated custom templates to `cfn-templates/custom-<service-name>.yaml`.

> **Next step:** Run the Checklist phase to generate the DR operational checklist.
