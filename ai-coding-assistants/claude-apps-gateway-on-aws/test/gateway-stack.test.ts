import * as cdk from "aws-cdk-lib";
import { Match, Template } from "aws-cdk-lib/assertions";
import { defaultGatewayConfig } from "../lib/config";
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
