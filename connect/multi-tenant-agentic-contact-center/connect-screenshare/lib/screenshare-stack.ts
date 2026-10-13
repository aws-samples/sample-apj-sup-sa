import * as path from "path";
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as iam from "aws-cdk-lib/aws-iam";
import * as connect from "aws-cdk-lib/aws-connect";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as lambdaNode from "aws-cdk-lib/aws-lambda-nodejs";
import * as apigwv2 from "aws-cdk-lib/aws-apigatewayv2";
import * as apigwInteg from "aws-cdk-lib/aws-apigatewayv2-integrations";
import * as apigwAuth from "aws-cdk-lib/aws-apigatewayv2-authorizers";
import * as cr from "aws-cdk-lib/custom-resources";

/**
 * AnyCompanyPay live screen sharing (OPT-IN).
 *
 * A merchant starts an Amazon Connect in-app/web call (WebRTC contact) from the
 * app and shares their screen; the agent answers in the embedded CCP and views
 * the shared screen. Built on the native Connect capability:
 *   StartWebRTCContact (AllowedCapabilities.Customer.ScreenShare = SEND)
 *   -> Amazon Chime SDK in the browser (audio + content share)
 *   -> agent CCP with allowFramedVideoCall / allowFramedScreenSharing(+PopUp)
 *      and the VideoContact.Access permission.
 *
 * ADDITIVE: a voice queue, a web-call flow, a security profile, the start API
 * (same Cognito JWT model as chat), and a runtime-config entry. Agents receive
 * these calls once their routing profile has the queue on the VOICE channel —
 * the connect stack adds it to anycompany-pay-chat when -c screenShareQueueArn is
 * supplied (infra/deploy-connect-stack.sh does that).
 */
export class ConnectScreenShareStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);
    const envName = ((this.node.tryGetContext("envName") as string) ?? "").trim();
    const sfx = envName ? `-${envName}` : "";
    const ctx = (k: string, d: string) => ((this.node.tryGetContext(k) as string | undefined) ?? d).toString();
    const reqCtx = (k: string, hint: string): string => {
      const v = this.node.tryGetContext(k) as string | undefined;
      if (!v) {
        throw new Error(
          `Missing required context "-c ${k}=...": ${hint} Run this module's deploy.sh ` +
            `(it discovers every value from the upstream stack outputs), or pass -c ${k}=... explicitly.`
        );
      }
      return v;
    };

    // --- Inputs (discovered by deploy.sh from the app + connect stack outputs) ---
    const instanceArn = reqCtx("connectInstanceArn", "Connect instance ARN (AnyCompanyPayConnectStack ConnectInstanceArn).");
    const instanceId = instanceArn.split("/").pop()!;
    const casesDomainId = reqCtx("casesDomainId", "Cases domain id (AnyCompanyPayConnectStack CasesDomainId).");
    const casesDomainArn = `arn:aws:cases:${this.region}:${this.account}:domain/${casesDomainId}`;
    const userPoolId = reqCtx("userPoolId", "Cognito user pool id (AnyCompanyPayAppStack UserPoolId).");
    const userPoolClientId = reqCtx("userPoolClientId", "Cognito app client id (AnyCompanyPayAppStack UserPoolClientId).");
    const distUrl = reqCtx("distUrl", "CloudFront URL (AnyCompanyPayAppStack CloudFrontUrl).");
    const runtimeConfigParam = reqCtx("runtimeConfigParam", "SSM runtime config (AnyCompanyPayAppStack RuntimeConfigParam).");
    const clusterName = reqCtx("clusterName", "ECS cluster (AnyCompanyPayAppStack ClusterName).");
    const serviceName = reqCtx("serviceName", "ECS service (AnyCompanyPayAppStack ServiceName).");

    // ------------------------------------------------------------------
    // Routing: always-open hours -> voice queue -> web-call flow
    // ------------------------------------------------------------------
    const hours = new connect.CfnHoursOfOperation(this, "ScreenShareHours", {
      instanceArn,
      name: `anycompany-pay-screenshare-hours${sfx}`,
      description: "AnyCompanyPay screen-share support (always open; tune to your support hours)",
      timeZone: ctx("timeZone", "UTC"),
      config: ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"].map((day) => ({
        day,
        startTime: { hours: 0, minutes: 0 },
        endTime: { hours: 23, minutes: 59 },
      })),
    });
    const queue = new connect.CfnQueue(this, "ScreenShareQueue", {
      instanceArn,
      name: `anycompany-pay-screenshare${sfx}`,
      description: "Merchant web calls with screen sharing (VOICE channel)",
      hoursOfOperationArn: hours.attrHoursOfOperationArn,
    });

    const [GREET, SETQ, XFER, END] = [
      "5c4e0000-0000-4000-8000-000000000001",
      "5c4e0000-0000-4000-8000-000000000002",
      "5c4e0000-0000-4000-8000-000000000003",
      "5c4e0000-0000-4000-8000-000000000004",
    ];
    const flowContent = {
      Version: "2019-10-30",
      StartAction: GREET,
      Metadata: {
        EntryPointPosition: { x: 20, y: 20 },
        ActionMetadata: {
          [GREET]: { Position: { x: 180, y: 20 } },
          [SETQ]: { Position: { x: 400, y: 20 } },
          [XFER]: { Position: { x: 620, y: 20 } },
          [END]: { Position: { x: 840, y: 20 } },
        },
      },
      Actions: [
        {
          Identifier: GREET,
          Type: "MessageParticipant",
          Parameters: {
            Text: "Thanks for calling AnyCompanyPay. Connecting you to a support agent. You can share your screen once you're connected.",
          },
          Transitions: { NextAction: SETQ, Errors: [{ NextAction: SETQ, ErrorType: "NoMatchingError" }], Conditions: [] },
        },
        {
          Identifier: SETQ,
          Type: "UpdateContactTargetQueue",
          Parameters: { QueueId: queue.attrQueueArn },
          Transitions: { NextAction: XFER, Errors: [{ NextAction: END, ErrorType: "NoMatchingError" }], Conditions: [] },
        },
        {
          Identifier: XFER,
          Type: "TransferContactToQueue",
          Parameters: {},
          Transitions: {
            NextAction: END,
            Errors: [
              { NextAction: END, ErrorType: "QueueAtCapacity" },
              { NextAction: END, ErrorType: "NoMatchingError" },
            ],
            Conditions: [],
          },
        },
        { Identifier: END, Type: "DisconnectParticipant", Parameters: {}, Transitions: {} },
      ],
    };
    const flow = new connect.CfnContactFlow(this, "ScreenShareFlow", {
      instanceArn,
      name: `anycompany-pay-screenshare-inbound${sfx}`,
      description: "Merchant web call with screen sharing -> screen-share queue",
      type: "CONTACT_FLOW",
      content: this.toJsonString(flowContent),
    });

    // Agents need VideoContact.Access to take web calls with video / screen
    // sharing. Granted as an ADDITIONAL profile (provision-video-agents.sh) so the
    // agents keep their existing profiles.
    const videoProfileName = `anycompany-pay-video-agent${sfx}`;
    const videoProfile = new connect.CfnSecurityProfile(this, "VideoAgentProfile", {
      instanceArn,
      securityProfileName: videoProfileName,
      description: "Adds video calls + screen sharing to agents (VideoContact.Access)",
      permissions: ["BasicAgentAccess", "VideoContact.Access"],
    });

    // ------------------------------------------------------------------
    // Start API: HTTP API + Cognito JWT authorizer -> ScreenShareApiFn
    // ------------------------------------------------------------------
    const fn = new lambdaNode.NodejsFunction(this, "ScreenShareApiFn", {
      entry: path.join(__dirname, "..", "lambda", "screenshare-api", "index.ts"),
      runtime: lambda.Runtime.NODEJS_22_X,
      timeout: cdk.Duration.seconds(15),
      memorySize: 256,
      // Bundled SDK: StartWebRTCContact needs a recent client.
      bundling: { minify: true, target: "node22", externalModules: [] },
      description: "Starts a merchant web call with screen sharing (tenant from the JWT)",
      environment: {
        CONNECT_INSTANCE_ID: instanceId,
        CONTACT_FLOW_ARN: flow.attrContactFlowArn,
        CASES_DOMAIN_ID: casesDomainId,
        CUSTOMER_VIDEO: ctx("customerVideo", "false"),
      },
    });
    fn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["connect:StartWebRTCContact", "connect:DescribeContact"],
        resources: [instanceArn, `${instanceArn}/*`],
      })
    );
    fn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["cases:GetCase", "cases:ListFields"],
        resources: [casesDomainArn, `${casesDomainArn}/*`],
      })
    );

    const api = new apigwv2.HttpApi(this, "ScreenShareApi", {
      corsPreflight: {
        allowOrigins: [distUrl],
        allowMethods: [apigwv2.CorsHttpMethod.POST, apigwv2.CorsHttpMethod.GET],
        allowHeaders: ["authorization", "content-type"],
      },
    });
    const integration = new apigwInteg.HttpLambdaIntegration("ScreenShareInteg", fn);
    const authorizer = new apigwAuth.HttpJwtAuthorizer(
      "ScreenShareJwtAuthorizer",
      `https://cognito-idp.${this.region}.amazonaws.com/${userPoolId}`,
      { jwtAudience: [userPoolClientId] }
    );
    api.addRoutes({ path: "/screenshare/start", methods: [apigwv2.HttpMethod.POST], integration, authorizer });
    api.addRoutes({ path: "/screenshare/status/{contactId}", methods: [apigwv2.HttpMethod.GET], integration, authorizer });

    // ------------------------------------------------------------------
    // Publish the API URL into the SPA runtime config + roll the ECS service
    // ------------------------------------------------------------------
    const serviceArn = cdk.Arn.format(
      { service: "ecs", resource: "service", resourceName: `${clusterName}/${serviceName}` },
      this
    );
    const configWriterFn = new lambdaNode.NodejsFunction(this, "ConfigWriterFn", {
      entry: path.join(__dirname, "..", "lambda", "config-writer", "index.ts"),
      runtime: lambda.Runtime.NODEJS_22_X,
      timeout: cdk.Duration.seconds(30),
      bundling: { minify: true, target: "node22", externalModules: ["@aws-sdk/*"] },
    });
    configWriterFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["ssm:GetParameter", "ssm:PutParameter"],
        resources: [`arn:aws:ssm:${this.region}:${this.account}:parameter${runtimeConfigParam}`],
      })
    );
    configWriterFn.addToRolePolicy(new iam.PolicyStatement({ actions: ["ecs:UpdateService"], resources: [serviceArn] }));
    const provider = new cr.Provider(this, "ConfigWriterProvider", { onEventHandler: configWriterFn });
    const apiUrl = api.apiEndpoint;
    new cdk.CustomResource(this, "ScreenShareRuntimeConfig", {
      serviceToken: provider.serviceToken,
      properties: {
        ParameterName: runtimeConfigParam,
        Patch: this.toJsonString({ connect: { screenShareApiUrl: apiUrl } }),
        EcsCluster: clusterName,
        EcsService: serviceName,
        PatchHash: apiUrl,
      },
    });

    const out = (k: string, v: string) => new cdk.CfnOutput(this, k, { value: v });
    out("ScreenShareApiUrl", apiUrl);
    out("ScreenShareQueueArn", queue.attrQueueArn);
    out("ScreenShareFlowArn", flow.attrContactFlowArn);
    out("VideoSecurityProfileName", videoProfileName);
    out("VideoSecurityProfileArn", videoProfile.attrSecurityProfileArn);
    out("ScreenShareApiFnName", fn.functionName);
  }
}
