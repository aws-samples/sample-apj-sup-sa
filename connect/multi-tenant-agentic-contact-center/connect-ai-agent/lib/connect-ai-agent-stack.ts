import * as path from "path";
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as lambdaNode from "aws-cdk-lib/aws-lambda-nodejs";
import * as aoss from "aws-cdk-lib/aws-opensearchserverless";
import * as agentcore from "aws-cdk-lib/aws-bedrockagentcore";
import * as connect from "aws-cdk-lib/aws-connect";
import * as cr from "aws-cdk-lib/custom-resources";

/**
 * AnyCompanyPay AI transaction Q&A — deployable AWS resources.
 *
 * Deploys the two Lambdas that make the AI-first "ask about my transactions"
 * flow tenant-safe:
 *   - TransactionToolFn : the MCP tool (AgentCore Gateway Lambda target). Runs in
 *     the Aurora VPC and queries the PRIVATE OpenSearch Serverless collection over
 *     SigV4, always filtered by the trusted merchant_id (never model args).
 *   - GatewayInterceptorFn : the AgentCore Gateway request interceptor (tenant
 *     gate) — pins merchant_id from the trusted session context, strips any
 *     model-supplied value, fails closed.
 *
 * Also created here (via the AWS::BedrockAgentCore::* CloudFormation resources):
 * the AgentCore Gateway (MCP, CUSTOM_JWT inbound auth with Amazon Connect as the
 * OIDC issuer), its request-interceptor attachment, the gateway execution role,
 * and the Lambda tool target. `cdk destroy` now tears all of this down — no CLI
 * teardown needed. (provision-gateway.sh is the legacy, pre-CFN path and is kept
 * only for reference.)
 *
 * One wrinkle handled below: the gateway's CUSTOM_JWT `allowedAudience` must equal
 * the gateway's OWN id (Amazon Connect puts the gateway id in the token `aud`).
 * That is a self-reference CloudFormation can't express, so the audience is set
 * immediately after creation by a small in-stack custom resource (read-modify-write
 * of the same gateway). It is part of the stack, so it is removed on destroy too.
 */
export class ConnectAiAgentStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);
    const region = this.region;

    // --- Inputs ---
    // Nothing here is hardcoded to a region or environment. The Aurora VPC/subnets
    // and the OpenSearch Serverless collection endpoint are REQUIRED and supplied
    // by this module's deploy.sh, which DISCOVERS them from the upstream stack
    // outputs (AnyCompanyPayAuroraStack / AnyCompanyPayZeroEtlStack). Running `cdk deploy` bare
    // (no deploy.sh, no -c) fails fast with a clear message instead of silently
    // wiring to another environment's / region's resources.
    // Optional env discriminator. Empty → no suffix (clean common names).
    const envName = ((this.node.tryGetContext("envName") as string) ?? "").trim();
    const sfx = envName ? `-${envName}` : "";
    const ctx = (k: string, d: string) => (this.node.tryGetContext(k) as string) ?? d;
    const reqCtx = (k: string, hint: string): string => {
      const v = this.node.tryGetContext(k) as string | undefined;
      if (!v) {
        throw new Error(
          `Missing required context "-c ${k}=...": ${hint} ` +
            `Run this module's deploy.sh (it discovers every required value from the ` +
            `upstream stack outputs), or pass -c ${k}=... explicitly.`
        );
      }
      return v;
    };

    const dbVpcId = reqCtx("dbVpcId", "Aurora VPC id (AnyCompanyPayAuroraStack output VpcId).");
    const dbSubnetIds = reqCtx(
      "dbSubnetIds",
      "comma-separated Aurora isolated subnet ids."
    ).split(",");
    const collectionName = ctx("collectionName", "anycompany-pay-tx");
    const collectionEndpoint = reqCtx(
      "collectionEndpoint",
      "private OpenSearch Serverless collection endpoint (AnyCompanyPayZeroEtlStack output CollectionEndpoint)."
    );
    // Collection id is the endpoint host label; the ARN is derived from it.
    const collectionId = ctx(
      "collectionId",
      collectionEndpoint.replace(/^https?:\/\//, "").split(".")[0]
    );
    const collectionArn = `arn:aws:aoss:${region}:${this.account}:collection/${collectionId}`;
    const indexName = ctx("indexName", "transactions");
    // Reserved tool-argument key the interceptor injects, and the tool reads,
    // the trusted tenant under. Passed to BOTH Lambdas so they can't drift.
    const trustedArgKey = ctx("trustedArgKey", "__trusted_merchant_id");

    // --- AgentCore Gateway inbound auth (Amazon Connect is the OIDC issuer) ---
    // The gateway's CUSTOM_JWT discovery URL is the Connect instance's OIDC
    // endpoint (NOT Cognito). Override connectAlias/connectDiscoveryUrl with -c
    // if the instance alias differs from the default.
    const connectAlias = ctx("connectAlias", `anycompany-pay-${this.account}${sfx}`);
    const discoveryUrl = ctx(
      "connectDiscoveryUrl",
      `https://${connectAlias}.my.connect.aws/.well-known/openid-configuration`
    );
    const mcpVersion = ctx("mcpVersion", "2025-03-26");
    const gatewayName = ctx("gatewayName", "anycompany-pay-transaction-tools");
    const toolTargetName = ctx("toolTargetName", "query-transactions");

    // Connect instance ARN — REQUIRED to attach the AI-agent security profile
    // (below) that grants the orchestrator permission to INVOKE the gateway MCP
    // tool. deploy.sh discovers it from AnyCompanyPayConnectStack-<env> (ConnectInstanceArn).
    const connectInstanceArn = reqCtx(
      "connectInstanceArn",
      "Connect instance ARN (AnyCompanyPayConnectStack-<env> output ConnectInstanceArn)."
    );
    const securityProfileName = ctx("securityProfileName", "anycompany-pay-ai-agent-tools");

    const vpc = ec2.Vpc.fromVpcAttributes(this, "DbVpc", {
      vpcId: dbVpcId,
      availabilityZones: [`${region}a`, `${region}b`],
      isolatedSubnetIds: dbSubnetIds,
    });

    // ------------------------------------------------------------------
    // Tool Lambda — in the Aurora VPC, queries the private collection.
    // The collection's VPC-endpoint SG already admits 443 from the VPC CIDR,
    // so no additional security group is needed for egress to the collection.
    // ------------------------------------------------------------------
    const toolFn = new lambdaNode.NodejsFunction(this, "TransactionToolFn", {
      entry: path.join(__dirname, "..", "lambda", "transaction-tool", "index.ts"),
      runtime: lambda.Runtime.NODEJS_22_X,
      timeout: cdk.Duration.seconds(20),
      memorySize: 256,
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      bundling: {
        nodeModules: ["@opensearch-project/opensearch", "@aws-sdk/credential-provider-node"],
        externalModules: [],
      },
      environment: {
        COLLECTION_ENDPOINT: collectionEndpoint,
        INDEX_NAME: indexName,
        TRUSTED_ARG_KEY: trustedArgKey,
      },
    });
    // Sign/authorize requests to the collection data plane (least privilege:
    // scoped to this collection ARN, not "*").
    toolFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["aoss:APIAccessAll"],
        resources: [collectionArn],
      })
    );

    // Read-only data-access policy for the tool role on the collection index.
    new aoss.CfnAccessPolicy(this, "ToolReadPolicy", {
      name: `${collectionName}-aiqa-read`,
      type: "data",
      policy: JSON.stringify([
        {
          Rules: [
            {
              ResourceType: "index",
              Resource: [`index/${collectionName}/*`],
              Permission: ["aoss:DescribeIndex", "aoss:ReadDocument"],
            },
            {
              ResourceType: "collection",
              Resource: [`collection/${collectionName}`],
              Permission: ["aoss:DescribeCollectionItems"],
            },
          ],
          Principal: [toolFn.role!.roleArn],
        },
      ]),
    });

    // ------------------------------------------------------------------
    // Gateway request interceptor — tenant gate. No VPC / no data access; it
    // only inspects the request context and returns an ALLOW/DENY decision.
    // ------------------------------------------------------------------
    const interceptorFn = new lambdaNode.NodejsFunction(this, "GatewayInterceptorFn", {
      entry: path.join(__dirname, "..", "lambda", "gateway-interceptor", "index.ts"),
      runtime: lambda.Runtime.NODEJS_22_X,
      timeout: cdk.Duration.seconds(10),
      memorySize: 128,
      // Bundle the Connect client so the interceptor can resolve the trusted
      // merchant_id from the contact (Hop 2) regardless of runtime SDK contents.
      bundling: { minify: true, target: "node22", nodeModules: ["@aws-sdk/client-connect"] },
      environment: {
        TRUSTED_ARG_KEY: trustedArgKey,
        MERCHANT_ATTR_KEY: ctx("merchantAttrKey", "merchant_id"),
      },
    });
    // Resolve the trusted tenant from the Connect contact at tools/call time.
    interceptorFn.addToRolePolicy(
      new iam.PolicyStatement({
        sid: "ResolveContactTenant",
        actions: ["connect:GetContactAttributes"],
        resources: [`arn:aws:connect:${region}:${this.account}:instance/*/contact/*`],
      })
    );

    // NOTE: Hop 1 (a "session seeder" Lambda that copied merchant_id into the
    // Q-in-Connect session via UpdateSessionData) was REMOVED. The gateway
    // interceptor resolves the trusted merchant_id directly from the Connect
    // contact (GetContactAttributes on the x-amz-connect-contact-id header), so
    // no session seeding is required for tenant isolation.

    // The AgentCore Gateway service principal must be able to invoke both
    // Lambdas (tool target + interceptor). Scope the resource policy to the
    // AgentCore service principal for this account.
    const agentCorePrincipal = new iam.ServicePrincipal("bedrock-agentcore.amazonaws.com");
    toolFn.addPermission("AgentCoreInvokeTool", {
      principal: agentCorePrincipal,
      action: "lambda:InvokeFunction",
      sourceAccount: this.account,
    });
    interceptorFn.addPermission("AgentCoreInvokeInterceptor", {
      principal: agentCorePrincipal,
      action: "lambda:InvokeFunction",
      sourceAccount: this.account,
    });

    // ==================================================================
    // AgentCore Gateway (AWS::BedrockAgentCore::*) — replaces provision-gateway.sh
    // ==================================================================

    // Gateway execution role: assumed by AgentCore, may invoke both Lambdas
    // (the tool target and the request interceptor).
    const gatewayRole = new iam.Role(this, "GatewayExecRole", {
      assumedBy: new iam.ServicePrincipal("bedrock-agentcore.amazonaws.com", {
        conditions: { StringEquals: { "aws:SourceAccount": this.account } },
      }),
      description: "AnyCompanyPay AgentCore Gateway: invoke the transaction tool + interceptor Lambdas",
    });
    gatewayRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ["lambda:InvokeFunction"],
        resources: [toolFn.functionArn, interceptorFn.functionArn],
      })
    );

    // The MCP tool schema. merchant_id is deliberately NOT a tool input — the
    // interceptor injects the trusted tenant. (SchemaDefinition has no `enum`;
    // the allowed statuses are described in the field text and enforced by the
    // tool Lambda.)
    const toolDefinition: agentcore.CfnGatewayTarget.ToolDefinitionProperty = {
      name: "query_transactions",
      description:
        "Query the calling merchant's own payment transactions (read-only). Returns matching transactions, a count, and totals. The merchant is determined automatically; never ask for or accept a merchant id.",
      inputSchema: {
        type: "object",
        properties: {
          status: {
            type: "string",
            description:
              "Optional status filter: succeeded, pending, in_progress, failed, refunded, or authorized",
          },
          transactionId: { type: "string", description: "Optional exact transaction id (txn_...)" },
          query: { type: "string", description: "Optional free-text search over id/method/currency/status" },
          limit: { type: "integer", description: "Max results to return (1-25)" },
        },
      },
    };

    // The Gateway itself (MCP protocol, CUSTOM_JWT inbound auth via Connect OIDC,
    // request interceptor attached). allowedAudience is set post-create by the
    // custom resource below (self-reference — see the class doc).
    const gateway = new agentcore.CfnGateway(this, "TransactionGateway", {
      name: gatewayName,
      roleArn: gatewayRole.roleArn,
      protocolType: "MCP",
      protocolConfiguration: { mcp: { supportedVersions: [mcpVersion] } },
      authorizerType: "CUSTOM_JWT",
      // The AgentCore API requires a CUSTOM_JWT authorizer to define at least one
      // of allowedAudience / allowedClients / allowedScopes / CustomClaims at
      // CREATE time. The real audience is the gateway's OWN id (a self-reference
      // CFN can't express), so seed a placeholder here; GatewayAudienceFn (below)
      // overwrites allowedAudience with [gatewayId] immediately after create.
      authorizerConfiguration: {
        customJwtAuthorizer: { discoveryUrl, allowedAudience: ["pending-set-after-create"] },
      },
      interceptorConfigurations: [
        {
          interceptor: { lambda: { arn: interceptorFn.functionArn } },
          interceptionPoints: ["REQUEST"],
          inputConfiguration: { passRequestHeaders: true },
        },
      ],
      description: "AnyCompanyPay transaction Q&A — tenant-isolated MCP gateway",
    });

    // The Lambda tool target (MCP). Gateway invokes it under its own IAM role.
    const target = new agentcore.CfnGatewayTarget(this, "TransactionToolTarget", {
      gatewayIdentifier: gateway.attrGatewayIdentifier,
      name: toolTargetName,
      targetConfiguration: {
        mcp: {
          lambda: {
            lambdaArn: toolFn.functionArn,
            toolSchema: { inlinePayload: [toolDefinition] },
          },
        },
      },
      credentialProviderConfigurations: [{ credentialProviderType: "GATEWAY_IAM_ROLE" }],
    });
    // (target implicitly depends on the gateway via gatewayIdentifier)

    // Self-reference workaround: set allowedAudience = [gatewayId]. A tiny
    // Lambda-backed custom resource reads the just-created gateway and writes the
    // audience back (read-modify-write, so no config is duplicated). On stack
    // delete it is a no-op — CloudFormation deletes the gateway itself.
    const audienceFn = new lambdaNode.NodejsFunction(this, "GatewayAudienceFn", {
      entry: path.join(__dirname, "..", "lambda", "gateway-audience", "index.ts"),
      runtime: lambda.Runtime.NODEJS_22_X,
      timeout: cdk.Duration.seconds(60),
      memorySize: 128,
      bundling: { minify: true, target: "node22", nodeModules: ["@aws-sdk/client-bedrock-agentcore-control"] },
    });
    audienceFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["bedrock-agentcore:GetGateway", "bedrock-agentcore:UpdateGateway"],
        resources: [gateway.attrGatewayArn],
      })
    );
    // UpdateGateway re-specifies the gateway's roleArn, so the caller needs
    // PassRole on it (scoped to the AgentCore service).
    audienceFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["iam:PassRole"],
        resources: [gatewayRole.roleArn],
        conditions: { StringEquals: { "iam:PassedToService": "bedrock-agentcore.amazonaws.com" } },
      })
    );
    const audienceProvider = new cr.Provider(this, "GatewayAudienceProvider", {
      onEventHandler: audienceFn,
    });
    const audienceCr = new cdk.CustomResource(this, "GatewayAudience", {
      serviceToken: audienceProvider.serviceToken,
      properties: {
        GatewayId: gateway.attrGatewayIdentifier,
        Region: this.region,
        // Re-run (re-assert the audience) whenever the gateway config changes.
        ConfigHash: `${discoveryUrl}|${mcpVersion}|${interceptorFn.functionArn}|${gatewayRole.roleArn}`,
      },
    });
    audienceCr.node.addDependency(gateway);
    audienceCr.node.addDependency(target);

    // ==================================================================
    // AI-agent security profile — the INVOCATION gate for the MCP tool
    // ==================================================================
    // An Amazon Q in Connect AI agent can DISCOVER a gateway tool (tools/list)
    // but can only INVOKE it (tools/call) when a security profile assigned to
    // the agent grants that specific MCP tool. Without this grant the
    // orchestrator silently never emits tools/call (the tool "lists but doesn't
    // fire"). See the AWS docs:
    //   - connect/latest/adminguide/ts-agentic-self-service.html
    //   - connect/latest/adminguide/ai-agent-security-profile-permissions.html
    //
    // The permission is the MCP tool identifier the gateway exposes:
    // "<targetName>___<toolName>" (here query-transactions___query_transactions).
    // The Namespace is the gateway id (== the MCP-server app namespace Connect
    // discovered). This is scriptable/CFN-able; ASSIGNING this profile to the AI
    // agent is a separate, console-only step (no qconnect API) — see deploy docs.
    const toolMcpId = `${toolTargetName}___query_transactions`;

    // IMPORTANT — this security profile is OPT-IN, and skipped on the first deploy.
    // Its `applications` grant references the gateway's MCP namespace + permission,
    // but Connect only recognises that namespace AFTER the gateway has been
    // registered as an MCP server on the instance (a console step — see
    // README §2 "One-time Connect setup"). Creating it during the initial deploy therefore
    // fails with:
    //   "Application namespace <gatewayId> or Application permission
    //    query-transactions___query_transactions is not valid".
    // So: do the console MCP registration first, THEN create the grant — either by
    // re-running deploy.sh with `-c withSecurityProfile=1`, or via provision-ai-agent.sh
    // (which creates it at the correct point and assigns it to the AI agent).
    if (/^(1|true|yes)$/i.test(ctx("withSecurityProfile", ""))) {
      const aiAgentSecurityProfile = new connect.CfnSecurityProfile(this, "AiAgentToolsSecurityProfile", {
        instanceArn: connectInstanceArn,
        securityProfileName,
        description: "Grants the AnyCompanyPay orchestration AI agent permission to invoke the transaction MCP tool.",
        applications: [
          {
            namespace: gateway.attrGatewayIdentifier,
            applicationPermissions: [toolMcpId],
            type: "MCP",
          },
        ],
      });
      aiAgentSecurityProfile.node.addDependency(gateway);
      new cdk.CfnOutput(this, "AiAgentSecurityProfileArn", {
        value: aiAgentSecurityProfile.attrSecurityProfileArn,
      });
    }

    // ------------------------------------------------------------------
    // Outputs
    // ------------------------------------------------------------------
    new cdk.CfnOutput(this, "TransactionToolFnArn", { value: toolFn.functionArn });
    new cdk.CfnOutput(this, "TransactionToolFnName", { value: toolFn.functionName });
    new cdk.CfnOutput(this, "GatewayInterceptorFnArn", { value: interceptorFn.functionArn });
    new cdk.CfnOutput(this, "GatewayInterceptorFnName", { value: interceptorFn.functionName });
    new cdk.CfnOutput(this, "CollectionArn", { value: collectionArn });
    new cdk.CfnOutput(this, "TrustedArgKey", { value: trustedArgKey });
    new cdk.CfnOutput(this, "GatewayId", { value: gateway.attrGatewayIdentifier });
    new cdk.CfnOutput(this, "GatewayArn", { value: gateway.attrGatewayArn });
    new cdk.CfnOutput(this, "GatewayUrl", { value: gateway.attrGatewayUrl });
    new cdk.CfnOutput(this, "GatewayTargetId", { value: target.attrTargetId });
    new cdk.CfnOutput(this, "AiAgentSecurityProfileName", { value: securityProfileName });
    new cdk.CfnOutput(this, "AiAgentToolMcpId", { value: toolMcpId });
  }
}
