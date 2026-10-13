import * as path from "path";
import * as cdk from "aws-cdk-lib";
import { Duration, RemovalPolicy, Stack } from "aws-cdk-lib";
import { Construct } from "constructs";
import * as certificatemanager from "aws-cdk-lib/aws-certificatemanager";
import * as cloudwatch from "aws-cdk-lib/aws-cloudwatch";
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
import {
  COLLECTOR_HEALTH_PORT,
  GatewayConfig,
  GATEWAY_CONTAINER_PORT,
  OTLP_LISTENER_PORT,
  validateResourceAttributes
} from "./config";

export interface GatewayStackProps extends cdk.StackProps {
  readonly config: GatewayConfig;
}

export class GatewayStack extends Stack {
  constructor(scope: Construct, id: string, props: GatewayStackProps) {
    super(scope, id, props);

    const { config } = props;
    validateResourceAttributes(config.telemetryResourceAttributes);
    const publicUrl = `https://${config.gatewayHost}`;
    // The gateway relays client OTLP to the collector through the internal ALB on
    // its own HTTPS port. The gateway only forwards to https:// (or loopback), so a
    // dedicated listener keeps its SSRF guard intact (no CLAUDE_GATEWAY_ALLOW_LOOPBACK).
    const otlpForwardUrl = `${publicUrl}:${OTLP_LISTENER_PORT}`;
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

    let collectorSecurityGroup: ec2.SecurityGroup | undefined;
    if (config.enableTelemetry) {
      // Only gateway tasks may reach the OTLP listener; developer CIDRs get 443 only.
      albSecurityGroup.addIngressRule(
        taskSecurityGroup,
        ec2.Port.tcp(OTLP_LISTENER_PORT),
        "Gateway telemetry relay to OTLP listener"
      );
      collectorSecurityGroup = new ec2.SecurityGroup(this, "CollectorSecurityGroup", {
        vpc,
        description: "Allow only the internal ALB to reach the OTLP collector",
        allowAllOutbound: true
      });
      collectorSecurityGroup.addIngressRule(albSecurityGroup, ec2.Port.tcp(OTLP_LISTENER_PORT), "ALB to collector OTLP/HTTP");
      collectorSecurityGroup.addIngressRule(albSecurityGroup, ec2.Port.tcp(COLLECTOR_HEALTH_PORT), "ALB health check to collector");
    }

    // Interface + S3 gateway endpoints keep AWS-service traffic (Bedrock
    // inference, secrets reads, image pulls, log delivery) on the AWS backbone
    // instead of the NAT-to-internet path. The S3 gateway is required for ECR:
    // the ecr.api/ecr.dkr endpoints serve auth and manifests, but layer blobs
    // are fetched from S3. NAT stays for the Cognito OIDC leg, which has no
    // VPC endpoint. Endpoints are regional: the bedrock-runtime endpoint only
    // covers inference when bedrockRegion matches the stack region; cross-
    // region Bedrock calls still egress via NAT. Opt out with
    // createVpcEndpoints=false to trade the per-AZ-hour endpoint cost for NAT
    // data charges.
    if (config.createVpcEndpoints) {
      const endpointSecurityGroup = new ec2.SecurityGroup(this, "VpcEndpointSecurityGroup", {
        vpc,
        description: "Allow only gateway tasks to reach VPC interface endpoints",
        allowAllOutbound: true
      });
      endpointSecurityGroup.addIngressRule(
        taskSecurityGroup,
        ec2.Port.tcp(443),
        "Gateway tasks to VPC endpoints"
      );
      if (collectorSecurityGroup) {
        // Collector reaches CloudWatch Monitoring (OTLP metrics), ECR and Logs over the endpoints.
        endpointSecurityGroup.addIngressRule(
          collectorSecurityGroup,
          ec2.Port.tcp(443),
          "Collector tasks to VPC endpoints"
        );
      }

      const interfaceEndpoints: Array<[string, ec2.InterfaceVpcEndpointAwsService]> = [
        ["BedrockRuntimeEndpoint", ec2.InterfaceVpcEndpointAwsService.BEDROCK_RUNTIME],
        ["SecretsManagerEndpoint", ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER],
        ["EcrApiEndpoint", ec2.InterfaceVpcEndpointAwsService.ECR],
        ["EcrDockerEndpoint", ec2.InterfaceVpcEndpointAwsService.ECR_DOCKER],
        ["CloudWatchLogsEndpoint", ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS],
        ["CloudWatchMonitoringEndpoint", ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_MONITORING]
      ];
      for (const [id, service] of interfaceEndpoints) {
        vpc.addInterfaceEndpoint(id, {
          service,
          securityGroups: [endpointSecurityGroup],
          privateDnsEnabled: true,
          // Suppress the default allow-from-VPC-CIDR ingress rule; only the
          // task SG rule added above should reach the endpoints.
          open: false
        });
      }

      vpc.addGatewayEndpoint("S3Endpoint", {
        service: ec2.GatewayVpcEndpointAwsService.S3
      });
    }

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
        GATEWAY_AVAILABLE_MODELS: config.availableModels.join(","),
        GATEWAY_DB_HOST: database.clusterEndpoint.hostname,
        GATEWAY_DB_NAME: config.databaseName,
        GATEWAY_DB_PORT: cdk.Token.asString(database.clusterEndpoint.port),
        GATEWAY_PUBLIC_URL: publicUrl,
        OIDC_ALLOWED_EMAIL_DOMAINS: config.allowedEmailDomains.join(","),
        OIDC_CLIENT_ID: userPoolClient.userPoolClientId,
        OIDC_ISSUER: `https://cognito-idp.${this.region}.amazonaws.com/${userPool.userPoolId}`,
        ...(config.enableTelemetry
          ? {
              TELEMETRY_FORWARD_URL: otlpForwardUrl,
              TELEMETRY_RESOURCE_ATTRIBUTES: Object.entries(config.telemetryResourceAttributes)
                .map(([k, v]) => `${k}=${v}`)
                .join(",")
            }
          : {})
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
      },
      // Boot runs Postgres migrations before /healthz answers; without a grace
      // period a slow cold start fails ALB health checks mid-rolling-deploy and
      // trips the circuit breaker into a spurious rollback.
      healthCheckGracePeriod: Duration.seconds(120)
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
      // Long-running streaming responses go quiet between tokens; the 60s default
      // (and anything short) drops them mid-stream. 3600s matches the upstream
      // deployment guidance.
      idleTimeout: Duration.seconds(3600)
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
        // Liveness, not readiness: /readyz checks Postgres, so a transient DB
        // blip would drain every replica from rotation at once even though
        // bearer tokens still validate locally. /healthz only asserts the
        // process is alive; the container health check covers the same path.
        path: "/healthz",
        healthyHttpCodes: "200",
        interval: Duration.seconds(30),
        timeout: Duration.seconds(5),
        healthyThresholdCount: 2,
        unhealthyThresholdCount: 3
      },
      deregistrationDelay: Duration.seconds(30)
    });

    new cloudwatch.Alarm(this, "UnhealthyHostAlarm", {
      alarmDescription: "One or more gateway tasks are failing the ALB /healthz check",
      metric: targetGroup.metrics.unhealthyHostCount({
        period: Duration.minutes(1),
        statistic: "max"
      }),
      threshold: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      evaluationPeriods: 3,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING
    });

    if (config.enableTelemetry && collectorSecurityGroup) {
      this.addTelemetryCollector(config, cluster, loadBalancer, certificate, collectorSecurityGroup);
    }

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

  /**
   * ADOT collector behind the ALB's OTLP listener. It receives the gateway's relayed
   * OTLP metrics and SigV4-signs them into the CloudWatch OTLP endpoint with its task
   * role, so no long-lived CloudWatch API key exists anywhere. Metrics then populate
   * CloudWatch > GenAI Observability > Coding Agent Insights.
   */
  private addTelemetryCollector(
    config: GatewayConfig,
    cluster: ecs.Cluster,
    loadBalancer: elbv2.ApplicationLoadBalancer,
    certificate: certificatemanager.ICertificate,
    securityGroup: ec2.SecurityGroup
  ): void {
    const taskDefinition = new ecs.FargateTaskDefinition(this, "CollectorTaskDefinition", {
      cpu: 256,
      memoryLimitMiB: 512,
      runtimePlatform: {
        cpuArchitecture: ecs.CpuArchitecture.ARM64,
        operatingSystemFamily: ecs.OperatingSystemFamily.LINUX
      }
    });
    taskDefinition.taskRole.addToPrincipalPolicy(
      new iam.PolicyStatement({
        sid: "PublishOtlpMetricsToCloudWatch",
        // The CloudWatch OTLP metrics endpoint authorizes SigV4 requests with this action.
        actions: ["cloudwatch:PutMetricData"],
        resources: ["*"]
      })
    );

    const logGroup = new logs.LogGroup(this, "CollectorLogGroup", {
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: RemovalPolicy.DESTROY
    });

    // Metrics only: Claude Code logs and traces can carry bash commands, file paths
    // and tool inputs, so the gateway forwards metrics alone (see render-gateway-config.sh).
    const collectorConfig = [
      "extensions:",
      "  health_check:",
      `    endpoint: 0.0.0.0:${COLLECTOR_HEALTH_PORT}`,
      "  sigv4auth:",
      `    region: ${this.region}`,
      "    service: monitoring",
      "receivers:",
      "  otlp:",
      "    protocols:",
      "      http:",
      `        endpoint: 0.0.0.0:${OTLP_LISTENER_PORT}`,
      "processors:",
      "  memory_limiter:",
      "    check_interval: 1s",
      "    limit_percentage: 80",
      "    spike_limit_percentage: 20",
      "  batch:",
      "    send_batch_size: 200",
      "    timeout: 10s",
      "exporters:",
      "  otlphttp/cloudwatch:",
      `    metrics_endpoint: https://monitoring.${this.region}.${this.urlSuffix}/v1/metrics`,
      "    auth:",
      "      authenticator: sigv4auth",
      "    retry_on_failure:",
      "      enabled: true",
      "    sending_queue:",
      "      enabled: true",
      "service:",
      "  extensions: [health_check, sigv4auth]",
      "  pipelines:",
      "    metrics:",
      "      receivers: [otlp]",
      "      processors: [memory_limiter, batch]",
      "      exporters: [otlphttp/cloudwatch]",
      ""
    ].join("\n");

    const container = taskDefinition.addContainer("CollectorContainer", {
      image: ecs.ContainerImage.fromRegistry(config.collectorImage),
      logging: ecs.LogDrivers.awsLogs({ streamPrefix: "collector", logGroup }),
      environment: { AOT_CONFIG_CONTENT: collectorConfig },
      readonlyRootFilesystem: true,
      linuxParameters: new ecs.LinuxParameters(this, "CollectorLinuxParameters", {})
    });
    container.linuxParameters?.dropCapabilities(ecs.Capability.ALL);
    container.addPortMappings(
      { containerPort: OTLP_LISTENER_PORT, protocol: ecs.Protocol.TCP },
      { containerPort: COLLECTOR_HEALTH_PORT, protocol: ecs.Protocol.TCP }
    );

    const service = new ecs.FargateService(this, "CollectorService", {
      cluster,
      taskDefinition,
      desiredCount: config.collectorDesiredCount,
      assignPublicIp: false,
      minHealthyPercent: 100,
      securityGroups: [securityGroup],
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
      circuitBreaker: { rollback: true },
      healthCheckGracePeriod: Duration.seconds(30)
    });

    // Same hostname and certificate as the gateway listener; reachable only from
    // gateway tasks (albSecurityGroup ingress on this port is scoped to the task SG).
    const listener = loadBalancer.addListener("OtlpListener", {
      port: OTLP_LISTENER_PORT,
      protocol: elbv2.ApplicationProtocol.HTTPS,
      certificates: [certificate],
      sslPolicy: elbv2.SslPolicy.RECOMMENDED_TLS,
      open: false
    });
    const targetGroup = listener.addTargets("CollectorTargets", {
      protocol: elbv2.ApplicationProtocol.HTTP,
      port: OTLP_LISTENER_PORT,
      targets: [service.loadBalancerTarget({ containerName: container.containerName, containerPort: OTLP_LISTENER_PORT })],
      healthCheck: {
        enabled: true,
        port: String(COLLECTOR_HEALTH_PORT),
        path: "/",
        healthyHttpCodes: "200",
        interval: Duration.seconds(15),
        healthyThresholdCount: 2,
        unhealthyThresholdCount: 3
      },
      deregistrationDelay: Duration.seconds(30)
    });

    // The gateway doesn't buffer telemetry: while no collector is healthy, exports are dropped.
    new cloudwatch.Alarm(this, "CollectorUnhealthyAlarm", {
      alarmDescription: "No healthy OTLP collector targets - Claude Code telemetry is being dropped",
      metric: targetGroup.metrics.healthyHostCount({ period: Duration.minutes(1), statistic: "min" }),
      threshold: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
      evaluationPeriods: 3,
      treatMissingData: cloudwatch.TreatMissingData.BREACHING
    });

    new cdk.CfnOutput(this, "OtlpForwardUrl", { value: `https://${config.gatewayHost}:${OTLP_LISTENER_PORT}` });
    new cdk.CfnOutput(this, "CollectorLogGroupName", { value: logGroup.logGroupName });
  }
}
