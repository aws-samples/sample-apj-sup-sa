import * as path from "path";
import * as cdk from "aws-cdk-lib";
import { Duration, RemovalPolicy, Stack } from "aws-cdk-lib";
import { Construct } from "constructs";
import * as certificatemanager from "aws-cdk-lib/aws-certificatemanager";
import * as cognito from "aws-cdk-lib/aws-cognito";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecrAssets from "aws-cdk-lib/aws-ecr-assets";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as elbv2 from "aws-cdk-lib/aws-elasticloadbalancingv2";
import * as iam from "aws-cdk-lib/aws-iam";
import * as logs from "aws-cdk-lib/aws-logs";
import * as rds from "aws-cdk-lib/aws-rds";
import * as route53 from "aws-cdk-lib/aws-route53";
import * as route53Targets from "aws-cdk-lib/aws-route53-targets";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import { GatewayConfig, GATEWAY_CONTAINER_PORT } from "./config";

export interface GatewayStackProps extends cdk.StackProps {
  readonly config: GatewayConfig;
}

export class GatewayStack extends Stack {
  constructor(scope: Construct, id: string, props: GatewayStackProps) {
    super(scope, id, props);

    const { config } = props;
    const publicUrl = `https://${config.gatewayHost}`;
    const callbackUrl = `${publicUrl}/oauth/callback`;

    const vpc = new ec2.Vpc(this, "GatewayVpc", {
      maxAzs: config.maxAzs,
      natGateways: config.natGateways,
      subnetConfiguration: [
        {
          cidrMask: 24,
          name: "Public",
          subnetType: ec2.SubnetType.PUBLIC
        },
        {
          cidrMask: 24,
          name: "Application",
          subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS
        },
        {
          cidrMask: 28,
          name: "Database",
          subnetType: ec2.SubnetType.PRIVATE_ISOLATED
        }
      ]
    });

    const albSecurityGroup = new ec2.SecurityGroup(this, "AlbSecurityGroup", {
      vpc,
      description: "Allow private-network HTTPS traffic to Claude Apps Gateway",
      allowAllOutbound: true
    });

    for (const cidr of config.allowedClientCidrs) {
      albSecurityGroup.addIngressRule(
        ec2.Peer.ipv4(cidr),
        ec2.Port.tcp(443),
        `Allow HTTPS from ${cidr}`
      );
    }

    const taskSecurityGroup = new ec2.SecurityGroup(this, "GatewayTaskSecurityGroup", {
      vpc,
      description: "Allow only the internal ALB to reach gateway tasks",
      allowAllOutbound: true
    });
    taskSecurityGroup.addIngressRule(
      albSecurityGroup,
      ec2.Port.tcp(GATEWAY_CONTAINER_PORT),
      "ALB to gateway HTTP"
    );

    const databaseSecurityGroup = new ec2.SecurityGroup(this, "DatabaseSecurityGroup", {
      vpc,
      description: "Allow only gateway tasks to reach PostgreSQL",
      allowAllOutbound: true
    });
    databaseSecurityGroup.addIngressRule(taskSecurityGroup, ec2.Port.tcp(5432), "Gateway tasks to PostgreSQL");

    const dbCredentials = new rds.DatabaseSecret(this, "DatabaseCredentials", {
      username: "gateway"
    });

    const database = new rds.DatabaseCluster(this, "GatewayDatabase", {
      vpc,
      vpcSubnets: {
        subnetType: ec2.SubnetType.PRIVATE_ISOLATED
      },
      securityGroups: [databaseSecurityGroup],
      engine: rds.DatabaseClusterEngine.auroraPostgres({
        version: rds.AuroraPostgresEngineVersion.VER_16_13
      }),
      writer: rds.ClusterInstance.serverlessV2("Writer"),
      serverlessV2MinCapacity: 0.5,
      serverlessV2MaxCapacity: 2,
      credentials: rds.Credentials.fromSecret(dbCredentials),
      defaultDatabaseName: config.databaseName,
      backup: {
        retention: Duration.days(7)
      },
      deletionProtection: false,
      removalPolicy: RemovalPolicy.DESTROY,
      storageEncrypted: true
    });

    const userPool = new cognito.UserPool(this, "GatewayUserPool", {
      selfSignUpEnabled: false,
      signInAliases: {
        email: true
      },
      autoVerify: {
        email: true
      },
      standardAttributes: {
        email: {
          required: true,
          mutable: true
        }
      },
      removalPolicy: RemovalPolicy.DESTROY
    });

    const userPoolDomain = userPool.addDomain("GatewayUserPoolDomain", {
      cognitoDomain: {
        domainPrefix: config.cognitoDomainPrefix
      }
    });

    const userPoolClient = userPool.addClient("GatewayUserPoolClient", {
      userPoolClientName: "claude-apps-gateway",
      generateSecret: true,
      oAuth: {
        flows: {
          authorizationCodeGrant: true
        },
        scopes: [cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL, cognito.OAuthScope.PROFILE],
        callbackUrls: [callbackUrl]
      },
      supportedIdentityProviders: [cognito.UserPoolClientIdentityProvider.COGNITO],
      preventUserExistenceErrors: true,
      refreshTokenValidity: Duration.days(1)
    });

    const oidcClientSecret = new secretsmanager.Secret(this, "OidcClientSecret", {
      description: "Cognito app client secret for Claude Apps Gateway OIDC",
      secretStringValue: userPoolClient.userPoolClientSecret
    });

    const gatewayJwtSecret = new secretsmanager.Secret(this, "GatewayJwtSecret", {
      description: "HS256 JWT signing secret for Claude Apps Gateway sessions",
      generateSecretString: {
        passwordLength: 48,
        excludePunctuation: true
      }
    });

    const cluster = new ecs.Cluster(this, "GatewayCluster", {
      vpc,
      containerInsightsV2: ecs.ContainerInsights.ENABLED
    });

    // The image asset below is pinned to linux/arm64, and prepare-claude-binary.sh
    // fetches the linux-arm64 binary — keep all three in sync when changing arch.
    const taskDefinition = new ecs.FargateTaskDefinition(this, "GatewayTaskDefinition", {
      cpu: 512,
      memoryLimitMiB: 1024,
      runtimePlatform: {
        cpuArchitecture: ecs.CpuArchitecture.ARM64,
        operatingSystemFamily: ecs.OperatingSystemFamily.LINUX
      }
    });

    taskDefinition.taskRole.addToPrincipalPolicy(
      new iam.PolicyStatement({
        sid: "InvokeClaudeModelsOnBedrock",
        actions: ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
        resources: [
          `arn:${this.partition}:bedrock:*:${this.account}:inference-profile/*`,
          `arn:${this.partition}:bedrock:*:${this.account}:application-inference-profile/*`,
          `arn:${this.partition}:bedrock:${config.bedrockRegion}:${this.account}:provisioned-model/*`,
          `arn:${this.partition}:bedrock:*::foundation-model/anthropic.*`
        ]
      })
    );

    const logGroup = new logs.LogGroup(this, "GatewayLogGroup", {
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: RemovalPolicy.DESTROY
    });

    const container = taskDefinition.addContainer("GatewayContainer", {
      image: ecs.ContainerImage.fromAsset(path.join(__dirname, "..", "docker"), {
        platform: ecrAssets.Platform.LINUX_ARM64
      }),
      logging: ecs.LogDrivers.awsLogs({
        streamPrefix: "gateway",
        logGroup
      }),
      environment: {
        BEDROCK_REGION: config.bedrockRegion,
        CLAUDE_GATEWAY_LOG_LEVEL: "info",
        CLAUDE_VERSION: config.claudeVersion,
        GATEWAY_DB_HOST: database.clusterEndpoint.hostname,
        GATEWAY_DB_NAME: config.databaseName,
        GATEWAY_DB_PORT: cdk.Token.asString(database.clusterEndpoint.port),
        GATEWAY_PUBLIC_URL: publicUrl,
        OIDC_ALLOWED_EMAIL_DOMAINS: config.allowedEmailDomains.join(","),
        OIDC_CLIENT_ID: userPoolClient.userPoolClientId,
        OIDC_ISSUER: `https://cognito-idp.${this.region}.amazonaws.com/${userPool.userPoolId}`
      },
      secrets: {
        GATEWAY_DB_PASSWORD: ecs.Secret.fromSecretsManager(dbCredentials, "password"),
        GATEWAY_DB_USERNAME: ecs.Secret.fromSecretsManager(dbCredentials, "username"),
        GATEWAY_JWT_SECRET: ecs.Secret.fromSecretsManager(gatewayJwtSecret),
        OIDC_CLIENT_SECRET: ecs.Secret.fromSecretsManager(oidcClientSecret)
      },
      healthCheck: {
        command: [
          "CMD-SHELL",
          `curl -fsS http://127.0.0.1:${GATEWAY_CONTAINER_PORT}/healthz >/dev/null || exit 1`
        ],
        interval: Duration.seconds(30),
        retries: 3,
        startPeriod: Duration.seconds(60),
        timeout: Duration.seconds(5)
      }
    });

    container.addPortMappings({
      containerPort: GATEWAY_CONTAINER_PORT,
      protocol: ecs.Protocol.TCP
    });

    const service = new ecs.FargateService(this, "GatewayService", {
      cluster,
      taskDefinition,
      desiredCount: config.desiredCount,
      assignPublicIp: false,
      minHealthyPercent: 100,
      securityGroups: [taskSecurityGroup],
      vpcSubnets: {
        subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS
      },
      circuitBreaker: {
        rollback: true
      }
    });
    // The gateway runs Postgres migrations at boot, so tasks crash-loop (and trip
    // the circuit breaker) if they start before the Aurora writer is available.
    service.node.addDependency(database);

    // ACM DNS validation needs public DNS, so the validation records go to the
    // public hosted zone for hostedZoneName; clients still resolve the gateway
    // through the private hosted zone below.
    const publicValidationZone = route53.HostedZone.fromLookup(this, "PublicValidationZone", {
      domainName: config.hostedZoneName,
      privateZone: false
    });

    const certificate = new certificatemanager.Certificate(this, "GatewayCertificate", {
      domainName: config.gatewayHost,
      validation: certificatemanager.CertificateValidation.fromDns(publicValidationZone)
    });

    const loadBalancer = new elbv2.ApplicationLoadBalancer(this, "GatewayAlb", {
      vpc,
      internetFacing: false,
      securityGroup: albSecurityGroup,
      vpcSubnets: {
        subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS
      },
      idleTimeout: Duration.minutes(5)
    });

    const listener = loadBalancer.addListener("HttpsListener", {
      port: 443,
      protocol: elbv2.ApplicationProtocol.HTTPS,
      certificates: [certificate],
      sslPolicy: elbv2.SslPolicy.RECOMMENDED_TLS,
      open: false
    });
    const targetGroup = listener.addTargets("GatewayTargets", {
      protocol: elbv2.ApplicationProtocol.HTTP,
      port: GATEWAY_CONTAINER_PORT,
      targets: [service],
      healthCheck: {
        enabled: true,
        path: "/readyz",
        healthyHttpCodes: "200",
        interval: Duration.seconds(30),
        timeout: Duration.seconds(5),
        healthyThresholdCount: 2,
        unhealthyThresholdCount: 3
      },
      deregistrationDelay: Duration.seconds(30)
    });

    // The private zone is scoped to the gateway FQDN itself (alias record at the
    // zone apex), NOT to hostedZoneName. A VPC-associated private zone is
    // authoritative for its entire zone name, so a zone at the domain apex would
    // make every other host in that domain (SSO, internal git, ...) resolve to
    // NXDOMAIN inside the VPC and for VPN clients using the VPC resolver.
    const privateHostedZone = new route53.PrivateHostedZone(this, "GatewayPrivateHostedZone", {
      zoneName: config.gatewayHost,
      vpc
    });

    new route53.ARecord(this, "GatewayPrivateAliasRecord", {
      zone: privateHostedZone,
      target: route53.RecordTarget.fromAlias(new route53Targets.LoadBalancerTarget(loadBalancer))
    });

    new cdk.CfnOutput(this, "GatewayUrl", {
      value: publicUrl
    });
    new cdk.CfnOutput(this, "AlbDnsName", {
      value: loadBalancer.loadBalancerDnsName
    });
    new cdk.CfnOutput(this, "PrivateHostedZoneId", {
      value: privateHostedZone.hostedZoneId
    });
    new cdk.CfnOutput(this, "UserPoolId", {
      value: userPool.userPoolId
    });
    new cdk.CfnOutput(this, "UserPoolClientId", {
      value: userPoolClient.userPoolClientId
    });
    new cdk.CfnOutput(this, "CognitoDomain", {
      value: userPoolDomain.baseUrl()
    });
    new cdk.CfnOutput(this, "RdsEndpoint", {
      value: database.clusterEndpoint.hostname
    });
    new cdk.CfnOutput(this, "LogGroupName", {
      value: logGroup.logGroupName
    });
  }
}
