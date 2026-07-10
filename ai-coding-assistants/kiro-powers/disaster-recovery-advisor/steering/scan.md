# Phase 1: AWS Resource Scan

This steering file guides the agent through credential setup and full resource discovery across all AWS service categories. Output is written to `aws-dr-state.json` in the user's working directory.

**Source:** [AWS DR Whitepaper](https://docs.aws.amazon.com/whitepapers/latest/disaster-recovery-workloads-on-aws/)

---

## Prerequisites Check

Run this before credential setup. If any prerequisite fails, halt and resolve it before continuing.

### Check `uvx`
```bash
uvx --version
```
If missing: prompt the user to install `uv` (`pip install uv`) and wait for confirmation.

### Check AWS API MCP server registration

Read `~/.kiro/settings/mcp.json` and check for an `awslabs.aws-api-mcp-server` entry with `"disabled": false`.

- **Found and enabled:** Proceed.
- **Missing or disabled:** Follow the auto-install flow defined in `POWER.md → Prerequisites Check → Step 2`. Do not proceed until the server is registered and Kiro has been restarted.

### Check AWS CLI
```bash
aws --version
```
If missing: direct the user to https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html and wait for confirmation.

---

## Credential Setup

The power never stores or reads credentials from `mcp.json`. Instead, credentials are loaded from a `.secret` file in the user's working directory. This file is git-ignored and never written to the state file or any output document.

### Step 1: Create the `.secret` file

Tell the user:

> **Before scanning, create a `.secret` file in your working directory with your AWS credentials.**
>
> The file must follow this exact format:
>
> ```
> AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE
> AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
> AWS_DEFAULT_REGION=us-east-1
> ```
>
> **Rules:**
> - One key=value pair per line, no spaces around `=`
> - Do NOT use quotes around values
> - Do NOT commit this file — it is already in `.gitignore`
> - Delete this file after the assessment is complete
>
> Alternatively, if you have an AWS CLI profile already configured (`aws configure`), set `AWS_PROFILE` in the file instead:
> ```
> AWS_PROFILE=my-readonly-profile
> AWS_DEFAULT_REGION=us-east-1
> ```
>
> Tell me **"credentials ready"** when the file is saved.

### Step 2: Load credentials from `.secret`

When the user confirms credentials are ready:

1. Read `.secret` from the working directory.
2. Parse each `KEY=VALUE` line. Strip leading/trailing whitespace from both key and value.
3. Set the parsed values as environment variables for all subsequent MCP server calls:
   - `AWS_ACCESS_KEY_ID`
   - `AWS_SECRET_ACCESS_KEY`
   - `AWS_DEFAULT_REGION` (or `AWS_REGION`)
   - `AWS_SESSION_TOKEN` (if present — for temporary credentials)
   - `AWS_PROFILE` (if present — for profile-based auth)
4. **Do NOT log, echo, or store any of these values** in the state file, chat output, or any generated document. Reference them only by key name.
5. If the file does not exist or cannot be parsed, display:
   > "`.secret` file not found or invalid. Please create it in your working directory and try again."
   > Halt until the file is provided.

### Step 3: Validate credentials

Run identity check:
```bash
aws sts get-caller-identity
```

- **On success:** Store `account_id` and `region` in state file metadata. Proceed to permission validation.
- **On failure:** Display the **full error message** from the AWS CLI. Prompt the user to fix the credentials in `.secret` and retry. Do NOT proceed until this succeeds.

### Step 4: Validate read-only permissions (no write access)

Run the following permission simulation checks using `aws iam simulate-principal-policy`. This verifies the credentials have the necessary read permissions and — critically — do NOT have write permissions.

First, get the caller ARN from the `get-caller-identity` output:
```bash
# Use the Arn from the previous get-caller-identity call
CALLER_ARN="<arn from get-caller-identity>"
```

**Check 1: Verify read permissions are present**

```bash
aws iam simulate-principal-policy \
  --policy-source-arn "$CALLER_ARN" \
  --action-names \
    "ec2:DescribeInstances" \
    "rds:DescribeDBInstances" \
    "s3:ListAllMyBuckets" \
    "iam:ListRoles" \
    "cloudformation:ListStacks"
```

All actions must return `"EvalDecision": "allowed"`. If any return `"implicitDeny"` or `"explicitDeny"`, warn the user:
> "⚠️ The credentials may lack some read permissions. The scan will continue but some services may be skipped. Missing: `[list denied actions]`"

**Check 2: Verify write permissions are ABSENT**

```bash
aws iam simulate-principal-policy \
  --policy-source-arn "$CALLER_ARN" \
  --action-names \
    "ec2:RunInstances" \
    "ec2:TerminateInstances" \
    "rds:CreateDBInstance" \
    "rds:DeleteDBInstance" \
    "s3:DeleteObject" \
    "s3:PutObject" \
    "iam:CreateUser" \
    "iam:DeleteUser" \
    "iam:AttachUserPolicy" \
    "cloudformation:CreateStack" \
    "cloudformation:DeleteStack"
```

**Expected result:** All actions should return `"implicitDeny"` or `"explicitDeny"`.

**If ANY write action returns `"allowed"`:** Display a warning and ask the user to confirm before proceeding:

> ⚠️ **WRITE PERMISSIONS DETECTED**
>
> The credentials provided have write access to AWS resources. This assessment is designed to use read-only credentials to prevent accidental changes.
>
> Write permissions detected for:
> - `[list of allowed write actions]`
>
> **Recommendation:** Use credentials with only `ReadOnlyAccess` attached (`arn:aws:iam::aws:policy/ReadOnlyAccess`).
>
> Do you want to:
> - **[A] Stop and provide read-only credentials** (recommended)
> - **[B] Continue anyway** (the scan will not make any changes, but the credentials could be misused if compromised)

If the user chooses [A]: halt and wait for new credentials in `.secret`.
If the user chooses [B]: log `"write_permissions_detected": true` in the state file metadata and proceed with a persistent warning banner in all output documents.

> **NOTE:** If `iam:SimulatePrincipalPolicy` itself returns `AccessDenied` (the credentials lack permission to simulate), skip the write-permission check, log a warning, and proceed:
> > "Could not validate write permissions (SimulatePrincipalPolicy denied). Proceeding on the assumption that credentials are read-only. Verify manually that `ReadOnlyAccess` is the only attached policy."

### Step 5: Optional — Create a read-only IAM user

If the user does not have read-only credentials and wants to create them:

```bash
# Create the user
aws iam create-user --user-name dr-advisor-readonly

# Attach ReadOnlyAccess managed policy
aws iam attach-user-policy \
  --user-name dr-advisor-readonly \
  --policy-arn arn:aws:iam::aws:policy/ReadOnlyAccess

# Generate access keys
aws iam create-access-key --user-name dr-advisor-readonly
```

Copy the `AccessKeyId` and `SecretAccessKey` from the output into `.secret`. Then re-run Steps 2–4.

> **REMINDER:** Delete this IAM user after the assessment:
> ```bash
> aws iam delete-access-key --user-name dr-advisor-readonly --access-key-id <KeyId>
> aws iam detach-user-policy --user-name dr-advisor-readonly --policy-arn arn:aws:iam::aws:policy/ReadOnlyAccess
> aws iam delete-user --user-name dr-advisor-readonly
> ```

---

## Scan Initialization

1. Check if `dr-state/` directory and `core.json` exist.
   - If `core.json` exists and `phases.scan.status == "completed"`: Ask the user — "A previous scan exists. Re-run scan (overwrites existing data) or skip to Analyze phase?"
   - If it exists but is incomplete or corrupted: Treat as missing, start fresh.
   - If it does not exist: Initialize a new state directory.

2. **Read intake data (if available):**

   Check if `intake.status == "completed"` in the state file.

   - **If intake was completed:** Read from `dr-state/intake.json`:
     - `primary_region` and `additional_regions` → use as scan scope
     - `recovery_region` → use as the mandatory recovery region scan target
     - `custom_services_from_bom` → add to `metadata.custom_services`
     - Display: "Using `dr-intake.json` data: primary=`[region]`, recovery=`[region]`, additional=`[regions]`."

   - **If intake was skipped or not present:** Fall through to the manual steps below.

3. **Confirm primary region** *(skip if intake provided this)*:
   > "What is your primary AWS region? (e.g., us-east-1)"

4. **Confirm recovery region — REQUIRED** *(skip if intake provided this)*:

   The recovery region scan is **mandatory**. Gap analysis works by comparing what exists in the primary region against what exists in the recovery region. Without scanning both, the power cannot determine what DR infrastructure is missing.

   > "What is your DR/recovery region? (e.g., us-west-2)"

   If the user has not decided on a recovery region:
   > "A recovery region is required for accurate gap analysis. Without it, the power cannot determine what DR infrastructure is missing. Please specify a recovery region before proceeding."
   > **Do not proceed until a recovery region is provided.**

5. **Check for Bill of Materials or explicit service callouts** *(skip if intake already provided this)*:

   Check whether the user has provided any of the following:
   - A **bill of materials** (BoM): a list, table, or document naming AWS services and resource types
   - An **explicit callout**: a message like "also scan Amazon Connect and AWS IoT Core"

   If a BoM or callout is present:
   - Parse all service names from the input.
   - For each service not already in the built-in scan catalog, add it to `metadata.custom_services`.
   - Research the correct read-only AWS CLI commands for each custom service.
   - If you cannot determine the CLI commands for a service, ask the user.
   - Confirm the extended scope with the user before proceeding.

6. **Prompt for additional regions** *(optional, beyond primary + recovery)*:
   > "Any additional regions to scan beyond primary (`{primary}`) and recovery (`{recovery}`)? (Press Enter to skip)"

7. Initialize the state directory and core file:

   Create `dr-state/` directory if it does not exist.

   Write `dr-state/core.json`:
```json
{
  "metadata": {
    "power_version": "1.0.0",
    "account_id": "<from sts get-caller-identity>",
    "primary_region": "<primary region>",
    "recovery_region": "<recovery region>",
    "additional_regions": [],
    "scan_timestamp": "<ISO 8601 timestamp>",
    "completed_phases": [],
    "custom_services": [],
    "write_permissions_detected": false
  },
  "phases": {
    "intake": { "status": "skipped" },
    "scan": { "status": "in_progress" },
    "analyze": { "status": "not_started" },
    "plan": { "status": "not_started" },
    "templates": { "status": "not_started" },
    "checklist": { "status": "not_started" }
  }
}
```

   Resource data is written to `dr-state/resources.json` — NOT to `core.json`. Keep `core.json` lean.

---

## Custom Service Scan

After completing all built-in service scans, scan any services in `metadata.custom_services`:

For each custom service:
1. Execute the CLI commands determined during Scan Initialization.
2. Capture key attributes: resource ID, resource name, region, ARN (if available), status/state, creation date.
3. Store results in `dr-state/resources.json` under `custom_resources["<service_name>"]`.
4. Apply the same error handling rules (AccessDenied → log and continue, Throttling → backoff, etc.).

**Example for Amazon Connect:**
```bash
# List Connect instances
aws connect list-instances --region <region>

# For each instance, list key resources
aws connect list-contact-flows --instance-id <id> --region <region>
aws connect list-queues --instance-id <id> --region <region>
aws connect list-routing-profiles --instance-id <id> --region <region>
```

**Example for AWS IoT Core:**
```bash
aws iot list-things --region <region>
aws iot list-topic-rules --region <region>
aws iot list-certificates --region <region>
```

**Example for AWS AppSync:**
```bash
aws appsync list-graphql-apis --region <region>
# For each API:
aws appsync list-data-sources --api-id <id> --region <region>
```

If the user provided resource types in their BoM (e.g., "Amazon Connect: instances, contact flows"), scan only those resource types. Otherwise scan all discoverable resource types for the service.

---

## Compute Scan

For each region in scope, execute the following commands and capture the listed key attributes.

### EC2 Instances
```bash
aws ec2 describe-instances --region <region>
```
Key attributes: `InstanceId`, `InstanceType`, `State.Name`, `Placement.AvailabilityZone`, `Tags`, `BlockDeviceMappings` (EBS volume IDs)

### EC2 AMIs (owned by account)
```bash
aws ec2 describe-images --owners self --region <region>
```
Key attributes: `ImageId`, `Name`, `CreationDate`, `State`

### EBS Volumes
```bash
aws ec2 describe-volumes --region <region>
```
Key attributes: `VolumeId`, `AvailabilityZone`, `Encrypted`, `Size`, `State`

### EBS Snapshots (owned by account)
```bash
aws ec2 describe-snapshots --owner-ids self --region <region>
```
Key attributes: `SnapshotId`, `VolumeId`, `StartTime`, `State`, `Encrypted`

### VPCs
```bash
aws ec2 describe-vpcs --region <region>
```
Key attributes: `VpcId`, `CidrBlock`, `IsDefault`, `Tags`

### Subnets
```bash
aws ec2 describe-subnets --region <region>
```
Key attributes: `SubnetId`, `VpcId`, `CidrBlock`, `AvailabilityZone`

### Security Groups
```bash
aws ec2 describe-security-groups --region <region>
```
Key attributes: `GroupId`, `GroupName`, `VpcId`, `IpPermissions` (inbound rules)

### Elastic IPs
```bash
aws ec2 describe-addresses --region <region>
```
Key attributes: `AllocationId`, `PublicIp`, `AssociationId`, `InstanceId`

### Auto Scaling Groups
```bash
aws autoscaling describe-auto-scaling-groups --region <region>
```
Key attributes: `AutoScalingGroupName`, `MinSize`, `MaxSize`, `DesiredCapacity`, `AvailabilityZones`, `LaunchTemplate`

### Launch Templates
```bash
aws ec2 describe-launch-templates --region <region>
```
Key attributes: `LaunchTemplateId`, `LaunchTemplateName`, `LatestVersionNumber`

### ECS Clusters
```bash
aws ecs list-clusters --region <region>
# Then for each cluster ARN:
aws ecs describe-clusters --clusters <arn> --region <region>
```
Key attributes: `clusterArn`, `clusterName`, `status`, `runningTasksCount`, `activeServicesCount`

### ECS Services
```bash
# For each cluster:
aws ecs list-services --cluster <arn> --region <region>
aws ecs describe-services --cluster <arn> --services <arns> --region <region>
```
Key attributes: `serviceName`, `desiredCount`, `runningCount`, `launchType`, `taskDefinition`

### ECS Task Definitions
```bash
aws ecs list-task-definitions --region <region>
```
Key attributes: `taskDefinitionArn`, `family`, `revision`

### ECR Repositories
```bash
aws ecr describe-repositories --region <region>
```
Key attributes: `repositoryName`, `repositoryArn`, `repositoryUri`, `imageTagMutability`

### ECR Replication Configuration
```bash
# Check replication rules configured on this registry
aws ecr describe-registry --region <region>
```
Key attributes: `replicationConfiguration.rules` (destinations, repositoryFilters)

### EKS Clusters
```bash
aws eks list-clusters --region <region>
# Then for each cluster:
aws eks describe-cluster --name <name> --region <region>
```
Key attributes: `name`, `status`, `version`, `roleArn`, `resourcesVpcConfig`

### EKS Node Groups
```bash
# For each EKS cluster:
aws eks list-nodegroups --cluster-name <name> --region <region>
aws eks describe-nodegroup --cluster-name <name> --nodegroup-name <ng> --region <region>
```
Key attributes: `nodegroupName`, `status`, `amiType`, `scalingConfig`

### Lambda Functions
```bash
aws lambda list-functions --region <region>
```
Key attributes: `FunctionName`, `Runtime`, `Handler`, `CodeSize`, `LastModified`

### Lambda Event Source Mappings
```bash
aws lambda list-event-source-mappings --region <region>
```
Key attributes: `UUID`, `EventSourceArn`, `FunctionArn`, `State`

### Elastic Beanstalk
```bash
aws elasticbeanstalk describe-applications --region <region>
aws elasticbeanstalk describe-environments --region <region>
```
Key attributes: `ApplicationName`, `EnvironmentName`, `Status`, `Health`, `SolutionStackName`

### EC2 Backup Status
For each EC2 instance, check AWS Backup coverage:
```bash
aws backup list-protected-resources --region <region>
# Filter results by resource ARN matching EC2 instance ARNs
```

---

## Database Scan

### RDS Instances
```bash
aws rds describe-db-instances --region <region>
```
Key attributes: `DBInstanceIdentifier`, `DBInstanceClass`, `Engine`, `MultiAZ`, `BackupRetentionPeriod`, `DBInstanceStatus`, `AvailabilityZone`

### RDS Clusters
```bash
aws rds describe-db-clusters --region <region>
```
Key attributes: `DBClusterIdentifier`, `Engine`, `MultiAZ`, `BackupRetentionPeriod`, `Status`

### RDS Snapshots
```bash
aws rds describe-db-snapshots --region <region>
```
Key attributes: `DBSnapshotIdentifier`, `DBInstanceIdentifier`, `SnapshotType`, `Status`, `PercentProgress`

### RDS Cross-Region Automated Backups
```bash
aws rds describe-db-instance-automated-backups --region <region>
```
Key attributes: `DBInstanceIdentifier`, `Region` (source region), `BackupRetentionPeriod`

> **Skip logic:** If `describe-db-instances` returns an empty list for a region, skip all subsequent RDS configuration checks for that region and record `"rds_resources": "none_found"`.

### DynamoDB Tables
```bash
aws dynamodb list-tables --region <region>
# Then for each table:
aws dynamodb describe-table --table-name <name> --region <region>
```
Key attributes: `TableName`, `TableStatus`, `GlobalTableVersion`, `BillingModeSummary`

### DynamoDB Global Tables
```bash
aws dynamodb list-global-tables --region <region>
```
Key attributes: `GlobalTableName`, `ReplicationGroup` (list of regions)

### DynamoDB Backups
```bash
aws dynamodb list-backups --region <region>
```
Key attributes: `BackupArn`, `BackupName`, `BackupStatus`, `BackupCreationDateTime`

### ElastiCache Clusters
```bash
aws elasticache describe-cache-clusters --region <region>
```
Key attributes: `CacheClusterId`, `Engine`, `CacheClusterStatus`, `NumCacheNodes`, `PreferredAvailabilityZone`

### ElastiCache Replication Groups
```bash
aws elasticache describe-replication-groups --region <region>
```
Key attributes: `ReplicationGroupId`, `Status`, `MultiAZ`, `SnapshotRetentionLimit`, `AutomaticFailover`

### ElastiCache Global Datastores
```bash
aws elasticache describe-global-replication-groups --region <region>
```
Key attributes: `GlobalReplicationGroupId`, `Status`, `Members` (list of regional replication groups and their roles)

### MemoryDB Clusters
```bash
aws memorydb describe-clusters --region <region>
```
Key attributes: `Name`, `Status`, `NodeType`, `EngineVersion`, `MultiRegionClusterName`

### MemoryDB Multi-Region Clusters
```bash
aws memorydb describe-multi-region-clusters --region <region>
```
Key attributes: `MultiRegionClusterName`, `Status`, `Clusters` (list of regional clusters)

### Redshift Clusters
```bash
aws redshift describe-clusters --region <region>
```
Key attributes: `ClusterIdentifier`, `ClusterStatus`, `AutomatedSnapshotRetentionPeriod`, `AvailabilityZone`, `Encrypted`

### Redshift Snapshot Copy Grants
```bash
aws redshift describe-snapshot-copy-grants --region <region>
```
Key attributes: `SnapshotCopyGrantName`, `KmsKeyId` (destination region KMS key)

### DocumentDB Clusters
```bash
aws docdb describe-db-clusters --region <region>
```
Key attributes: `DBClusterIdentifier`, `Status`, `MultiAZ`, `BackupRetentionPeriod`, `AvailabilityZones`

---

## Storage Scan

### S3 Buckets
```bash
aws s3api list-buckets
# Note: S3 is global; run once, not per-region
```
Key attributes: `Name`, `CreationDate`

For each bucket, independently attempt all three attribute checks:

```bash
# Versioning status
aws s3api get-bucket-versioning --bucket <name>

# Cross-Region Replication configuration
aws s3api get-bucket-replication --bucket <name>

# Lifecycle policies
aws s3api get-bucket-lifecycle-configuration --bucket <name>
```

> **Partial check handling:** If any individual check returns `AccessDenied`, record that attribute as `"status": "inaccessible"`. Continue with remaining attributes and remaining buckets.

### EFS File Systems
```bash
aws efs describe-file-systems --region <region>
```
Key attributes: `FileSystemId`, `LifeCycleState`, `Encrypted`, `SizeInBytes`, `NumberOfMountTargets`

### EFS Replication Configurations
```bash
aws efs describe-replication-configurations --region <region>
```
Key attributes: `SourceFileSystemId`, `Destinations` (region, status)

### FSx File Systems
```bash
aws fsx describe-file-systems --region <region>
```
Key attributes: `FileSystemId`, `FileSystemType`, `StorageCapacity`, `Lifecycle`

---

## Networking Scan

### Application/Network Load Balancers (ALB/NLB)
```bash
aws elbv2 describe-load-balancers --region <region>
```
Key attributes: `LoadBalancerArn`, `LoadBalancerName`, `Type`, `Scheme`, `AvailabilityZones`, `State`

### Classic Load Balancers (CLB)
```bash
aws elb describe-load-balancers --region <region>
```
Key attributes: `LoadBalancerName`, `AvailabilityZones`, `HealthCheck`, `DNSName`

### Route53 Hosted Zones
```bash
aws route53 list-hosted-zones
# Note: Route53 is global; run once
```
Key attributes: `Id`, `Name`, `Type` (Public/Private), `ResourceRecordSetCount`

### Route53 Health Checks
```bash
aws route53 list-health-checks
```
Key attributes: `Id`, `HealthCheckConfig.Type`, `HealthCheckConfig.FailureThreshold`, `HealthCheckConfig.RequestInterval`

### Route53 Application Recovery Controller (ARC)
```bash
# List ARC clusters
aws route53-recovery-control-config list-clusters --region us-west-2

# For each cluster, list control panels and routing controls
aws route53-recovery-control-config list-control-panels \
  --cluster-arn <cluster-arn> --region us-west-2
aws route53-recovery-control-config list-routing-controls \
  --control-panel-arn <panel-arn> --region us-west-2

# List readiness checks
aws route53-recovery-readiness list-readiness-checks --region us-west-2
```
Key attributes: `ClusterArn`, `ControlPanelArn`, `RoutingControlArn`, `RoutingControlState`

> **Note:** Route53 ARC API endpoint is `us-west-2` regardless of your primary region.

### CloudFront Distributions
```bash
aws cloudfront list-distributions
# Note: CloudFront is global; run once
```
Key attributes: `Id`, `DomainName`, `Origins`, `DefaultCacheBehavior`, `Status`

### API Gateway REST APIs
```bash
aws apigateway get-rest-apis --region <region>
```
Key attributes: `id`, `name`, `endpointConfiguration.types`

### API Gateway HTTP APIs
```bash
aws apigatewayv2 get-apis --region <region>
```
Key attributes: `ApiId`, `Name`, `ProtocolType`

### VPN Connections
```bash
aws ec2 describe-vpn-connections --region <region>
```
Key attributes: `VpnConnectionId`, `State`, `CustomerGatewayId`, `VpnGatewayId`

### Direct Connect Connections
```bash
aws directconnect describe-connections --region <region>
```
Key attributes: `connectionId`, `connectionName`, `connectionState`, `bandwidth`, `location`

### Global Accelerator
```bash
aws globalaccelerator list-accelerators --region us-west-2
# Note: Global Accelerator API endpoint is us-west-2
```
Key attributes: `AcceleratorArn`, `Name`, `Status`, `IpSets`

### Transit Gateways
```bash
aws ec2 describe-transit-gateways --region <region>
```
Key attributes: `TransitGatewayId`, `State`, `OwnerId`, `Description`

---

## Messaging & Streaming Scan

### SQS Queues
```bash
aws sqs list-queues --region <region>
# Then for each queue URL:
aws sqs get-queue-attributes --queue-url <url> --attribute-names All --region <region>
```
Key attributes: `QueueUrl`, `MessageRetentionPeriod`, `RedrivePolicy` (DLQ config), `ApproximateNumberOfMessages`

### SNS Topics
```bash
aws sns list-topics --region <region>
# Then for each topic ARN:
aws sns get-topic-attributes --topic-arn <arn> --region <region>
```
Key attributes: `TopicArn`, `DisplayName`, `SubscriptionsConfirmed`

### Kinesis Data Streams
```bash
aws kinesis list-streams --region <region>
# Then for each stream:
aws kinesis describe-stream --stream-name <name> --region <region>
```
Key attributes: `StreamName`, `StreamStatus`, `RetentionPeriodHours`, `ShardCount`

### MSK Clusters
```bash
aws kafka list-clusters --region <region>
```
Key attributes: `ClusterArn`, `ClusterName`, `State`, `BrokerNodeGroupInfo`

### Kinesis Firehose Delivery Streams
```bash
aws firehose list-delivery-streams --region <region>
# Then for each stream:
aws firehose describe-delivery-stream --delivery-stream-name <name> --region <region>
```
Key attributes: `DeliveryStreamName`, `DeliveryStreamStatus`, `DeliveryStreamType`, `Destinations`

---

## Security & Identity Scan

### IAM Roles (DR-relevant)
```bash
aws iam list-roles
# Filter to roles with "drs", "disaster", "recovery", "backup", or "replication" in name/description
```
Key attributes: `RoleName`, `Arn`, `AttachedPolicies`

### KMS Keys
```bash
aws kms list-keys --region <region>
# Then for each key:
aws kms describe-key --key-id <id> --region <region>
```
Key attributes: `KeyId`, `KeyArn`, `KeyState`, `KeyManager` (AWS vs CUSTOMER), `MultiRegion`, `MultiRegionConfiguration.ReplicaKeys`

### Secrets Manager Secrets
```bash
aws secretsmanager list-secrets --region <region>
```
Key attributes: `Name`, `ARN`, `ReplicationStatus`, `RotationEnabled`, `LastChangedDate`

### ACM Certificates
```bash
aws acm list-certificates --region <region>
```
Key attributes: `CertificateArn`, `DomainName`, `Status`

### WAF Web ACLs
```bash
aws wafv2 list-web-acls --scope REGIONAL --region <region>
aws wafv2 list-web-acls --scope CLOUDFRONT --region us-east-1
```
Key attributes: `Id`, `Name`, `ARN`

---

## Operations & Monitoring Scan

### CloudFormation Stacks
```bash
aws cloudformation list-stacks --region <region>
```
Key attributes: `StackName`, `StackStatus`, `CreationTime`, `StackId`

### CloudFormation StackSets
```bash
aws cloudformation list-stack-sets --region <region>
```
Key attributes: `StackSetName`, `Status`, `Description`

### CloudWatch Alarms
```bash
aws cloudwatch describe-alarms --region <region>
```
Key attributes: `AlarmName`, `StateValue`, `MetricName`, `Namespace`, `AlarmActions`

### CloudWatch Synthetics Canaries
```bash
aws synthetics describe-canaries --region <region>
```
Key attributes: `Name`, `Status.State`, `Schedule.Expression`, `RuntimeVersion`

### CloudWatch Dashboards
```bash
aws cloudwatch list-dashboards --region <region>
```
Key attributes: `DashboardName`, `LastModified`

### Systems Manager Parameters
```bash
aws ssm describe-parameters --region <region>
```
Key attributes: `Name`, `Type`, `DataType`, `LastModifiedDate`

### AWS Backup Plans
```bash
aws backup list-backup-plans --region <region>
```
Key attributes: `BackupPlanId`, `BackupPlanName`, `CreationDate`

### AWS Backup Vaults
```bash
aws backup list-backup-vaults --region <region>
```
Key attributes: `BackupVaultName`, `BackupVaultArn`, `NumberOfRecoveryPoints`

### AWS Backup Protected Resources
```bash
aws backup list-protected-resources --region <region>
```
Key attributes: `ResourceArn`, `ResourceType`, `LastBackupTime`

---

## Analytics Scan

### Glue Databases
```bash
aws glue get-databases --region <region>
```
Key attributes: `Name`, `LocationUri`, `CreateTime`

### Glue Tables
```bash
# For each Glue database:
aws glue get-tables --database-name <name> --region <region>
```
Key attributes: `Name`, `DatabaseName`, `StorageDescriptor.Location`, `TableType`

### Glue Jobs
```bash
aws glue list-jobs --region <region>
```
Key attributes: `Name`, `Command`, `DefaultArguments`

### Glue Crawlers
```bash
aws glue list-crawlers --region <region>
```
Key attributes: `Name`, `State`, `Targets`, `DatabaseName`

### Athena Workgroups
```bash
aws athena list-work-groups --region <region>
```
Key attributes: `Name`, `State`, `Configuration`

### EMR Clusters
```bash
aws emr list-clusters --region <region>
```
Key attributes: `Id`, `Name`, `Status.State`, `NormalizedInstanceHours`

### OpenSearch Domains
```bash
aws opensearch list-domain-names --region <region>
# Then for each domain:
aws opensearch describe-domain --domain-name <name> --region <region>
```
Key attributes: `DomainName`, `EngineVersion`, `ClusterConfig`, `SnapshotOptions`, `AutoTuneOptions`

Check cross-cluster replication status:
```bash
aws opensearch describe-inbound-connections --region <region>
aws opensearch describe-outbound-connections --region <region>
```

### QuickSight Datasets
```bash
aws quicksight list-data-sets --aws-account-id <account-id> --region <region>
```
Key attributes: `DataSetId`, `Name`, `ImportMode` (metadata only — no data content)

---

## AI/ML Scan

### SageMaker Domains
```bash
aws sagemaker list-domains --region <region>
```
Key attributes: `DomainId`, `DomainName`, `Status`, `HomeEfsFileSystemId`

### SageMaker Endpoints
```bash
aws sagemaker list-endpoints --region <region>
```
Key attributes: `EndpointName`, `EndpointStatus`, `EndpointConfigName`, `CreationTime`

### SageMaker Models
```bash
aws sagemaker list-models --region <region>
```
Key attributes: `ModelName`, `CreationTime`

For each model, get the S3 artifact URI:
```bash
aws sagemaker describe-model --model-name <name> --region <region>
# Capture: PrimaryContainer.ModelDataUrl (S3 URI of model artifact)
```

### SageMaker Feature Groups
```bash
aws sagemaker list-feature-groups --region <region>
```
Key attributes: `FeatureGroupName`, `FeatureGroupStatus`, `OfflineStoreConfig.S3StorageConfig.S3Uri`

### Bedrock Custom Models
```bash
aws bedrock list-custom-models --region <region>
```
Key attributes: `modelArn`, `modelName`, `baseModelId`, `creationTime`

### Bedrock Knowledge Bases
```bash
aws bedrock-agent list-knowledge-bases --region <region>
# Then for each knowledge base:
aws bedrock-agent get-knowledge-base --knowledge-base-id <id> --region <region>
# Capture: storageConfiguration.s3Configuration.bucketArn
```
Key attributes: `knowledgeBaseId`, `name`, `status`, `storageConfiguration`

### Bedrock Agents
```bash
aws bedrock-agent list-agents --region <region>
```
Key attributes: `agentId`, `agentName`, `agentStatus`

### Comprehend Endpoints
```bash
aws comprehend list-endpoints --region <region>
```
Key attributes: `EndpointArn`, `Status`, `ModelArn`

### Rekognition Collections
```bash
aws rekognition list-collections --region <region>
```
Key attributes: `CollectionIds` (list of collection IDs)

---

## Error Handling

Apply the following error handling rules for all AWS CLI commands executed via the MCP server:

### AccessDenied / UnauthorizedOperation
- Log to `scan.skipped_services`: `{ "command": "<cmd>", "service": "<service>", "region": "<region>", "error": "<message>" }`
- Continue scanning remaining services and regions
- Do NOT halt the scan phase

### ThrottlingException / RequestLimitExceeded
- Implement exponential backoff: wait 2s → retry; wait 4s → retry; wait 8s → retry (max 3 retries)
- If all 3 retries fail: log to `scan.skipped_services` and continue
- Do NOT halt the scan phase

### Region Not Enabled
- Log to `scan.skipped_regions`: `{ "region": "<region>", "reason": "region_not_enabled" }`
- Continue scanning other regions

### Invalid JSON Response
- Log raw response to `scan.skipped_services` with error `"invalid_json_response"`
- Continue scanning

### MCP Server Unavailable / Connection Error
- Display: "The AWS API MCP server is not running or is unreachable."
- Check if `awslabs.aws-api-mcp-server` is registered in `~/.kiro/settings/mcp.json`:
  - If missing: run the auto-install flow from `POWER.md → Prerequisites Check → Step 2`
  - If present but not responding: ask the user to reload MCP servers from the Kiro MCP Server view, then retry
- **HALT the scan phase** — do not continue without a working MCP server

---

## Scan Execution Order

Run all service scans **twice** — once for the primary region and once for the recovery region. Store results separately so gap analysis can compare them directly.

For each region in `[primary_region, recovery_region] + additional_regions`:
1. Run all built-in service scans (Compute, Database, Storage, Networking, etc.)
2. Run all custom service scans
3. Store results under `resources.<region>` in `dr-state/resources.json`

Display progress as each region completes:
> "✓ Primary region (`us-east-1`) scan complete — 247 resources found"
> "✓ Recovery region (`us-west-2`) scan complete — 12 resources found"

The difference in resource counts between regions is itself a signal — a recovery region with very few resources likely has significant DR gaps.

---

## Scan Completion

1. Write the full resource inventory to `dr-state/resources.json`, keyed by region:

```json
{
  "scan_timestamp": "<ISO 8601>",
  "primary_region": "us-east-1",
  "recovery_region": "us-west-2",
  "resources": {
    "us-east-1": {
      "ec2": { "instances": [], "amis": [], "volumes": [] },
      "rds": { "instances": [], "clusters": [], "snapshots": [] },
      "s3": { "buckets": [] },
      "route53": { "hosted_zones": [], "health_checks": [], "arc_clusters": [] },
      "elb": { "load_balancers": [] },
      "cloudfront": { "distributions": [] },
      "vpc": { "vpcs": [], "subnets": [], "security_groups": [], "route_tables": [], "nacls": [] },
      "...": "all other built-in service resources"
    },
    "us-west-2": {
      "ec2": { "instances": [], "amis": [], "volumes": [] },
      "rds": { "instances": [], "clusters": [], "snapshots": [] },
      "s3": { "buckets": [] },
      "route53": { "hosted_zones": [], "health_checks": [], "arc_clusters": [] },
      "elb": { "load_balancers": [] },
      "cloudfront": { "distributions": [] },
      "vpc": { "vpcs": [], "subnets": [], "security_groups": [], "route_tables": [], "nacls": [] },
      "...": "all other built-in service resources"
    }
  },
  "custom_resources": {
    "us-east-1": {},
    "us-west-2": {}
  },
  "skipped_services": [],
  "skipped_regions": []
}
```

2. Update `dr-state/core.json`:
   - Set `phases.scan.status = "completed"` (or `"completed_with_warnings"` if skipped items exist)
   - Append `"scan"` to `metadata.completed_phases`
   - Set `phases.scan.resource_counts` to a per-region summary: `{ "us-east-1": 247, "us-west-2": 12 }`
   - Write `core.json` **after** `resources.json` is fully written.

Display a per-region summary table:

```
Scan Complete
=============
Region        | Service          | Resources Found
--------------|------------------|----------------
us-east-1     | EC2 Instances    | 24
us-east-1     | RDS Instances    | 6
us-east-1     | S3 Buckets       | 18
...           | ...              | ...
us-east-1     | TOTAL            | 247
--------------|------------------|----------------
us-west-2     | EC2 Instances    | 0
us-west-2     | RDS Instances    | 1
us-west-2     | S3 Buckets       | 3
...           | ...              | ...
us-west-2     | TOTAL            | 12
```

> ⚠️ **Low recovery region resource count** — If the recovery region has significantly fewer resources than the primary region, this is a strong indicator of DR gaps. The Analyze phase will identify specifics.

If any services or regions were skipped, display a warnings table:

```
Skipped Items
=============
Service/Region   | Reason
-----------------|--------
<service>        | AccessDenied
<region>         | Region not enabled
```

> **Next step:** Run the Analyze phase to classify workload tiers and identify DR gaps by comparing primary and recovery region resources.
