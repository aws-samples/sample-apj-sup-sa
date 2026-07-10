# Cached DR Patterns Reference

This file contains pre-researched CloudFormation YAML snippets and DR pattern references.
It is loaded on-demand by `steering/templates.md` to reduce generation time and ensure consistency.

---

## Recovery VPC Pattern {#recovery-vpc-pattern}

<!-- Source: AWS CloudFormation Best Practices
     https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/best-practices.html -->

Dual-AZ VPC with public and private subnets, Internet Gateway, NAT Gateway, and route tables.

```yaml
# Recovery VPC Pattern
# Source: https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/best-practices.html

Parameters:
  VpcCidr:
    Type: String
    Default: "10.1.0.0/16"
    Description: CIDR block for the recovery VPC
  PublicSubnet1Cidr:
    Type: String
    Default: "10.1.0.0/24"
  PublicSubnet2Cidr:
    Type: String
    Default: "10.1.1.0/24"
  PrivateSubnet1Cidr:
    Type: String
    Default: "10.1.2.0/24"
  PrivateSubnet2Cidr:
    Type: String
    Default: "10.1.3.0/24"
  RecoveryRegion:
    Type: String
    Description: AWS region for the recovery environment

Resources:
  RecoveryVPC:
    Type: AWS::EC2::VPC
    Properties:
      CidrBlock: !Ref VpcCidr
      EnableDnsSupport: true
      EnableDnsHostnames: true
      Tags:
        - Key: Name
          Value: !Sub "recovery-vpc-${AWS::StackName}"
        - Key: Purpose
          Value: DisasterRecovery

  InternetGateway:
    Type: AWS::EC2::InternetGateway
    Properties:
      Tags:
        - Key: Name
          Value: !Sub "recovery-igw-${AWS::StackName}"

  VPCGatewayAttachment:
    Type: AWS::EC2::VPCGatewayAttachment
    Properties:
      VpcId: !Ref RecoveryVPC
      InternetGatewayId: !Ref InternetGateway

  PublicSubnet1:
    Type: AWS::EC2::Subnet
    Properties:
      VpcId: !Ref RecoveryVPC
      CidrBlock: !Ref PublicSubnet1Cidr
      AvailabilityZone: !Select [0, !GetAZs !Ref RecoveryRegion]
      MapPublicIpOnLaunch: true
      Tags:
        - Key: Name
          Value: !Sub "recovery-public-subnet-1-${AWS::StackName}"

  PublicSubnet2:
    Type: AWS::EC2::Subnet
    Properties:
      VpcId: !Ref RecoveryVPC
      CidrBlock: !Ref PublicSubnet2Cidr
      AvailabilityZone: !Select [1, !GetAZs !Ref RecoveryRegion]
      MapPublicIpOnLaunch: true
      Tags:
        - Key: Name
          Value: !Sub "recovery-public-subnet-2-${AWS::StackName}"

  PrivateSubnet1:
    Type: AWS::EC2::Subnet
    Properties:
      VpcId: !Ref RecoveryVPC
      CidrBlock: !Ref PrivateSubnet1Cidr
      AvailabilityZone: !Select [0, !GetAZs !Ref RecoveryRegion]
      Tags:
        - Key: Name
          Value: !Sub "recovery-private-subnet-1-${AWS::StackName}"

  PrivateSubnet2:
    Type: AWS::EC2::Subnet
    Properties:
      VpcId: !Ref RecoveryVPC
      CidrBlock: !Ref PrivateSubnet2Cidr
      AvailabilityZone: !Select [1, !GetAZs !Ref RecoveryRegion]
      Tags:
        - Key: Name
          Value: !Sub "recovery-private-subnet-2-${AWS::StackName}"

  NatGatewayEIP:
    Type: AWS::EC2::EIP
    DependsOn: VPCGatewayAttachment
    Properties:
      Domain: vpc

  NatGateway:
    Type: AWS::EC2::NatGateway
    Properties:
      AllocationId: !GetAtt NatGatewayEIP.AllocationId
      SubnetId: !Ref PublicSubnet1
      Tags:
        - Key: Name
          Value: !Sub "recovery-nat-${AWS::StackName}"

  PublicRouteTable:
    Type: AWS::EC2::RouteTable
    Properties:
      VpcId: !Ref RecoveryVPC
      Tags:
        - Key: Name
          Value: !Sub "recovery-public-rt-${AWS::StackName}"

  PublicRoute:
    Type: AWS::EC2::Route
    DependsOn: VPCGatewayAttachment
    Properties:
      RouteTableId: !Ref PublicRouteTable
      DestinationCidrBlock: "0.0.0.0/0"
      GatewayId: !Ref InternetGateway

  PublicSubnet1RouteTableAssociation:
    Type: AWS::EC2::SubnetRouteTableAssociation
    Properties:
      SubnetId: !Ref PublicSubnet1
      RouteTableId: !Ref PublicRouteTable

  PublicSubnet2RouteTableAssociation:
    Type: AWS::EC2::SubnetRouteTableAssociation
    Properties:
      SubnetId: !Ref PublicSubnet2
      RouteTableId: !Ref PublicRouteTable

  PrivateRouteTable:
    Type: AWS::EC2::RouteTable
    Properties:
      VpcId: !Ref RecoveryVPC
      Tags:
        - Key: Name
          Value: !Sub "recovery-private-rt-${AWS::StackName}"

  PrivateRoute:
    Type: AWS::EC2::Route
    Properties:
      RouteTableId: !Ref PrivateRouteTable
      DestinationCidrBlock: "0.0.0.0/0"
      NatGatewayId: !Ref NatGateway

  PrivateSubnet1RouteTableAssociation:
    Type: AWS::EC2::SubnetRouteTableAssociation
    Properties:
      SubnetId: !Ref PrivateSubnet1
      RouteTableId: !Ref PrivateRouteTable

  PrivateSubnet2RouteTableAssociation:
    Type: AWS::EC2::SubnetRouteTableAssociation
    Properties:
      SubnetId: !Ref PrivateSubnet2
      RouteTableId: !Ref PrivateRouteTable

Outputs:
  VpcId:
    Value: !Ref RecoveryVPC
    Export:
      Name: !Sub "${AWS::StackName}-VpcId"
  PublicSubnet1Id:
    Value: !Ref PublicSubnet1
    Export:
      Name: !Sub "${AWS::StackName}-PublicSubnet1Id"
  PublicSubnet2Id:
    Value: !Ref PublicSubnet2
    Export:
      Name: !Sub "${AWS::StackName}-PublicSubnet2Id"
  PrivateSubnet1Id:
    Value: !Ref PrivateSubnet1
    Export:
      Name: !Sub "${AWS::StackName}-PrivateSubnet1Id"
  PrivateSubnet2Id:
    Value: !Ref PrivateSubnet2
    Export:
      Name: !Sub "${AWS::StackName}-PrivateSubnet2Id"
```

---

## DRS Staging Area Network Pattern {#drs-staging-area-network-pattern}

<!-- Source: AWS Elastic Disaster Recovery Best Practices
     https://docs.aws.amazon.com/drs/latest/userguide/best_practices_drs.html -->

### Replication Subnet CIDR Sizing Table

One replication server is required per source server. Use this table to size the staging subnet:

| EC2 Instance Count | Recommended CIDR | Available IPs |
|--------------------|-----------------|---------------|
| ≤ 14               | /28             | 16            |
| ≤ 30               | /27             | 32            |
| ≤ 62               | /26             | 64            |
| ≤ 126              | /25             | 128           |
| ≤ 254              | /24             | 256           |
| > 254              | /23             | 512 (+ multi-account guidance) |

```yaml
# DRS Staging Area Network Pattern
# Source: https://docs.aws.amazon.com/drs/latest/userguide/best_practices_drs.html

Parameters:
  VpcId:
    Type: AWS::EC2::VPC::Id
    Description: VPC ID where DRS staging subnet will be created
  StagingSubnetCidr:
    Type: String
    Default: "10.1.4.0/24"
    Description: CIDR for DRS replication staging subnet (size based on EC2 instance count)
  RecoveryRegion:
    Type: String

Resources:
  DRSStagingSubnet:
    Type: AWS::EC2::Subnet
    Properties:
      VpcId: !Ref VpcId
      CidrBlock: !Ref StagingSubnetCidr
      AvailabilityZone: !Select [0, !GetAZs !Ref RecoveryRegion]
      Tags:
        - Key: Name
          Value: !Sub "drs-staging-subnet-${AWS::StackName}"
        - Key: Purpose
          Value: DRSReplication

  DRSReplicationSecurityGroup:
    Type: AWS::EC2::SecurityGroup
    Properties:
      GroupDescription: Security group for AWS DRS replication traffic
      VpcId: !Ref VpcId
      # TCP 1500 inbound required for DRS replication data transfer
      SecurityGroupIngress:
        - IpProtocol: tcp
          FromPort: 1500
          ToPort: 1500
          CidrIp: "0.0.0.0/0"
          Description: DRS replication data channel
        - IpProtocol: tcp
          FromPort: 443
          ToPort: 443
          CidrIp: "0.0.0.0/0"
          Description: DRS control channel (HTTPS)
      SecurityGroupEgress:
        - IpProtocol: "-1"
          CidrIp: "0.0.0.0/0"
          Description: Allow all outbound
      Tags:
        - Key: Name
          Value: !Sub "drs-replication-sg-${AWS::StackName}"

Outputs:
  DRSStagingSubnetId:
    Value: !Ref DRSStagingSubnet
    Export:
      Name: !Sub "${AWS::StackName}-DRSStagingSubnetId"
  DRSReplicationSecurityGroupId:
    Value: !Ref DRSReplicationSecurityGroup
    Export:
      Name: !Sub "${AWS::StackName}-DRSReplicationSGId"
```

---

## Route53 Health Check and Failover Pattern {#route53-health-check-and-failover-pattern}

<!-- Source: AWS Route53 Developer Guide — Health Checks and DNS Failover
     https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/dns-failover.html -->

Health check with configurable `FailureThreshold` (default 3) and `RequestInterval` (default 30s), plus primary and secondary failover record sets.

```yaml
# Route53 Health Check and Failover Pattern
# Source: https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/dns-failover.html

Parameters:
  HostedZoneId:
    Type: String
    Description: Route53 Hosted Zone ID
  DomainName:
    Type: String
    Description: Fully qualified domain name (e.g., app.example.com)
  PrimaryEndpoint:
    Type: String
    Description: Primary endpoint IP or hostname
  SecondaryEndpoint:
    Type: String
    Description: Secondary (DR) endpoint IP or hostname
  HealthCheckFailureThreshold:
    Type: Number
    Default: 3
    Description: Number of consecutive health check failures before failover
  HealthCheckRequestInterval:
    Type: Number
    Default: 30
    AllowedValues: [10, 30]
    Description: Health check interval in seconds (10 or 30)
  TTL:
    Type: Number
    Default: 60
    Description: DNS record TTL in seconds

Resources:
  PrimaryHealthCheck:
    Type: AWS::Route53::HealthCheck
    Properties:
      HealthCheckConfig:
        Type: HTTPS
        FullyQualifiedDomainName: !Ref PrimaryEndpoint
        Port: 443
        ResourcePath: "/health"
        FailureThreshold: !Ref HealthCheckFailureThreshold
        RequestInterval: !Ref HealthCheckRequestInterval
        EnableSNI: true
      HealthCheckTags:
        - Key: Name
          Value: !Sub "primary-health-check-${AWS::StackName}"

  PrimaryDNSRecord:
    Type: AWS::Route53::RecordSet
    Properties:
      HostedZoneId: !Ref HostedZoneId
      Name: !Ref DomainName
      Type: A
      TTL: !Ref TTL
      Failover: PRIMARY
      SetIdentifier: "primary"
      HealthCheckId: !Ref PrimaryHealthCheck
      ResourceRecords:
        - !Ref PrimaryEndpoint

  SecondaryDNSRecord:
    Type: AWS::Route53::RecordSet
    Properties:
      HostedZoneId: !Ref HostedZoneId
      Name: !Ref DomainName
      Type: A
      TTL: !Ref TTL
      Failover: SECONDARY
      SetIdentifier: "secondary"
      ResourceRecords:
        - !Ref SecondaryEndpoint

Outputs:
  HealthCheckId:
    Value: !Ref PrimaryHealthCheck
    Export:
      Name: !Sub "${AWS::StackName}-HealthCheckId"
```

---

## AWS Backup Vault and Cross-Region Copy Pattern {#aws-backup-vault-and-cross-region-copy-pattern}

<!-- Source: AWS Backup — Cross-Region Backup
     https://docs.aws.amazon.com/aws-backup/latest/devguide/cross-region-backup.html -->

Backup vault, backup plan with configurable schedule and retention, and cross-region copy rule.

```yaml
# AWS Backup Vault and Cross-Region Copy Pattern
# Source: https://docs.aws.amazon.com/aws-backup/latest/devguide/cross-region-backup.html

Parameters:
  BackupVaultName:
    Type: String
    Default: "dr-backup-vault"
  BackupSchedule:
    Type: String
    Default: "cron(0 5 ? * * *)"
    Description: Cron expression for backup schedule (default: daily at 05:00 UTC)
  BackupRetentionDays:
    Type: Number
    Default: 30
    Description: Number of days to retain backups in primary vault
  CrossRegionRetentionDays:
    Type: Number
    Default: 30
    Description: Number of days to retain cross-region backup copies
  DestinationRegion:
    Type: String
    Description: Target region for cross-region backup copies
  BackupRoleArn:
    Type: String
    Description: IAM role ARN for AWS Backup service

Resources:
  BackupVault:
    Type: AWS::Backup::BackupVault
    Properties:
      BackupVaultName: !Ref BackupVaultName
      BackupVaultTags:
        Purpose: DisasterRecovery

  BackupPlan:
    Type: AWS::Backup::BackupPlan
    Properties:
      BackupPlan:
        BackupPlanName: !Sub "dr-backup-plan-${AWS::StackName}"
        BackupPlanRule:
          - RuleName: DailyBackupWithCrossRegionCopy
            TargetBackupVault: !Ref BackupVaultName
            ScheduleExpression: !Ref BackupSchedule
            StartWindowMinutes: 60
            CompletionWindowMinutes: 180
            Lifecycle:
              DeleteAfterDays: !Ref BackupRetentionDays
            CopyActions:
              - DestinationBackupVaultArn: !Sub
                  - "arn:aws:backup:${DestRegion}:${AWS::AccountId}:backup-vault:${VaultName}"
                  - DestRegion: !Ref DestinationRegion
                    VaultName: !Ref BackupVaultName
                Lifecycle:
                  DeleteAfterDays: !Ref CrossRegionRetentionDays

  BackupSelection:
    Type: AWS::Backup::BackupSelection
    Properties:
      BackupPlanId: !Ref BackupPlan
      BackupSelection:
        SelectionName: "AllTaggedResources"
        IamRoleArn: !Ref BackupRoleArn
        ListOfTags:
          - ConditionType: STRINGEQUALS
            ConditionKey: "backup"
            ConditionValue: "true"

Outputs:
  BackupVaultArn:
    Value: !GetAtt BackupVault.BackupVaultArn
    Export:
      Name: !Sub "${AWS::StackName}-BackupVaultArn"
  BackupPlanId:
    Value: !Ref BackupPlan
    Export:
      Name: !Sub "${AWS::StackName}-BackupPlanId"
```

---

## DRS IAM Roles Pattern {#drs-iam-roles-pattern}

<!-- Source: AWS Elastic Disaster Recovery — IAM Permissions
     https://docs.aws.amazon.com/drs/latest/userguide/security-iam.html -->

IAM roles for DRS agent installation and replication server operation.

```yaml
# DRS IAM Roles Pattern
# Source: https://docs.aws.amazon.com/drs/latest/userguide/security-iam.html

Resources:
  DRSAgentInstallationRole:
    Type: AWS::IAM::Role
    Properties:
      RoleName: "AWSElasticDisasterRecoveryAgentInstallationRole"
      AssumeRolePolicyDocument:
        Version: "2012-10-17"
        Statement:
          - Effect: Allow
            Principal:
              Service: ec2.amazonaws.com
            Action: sts:AssumeRole
      ManagedPolicyArns:
        - arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
      Policies:
        - PolicyName: DRSAgentInstallationPolicy
          PolicyDocument:
            Version: "2012-10-17"
            Statement:
              - Effect: Allow
                Action:
                  - drs:CreateReplicationConfigurationTemplate
                  - drs:DescribeReplicationConfigurationTemplates
                  - drs:UpdateReplicationConfigurationTemplate
                  - drs:DeleteReplicationConfigurationTemplate
                  - drs:DescribeSourceServers
                  - drs:InitializeService
                  - drs:SendClientMetricsForDrs
                  - drs:SendClientLogsForDrs
                Resource: "*"
              - Effect: Allow
                Action:
                  - ec2:DescribeInstances
                  - ec2:DescribeInstanceTypes
                  - ec2:DescribeVolumes
                  - ec2:DescribeSnapshots
                Resource: "*"
      Tags:
        - Key: Purpose
          Value: DRSAgentInstallation

  DRSAgentInstallationInstanceProfile:
    Type: AWS::IAM::InstanceProfile
    Properties:
      InstanceProfileName: "AWSElasticDisasterRecoveryAgentInstallationProfile"
      Roles:
        - !Ref DRSAgentInstallationRole

  DRSReplicationServerRole:
    Type: AWS::IAM::Role
    Properties:
      RoleName: "AWSElasticDisasterRecoveryReplicationServerRole"
      AssumeRolePolicyDocument:
        Version: "2012-10-17"
        Statement:
          - Effect: Allow
            Principal:
              Service: ec2.amazonaws.com
            Action: sts:AssumeRole
      ManagedPolicyArns:
        - arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
      Policies:
        - PolicyName: DRSReplicationServerPolicy
          PolicyDocument:
            Version: "2012-10-17"
            Statement:
              - Effect: Allow
                Action:
                  - drs:CreateReplicationConfigurationTemplate
                  - drs:DescribeReplicationConfigurationTemplates
                  - drs:DescribeSourceServers
                  - drs:GetReplicationConfiguration
                  - drs:UpdateReplicationConfiguration
                  - drs:SendClientMetricsForDrs
                  - drs:SendClientLogsForDrs
                Resource: "*"
              - Effect: Allow
                Action:
                  - ec2:DescribeInstances
                  - ec2:DescribeVolumes
                  - ec2:CreateVolume
                  - ec2:AttachVolume
                  - ec2:DetachVolume
                  - ec2:DeleteVolume
                  - ec2:CreateSnapshot
                  - ec2:DeleteSnapshot
                  - ec2:DescribeSnapshots
                  - ec2:CreateTags
                Resource: "*"
      Tags:
        - Key: Purpose
          Value: DRSReplicationServer

  DRSReplicationServerInstanceProfile:
    Type: AWS::IAM::InstanceProfile
    Properties:
      InstanceProfileName: "AWSElasticDisasterRecoveryReplicationServerProfile"
      Roles:
        - !Ref DRSReplicationServerRole

Outputs:
  DRSAgentInstallationRoleArn:
    Value: !GetAtt DRSAgentInstallationRole.Arn
    Export:
      Name: !Sub "${AWS::StackName}-DRSAgentInstallationRoleArn"
  DRSReplicationServerRoleArn:
    Value: !GetAtt DRSReplicationServerRole.Arn
    Export:
      Name: !Sub "${AWS::StackName}-DRSReplicationServerRoleArn"
  DRSAgentInstallationInstanceProfileArn:
    Value: !GetAtt DRSAgentInstallationInstanceProfile.Arn
    Export:
      Name: !Sub "${AWS::StackName}-DRSAgentInstallationInstanceProfileArn"
```

---

## Multi-Site Active/Active Routing Pattern {#multi-site-activactive-routing-pattern}

<!-- Source: AWS Route53 — Latency-Based Routing
     https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/routing-policy-latency.html
     AWS Global Accelerator Developer Guide
     https://docs.aws.amazon.com/global-accelerator/latest/dg/what-is-global-accelerator.html -->

Route53 latency-based routing records and Global Accelerator listener + endpoint group snippets.

```yaml
# Multi-Site Active/Active Routing Pattern
# Source: https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/routing-policy-latency.html
#         https://docs.aws.amazon.com/global-accelerator/latest/dg/what-is-global-accelerator.html

Parameters:
  HostedZoneId:
    Type: String
  DomainName:
    Type: String
  PrimaryRegion:
    Type: String
  RecoveryRegion:
    Type: String
  PrimaryALBDnsName:
    Type: String
    Description: DNS name of the primary region ALB
  SecondaryALBDnsName:
    Type: String
    Description: DNS name of the recovery region ALB
  PrimaryALBHostedZoneId:
    Type: String
    Description: Hosted zone ID of the primary ALB
  SecondaryALBHostedZoneId:
    Type: String
    Description: Hosted zone ID of the secondary ALB
  TTL:
    Type: Number
    Default: 60

Resources:
  # --- Route53 Latency-Based Routing ---
  PrimaryLatencyRecord:
    Type: AWS::Route53::RecordSet
    Properties:
      HostedZoneId: !Ref HostedZoneId
      Name: !Ref DomainName
      Type: A
      Region: !Ref PrimaryRegion
      SetIdentifier: !Sub "primary-${PrimaryRegion}"
      AliasTarget:
        DNSName: !Ref PrimaryALBDnsName
        HostedZoneId: !Ref PrimaryALBHostedZoneId
        EvaluateTargetHealth: true

  SecondaryLatencyRecord:
    Type: AWS::Route53::RecordSet
    Properties:
      HostedZoneId: !Ref HostedZoneId
      Name: !Ref DomainName
      Type: A
      Region: !Ref RecoveryRegion
      SetIdentifier: !Sub "secondary-${RecoveryRegion}"
      AliasTarget:
        DNSName: !Ref SecondaryALBDnsName
        HostedZoneId: !Ref SecondaryALBHostedZoneId
        EvaluateTargetHealth: true

  # --- Global Accelerator (alternative to Route53 latency routing) ---
  GlobalAccelerator:
    Type: AWS::GlobalAccelerator::Accelerator
    Properties:
      Name: !Sub "dr-global-accelerator-${AWS::StackName}"
      IpAddressType: IPV4
      Enabled: true
      Tags:
        - Key: Purpose
          Value: MultiSiteActiveActive

  GlobalAcceleratorListener:
    Type: AWS::GlobalAccelerator::Listener
    Properties:
      AcceleratorArn: !Ref GlobalAccelerator
      Protocol: TCP
      PortRanges:
        - FromPort: 443
          ToPort: 443
        - FromPort: 80
          ToPort: 80

  PrimaryEndpointGroup:
    Type: AWS::GlobalAccelerator::EndpointGroup
    Properties:
      ListenerArn: !Ref GlobalAcceleratorListener
      EndpointGroupRegion: !Ref PrimaryRegion
      TrafficDialPercentage: 50
      HealthCheckProtocol: HTTPS
      HealthCheckPort: 443
      HealthCheckPath: "/health"
      ThresholdCount: 3
      EndpointConfigurations:
        - EndpointId: !Ref PrimaryALBDnsName
          Weight: 128
          ClientIPPreservationEnabled: true

  SecondaryEndpointGroup:
    Type: AWS::GlobalAccelerator::EndpointGroup
    Properties:
      ListenerArn: !Ref GlobalAcceleratorListener
      EndpointGroupRegion: !Ref RecoveryRegion
      TrafficDialPercentage: 50
      HealthCheckProtocol: HTTPS
      HealthCheckPort: 443
      HealthCheckPath: "/health"
      ThresholdCount: 3
      EndpointConfigurations:
        - EndpointId: !Ref SecondaryALBDnsName
          Weight: 128
          ClientIPPreservationEnabled: true

Outputs:
  GlobalAcceleratorArn:
    Value: !GetAtt GlobalAccelerator.AcceleratorArn
    Export:
      Name: !Sub "${AWS::StackName}-GlobalAcceleratorArn"
  GlobalAcceleratorDnsName:
    Value: !GetAtt GlobalAccelerator.DnsName
    Export:
      Name: !Sub "${AWS::StackName}-GlobalAcceleratorDnsName"
```

---

## DR Strategy Cost-Complexity Reference Table {#dr-strategy-cost-complexity-reference-table}

<!-- Source: AWS Disaster Recovery Workloads on AWS Whitepaper
     https://docs.aws.amazon.com/whitepapers/latest/disaster-recovery-workloads-on-aws/disaster-recovery-options-in-the-cloud.html
     AWS DR Architecture Series (Parts I–IV) by Seth Eliot
     https://aws.amazon.com/blogs/architecture/tag/disaster-recovery-series/ -->

Four-quadrant summary of AWS DR strategies with RTO/RPO ranges and relative cost.

| Strategy | RTO Range | RPO Range | Relative Cost | Complexity | Best For |
|----------|-----------|-----------|---------------|------------|----------|
| **Backup & Restore** | Hours to days | Hours to days | $ (Lowest) | Low | Tier 4 Non-Critical; cost-sensitive workloads with relaxed recovery targets |
| **Pilot Light** | Tens of minutes to hours | Minutes to hours | $$ | Medium | Tier 3 Standard; workloads needing faster recovery than Backup & Restore but not full standby |
| **Warm Standby** | Minutes | Seconds to minutes | $$$ | Medium-High | Tier 2 Important; business-critical workloads requiring near-continuous availability |
| **Hot Standby** | Near-zero | Near-zero | $$$$ | High | Tier 1 Critical variant; Warm Standby at full production capacity |
| **Multi-Site Active/Active** | Near-zero (seconds) | Near-zero (seconds) | $$$$ (Highest) | High | Tier 1 Critical; mission-critical workloads with zero-downtime requirements |

### Strategy Details

**Backup & Restore**
- Infrastructure is not pre-provisioned in the DR region
- Data is backed up and copied to DR region via AWS Backup or S3 CRR
- Recovery requires provisioning all infrastructure from scratch using CloudFormation/CDK
- Use **EC2 Image Builder** to create golden AMIs and copy them to the recovery region
- Use **EventBridge + Lambda + Step Functions** to automate detection and restore orchestration
- Lowest cost but highest RTO/RPO
- Source: [Part II](https://aws.amazon.com/blogs/architecture/disaster-recovery-dr-architecture-on-aws-part-ii-backup-and-restore-with-rapid-recovery/)

**Pilot Light**
- Core data replication is always running (e.g., Aurora Global Database, RDS cross-region replica)
- Minimal compute infrastructure is pre-provisioned (e.g., EC2 Auto Scaling group with 0 desired capacity)
- On failover: deploy EC2 instances from golden AMIs, scale up, redirect traffic
- Use CloudFormation `ActiveOrPassive` parameter pattern to manage zero vs N instances
- Moderate cost; faster recovery than Backup & Restore
- Source: [Part III](https://aws.amazon.com/blogs/architecture/disaster-recovery-dr-architecture-on-aws-part-iii-pilot-light-and-warm-standby/)

**Warm Standby**
- Scaled-down but fully functional copy of production runs in DR region (minimum 1 instance per tier)
- Recovery region endpoint can handle requests at reduced capacity immediately
- On failover: scale up to full capacity via Auto Scaling, redirect traffic via Route 53 or Global Accelerator
- Use CloudFormation `ActiveOrPassive` parameter pattern; set `DesiredCapacity` to reduced value
- **Hot Standby** variant: same as Warm Standby but at full production capacity (higher cost, near-zero RTO)
- Higher cost; recovery in minutes
- Source: [Part III](https://aws.amazon.com/blogs/architecture/disaster-recovery-dr-architecture-on-aws-part-iii-pilot-light-and-warm-standby/)

**Multi-Site Active/Active**
- Full production capacity runs simultaneously in both regions
- Three write patterns (choose based on consistency requirements):
  - **Read-local/Write-local**: writes go to local region; use DynamoDB Global Tables (last-writer-wins); best for eventual consistency
  - **Read-local/Write-global**: all writes route to a designated global write region; use Aurora Global Database with write forwarding or ElastiCache Global Datastore
  - **Read-local/Write-partitioned**: each record has a home region based on partition key; writes route to home region; best for write-heavy globally distributed workloads
- Traffic routing: Route 53 latency-based or geolocation routing, or Global Accelerator
- On failure: traffic automatically shifts to healthy region; if write-global, promote new global write region
- Highest cost; near-zero RTO/RPO
- Source: [Part IV](https://aws.amazon.com/blogs/architecture/disaster-recovery-dr-architecture-on-aws-part-iv-multi-site-active-active/)

---

## Analytics DR Patterns {#analytics-dr-patterns}

<!-- Sources:
     Redshift DR: https://aws.amazon.com/blogs/big-data/implement-disaster-recovery-with-amazon-redshift/
     OpenSearch DR: https://aws.amazon.com/blogs/big-data/achieve-data-resilience-using-amazon-opensearch-service-disaster-recovery-with-snapshot-and-restore/ -->

### Redshift Cross-Region Snapshot Copy

```yaml
# Redshift Cross-Region Snapshot Copy
# Source: https://aws.amazon.com/blogs/big-data/implement-disaster-recovery-with-amazon-redshift/

# Enable cross-region snapshot copy via AWS CLI (CloudFormation does not directly support
# EnableSnapshotCopy; use a Custom Resource or CLI post-deployment):
#
# aws redshift enable-snapshot-copy \
#   --cluster-identifier <cluster-id> \
#   --destination-region <dr-region> \
#   --retention-period 7 \
#   --manual-snapshot-retention-period 30

# CloudFormation Custom Resource approach:
RedshiftSnapshotCopyCustomResource:
  Type: AWS::CloudFormation::CustomResource
  Properties:
    ServiceToken: !GetAtt RedshiftSnapshotCopyFunction.Arn
    ClusterIdentifier: !Ref RedshiftClusterIdentifier
    DestinationRegion: !Ref DestinationRegion
    RetentionPeriod: !Ref SnapshotRetentionDays
```

### OpenSearch Cross-Cluster Replication

```yaml
# OpenSearch Cross-Cluster Replication Configuration Reference
# Source: https://aws.amazon.com/blogs/big-data/achieve-data-resilience-using-amazon-opensearch-service-disaster-recovery-with-snapshot-and-restore/
#
# OpenSearch cross-cluster replication is configured via the OpenSearch API, not CloudFormation.
# Use the following approach:
#
# 1. Create a connection from the follower (DR) domain to the leader (primary) domain:
#    POST https://<follower-domain>/_plugins/_replication/_autofollow
#    {
#      "leader_alias": "<connection-alias>",
#      "name": "<replication-rule-name>",
#      "pattern": "index-*",
#      "use_roles": {
#        "leader_cluster_role": "all_access",
#        "follower_cluster_role": "all_access"
#      }
#    }
#
# 2. For snapshot-based DR (simpler alternative):
#    - Register S3 as snapshot repository on both domains
#    - Schedule automated snapshots to S3
#    - Restore from S3 snapshot in DR region on failover
#
# S3 Snapshot Repository Registration:
#    POST https://<domain>/_snapshot/<repo-name>
#    {
#      "type": "s3",
#      "settings": {
#        "bucket": "<s3-bucket-name>",
#        "region": "<primary-region>",
#        "role_arn": "<iam-role-arn>"
#      }
#    }
```

### Glue Catalog Export/Import

```yaml
# Glue Data Catalog Export/Import for DR
# Source: https://docs.aws.amazon.com/glue/latest/dg/what-is-glue.html
#
# The Glue Data Catalog does not support native cross-region replication.
# Use the following approach for DR:
#
# Export (primary region):
#   aws glue get-databases --region <primary-region> > glue-databases.json
#   aws glue get-tables --database-name <db-name> --region <primary-region> > glue-tables.json
#
# Import (DR region) — use AWS Glue API or a Lambda function triggered by EventBridge:
#   aws glue create-database --database-input file://glue-databases.json --region <dr-region>
#   aws glue create-table --database-name <db-name> --table-input file://glue-tables.json --region <dr-region>
#
# Recommended: Use AWS Glue DataBrew or a scheduled Lambda to sync catalog metadata
# between regions as part of the DR runbook.
```

---

## AI/ML DR Patterns {#aiml-dr-patterns}

<!-- Sources:
     SageMaker Cross-Region DR: https://aws.amazon.com/blogs/machine-learning/implement-amazon-sagemaker-domain-cross-region-disaster-recovery-using-custom-amazon-efs-instances/
     Well-Architected GenAI Lens: https://docs.aws.amazon.com/wellarchitected/latest/generative-ai-lens/genrel05-bp02.html -->

### SageMaker Model Artifact S3 Backup

```yaml
# SageMaker Model Artifact S3 Backup Approach
# Source: https://aws.amazon.com/blogs/machine-learning/implement-amazon-sagemaker-domain-cross-region-disaster-recovery-using-custom-amazon-efs-instances/
#
# SageMaker model artifacts are stored in S3. Enable CRR on the model artifact bucket:

SageMakerModelArtifactBucketReplication:
  Type: AWS::S3::BucketPolicy
  # Note: CRR is configured on the source bucket in the primary region.
  # The following is a reference for the replication configuration:
  #
  # ReplicationConfiguration:
  #   Role: !GetAtt S3ReplicationRole.Arn
  #   Rules:
  #     - Id: SageMakerModelArtifactReplication
  #       Status: Enabled
  #       Filter:
  #         Prefix: "models/"
  #       Destination:
  #         Bucket: !Sub "arn:aws:s3:::${DRModelArtifactBucket}"
  #         StorageClass: STANDARD
  #
  # Recovery procedure:
  # 1. Identify model artifact S3 URI from SageMaker model: PrimaryContainer.ModelDataUrl
  # 2. Verify CRR has replicated artifact to DR bucket
  # 3. Create new SageMaker model in DR region pointing to replicated S3 URI
  # 4. Create endpoint configuration and deploy endpoint in DR region
```

### Bedrock Knowledge Base S3 CRR Requirement

```yaml
# Bedrock Knowledge Base S3 CRR Requirement
# Source: https://docs.aws.amazon.com/wellarchitected/latest/generative-ai-lens/genrel05-bp02.html
#
# Bedrock Knowledge Bases use S3 as the backing data source.
# For DR, enable CRR on the knowledge base S3 bucket:
#
# 1. Identify the backing S3 bucket:
#    aws bedrock-agent get-knowledge-base --knowledge-base-id <id>
#    Look for: storageConfiguration.s3Configuration.bucketArn
#
# 2. Enable CRR on the identified bucket (see Recovery VPC pattern for S3 CRR config)
#
# 3. In the DR region, create a new Bedrock Knowledge Base pointing to the replicated bucket:
#    aws bedrock-agent create-knowledge-base \
#      --name "<kb-name>-dr" \
#      --role-arn <role-arn> \
#      --knowledge-base-configuration type=VECTOR,vectorKnowledgeBaseConfiguration=... \
#      --storage-configuration type=S3,s3Configuration={bucketArn=<dr-bucket-arn>}
#
# 4. Sync the knowledge base after failover:
#    aws bedrock-agent start-ingestion-job \
#      --knowledge-base-id <dr-kb-id> \
#      --data-source-id <data-source-id>
```

### SageMaker Domain EFS Cross-Region Replication

```yaml
# SageMaker Domain EFS Cross-Region Replication
# Source: https://aws.amazon.com/blogs/machine-learning/implement-amazon-sagemaker-domain-cross-region-disaster-recovery-using-custom-amazon-efs-instances/
#
# SageMaker Domains use EFS for user profile home directories and shared spaces.
# Enable EFS replication to the DR region:

SageMakerDomainEFSReplication:
  Type: AWS::EFS::ReplicationConfiguration
  Properties:
    SourceFileSystemId: !Ref SageMakerDomainEFSId
    Destinations:
      - Region: !Ref DestinationRegion
        # KmsKeyId: !Ref DRKMSKeyId  # Optional: specify KMS key in DR region

# Recovery procedure:
# 1. Identify the SageMaker Domain EFS file system ID:
#    aws sagemaker describe-domain --domain-id <domain-id>
#    Look for: HomeEfsFileSystemId
#
# 2. Verify EFS replication status:
#    aws efs describe-replication-configurations --source-file-system-id <efs-id>
#
# 3. In DR region, create a new SageMaker Domain using the replicated EFS:
#    aws sagemaker create-domain \
#      --domain-name "<domain-name>-dr" \
#      --auth-mode IAM \
#      --default-user-settings ... \
#      --home-efs-file-system-id <replicated-efs-id>
#
# Note: SageMaker Domain cross-region DR requires custom EFS instances.
# The replicated EFS must be in the same VPC as the DR Domain or accessible via VPC peering.
```

---

## Active/Passive CloudFormation Pattern {#active-passive-cloudformation-pattern}

<!-- Source: AWS DR Architecture Series, Part III — Pilot Light and Warm Standby
     https://aws.amazon.com/blogs/architecture/disaster-recovery-dr-architecture-on-aws-part-iii-pilot-light-and-warm-standby/
     Full template: https://www.wellarchitectedlabs.com/Reliability/Common/Code/CloudFormation/staticwebapp-active-passive.yaml -->

Single CloudFormation template that deploys either an active (primary) or passive (recovery) stack using a parameter. Used for both Pilot Light (0 instances when passive) and Warm Standby (reduced instances when passive).

```yaml
# Active/Passive CloudFormation Pattern
# Source: https://aws.amazon.com/blogs/architecture/disaster-recovery-dr-architecture-on-aws-part-iii-pilot-light-and-warm-standby/
# Full reference template: https://www.wellarchitectedlabs.com/Reliability/Common/Code/CloudFormation/staticwebapp-active-passive.yaml

Parameters:
  ActiveOrPassive:
    Default: "active"
    Description: Is this the active (primary) deployment or the passive (recovery) deployment?
    Type: String
    AllowedValues:
      - active
      - passive
    ConstraintDescription: Enter active or passive, all lowercase

  Web1AutoScaleDesired:
    Default: "3"
    Description: Desired number of instances in the active deployment
    Type: Number

  Web1AutoScaleMax:
    Default: "6"
    Description: Maximum number of instances in auto scaling group
    Type: Number

# Determine whether this is an active stack or passive stack
Conditions:
  IsActive: !Equals [!Ref ActiveOrPassive, "active"]

Resources:
  WebAppAutoScalingGroup:
    Type: AWS::AutoScaling::AutoScalingGroup
    Properties:
      # Pilot Light: 0 instances when passive, N when active
      # Warm Standby: set a reduced minimum (e.g., 1) instead of 0
      MinSize: !If [IsActive, !Ref Web1AutoScaleDesired, "0"]
      MaxSize: !Ref Web1AutoScaleMax
      DesiredCapacity: !If [IsActive, !Ref Web1AutoScaleDesired, "0"]
      # ... other properties

# To activate the passive stack (failover), run:
# aws cloudformation update-stack \
#   --stack-name <recovery-stack-name> \
#   --use-previous-template \
#   --capabilities CAPABILITY_NAMED_IAM \
#   --parameters ParameterKey=ActiveOrPassive,ParameterValue=active
```

---

## Disaster Detection Architecture {#disaster-detection-architecture}

<!-- Source: AWS DR Architecture Series, Part II — Backup and Restore with Rapid Recovery
     https://aws.amazon.com/blogs/architecture/disaster-recovery-dr-architecture-on-aws-part-ii-backup-and-restore-with-rapid-recovery/
     AWS DR Architecture Series, Part III — Pilot Light and Warm Standby
     https://aws.amazon.com/blogs/architecture/disaster-recovery-dr-architecture-on-aws-part-iii-pilot-light-and-warm-standby/ -->

Automated detection is critical for low RTO. Do not wait for operators or customers to notice — automate detection using the following pattern.

### Detection Sources

**1. CloudWatch Alarms** — metric-based health checks:
- Server liveness metrics (ping) alone are insufficient
- Service API error rates and response latencies
- **CloudWatch Synthetics canaries** — scripts that call your service and validate responses; best insight into workload health
- **CloudWatch Anomaly Detection** — detects if KPIs (e.g., order rates, active sessions) drop unexpectedly

**2. AWS Health Events** — AWS service disruptions affecting your account:
```json
{
  "source": ["aws.health"],
  "detail-type": ["AWS Health Event"],
  "detail": {
    "service": ["S3"],
    "eventTypeCategory": ["issue"],
    "eventTypeCode": ["AWS_S3_INCREASED_GET_API_ERROR_RATES", "AWS_S3_INCREASED_PUT_API_ERROR_RATES"]
  }
}
```

### EventBridge Detection + Response Pattern

```yaml
# EventBridge rule reacting to CloudWatch Alarm state change
DetectionRule:
  Type: AWS::Events::Rule
  Properties:
    EventPattern:
      source: ["aws.cloudwatch"]
      detail-type: ["CloudWatch Alarm State Change"]
      detail:
        state:
          value: ["ALARM"]
        alarmName:
          prefix: "workload-health-"
    Targets:
      # Option A: Create OpsItem in Systems Manager for tracking
      - Arn: !Sub "arn:aws:ssm:${AWS::Region}:${AWS::AccountId}:opsitem"
        Id: "OpsItemTarget"
      # Option B: Invoke Lambda for automated response
      - Arn: !GetAtt DisasterResponseFunction.Arn
        Id: "LambdaTarget"
      # Option C: Send SNS notification to on-call team
      - Arn: !Ref AlertTopic
        Id: "SNSTarget"

# CloudWatch Synthetics canary for workload health validation
HealthCanary:
  Type: AWS::Synthetics::Canary
  Properties:
    Name: workload-health-canary
    RuntimeVersion: syn-nodejs-puppeteer-6.2
    Schedule:
      Expression: rate(1 minute)
    Code:
      Handler: pageLoadBlueprint.handler
      S3Bucket: !Ref CanaryArtifactBucket
      S3Key: canary-script.zip
    RunConfig:
      TimeoutInSeconds: 60
    SuccessRetentionPeriod: 31
    FailureRetentionPeriod: 31
    ExecutionRoleArn: !GetAtt CanaryRole.Arn
```

### Step Functions DR Orchestration

For comprehensive automation, use Step Functions to orchestrate the full restore sequence:

```yaml
# Step Functions state machine for DR orchestration
DROrchestrationStateMachine:
  Type: AWS::StepFunctions::StateMachine
  Properties:
    StateMachineName: dr-restore-orchestration
    Definition:
      Comment: Orchestrates DR restore — infrastructure deploy, data restore, integration
      StartAt: DeployRecoveryInfrastructure
      States:
        DeployRecoveryInfrastructure:
          Type: Task
          Resource: arn:aws:states:::cloudformation:createStack.sync
          Parameters:
            StackName: recovery-stack
            TemplateURL: !Sub "s3://${TemplateBucket}/recovery-template.yaml"
            Parameters:
              - ParameterKey: ActiveOrPassive
                ParameterValue: active
          Next: RestoreDataFromBackup

        RestoreDataFromBackup:
          Type: Task
          Resource: arn:aws:states:::aws-sdk:backup:startRestoreJob
          Parameters:
            RecoveryPointArn.$: "$.recoveryPointArn"
            Metadata.$: "$.restoreMetadata"
            IamRoleArn: !GetAtt BackupRestoreRole.Arn
          Next: WaitForRestore

        WaitForRestore:
          Type: Wait
          Seconds: 60
          Next: CheckRestoreStatus

        CheckRestoreStatus:
          Type: Task
          Resource: arn:aws:states:::aws-sdk:backup:describeRestoreJob
          Parameters:
            RestoreJobId.$: "$.restoreJobId"
          Next: IsRestoreComplete

        IsRestoreComplete:
          Type: Choice
          Choices:
            - Variable: "$.Status"
              StringEquals: COMPLETED
              Next: IntegrateResources
            - Variable: "$.Status"
              StringEquals: FAILED
              Next: RestoreFailed
          Default: WaitForRestore

        IntegrateResources:
          Type: Task
          Resource: !GetAtt IntegrationFunction.Arn
          End: true

        RestoreFailed:
          Type: Fail
          Error: RestoreJobFailed
```

---

## Route 53 Application Recovery Controller (ARC) Pattern {#route53-arc-pattern}

<!-- Source: AWS DR Architecture Series, Part III — Pilot Light and Warm Standby
     https://aws.amazon.com/blogs/architecture/disaster-recovery-dr-architecture-on-aws-part-iii-pilot-light-and-warm-standby/
     AWS Route 53 ARC Documentation
     https://docs.aws.amazon.com/r53recovery/latest/dg/what-is-route53-recovery.html -->

Route 53 ARC provides manually-initiated failover using data-plane health check switches — more resilient than control-plane operations like weighted routing changes.

```yaml
# Route 53 ARC — Readiness Check and Routing Control
# Source: https://docs.aws.amazon.com/r53recovery/latest/dg/what-is-route53-recovery.html

# Routing controls act as on/off switches for Route 53 health checks
# Use the data-plane API (not console) for failover — more resilient

# CLI commands for failover using Route 53 ARC:
#
# 1. List routing controls to find the ARNs:
#    aws route53-recovery-control-config list-routing-controls \
#      --control-panel-arn <control-panel-arn>
#
# 2. Toggle routing control to initiate failover (data-plane operation):
#    aws route53-recovery-cluster update-routing-control-state \
#      --routing-control-arn <primary-routing-control-arn> \
#      --routing-control-state Off
#    aws route53-recovery-cluster update-routing-control-state \
#      --routing-control-arn <recovery-routing-control-arn> \
#      --routing-control-state On
#
# 3. Verify DNS has updated:
#    dig <your-domain> @8.8.8.8

# CloudFormation for Route 53 ARC Cluster and Control Panel
ARCCluster:
  Type: AWS::Route53RecoveryControl::Cluster
  Properties:
    Name: !Sub "dr-cluster-${AWS::StackName}"

ARCControlPanel:
  Type: AWS::Route53RecoveryControl::ControlPanel
  Properties:
    ClusterArn: !GetAtt ARCCluster.ClusterArn
    Name: !Sub "dr-control-panel-${AWS::StackName}"

PrimaryRoutingControl:
  Type: AWS::Route53RecoveryControl::RoutingControl
  Properties:
    ClusterArn: !GetAtt ARCCluster.ClusterArn
    ControlPanelArn: !GetAtt ARCControlPanel.ControlPanelArn
    Name: primary-routing-control

RecoveryRoutingControl:
  Type: AWS::Route53RecoveryControl::RoutingControl
  Properties:
    ClusterArn: !GetAtt ARCCluster.ClusterArn
    ControlPanelArn: !GetAtt ARCControlPanel.ControlPanelArn
    Name: recovery-routing-control

# Route 53 health checks backed by ARC routing controls
PrimaryARCHealthCheck:
  Type: AWS::Route53::HealthCheck
  Properties:
    HealthCheckConfig:
      Type: RECOVERY_CONTROL
      RoutingControlArn: !GetAtt PrimaryRoutingControl.RoutingControlArn

RecoveryARCHealthCheck:
  Type: AWS::Route53::HealthCheck
  Properties:
    HealthCheckConfig:
      Type: RECOVERY_CONTROL
      RoutingControlArn: !GetAtt RecoveryRoutingControl.RoutingControlArn
```

---

## Aurora Global Database Pattern {#aurora-global-database-pattern}

<!-- Source: AWS DR Architecture Series, Part IV — Multi-site Active/Active
     https://aws.amazon.com/blogs/architecture/disaster-recovery-dr-architecture-on-aws-part-iv-multi-site-active-active/
     Aurora Global Database Documentation
     https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-global-database.html -->

Aurora Global Database for read-local/write-global pattern. Primary cluster accepts writes; secondary clusters in other regions forward writes to primary and serve reads locally.

```yaml
# Aurora Global Database — Read-local/Write-global Pattern
# Source: https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-global-database.html

Parameters:
  PrimaryRegion:
    Type: String
  RecoveryRegion:
    Type: String
  DBMasterUsername:
    Type: String
    NoEcho: true
  DBMasterPassword:
    Type: String
    NoEcho: true

Resources:
  # Primary cluster (global write region)
  AuroraGlobalCluster:
    Type: AWS::RDS::GlobalCluster
    Properties:
      GlobalClusterIdentifier: !Sub "global-cluster-${AWS::StackName}"
      Engine: aurora-postgresql
      EngineVersion: "15.4"
      StorageEncrypted: true

  PrimaryDBCluster:
    Type: AWS::RDS::DBCluster
    Properties:
      GlobalClusterIdentifier: !Ref AuroraGlobalCluster
      Engine: aurora-postgresql
      EngineVersion: "15.4"
      MasterUsername: !Ref DBMasterUsername
      MasterUserPassword: !Ref DBMasterPassword
      EnableGlobalWriteForwarding: false  # Primary does not need write forwarding
      BackupRetentionPeriod: 7
      StorageEncrypted: true

  # Secondary cluster (recovery region) — deploy this in the recovery region stack
  # SecondaryDBCluster:
  #   Type: AWS::RDS::DBCluster
  #   Properties:
  #     GlobalClusterIdentifier: !Ref AuroraGlobalCluster  # Same global cluster ID
  #     Engine: aurora-postgresql
  #     EngineVersion: "15.4"
  #     EnableGlobalWriteForwarding: true  # Forwards writes to primary region
  #     StorageEncrypted: true

# Failover promotion (run in recovery region when primary fails):
# aws rds failover-global-cluster \
#   --global-cluster-identifier global-cluster-<name> \
#   --target-db-cluster-identifier <recovery-cluster-arn>
```

---

## ElastiCache Global Datastore Pattern {#elasticache-global-datastore-pattern}

<!-- Source: AWS DR Architecture Series, Part IV — Multi-site Active/Active
     https://aws.amazon.com/blogs/architecture/disaster-recovery-dr-architecture-on-aws-part-iv-multi-site-active-active/
     ElastiCache Well-Architected Lens — Reliability Pillar
     https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/ReliabilityPillar.html
     ElastiCache Global Datastore Documentation
     https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/Redis-Global-Datastore.html -->

### ElastiCache DR Options by Tier

| Option | Scope | RPO | RTO | Best For |
|--------|-------|-----|-----|---------|
| Multi-AZ (cluster mode enabled) | In-region | Near-zero | Seconds (auto-failover) | All tiers — baseline HA |
| Global Datastore | Cross-region | Seconds (replication lag) | Minutes | Tier 1, 2 — cross-region DR |
| Snapshot + restore | Cross-region | Hours | Hours | Tier 3, 4 — Backup & Restore |

> **Important:** ElastiCache for **Memcached** has NO replication and NO backup capability — it is purely ephemeral. If Memcached is used for session state or any persistent data, flag this as a critical DR gap regardless of tier.

> **Engine note:** ElastiCache now supports **Valkey** and **Redis OSS**. Both support the same DR options (Global Datastore, Multi-AZ, snapshots). Memcached supports none.

### In-Region HA (All Tiers — Required Baseline)

```yaml
# ElastiCache Cluster Mode Enabled — Multi-AZ HA
# Source: https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/ReliabilityPillar.html
#
# Best practices:
# - Cluster mode enabled (CME) — Multi-AZ is automatic
# - Minimum 2 replicas per shard for read availability
# - Minimum 3 shards for quorum during failover (Redis Cluster Protocol requires majority)
# - Use Graviton2-based node types for better replication performance
# - Monitor: BytesUsedForCache, DatabaseMemoryUsagePercentage, ReplicationLag

PrimaryReplicationGroup:
  Type: AWS::ElastiCache::ReplicationGroup
  Properties:
    ReplicationGroupDescription: "Production cache — cluster mode enabled"
    Engine: redis  # or valkey
    CacheNodeType: cache.r6g.large  # Graviton2 recommended
    NumNodeGroups: 3          # Minimum 3 shards for quorum
    ReplicasPerNodeGroup: 2   # Minimum 2 replicas per shard
    AutomaticFailoverEnabled: true
    MultiAZEnabled: true
    AtRestEncryptionEnabled: true
    TransitEncryptionEnabled: true
    SnapshotRetentionLimit: 7  # Days — for RPO via snapshots
    SnapshotWindow: "03:00-04:00"
```

### Global Datastore — Cross-Region Replication (Tier 1, 2)

```yaml
# ElastiCache Global Datastore — Cross-Region Session Replication
# Source: https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/Redis-Global-Datastore.html
#
# Global Datastore: 1 primary cluster + up to 2 secondary clusters in other regions
# Replication: asynchronous, RPO = ReplicationLag (typically milliseconds to seconds)
# Failover: manual promotion of secondary to primary

# Note: Global Datastore is configured via CLI/API, not directly in CloudFormation.

# Step 1: Create the primary replication group (in primary region):
# aws elasticache create-replication-group \
#   --replication-group-id primary-session-cache \
#   --replication-group-description "Primary session cache" \
#   --engine redis \
#   --cache-node-type cache.r6g.large \
#   --num-node-groups 3 \
#   --replicas-per-node-group 2 \
#   --automatic-failover-enabled \
#   --multi-az-enabled \
#   --region <primary-region>

# Step 2: Create the Global Datastore linking primary and secondary:
# aws elasticache create-global-replication-group \
#   --global-replication-group-id-suffix session-global \
#   --primary-replication-group-id primary-session-cache \
#   --region <primary-region>

# Step 3: Add secondary region to the Global Datastore:
# aws elasticache create-replication-group \
#   --replication-group-id secondary-session-cache \
#   --replication-group-description "Secondary session cache" \
#   --global-replication-group-id global-session-global \
#   --region <recovery-region>

# Monitoring replication lag (critical for RPO tracking):
# aws cloudwatch get-metric-statistics \
#   --namespace AWS/ElastiCache \
#   --metric-name ReplicationLag \
#   --dimensions Name=ReplicationGroupId,Value=secondary-session-cache \
#   --start-time <start> --end-time <end> \
#   --period 60 --statistics Average \
#   --region <recovery-region>

# Failover — promote secondary to primary:
# aws elasticache failover-global-replication-group \
#   --global-replication-group-id global-session-global \
#   --primary-region <recovery-region> \
#   --primary-replication-group-id secondary-session-cache

# Pre-production failover testing (use TestFailover API):
# aws elasticache test-failover \
#   --replication-group-id <replication-group-id> \
#   --node-group-id <shard-id> \
#   --region <region>

# CloudFormation for the primary replication group:
SessionCacheReplicationGroup:
  Type: AWS::ElastiCache::ReplicationGroup
  Properties:
    ReplicationGroupDescription: "Session cache with Global Datastore"
    Engine: redis
    CacheNodeType: cache.r6g.large
    NumNodeGroups: 3
    ReplicasPerNodeGroup: 2
    AutomaticFailoverEnabled: true
    MultiAZEnabled: true
    AtRestEncryptionEnabled: true
    TransitEncryptionEnabled: true
    SnapshotRetentionLimit: 7
```

---

## MemoryDB for Redis Multi-Region Pattern {#memorydb-multi-region-pattern}

<!-- Source: MemoryDB Multi-Region Documentation
     https://docs.aws.amazon.com/memorydb/latest/devguide/multi-Region.monitoring.html
     MemoryDB Developer Guide
     https://docs.aws.amazon.com/memorydb/latest/devguide/what-is-memorydb.html -->

MemoryDB for Redis is a durable, Redis-compatible in-memory database (not just a cache). It supports native **Multi-Region clusters** — distinct from ElastiCache Global Datastore.

> **Key difference from ElastiCache:** MemoryDB persists all data to a Multi-AZ transaction log, providing durability guarantees that ElastiCache does not. Use MemoryDB when Redis data must survive node failures without data loss.

```yaml
# MemoryDB Multi-Region Cluster
# Source: https://docs.aws.amazon.com/memorydb/latest/devguide/multi-Region.monitoring.html
#
# Multi-Region cluster: active in multiple regions simultaneously
# Replication: asynchronous between regional clusters
# Monitoring metric: MultiRegionClusterReplicationLag (milliseconds, per shard)
#
# An elevated MultiRegionClusterReplicationLag indicates a region is falling behind.
# If a region becomes isolated, redirect reads/writes to a healthy region.

# CLI: Create a Multi-Region MemoryDB cluster
# aws memorydb create-multi-region-cluster \
#   --multi-region-cluster-name-suffix my-app-cache \
#   --node-type db.r6g.large \
#   --engine-version 7.1 \
#   --region <primary-region>

# CLI: Add a regional cluster to the Multi-Region cluster
# aws memorydb create-cluster \
#   --cluster-name my-app-cache-recovery \
#   --multi-region-cluster-name multi-region-my-app-cache \
#   --node-type db.r6g.large \
#   --subnet-group-name <recovery-subnet-group> \
#   --region <recovery-region>

# CloudWatch monitoring for MemoryDB Multi-Region:
MemoryDBReplicationLagAlarm:
  Type: AWS::CloudWatch::Alarm
  Properties:
    AlarmName: !Sub "memorydb-replication-lag-${AWS::StackName}"
    Namespace: AWS/MemoryDB
    MetricName: MultiRegionClusterReplicationLag
    Dimensions:
      - Name: ClusterName
        Value: !Ref MemoryDBClusterName
    Statistic: Average
    Period: 60
    EvaluationPeriods: 3
    Threshold: 5000  # 5 seconds — adjust based on RPO target
    ComparisonOperator: GreaterThanThreshold
    AlarmActions:
      - !Ref AlertTopic
```

---

## EC2 Image Builder — Golden AMI Cross-Region Pattern {#golden-ami-pattern}

<!-- Source: AWS DR Architecture Series, Part II — Backup and Restore with Rapid Recovery
     https://aws.amazon.com/blogs/architecture/disaster-recovery-dr-architecture-on-aws-part-ii-backup-and-restore-with-rapid-recovery/
     EC2 Image Builder Documentation
     https://docs.aws.amazon.com/imagebuilder/latest/userguide/what-is-image-builder.html -->

EC2 Image Builder creates golden AMIs with required OS and packages, then copies them to the recovery region for use in Backup & Restore and Pilot Light strategies.

```yaml
# EC2 Image Builder — Golden AMI with Cross-Region Distribution
# Source: https://docs.aws.amazon.com/imagebuilder/latest/userguide/what-is-image-builder.html

Parameters:
  RecoveryRegion:
    Type: String
    Description: Region to copy golden AMI to

Resources:
  GoldenAMIInfrastructureConfig:
    Type: AWS::ImageBuilder::InfrastructureConfiguration
    Properties:
      Name: !Sub "golden-ami-infra-${AWS::StackName}"
      InstanceTypes: ["t3.medium"]
      InstanceProfileName: !Ref ImageBuilderInstanceProfile
      TerminateInstanceOnFailure: true

  GoldenAMIDistributionConfig:
    Type: AWS::ImageBuilder::DistributionConfiguration
    Properties:
      Name: !Sub "golden-ami-distribution-${AWS::StackName}"
      Distributions:
        # Primary region
        - Region: !Ref AWS::Region
          AmiDistributionConfiguration:
            Name: !Sub "golden-ami-primary-{{ imagebuilder:buildDate }}"
            AmiTags:
              Purpose: GoldenAMI
              Environment: production
        # Recovery region — copy automatically
        - Region: !Ref RecoveryRegion
          AmiDistributionConfiguration:
            Name: !Sub "golden-ami-recovery-{{ imagebuilder:buildDate }}"
            AmiTags:
              Purpose: GoldenAMI
              Environment: recovery

  GoldenAMIPipeline:
    Type: AWS::ImageBuilder::ImagePipeline
    Properties:
      Name: !Sub "golden-ami-pipeline-${AWS::StackName}"
      InfrastructureConfigurationArn: !Ref GoldenAMIInfrastructureConfig
      DistributionConfigurationArn: !Ref GoldenAMIDistributionConfig
      Schedule:
        ScheduleExpression: "cron(0 0 * * ? *)"  # Daily
        PipelineExecutionStartCondition: EXPRESSION_MATCH_AND_DEPENDENCY_UPDATES_AVAILABLE
      Status: ENABLED
```

---

## Database Live Replication DR Patterns {#database-live-replication-patterns}

<!-- Sources:
     Aurora Global Database DR: https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-global-database-disaster-recovery.html
     RDS Read Replicas: https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_ReadRepl.html
     RDS Cross-Region Read Replicas: https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_ReadRepl.XRgn.html
     Route53 ARC Aurora execution block: https://docs.aws.amazon.com/r53recovery/latest/dg/aurora-global-database-block.html
     Route53 ARC RDS Promote Read Replica block: https://docs.aws.amazon.com/r53recovery/latest/dg/rds-promote-read-replica-block.html
     DynamoDB Global Tables design: https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/bp-global-table-design.html -->

### Decision Matrix: Which Database DR Pattern to Use

| Database | Strategy | Replication Type | RPO | RTO | Best For |
|----------|----------|-----------------|-----|-----|---------|
| Aurora MySQL/PostgreSQL | Global Database — Switchover | Synchronous (planned) | **0** (zero data loss) | Minutes | Planned failover, DR drills, regional rotation |
| Aurora MySQL/PostgreSQL | Global Database — Failover | Asynchronous | Seconds | Minutes | Unplanned outage |
| RDS MySQL/PostgreSQL/MariaDB | Cross-Region Read Replica | Asynchronous | Seconds–minutes | Minutes (+ promotion time) | Pilot Light, Warm Standby |
| RDS Oracle/SQL Server EE | Cross-Region Read Replica | Asynchronous | Seconds–minutes | Minutes | Pilot Light, Warm Standby |
| RDS Oracle/SQL Server SE | AWS Backup cross-region copy | Snapshot-based | Hours | Hours | Backup & Restore only |
| DynamoDB | Global Tables (MREC) | Asynchronous multi-active | Sub-second | Near-zero | Multi-Site Active/Active, eventual consistency |
| DynamoDB | Global Tables (MRSC) | Synchronous reads | Sub-second | Near-zero | Multi-Site Active/Active, strong consistency |

> **Key insight:** AWS Backup cross-region copy is appropriate for Backup & Restore (Tier 4/3). For Pilot Light and Warm Standby (Tier 2/1), live replication via read replicas or Aurora Global Database is required to meet RPO targets.

---

### Aurora Global Database — Switchover vs Failover

```yaml
# Aurora Global Database — Switchover (Planned, RPO=0)
# Source: https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-global-database-disaster-recovery.html
#
# Use switchover for:
# - DR drills (zero data loss)
# - Planned maintenance
# - Regional rotation (financial services compliance)
# - Follow-the-sun write region changes
# - Zero-data-loss failback after an unplanned failover
#
# How it works:
# 1. Aurora waits for secondary cluster to fully sync with primary (RPO=0)
# 2. Primary cluster becomes read-only
# 3. Secondary cluster promotes one reader to writer
# 4. Replication topology is preserved (same number of clusters, same regions)
#
# CLI command:
# aws rds switchover-global-cluster \
#   --global-cluster-identifier <global-cluster-id> \
#   --target-db-cluster-identifier <secondary-cluster-arn>
#
# Prerequisite: Primary and secondary must be on same major.minor engine version
# (patch levels may differ depending on engine version — check compatibility matrix)

# Aurora Global Database — Failover (Unplanned, RPO=seconds)
# Use failover for:
# - Unplanned regional outage
# - Primary cluster unavailable
#
# How it works:
# 1. Detach secondary cluster from global database
# 2. Promote secondary cluster to standalone primary
# 3. RPO = replication lag at time of failure (typically seconds)
# 4. After recovery: re-attach original primary as new secondary
#
# CLI command:
# aws rds failover-global-cluster \
#   --global-cluster-identifier <global-cluster-id> \
#   --target-db-cluster-identifier <secondary-cluster-arn>
#
# Note: After unplanned failover, use switchover (not failover) to fail back
# to original region — this ensures zero data loss on the return trip.

# Route53 ARC Aurora Global Database Execution Block
# Automates switchover/failover as part of a Region Switch plan
# Source: https://docs.aws.amazon.com/r53recovery/latest/dg/aurora-global-database-block.html
#
# Configuration in ARC Region Switch plan:
# - Block type: Aurora Global Database
# - Global cluster identifier: <global-cluster-id>
# - Cluster ARN per region: <primary-arn>, <secondary-arn>
# - Option: "Switchover" (graceful, RPO=0) or "Failover (data loss)" (ungraceful)
# - Timeout: 600 seconds (recommended)
#
# ARC validates before execution:
# - Global cluster exists
# - DB clusters exist in both source and destination regions
# - DB instances exist in both clusters
# - Engine versions are compatible for switchover
```

---

### RDS Cross-Region Read Replica — Promotion Pattern

```yaml
# RDS Cross-Region Read Replica for DR
# Source: https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_ReadRepl.XRgn.html
#
# Supported engines for cross-region read replicas:
# - MySQL (5.6+), MariaDB, PostgreSQL
# - Oracle EE, SQL Server EE (read replicas)
# - NOT supported: Oracle SE2, SQL Server SE (use AWS Backup instead)
#
# Replication: Asynchronous — RPO = replication lag (seconds to minutes)
# Promotion: Breaks replication, creates standalone instance — RTO = minutes

# CloudFormation: Create cross-region read replica
CrossRegionReadReplica:
  Type: AWS::RDS::DBInstance
  Properties:
    DBInstanceIdentifier: !Sub "${PrimaryDBIdentifier}-replica-${RecoveryRegion}"
    SourceDBInstanceIdentifier: !Sub
      - "arn:aws:rds:${PrimaryRegion}:${AWS::AccountId}:db:${PrimaryDBIdentifier}"
      - PrimaryRegion: !Ref PrimaryRegion
    DBInstanceClass: !Ref DBInstanceClass
    # Read replica inherits engine, storage, and parameter group from source
    # Multi-AZ can be enabled on the replica independently
    MultiAZ: true
    AutoMinorVersionUpgrade: true
    Tags:
      - Key: Purpose
        Value: DRReadReplica
      - Key: PrimaryRegion
        Value: !Ref PrimaryRegion

# Promotion to standalone (run during failover):
# aws rds promote-read-replica \
#   --db-instance-identifier <replica-id> \
#   --region <recovery-region>
#
# After promotion:
# - Replica becomes a standalone read/write instance
# - Replication from primary is permanently broken
# - Backup retention, preferred backup window, and Multi-AZ config are inherited
# - DNS endpoint remains the same
#
# Post-failover: Re-establish replication in reverse direction
# (create a new read replica in the original primary region pointing to the promoted instance)

# Route53 ARC Promote Read Replica Execution Block
# Automates promotion as part of a Region Switch plan
# Source: https://docs.aws.amazon.com/r53recovery/latest/dg/rds-promote-read-replica-block.html
#
# Configuration in ARC Region Switch plan:
# - Block type: RDS Promote Read Replica
# - RDS DB instance ARN per region: <replica-arn-in-recovery-region>
# - Timeout: 600 seconds (recommended)
#
# ARC validates before execution:
# - DB instances exist in specified regions
# - Non-primary region instances are read replicas (not standalone)
# - Read replicas are in "available" state
# - Instances are properly configured for cross-region replication
#
# After promotion, add a "Create Cross-Region Replica" block to re-establish
# replication from the new primary back to the original region:
# Source: https://docs.aws.amazon.com/r53recovery/latest/dg/rds-create-cross-region-replica-block.html

# Monitoring replication lag (critical for RPO tracking):
# aws cloudwatch get-metric-statistics \
#   --namespace AWS/RDS \
#   --metric-name ReplicaLag \
#   --dimensions Name=DBInstanceIdentifier,Value=<replica-id> \
#   --start-time <start> --end-time <end> \
#   --period 60 --statistics Average \
#   --region <recovery-region>
```

---

### DynamoDB Global Tables — MREC vs MRSC

```yaml
# DynamoDB Global Tables — Consistency Mode Selection
# Source: https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/bp-global-table-design.html
#
# Two consistency modes (set at table creation, cannot be changed):
#
# MREC (Multi-Region Eventually Consistent) — Default
# - Writes accepted in any region, replicated asynchronously (typically sub-second)
# - Last-writer-wins conflict resolution
# - Reads are eventually consistent across regions
# - Best for: high write throughput, eventual consistency acceptable
# - RPO: sub-second (replication lag)
# - Use case: session data, shopping carts, user preferences
#
# MRSC (Multi-Region Strong Consistency)
# - Strongly consistent reads available in all regions
# - Higher cost (additional read capacity consumed for consistency)
# - Best for: financial transactions, inventory, anything requiring strong consistency
# - RPO: sub-second
# - Use case: account balances, order status, inventory counts

# CloudFormation: DynamoDB Global Table
GlobalTable:
  Type: AWS::DynamoDB::GlobalTable
  Properties:
    TableName: !Ref TableName
    BillingMode: PAY_PER_REQUEST
    # Choose consistency mode at creation:
    # For MREC (default): no additional configuration needed
    # For MRSC: set ConsistencyMode on the table
    AttributeDefinitions:
      - AttributeName: pk
        AttributeType: S
      - AttributeName: sk
        AttributeType: S
    KeySchema:
      - AttributeName: pk
        KeyType: HASH
      - AttributeName: sk
        KeyType: RANGE
    StreamSpecification:
      StreamViewType: NEW_AND_OLD_IMAGES  # Required for global tables
    Replicas:
      - Region: !Ref PrimaryRegion
        PointInTimeRecoverySpecification:
          PointInTimeRecoveryEnabled: true  # Always enable PITR for DR
        Tags:
          - Key: Purpose
            Value: GlobalTablePrimary
      - Region: !Ref RecoveryRegion
        PointInTimeRecoverySpecification:
          PointInTimeRecoveryEnabled: true
        Tags:
          - Key: Purpose
            Value: GlobalTableReplica

# Key DR considerations for DynamoDB Global Tables:
# 1. Always enable Point-in-Time Recovery (PITR) on ALL replicas
#    — protects against accidental deletes/corruption that replicate globally
# 2. For evacuation (planned region removal):
#    - "Live evacuation": route traffic away first, then remove replica
#    - "Offline evacuation": remove replica while region is down (data may be lost)
# 3. DynamoDB does NOT have a global endpoint — applications must connect to
#    the regional endpoint. Traffic routing (Route53/Global Accelerator) handles
#    directing users to the correct region.
# 4. Throughput capacity: each replica needs sufficient RCU/WCU independently.
#    In active/active, writes in one region consume WCU in ALL replicas.
```

---

### Database DR Pattern Selection by Workload Tier

Use this decision tree when recommending database DR patterns during Phase 2 (Analyze):

```
For each database resource in the workload:

IF Aurora MySQL or Aurora PostgreSQL:
  → Tier 1 (Multi-Site Active/Active): Aurora Global Database + MREC DynamoDB Global Tables
    - Write pattern: read-local/write-local OR read-local/write-global (Aurora write forwarding)
    - Failover: Route53 ARC Aurora execution block (switchover for drills, failover for outages)
  → Tier 1/2 (Warm Standby / Pilot Light): Aurora Global Database secondary cluster
    - Secondary cluster: headless (no instances) for Pilot Light, 1 instance for Warm Standby
    - Failover: Route53 ARC Aurora execution block (switchover preferred)
  → Tier 3/4 (Backup & Restore): Aurora automated snapshots + AWS Backup cross-region copy

IF RDS MySQL / PostgreSQL / MariaDB:
  → Tier 1/2 (Warm Standby / Pilot Light): Cross-region read replica
    - Monitor ReplicaLag CloudWatch metric — alert if lag > RPO target
    - Failover: Route53 ARC "Promote Read Replica" execution block
    - Post-failover: Route53 ARC "Create Cross-Region Replica" block to re-establish replication
  → Tier 3/4 (Backup & Restore): AWS Backup cross-region copy

IF RDS Oracle EE / SQL Server EE:
  → Tier 1/2: Cross-region read replica (mounted/standby mode)
  → Tier 3/4: AWS Backup cross-region copy

IF RDS Oracle SE2 / SQL Server SE:
  → All tiers: AWS Backup cross-region copy (no read replica support)
  → Consider AWS DMS for near-real-time replication if RPO < 1 hour required

IF DynamoDB:
  → Tier 1 (Multi-Site Active/Active): Global Tables MREC (eventual) or MRSC (strong)
    - Always enable PITR on all replicas
    - Choose write pattern: write-local, write-global, or write-partitioned
  → Tier 2/3 (Warm Standby / Pilot Light): Global Tables with traffic routing to primary
  → Tier 4 (Backup & Restore): DynamoDB on-demand backups + cross-region copy via AWS Backup

IF ElastiCache Redis / Valkey:
  → Tier 1/2: Global Datastore (cross-region replication, RPO=seconds)
    - Cluster mode enabled, min 3 shards, min 2 replicas/shard
    - Monitor ReplicationLag metric — alert if lag > RPO target
    - Test failover with TestFailover API before production events
    - Failover: aws elasticache failover-global-replication-group
  → Tier 3/4: Snapshot + restore in recovery region (RPO=hours)
    - SnapshotRetentionLimit ≥ 7 days

IF ElastiCache Memcached:
  → ALL TIERS: ⚠️ CRITICAL GAP — Memcached has NO replication and NO backup
    - Data is entirely ephemeral — lost on any node failure
    - Recommendation: migrate to ElastiCache for Redis/Valkey or MemoryDB
    - If data is truly ephemeral (cache-only, no session state): document this explicitly

IF MemoryDB for Redis:
  → Tier 1/2: Multi-Region cluster (native cross-region replication)
    - Monitor MultiRegionClusterReplicationLag metric
    - Redirect traffic to healthy region if lag becomes elevated
  → Tier 3/4: Snapshot + restore

IF Redshift:
  → Tier 1/2: Cross-region snapshot copy (automated)
  → Tier 3/4: AWS Backup cross-region copy
```

---

## Container Workload DR Patterns {#container-dr-patterns}

<!-- Sources:
     EKS Backup with AWS Backup: https://docs.aws.amazon.com/eks/latest/userguide/integration-backup.html
     EKS HA and Resiliency: https://docs.aws.amazon.com/prescriptive-guidance/latest/ha-resiliency-amazon-eks-apps/ha-resilience-design.html
     ECR Private Image Replication: https://docs.aws.amazon.com/AmazonECR/latest/userguide/replication.html
     ECR Replication Configuration: https://docs.aws.amazon.com/AmazonECR/latest/userguide/registry-settings-configure.html -->

### DR Strategy by Container Platform

| Platform | DR Approach | Key Dependency | RPO | RTO |
|----------|------------|----------------|-----|-----|
| **ECS on EC2** | Redeploy service from IaC in recovery region | ECR images + task definitions in recovery region | Near-zero (stateless) | Minutes |
| **ECS on Fargate** | Redeploy service from IaC in recovery region | ECR images + task definitions in recovery region | Near-zero (stateless) | Minutes |
| **EKS** | Redeploy cluster from IaC + restore workloads from AWS Backup | ECR images + Helm charts/manifests in recovery region | Minutes (backup lag) | Minutes–hours |
| **EKS with persistent storage** | AWS Backup cross-region copy for EBS/EFS volumes | ECR images + manifests + volume backups | Minutes (backup lag) | Minutes–hours |

> **Key insight:** Container compute (ECS tasks, EKS pods) is stateless — it can be redeployed from IaC in minutes. The DR dependencies are: (1) container images available in ECR in the recovery region, (2) task definitions / Kubernetes manifests available, (3) stateful data (databases, volumes) recovered separately.

---

### ECR Cross-Region Replication {#ecr-cross-region-replication}

```yaml
# ECR Cross-Region Replication
# Source: https://docs.aws.amazon.com/AmazonECR/latest/userguide/replication.html
#
# ECR supports cross-region AND cross-account replication.
# Only the DESTINATION account needs a registry permissions policy.
# The source account only needs to configure replication rules.
#
# Replication is triggered by push/restore — existing images are NOT retroactively replicated.
# Configure replication BEFORE pushing images to ensure recovery region has all images.

# Step 1: Configure replication rules on the SOURCE registry
# aws ecr put-replication-configuration \
#   --replication-configuration '{
#     "rules": [
#       {
#         "destinations": [
#           {
#             "region": "<recovery-region>",
#             "registryId": "<destination-account-id>"
#           }
#         ],
#         "repositoryFilters": [
#           {
#             "filter": "prod/",
#             "filterType": "PREFIX_MATCH"
#           }
#         ]
#       }
#     ]
#   }' \
#   --region <primary-region>
#
# Step 2: If cross-account, configure registry policy on DESTINATION account
# aws ecr put-registry-policy \
#   --policy-text '{
#     "Version": "2012-10-17",
#     "Statement": [
#       {
#         "Sid": "AllowCrossAccountReplication",
#         "Effect": "Allow",
#         "Principal": { "AWS": "arn:aws:iam::<source-account-id>:root" },
#         "Action": ["ecr:ReplicateImage", "ecr:CreateRepository"],
#         "Resource": "*"
#       }
#     ]
#   }' \
#   --region <recovery-region>

# Verify replication status:
# aws ecr describe-registry --region <primary-region>
# aws ecr describe-repositories --region <recovery-region>

# CloudFormation: ECR repository with lifecycle policy (apply in both regions)
ECRRepository:
  Type: AWS::ECR::Repository
  Properties:
    RepositoryName: !Ref RepositoryName
    ImageScanningConfiguration:
      ScanOnPush: true
    EncryptionConfiguration:
      EncryptionType: KMS
    LifecyclePolicy:
      LifecyclePolicyText: |
        {
          "rules": [
            {
              "rulePriority": 1,
              "description": "Keep last 10 production images",
              "selection": {
                "tagStatus": "tagged",
                "tagPrefixList": ["prod-"],
                "countType": "imageCountMoreThan",
                "countNumber": 10
              },
              "action": { "type": "expire" }
            }
          ]
        }
```

---

### ECS / Fargate DR Pattern {#ecs-fargate-dr-pattern}

```yaml
# ECS / Fargate DR Pattern
# Source: https://docs.aws.amazon.com/AmazonECS/latest/developerguide/
#
# ECS and Fargate are stateless compute — DR is about ensuring:
# 1. ECR images are replicated to recovery region (see #ecr-cross-region-replication)
# 2. Task definitions are registered in recovery region
# 3. ECS cluster and service are deployed in recovery region via IaC
# 4. Load balancer and service discovery are configured in recovery region
# 5. Stateful data (RDS, DynamoDB, S3) is handled separately

# CloudFormation: ECS Fargate Service (deploy in both primary and recovery regions)
ECSCluster:
  Type: AWS::ECS::Cluster
  Properties:
    ClusterName: !Sub "${AppName}-cluster-${AWS::Region}"
    ClusterSettings:
      - Name: containerInsights
        Value: enabled

ECSTaskDefinition:
  Type: AWS::ECS::TaskDefinition
  Properties:
    Family: !Sub "${AppName}-task"
    NetworkMode: awsvpc
    RequiresCompatibilities: [FARGATE]
    Cpu: "256"
    Memory: "512"
    ExecutionRoleArn: !GetAtt ECSExecutionRole.Arn
    TaskRoleArn: !GetAtt ECSTaskRole.Arn
    ContainerDefinitions:
      - Name: !Ref AppName
        # Use the ECR image URI — must exist in the CURRENT region's ECR
        Image: !Sub "${AWS::AccountId}.dkr.ecr.${AWS::Region}.amazonaws.com/${RepositoryName}:${ImageTag}"
        PortMappings:
          - ContainerPort: 8080
        LogConfiguration:
          LogDriver: awslogs
          Options:
            awslogs-group: !Ref LogGroup
            awslogs-region: !Ref AWS::Region
            awslogs-stream-prefix: !Ref AppName

ECSService:
  Type: AWS::ECS::Service
  Properties:
    Cluster: !Ref ECSCluster
    TaskDefinition: !Ref ECSTaskDefinition
    LaunchType: FARGATE
    DesiredCount: !If [IsRecoveryRegion, 1, 3]  # Reduced capacity in recovery (Warm Standby)
    NetworkConfiguration:
      AwsvpcConfiguration:
        Subnets: !Ref PrivateSubnets
        SecurityGroups: [!Ref AppSecurityGroup]
    LoadBalancers:
      - ContainerName: !Ref AppName
        ContainerPort: 8080
        TargetGroupArn: !Ref TargetGroup
    DeploymentConfiguration:
      MinimumHealthyPercent: 50
      MaximumPercent: 200

# For Pilot Light: set DesiredCount to 0 in recovery region
# For Warm Standby: set DesiredCount to reduced value (e.g., 1)
# For Active/Active: set DesiredCount to full production value

# Failover procedure:
# 1. Verify ECR images exist in recovery region
# 2. Update ECS service DesiredCount to production value:
#    aws ecs update-service \
#      --cluster <cluster-name> \
#      --service <service-name> \
#      --desired-count <production-count> \
#      --region <recovery-region>
# 3. Wait for tasks to reach RUNNING state:
#    aws ecs wait services-stable \
#      --cluster <cluster-name> \
#      --services <service-name> \
#      --region <recovery-region>
# 4. Update Route53 / Global Accelerator to route traffic to recovery region
```

---

### EKS DR Pattern {#eks-dr-pattern}

```yaml
# EKS DR Pattern
# Source: https://docs.aws.amazon.com/eks/latest/userguide/integration-backup.html
#         https://docs.aws.amazon.com/prescriptive-guidance/latest/ha-resiliency-amazon-eks-apps/
#
# EKS DR approach:
# - AWS manages the control plane (etcd, API server) — no etcd backup needed
# - You manage: node groups, workloads, persistent storage, add-ons, RBAC
# - AWS Backup can back up EKS cluster state + persistent volumes (EBS, EFS, S3)
# - Recovery region cluster is redeployed from IaC (eksctl, Terraform, CDK)
# - Workloads are restored from AWS Backup or redeployed from Helm charts / manifests

# Prerequisites for AWS Backup of EKS:
# - EKS cluster authorization mode must be "API" or "API_AND_CONFIG_MAP"
# - IAM role must have AWSBackupServiceRolePolicyForBackup policy
# - For S3-backed persistent volumes: also attach AWSBackupServiceRolePolicyForS3Backup

# AWS Backup plan for EKS (CloudFormation):
EKSBackupPlan:
  Type: AWS::Backup::BackupPlan
  Properties:
    BackupPlan:
      BackupPlanName: !Sub "eks-backup-${AWS::StackName}"
      BackupPlanRule:
        - RuleName: DailyEKSBackup
          TargetBackupVault: !Ref BackupVaultName
          ScheduleExpression: "cron(0 2 * * ? *)"  # Daily at 02:00 UTC
          StartWindowMinutes: 60
          CompletionWindowMinutes: 180
          Lifecycle:
            DeleteAfterDays: 30
          CopyActions:
            - DestinationBackupVaultArn: !Sub
                - "arn:aws:backup:${RecoveryRegion}:${AWS::AccountId}:backup-vault:${VaultName}"
                - RecoveryRegion: !Ref RecoveryRegion
                  VaultName: !Ref BackupVaultName
              Lifecycle:
                DeleteAfterDays: 30

EKSBackupSelection:
  Type: AWS::Backup::BackupSelection
  Properties:
    BackupPlanId: !Ref EKSBackupPlan
    BackupSelection:
      SelectionName: EKSClusters
      IamRoleArn: !GetAtt BackupRole.Arn
      Resources:
        - !Sub "arn:aws:eks:${AWS::Region}:${AWS::AccountId}:cluster/${ClusterName}"

# EKS HA best practices (in-region, required baseline):
# - Spread node groups across at least 3 AZs
# - Use Pod Disruption Budgets (PDB) for critical workloads
# - Configure liveness and readiness probes on all containers
# - Use topology spread constraints to distribute pods across AZs
# - Minimum 2 replicas for all Deployments (never run single-replica in production)

# Cross-region DR procedure for EKS:
# 1. Ensure ECR images are replicated to recovery region (see #ecr-cross-region-replication)
# 2. Store Kubernetes manifests / Helm charts in S3 with CRR or in a Git repo
# 3. Deploy EKS cluster in recovery region from IaC (eksctl/Terraform/CDK)
# 4. Apply add-ons (VPC CNI, CoreDNS, kube-proxy, AWS Load Balancer Controller)
# 5. Restore workloads from AWS Backup OR apply manifests from Git/S3
# 6. Restore persistent volumes from AWS Backup cross-region copy
# 7. Update Route53 / Global Accelerator to route traffic to recovery region

# Manifest/Helm chart storage in S3 with CRR:
ManifestsBucket:
  Type: AWS::S3::Bucket
  Properties:
    BucketName: !Sub "eks-manifests-${AWS::AccountId}-${AWS::Region}"
    VersioningConfiguration:
      Status: Enabled
    ReplicationConfiguration:
      Role: !GetAtt S3ReplicationRole.Arn
      Rules:
        - Id: ReplicateManifests
          Status: Enabled
          Destination:
            Bucket: !Sub "arn:aws:s3:::eks-manifests-${AWS::AccountId}-${RecoveryRegion}"
            StorageClass: STANDARD

# EKS Pilot Light pattern:
# - Recovery region: EKS cluster deployed (control plane running) but 0 worker nodes
# - On failover: add node group, restore workloads from backup or manifests
# - Cost: only control plane charges (~$0.10/hr) until failover

# EKS Warm Standby pattern:
# - Recovery region: EKS cluster with reduced node group (e.g., 1 node per AZ)
# - Workloads running at reduced replica count
# - On failover: scale node group and deployments to production capacity
```
