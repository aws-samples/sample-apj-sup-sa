import * as cdk from "aws-cdk-lib";
import { Match, Template } from "aws-cdk-lib/assertions";
import { defaultGatewayConfig, validateResourceAttributes } from "../lib/config";
import { GatewayStack } from "../lib/gateway-stack";

const testConfig = defaultGatewayConfig;

function synthTemplate(): Template {
  const app = new cdk.App();
  const stack = new GatewayStack(app, "TestGatewayStack", {
    env: {
      account: "123456789012",
      region: "us-east-1"
    },
    config: testConfig
  });
  return Template.fromStack(stack);
}

const template = synthTemplate();

describe("GatewayStack", () => {
  test("uses an internal ALB and does not create CloudFront", () => {
    template.hasResourceProperties("AWS::ElasticLoadBalancingV2::LoadBalancer", {
      Scheme: "internal",
      Type: "application"
    });
    template.resourceCountIs("AWS::CloudFront::Distribution", 0);
  });

  test("issues a DNS-validated ACM certificate for the gateway hostname", () => {
    template.hasResourceProperties("AWS::CertificateManager::Certificate", {
      DomainName: testConfig.gatewayHost,
      ValidationMethod: "DNS"
    });
  });

  test("creates Cognito OAuth client with the gateway callback URL", () => {
    template.hasResourceProperties("AWS::Cognito::UserPoolClient", {
      AllowedOAuthFlows: ["code"],
      AllowedOAuthFlowsUserPoolClient: true,
      AllowedOAuthScopes: Match.arrayWith(["openid", "email", "profile"]),
      CallbackURLs: [`https://${testConfig.gatewayHost}/oauth/callback`],
      GenerateSecret: true,
      SupportedIdentityProviders: ["COGNITO"]
    });
  });

  test("injects gateway secrets into the ECS task definition", () => {
    template.hasResourceProperties("AWS::ECS::TaskDefinition", {
      ContainerDefinitions: Match.arrayWith([
        Match.objectLike({
          Name: "GatewayContainer",
          Environment: Match.arrayWith([
            Match.objectLike({
              Name: "BEDROCK_REGION",
              Value: "us-east-1"
            }),
            Match.objectLike({
              Name: "GATEWAY_AVAILABLE_MODELS",
              Value: testConfig.availableModels.join(",")
            }),
            Match.objectLike({
              Name: "GATEWAY_PUBLIC_URL",
              Value: `https://${testConfig.gatewayHost}`
            })
          ]),
          Secrets: Match.arrayWith([
            Match.objectLike({ Name: "GATEWAY_DB_PASSWORD" }),
            Match.objectLike({ Name: "GATEWAY_DB_USERNAME" }),
            Match.objectLike({ Name: "GATEWAY_JWT_SECRET" }),
            Match.objectLike({ Name: "OIDC_CLIENT_SECRET" })
          ])
        })
      ])
    });
  });

  test("grants the task role only Bedrock invoke permissions for model calls", () => {
    template.hasResourceProperties("AWS::IAM::Policy", {
      PolicyDocument: {
        Statement: Match.arrayWith([
          Match.objectLike({
            Action: Match.arrayWith(["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]),
            Effect: "Allow",
            Sid: "InvokeClaudeModelsOnBedrock"
          })
        ])
      }
    });

    const policies = template.findResources("AWS::IAM::Policy");
    const serialized = JSON.stringify(policies);
    expect(serialized).toContain("inference-profile/*");
    expect(serialized).toContain("foundation-model/anthropic.*");
  });

  test("scopes private DNS to the gateway FQDN so the parent domain is not shadowed", () => {
    // The private zone must cover only the gateway hostname; a zone named
    // hostedZoneName would shadow every other record in the corporate domain
    // for VPC/VPN clients.
    template.resourceCountIs("AWS::Route53::HostedZone", 1);
    template.hasResourceProperties("AWS::Route53::HostedZone", {
      Name: `${testConfig.gatewayHost}.`,
      VPCs: Match.arrayWith([
        Match.objectLike({
          VPCRegion: "us-east-1"
        })
      ])
    });

    template.hasResourceProperties("AWS::Route53::RecordSet", {
      Name: `${testConfig.gatewayHost}.`,
      Type: "A"
    });
  });

  test("creates interface endpoints and an S3 gateway endpoint for AWS-service traffic", () => {
    // 6 interface endpoints (Bedrock Runtime, Secrets Manager, ECR api+dkr,
    // Logs, Monitoring) + 1 S3 gateway endpoint.
    template.resourceCountIs("AWS::EC2::VPCEndpoint", 7);
    template.hasResourceProperties("AWS::EC2::VPCEndpoint", {
      ServiceName: "com.amazonaws.us-east-1.bedrock-runtime",
      VpcEndpointType: "Interface",
      PrivateDnsEnabled: true
    });
    template.hasResourceProperties("AWS::EC2::VPCEndpoint", {
      VpcEndpointType: "Gateway"
    });
  });

  test("omits VPC endpoints when createVpcEndpoints is false", () => {
    const app = new cdk.App();
    const stack = new GatewayStack(app, "TestGatewayStackNoEndpoints", {
      env: {
        account: "123456789012",
        region: "us-east-1"
      },
      config: { ...testConfig, createVpcEndpoints: false }
    });
    Template.fromStack(stack).resourceCountIs("AWS::EC2::VPCEndpoint", 0);
  });

  test("alarms on unhealthy target hosts", () => {
    template.hasResourceProperties("AWS::CloudWatch::Alarm", {
      MetricName: "UnHealthyHostCount",
      Namespace: "AWS/ApplicationELB",
      ComparisonOperator: "GreaterThanOrEqualToThreshold",
      Threshold: 1,
      EvaluationPeriods: 3
    });
  });

  test("restricts ALB, ECS task, and database ingress ports", () => {
    template.hasResourceProperties("AWS::EC2::SecurityGroup", {
      GroupDescription: "Allow private-network HTTPS traffic to Claude Apps Gateway",
      SecurityGroupIngress: Match.arrayWith([
        Match.objectLike({
          CidrIp: "10.0.0.0/8",
          FromPort: 443,
          IpProtocol: "tcp",
          ToPort: 443
        })
      ])
    });
    template.hasResourceProperties("AWS::EC2::SecurityGroupIngress", {
      FromPort: 8080,
      IpProtocol: "tcp",
      ToPort: 8080
    });
    template.hasResourceProperties("AWS::EC2::SecurityGroupIngress", {
      FromPort: 5432,
      IpProtocol: "tcp",
      ToPort: 5432
    });

    const securityGroups = template.findResources("AWS::EC2::SecurityGroup");
    const serialized = JSON.stringify(securityGroups);
    expect(serialized).not.toContain('"CidrIp":"0.0.0.0/0","Description":"Allow from anyone on port 443"');
  });
});

describe("GatewayStack telemetry (CloudWatch Coding Agent Insights)", () => {
  const containerEnv = (name: string) =>
    Object.values(template.findResources("AWS::ECS::TaskDefinition"))
      .flatMap((td: any) => td.Properties.ContainerDefinitions)
      .find((c: any) => c.Name === name).Environment as Array<{ Name: string; Value: unknown }>;

  test("points the gateway's telemetry relay at the ALB OTLP listener", () => {
    const env = containerEnv("GatewayContainer");
    expect(env).toEqual(expect.arrayContaining([
      { Name: "TELEMETRY_FORWARD_URL", Value: `https://${testConfig.gatewayHost}:4318` },
      { Name: "TELEMETRY_RESOURCE_ATTRIBUTES", Value: "organization=example-org" }
    ]));
  });

  test("runs a hardened ARM64 ADOT collector that SigV4-signs metrics to the CloudWatch OTLP endpoint", () => {
    template.hasResourceProperties("AWS::ECS::TaskDefinition", {
      RuntimePlatform: { CpuArchitecture: "ARM64" },
      ContainerDefinitions: [
        Match.objectLike({
          Name: "CollectorContainer",
          Image: testConfig.collectorImage,
          ReadonlyRootFilesystem: true,
          LinuxParameters: { Capabilities: { Drop: ["ALL"] } }
        })
      ]
    });
    const config = JSON.stringify(containerEnv("CollectorContainer"));
    expect(config).toContain("service: monitoring");
    expect(config).toContain("authenticator: sigv4auth");
    expect(config).toContain("/v1/metrics");
    // Metrics pipeline only; no logs/traces exporters.
    expect(config).not.toMatch(/logs:|traces:/);
    template.hasResourceProperties("AWS::ECS::Service", { DesiredCount: 2, LaunchType: "FARGATE" });
  });

  test("grants the collector only cloudwatch:PutMetricData", () => {
    const policies = Object.values(template.findResources("AWS::IAM::Policy")) as any[];
    const collector = policies.filter((p) =>
      p.Properties.PolicyDocument.Statement.some((st: any) => st.Sid === "PublishOtlpMetricsToCloudWatch"));
    expect(collector).toHaveLength(1);
    const actions = collector[0].Properties.PolicyDocument.Statement.flatMap((st: any) => [].concat(st.Action));
    expect(actions).toEqual(["cloudwatch:PutMetricData"]);
  });

  test("exposes OTLP on an HTTPS listener with the gateway certificate and a collector health check", () => {
    template.hasResourceProperties("AWS::ElasticLoadBalancingV2::Listener", {
      Port: 4318,
      Protocol: "HTTPS",
      SslPolicy: Match.anyValue(),
      Certificates: Match.anyValue()
    });
    template.hasResourceProperties("AWS::ElasticLoadBalancingV2::TargetGroup", {
      Port: 4318,
      HealthCheckPort: "13133",
      HealthCheckPath: "/"
    });
  });

  test("lets only gateway tasks reach the OTLP listener, never client CIDRs", () => {
    const ingress = Object.values(template.findResources("AWS::EC2::SecurityGroupIngress")) as any[];
    const otlpToAlb = ingress.filter((r) => r.Properties.FromPort === 4318 && r.Properties.Description?.includes("telemetry relay"));
    expect(otlpToAlb).toHaveLength(1);
    expect(otlpToAlb[0].Properties.SourceSecurityGroupId).toBeDefined();
    const albSg = Object.values(template.findResources("AWS::EC2::SecurityGroup")).find((sg: any) =>
      sg.Properties.GroupDescription === "Allow private-network HTTPS traffic to Claude Apps Gateway") as any;
    expect((albSg.Properties.SecurityGroupIngress || []).every((r: any) => r.FromPort === 443)).toBe(true);
  });

  test("lets the collector use the VPC interface endpoints", () => {
    template.hasResourceProperties("AWS::EC2::SecurityGroupIngress", {
      Description: "Collector tasks to VPC endpoints",
      FromPort: 443
    });
  });

  test("alarms when no collector is healthy", () => {
    template.hasResourceProperties("AWS::CloudWatch::Alarm", {
      MetricName: "HealthyHostCount",
      ComparisonOperator: "LessThanThreshold",
      TreatMissingData: "breaching"
    });
  });

  test("creates no collector, listener or relay config when enableTelemetry is false", () => {
    const app = new cdk.App();
    const stack = new GatewayStack(app, "TestGatewayStackNoTelemetry", {
      env: { account: "123456789012", region: "us-east-1" },
      config: { ...testConfig, enableTelemetry: false }
    });
    const t = Template.fromStack(stack);
    t.resourceCountIs("AWS::ECS::Service", 1);
    t.resourceCountIs("AWS::ElasticLoadBalancingV2::Listener", 1);
    expect(JSON.stringify(t.toJSON())).not.toContain("TELEMETRY_FORWARD_URL");
  });

  test.each([
    [{ "user.email": "x" }, /reserved/],
    [{ "service.name": "x" }, /reserved/],
    [{ "bad name": "x" }, /invalid name/],
    [{ organization: "a b" }, /invalid value/],
    [{ organization: "a,b" }, /invalid value/],
    [{ organization: "" }, /invalid value/],
    [{ organization: "x".repeat(256) }, /255/]
  ])("rejects invalid resource attribute %j at synth time", (attributes, message) => {
    expect(() => validateResourceAttributes(attributes as Record<string, string>)).toThrow(message);
  });

  test("accepts the attributes Coding Agent Insights slices by", () => {
    expect(() => validateResourceAttributes({
      organization: "acme", department: "engineering", "team.id": "platform", cost_center: "cc-1234"
    })).not.toThrow();
  });
});
