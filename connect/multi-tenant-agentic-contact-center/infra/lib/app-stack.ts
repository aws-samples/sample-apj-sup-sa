import * as path from "path";
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecs from "aws-cdk-lib/aws-ecs";
import * as elbv2 from "aws-cdk-lib/aws-elasticloadbalancingv2";
import * as cloudfront from "aws-cdk-lib/aws-cloudfront";
import * as origins from "aws-cdk-lib/aws-cloudfront-origins";
import * as cognito from "aws-cdk-lib/aws-cognito";
import * as ssm from "aws-cdk-lib/aws-ssm";
import * as logs from "aws-cdk-lib/aws-logs";
import * as cr from "aws-cdk-lib/custom-resources";

export interface AppStackProps extends cdk.StackProps {
  /** OPTIONAL env discriminator to namespace names for a second copy in one
   *  account+region. Empty/omitted → clean common names with no suffix. */
  envName?: string;
}

/**
 * Module 1 — the web application and its platform, with NO Amazon Connect
 * dependency. Deployable on its own: the app runs, login works, and Connect
 * features stay dormant until the connect stack is deployed.
 *
 * Runtime config for the SPA lives in an SSM parameter (`/anycompany-pay/<env>/runtime-config`)
 * that the ECS task reads as a secret. This stack seeds it with Cognito values;
 * the connect stack later merges in the Connect values. Decoupling config from
 * the task definition is what lets the two modules deploy independently.
 */
export class AnyCompanyPayAppStack extends cdk.Stack {
  readonly userPool: cognito.UserPool;
  readonly userPoolClient: cognito.UserPoolClient;
  readonly cognitoDomainFqdn: string;
  readonly distUrl: string;
  readonly cluster: ecs.Cluster;
  readonly service: ecs.FargateService;
  readonly runtimeConfigParamName: string;
  readonly runtimeConfigParamArn: string;

  constructor(scope: Construct, id: string, props: AppStackProps) {
    super(scope, id, props);
    // Optional env discriminator. Empty → no suffix (clean common names).
    const env = (props.envName ?? "").trim();
    const sfx = env ? `-${env}` : ""; // name suffix: "-foo" or ""
    const seg = env ? `/${env}` : ""; // path segment: "/foo" or ""

    // ------------------------------------------------------------------
    // Networking
    // ------------------------------------------------------------------
    const vpc = new ec2.Vpc(this, "Vpc", {
      maxAzs: 2,
      natGateways: 1,
      subnetConfiguration: [
        { name: "public", subnetType: ec2.SubnetType.PUBLIC, cidrMask: 24 },
        { name: "private", subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS, cidrMask: 24 },
      ],
    });

    const albSg = new ec2.SecurityGroup(this, "AlbSg", {
      vpc,
      description: "Internal ALB - accepts traffic from CloudFront VPC origin",
      allowAllOutbound: true,
    });
    const serviceSg = new ec2.SecurityGroup(this, "ServiceSg", {
      vpc,
      description: "Fargate service - accepts traffic from the ALB only",
      allowAllOutbound: true,
    });
    serviceSg.addIngressRule(albSg, ec2.Port.tcp(80), "ALB to Fargate task (nginx)");

    // Only CloudFront's origin-facing prefix list may reach the internal ALB.
    const cfPrefixList = new cr.AwsCustomResource(this, "CfOriginPrefixList", {
      onUpdate: {
        service: "EC2",
        action: "describeManagedPrefixLists",
        parameters: {
          Filters: [
            { Name: "prefix-list-name", Values: ["com.amazonaws.global.cloudfront.origin-facing"] },
          ],
        },
        physicalResourceId: cr.PhysicalResourceId.of("cloudfront-origin-facing-prefix-list"),
      },
      policy: cr.AwsCustomResourcePolicy.fromSdkCalls({
        resources: cr.AwsCustomResourcePolicy.ANY_RESOURCE,
      }),
      installLatestAwsSdk: false,
    });
    albSg.addIngressRule(
      ec2.Peer.prefixList(cfPrefixList.getResponseField("PrefixLists.0.PrefixListId")),
      ec2.Port.tcp(80),
      "CloudFront VPC origin"
    );

    const alb = new elbv2.ApplicationLoadBalancer(this, "Alb", {
      vpc,
      internetFacing: false,
      securityGroup: albSg,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
    });
    // Reject requests carrying malformed headers rather than forwarding them.
    alb.setAttribute("routing.http.drop_invalid_header_fields.enabled", "true");

    // ------------------------------------------------------------------
    // CloudFront -> VPC origin -> internal ALB
    // ------------------------------------------------------------------
    const distribution = new cloudfront.Distribution(this, "Distribution", {
      comment: `AnyCompanyPay SPA (${env || "default"}) - VPC origin, client-side Cognito auth`,
      defaultRootObject: "index.html",
      priceClass: cloudfront.PriceClass.PRICE_CLASS_ALL,
      defaultBehavior: {
        origin: origins.VpcOrigin.withApplicationLoadBalancer(alb, {
          protocolPolicy: cloudfront.OriginProtocolPolicy.HTTP_ONLY,
          httpPort: 80,
        }),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD_OPTIONS,
        cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
        originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
      },
    });
    this.distUrl = `https://${distribution.distributionDomainName}`;

    // ------------------------------------------------------------------
    // Cognito (names suffixed with env so this env is independent)
    // ------------------------------------------------------------------
    this.userPool = new cognito.UserPool(this, "UserPool", {
      userPoolName: `anycompany-pay-users${sfx}`,
      selfSignUpEnabled: false,
      signInAliases: { email: true },
      autoVerify: { email: true },
      standardAttributes: { email: { required: true, mutable: true } },
      customAttributes: {
        merchant_id: new cognito.StringAttribute({ minLen: 1, maxLen: 64, mutable: true }),
        merchant_name: new cognito.StringAttribute({ minLen: 1, maxLen: 128, mutable: true }),
      },
      passwordPolicy: {
        minLength: 8,
        requireLowercase: true,
        requireUppercase: true,
        requireDigits: true,
        requireSymbols: false,
      },
      accountRecovery: cognito.AccountRecovery.EMAIL_ONLY,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const userPoolDomain = this.userPool.addDomain("HostedUiDomain", {
      cognitoDomain: { domainPrefix: `anycompany-pay-${this.account}${sfx}` },
    });

    new cognito.CfnUserPoolGroup(this, "AdminGroup", {
      userPoolId: this.userPool.userPoolId,
      groupName: "admin",
      description: "AnyCompanyPay operations / admin users",
    });
    new cognito.CfnUserPoolGroup(this, "MerchantGroup", {
      userPoolId: this.userPool.userPoolId,
      groupName: "merchant",
      description: "AnyCompanyPay merchant users",
    });

    this.userPoolClient = this.userPool.addClient("WebClient", {
      userPoolClientName: "anycompany-pay-web",
      generateSecret: false,
      authFlows: { userSrp: true },
      oAuth: {
        flows: { authorizationCodeGrant: true },
        scopes: [cognito.OAuthScope.OPENID, cognito.OAuthScope.EMAIL, cognito.OAuthScope.PROFILE],
        callbackUrls: [`${this.distUrl}/auth/callback`, "http://localhost:5173/auth/callback"],
        logoutUrls: [`${this.distUrl}/`, "http://localhost:5173/"],
      },
      preventUserExistenceErrors: true,
    });

    this.cognitoDomainFqdn = `${userPoolDomain.domainName}.auth.${this.region}.amazoncognito.com`;

    // ------------------------------------------------------------------
    // SSM runtime config parameter (single source of truth for the SPA).
    // Seeded here with Cognito values + empty connect; the connect stack
    // merges in Connect values later via the same config-writer.
    // ------------------------------------------------------------------
    this.runtimeConfigParamName = `/anycompany-pay${seg}/runtime-config`;
    this.runtimeConfigParamArn = cdk.Arn.format(
      { service: "ssm", resource: "parameter", resourceName: `anycompany-pay${seg}/runtime-config` },
      this
    );

    // Owned by this stack as a real SSM parameter (a CFN resource, referenced by
    // ARN — no eager deploy-time resolution). Seeded with Cognito values + empty
    // connect. The connect stack merges Connect values in later via PutParameter;
    // because this value string is stable across app redeploys (same pool), that
    // merge is preserved unless the Cognito identifiers themselves change.
    const runtimeConfigParam = new ssm.StringParameter(this, "RuntimeConfig", {
      parameterName: this.runtimeConfigParamName,
      description: "AnyCompanyPay SPA runtime config (Cognito seeded; connect merged by the connect stack)",
      tier: ssm.ParameterTier.STANDARD,
      stringValue: JSON.stringify({
        region: this.region,
        userPoolId: this.userPool.userPoolId,
        clientId: this.userPoolClient.userPoolClientId,
        domain: this.cognitoDomainFqdn,
        connect: {},
      }),
    });

    // ------------------------------------------------------------------
    // ECS Fargate service (nginx serving the SPA). The container reads the
    // whole runtime config as one SSM-backed secret (RUNTIME_CONFIG).
    // ------------------------------------------------------------------
    this.cluster = new ecs.Cluster(this, "Cluster", {
      vpc,
      containerInsightsV2: ecs.ContainerInsights.ENABLED,
    });

    const taskDef = new ecs.FargateTaskDefinition(this, "TaskDef", {
      cpu: 256,
      memoryLimitMiB: 512,
      runtimePlatform: {
        cpuArchitecture: ecs.CpuArchitecture.X86_64,
        operatingSystemFamily: ecs.OperatingSystemFamily.LINUX,
      },
    });

    const container = taskDef.addContainer("web", {
      image: ecs.ContainerImage.fromAsset(path.join(__dirname, "..", ".."), {
        platform: cdk.aws_ecr_assets.Platform.LINUX_AMD64,
      }),
      secrets: {
        RUNTIME_CONFIG: ecs.Secret.fromSsmParameter(runtimeConfigParam),
      },
      logging: ecs.LogDrivers.awsLogs({
        streamPrefix: "anycompany-pay",
        logRetention: logs.RetentionDays.ONE_WEEK,
      }),
    });
    container.addPortMappings({ containerPort: 80, protocol: ecs.Protocol.TCP });

    this.service = new ecs.FargateService(this, "Service", {
      cluster: this.cluster,
      taskDefinition: taskDef,
      desiredCount: 1,
      assignPublicIp: false,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS },
      securityGroups: [serviceSg],
      circuitBreaker: { rollback: true },
      minHealthyPercent: 100,
      maxHealthyPercent: 200,
    });
    // The parameter must exist before tasks launch (they read it as a secret).
    this.service.node.addDependency(runtimeConfigParam);

    const listener = alb.addListener("Http", {
      port: 80,
      protocol: elbv2.ApplicationProtocol.HTTP,
      open: false,
    });
    listener.addTargets("Fargate", {
      port: 80,
      protocol: elbv2.ApplicationProtocol.HTTP,
      targets: [this.service],
      healthCheck: {
        path: "/healthz",
        healthyHttpCodes: "200",
        interval: cdk.Duration.seconds(30),
        healthyThresholdCount: 2,
        unhealthyThresholdCount: 3,
      },
      deregistrationDelay: cdk.Duration.seconds(15),
    });

    const scaling = this.service.autoScaleTaskCount({ minCapacity: 1, maxCapacity: 4 });
    scaling.scaleOnCpuUtilization("CpuScaling", {
      targetUtilizationPercent: 60,
      scaleInCooldown: cdk.Duration.seconds(60),
      scaleOutCooldown: cdk.Duration.seconds(60),
    });

    // ------------------------------------------------------------------
    // Outputs
    // ------------------------------------------------------------------
    new cdk.CfnOutput(this, "CloudFrontUrl", { value: this.distUrl });
    new cdk.CfnOutput(this, "UserPoolId", { value: this.userPool.userPoolId });
    new cdk.CfnOutput(this, "UserPoolClientId", { value: this.userPoolClient.userPoolClientId });
    new cdk.CfnOutput(this, "CognitoHostedUiDomain", { value: this.cognitoDomainFqdn });
    new cdk.CfnOutput(this, "RuntimeConfigParam", { value: this.runtimeConfigParamName });
    new cdk.CfnOutput(this, "ClusterName", { value: this.cluster.clusterName });
    new cdk.CfnOutput(this, "ServiceName", { value: this.service.serviceName });
  }
}
