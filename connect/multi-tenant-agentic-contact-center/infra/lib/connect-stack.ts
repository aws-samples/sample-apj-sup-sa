import * as path from "path";
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as iam from "aws-cdk-lib/aws-iam";
import * as kms from "aws-cdk-lib/aws-kms";
import * as connect from "aws-cdk-lib/aws-connect";
import * as cases from "aws-cdk-lib/aws-cases";
import * as customerprofiles from "aws-cdk-lib/aws-customerprofiles";
import * as secretsmanager from "aws-cdk-lib/aws-secretsmanager";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as lambdaNode from "aws-cdk-lib/aws-lambda-nodejs";
import * as apigwv2 from "aws-cdk-lib/aws-apigatewayv2";
import * as apigwInteg from "aws-cdk-lib/aws-apigatewayv2-integrations";
import * as apigwAuth from "aws-cdk-lib/aws-apigatewayv2-authorizers";
import * as cr from "aws-cdk-lib/custom-resources";

export interface ConnectStackProps extends cdk.StackProps {
  /** OPTIONAL env discriminator. Empty/omitted → clean common names (no suffix). */
  envName?: string;
  userPoolId: string;
  userPoolClientId: string;
  distUrl: string;
  clusterName: string;
  serviceName: string;
  runtimeConfigParamName: string;
  runtimeConfigParamArn: string;
}

/**
 * Module 2 — Amazon Connect integration. Depends on the app stack (imports its
 * Cognito, CloudFront URL, and ECS service). It provisions the Connect instance,
 * Cases (API + domain), and Customer Profiles, AUTOMATING what used to be manual
 * console steps (Customer Profiles domain + KMS, Cases-domain-to-instance
 * association). Finally it merges the Connect values into the shared SSM runtime
 * config and forces the app's ECS service to pick them up.
 */
export class AnyCompanyPayConnectStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props: ConnectStackProps) {
    super(scope, id, props);
    // Optional env discriminator. Empty → no suffix (clean common names).
    const env = (props.envName ?? "").trim();
    const sfx = env ? `-${env}` : ""; // name suffix: "-foo" or ""
    const seg = env ? `/${env}` : ""; // path segment: "/foo" or ""
    const label = env || "default";
    const connectAlias = `anycompany-pay-${this.account}${sfx}`;

    // ------------------------------------------------------------------
    // Amazon Connect instance
    // ------------------------------------------------------------------
    const connectInstance = new connect.CfnInstance(this, "ConnectInstance", {
      identityManagementType: "CONNECT_MANAGED",
      instanceAlias: connectAlias,
      attributes: { inboundCalls: true, outboundCalls: true },
    });
    const ccpUrl = `https://${connectAlias}.my.connect.aws/ccp-v2/`;

    // Register the CloudFront origin as an approved origin so the CCP can frame.
    new cr.AwsCustomResource(this, "ConnectApprovedOrigin", {
      onUpdate: {
        service: "Connect",
        action: "associateApprovedOrigin",
        parameters: { InstanceId: connectInstance.attrId, Origin: props.distUrl },
        physicalResourceId: cr.PhysicalResourceId.of(`connect-approved-origin-${props.distUrl}`),
      },
      onDelete: {
        service: "Connect",
        action: "disassociateApprovedOrigin",
        parameters: { InstanceId: connectInstance.attrId, Origin: props.distUrl },
      },
      policy: cr.AwsCustomResourcePolicy.fromStatements([
        new iam.PolicyStatement({
          actions: [
            "connect:AssociateApprovedOrigin",
            "connect:DisassociateApprovedOrigin",
            "connect:ListApprovedOrigins",
          ],
          // Scoped to this instance only — these actions do support instance ARNs.
          resources: [connectInstance.attrArn],
        }),
      ]),
      installLatestAwsSdk: false,
    });

    // ------------------------------------------------------------------
    // Amazon Connect Cases — domain, fields, template
    // ------------------------------------------------------------------
    const casesDomain = new cases.CfnDomain(this, "CasesDomain", {
      name: `anycompany-pay-cases-${this.account}${sfx}`,
    });
    const mkField = (logicalId: string, name: string) =>
      new cases.CfnField(this, logicalId, {
        domainId: casesDomain.attrDomainId,
        name,
        type: "Text",
      });
    const fSummary = mkField("CaseFieldSummary", "summary");
    const fPriority = mkField("CaseFieldPriority", "priority");
    const fStatus = mkField("CaseFieldStatus", "case_status");
    const fMerchant = mkField("CaseFieldMerchant", "merchant");
    const fMerchantId = mkField("CaseFieldMerchantId", "merchant_id");

    const caseTemplate = new cases.CfnTemplate(this, "CaseTemplate", {
      domainId: casesDomain.attrDomainId,
      name: "AnyCompanyPaySupport",
      status: "Active",
      requiredFields: [{ fieldId: "title" }],
    });
    caseTemplate.addDependency(fSummary);
    caseTemplate.addDependency(fPriority);
    caseTemplate.addDependency(fStatus);
    caseTemplate.addDependency(fMerchant);
    caseTemplate.addDependency(fMerchantId);

    // Automates the manual console step: associate the Cases domain with the
    // Connect instance (so the agent workspace shows the Cases tab).
    new connect.CfnIntegrationAssociation(this, "CasesInstanceAssociation", {
      instanceId: connectInstance.attrArn,
      integrationType: "CASES_DOMAIN",
      integrationArn: casesDomain.attrDomainArn,
    });

    // ------------------------------------------------------------------
    // Amazon Connect Customer Profiles — domain + KMS key.
    // Automates the manual console "Enable Customer Profiles" step.
    // ------------------------------------------------------------------
    const cpKey = new kms.Key(this, "CustomerProfilesKey", {
      description: `AnyCompanyPay Customer Profiles (${label})`,
      enableKeyRotation: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    // Customer Profiles service must be able to use the key.
    cpKey.grantEncryptDecrypt(new iam.ServicePrincipal("profile.amazonaws.com"));

    new customerprofiles.CfnDomain(this, "CustomerProfilesDomain", {
      domainName: `anycompany-pay-customer-profile${sfx}`,
      defaultExpirationDays: 366,
      defaultEncryptionKey: cpKey.keyArn,
    });

    // ------------------------------------------------------------------
    // Chat routing: hours of operation -> queue -> routing profile (Chat).
    // This is the infrastructure an agent needs to RECEIVE merchant chats.
    // The agent user itself is created out-of-band (provision-agent.sh) so no
    // password ends up in the CloudFormation template.
    // ------------------------------------------------------------------
    const hours = new connect.CfnHoursOfOperation(this, "SupportHours", {
      instanceArn: connectInstance.attrArn,
      name: `anycompany-pay-24x7${sfx}`,
      description: "AnyCompanyPay support — always open",
      timeZone: "UTC",
      config: ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"].map(
        (day) => ({
          day,
          startTime: { hours: 0, minutes: 0 },
          endTime: { hours: 23, minutes: 59 },
        })
      ),
    });

    const supportQueue = new connect.CfnQueue(this, "SupportQueue", {
      instanceArn: connectInstance.attrArn,
      name: `anycompany-pay-support${sfx}`,
      description: "AnyCompanyPay merchant support (chat)",
      hoursOfOperationArn: hours.attrHoursOfOperationArn,
    });

    // OPT-IN routing module (connect-routing/): its tier chat queues, as
    // "vip,key,shared" queue ARNs. When supplied, this profile's users (agent1,
    // admin) also serve those queues, so they keep receiving merchant chats once
    // chats are routed by tier. Omitted -> the profile is exactly as before.
    const routingQueueArns = ((this.node.tryGetContext("routingQueueArns") as string | undefined) ?? "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    const routingQueuePriority = [1, 1, 2]; // vip, key-account, shared (design §6.2)
    // OPT-IN screen-share module (connect-screenshare/): its web-call queue. When
    // supplied, this profile also takes VOICE so its users answer merchant web
    // calls with screen sharing. Omitted -> unchanged.
    const screenShareQueueArn = (this.node.tryGetContext("screenShareQueueArn") as string | undefined) || "";

    const chatRoutingProfile = new connect.CfnRoutingProfile(this, "ChatRoutingProfile", {
      instanceArn: connectInstance.attrArn,
      name: `anycompany-pay-chat${sfx}`,
      description: "Handles AnyCompanyPay merchant chats",
      defaultOutboundQueueArn: supportQueue.attrQueueArn,
      mediaConcurrencies: [
        { channel: "CHAT", concurrency: 5 },
        ...(screenShareQueueArn ? [{ channel: "VOICE", concurrency: 1 }] : []),
      ],
      queueConfigs: [
        {
          delay: 0,
          priority: 1,
          queueReference: { channel: "CHAT", queueArn: supportQueue.attrQueueArn },
        },
        ...(screenShareQueueArn
          ? [{ delay: 0, priority: 1, queueReference: { channel: "VOICE", queueArn: screenShareQueueArn } }]
          : []),
        ...routingQueueArns.map((queueArn, i) => ({
          delay: 0,
          priority: routingQueuePriority[i] ?? 2,
          queueReference: { channel: "CHAT", queueArn },
        })),
      ],
    });

    // ------------------------------------------------------------------
    // Amazon Connect users (admin + agent) — created HERE during the stack
    // deploy with a Secrets Manager GENERATED password. The plaintext password
    // never lands in the CloudFormation template, the CDK synth output, or any
    // script: the ConnectUserFn custom resource reads it from Secrets Manager at
    // deploy time and calls connect:CreateUser. This supersedes the manual
    // provision-agent.sh (kept only as a fallback).
    // ------------------------------------------------------------------
    const connectUserFn = new lambdaNode.NodejsFunction(this, "ConnectUserFn", {
      entry: path.join(__dirname, "..", "lambda", "connect-user", "index.ts"),
      runtime: lambda.Runtime.NODEJS_22_X,
      timeout: cdk.Duration.seconds(60),
      memorySize: 256,
      // The Node 22 Lambda runtime ships the AWS SDK v3 (incl. client-connect +
      // client-secrets-manager), so keep them external rather than bundling —
      // same pattern as ConfigWriterFn below. Avoids adding them to package.json.
      bundling: { minify: true, target: "node22", externalModules: ["@aws-sdk/*"] },
    });
    connectUserFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          "connect:CreateUser",
          "connect:DeleteUser",
          "connect:ListUsers",
          "connect:DescribeUser",
          "connect:ListSecurityProfiles",
          "connect:ListRoutingProfiles",
          "connect:UpdateUserRoutingProfile",
          "connect:UpdateUserSecurityProfiles",
        ],
        resources: [connectInstance.attrArn, `${connectInstance.attrArn}/*`],
      })
    );
    const connectUserProvider = new cr.Provider(this, "ConnectUserProvider", {
      onEventHandler: connectUserFn,
    });

    const makeConnectUser = (
      logicalId: string,
      opts: {
        username: string;
        securityProfileName: string;
        routingProfileName: string;
        firstName: string;
        lastName: string;
        email?: string;
      }
    ): secretsmanager.Secret => {
      const secret = new secretsmanager.Secret(this, `${logicalId}Secret`, {
        secretName: `anycompany-pay${seg}/connect/${opts.username}`,
        description: `AnyCompanyPay Connect user "${opts.username}" (${label}) — generated password`,
        generateSecretString: {
          secretStringTemplate: JSON.stringify({ username: opts.username }),
          generateStringKey: "password",
          passwordLength: 20,
          // upper+lower+digits only: satisfies Connect's password policy and
          // avoids any special character Connect might reject.
          // requireEachIncludedType guarantees >=1 uppercase, >=1 lowercase, >=1 digit.
          excludePunctuation: true,
          requireEachIncludedType: true,
        },
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      });
      secret.grantRead(connectUserFn);

      const user = new cdk.CustomResource(this, logicalId, {
        serviceToken: connectUserProvider.serviceToken,
        properties: {
          InstanceId: connectInstance.attrId,
          Username: opts.username,
          SecretArn: secret.secretArn,
          SecurityProfileName: opts.securityProfileName,
          RoutingProfileName: opts.routingProfileName,
          FirstName: opts.firstName,
          LastName: opts.lastName,
          Email: opts.email ?? "",
          // Re-run (reconcile profiles) if any of these change.
          ConfigHash: `${opts.securityProfileName}|${opts.routingProfileName}|${opts.email ?? ""}`,
        },
      });
      user.node.addDependency(connectInstance);
      user.node.addDependency(chatRoutingProfile);
      return secret;
    };

    // Admin — full instance admin (flow builder, queues, users). CreateUser
    // requires a routing profile even for admins, so reuse the chat routing
    // profile (admins don't take contacts).
    const adminSecret = makeConnectUser("ConnectAdminUser", {
      username: `anycompany-pay-admin${sfx}`,
      securityProfileName: "Admin",
      routingProfileName: `anycompany-pay-chat${sfx}`,
      firstName: "AnyCompanyPay",
      lastName: "Admin",
    });
    // Agent — answers merchant chats via the embedded CCP.
    const agentSecret = makeConnectUser("ConnectAgentUser", {
      username: "agent1",
      securityProfileName: "Agent",
      routingProfileName: `anycompany-pay-chat${sfx}`,
      firstName: "AnyCompanyPay",
      lastName: "Agent",
      email: "agent1@anycompany-pay.example",
    });

    // ------------------------------------------------------------------
    // Inbound chat flow (Amazon Connect Flow language). Greets the merchant,
    // sets the support queue as the target, and transfers the contact to it.
    // The queue ARN is injected via this.toJsonString so the CFN token resolves
    // inside the flow content string.
    // ------------------------------------------------------------------
    const A_GREET = "11111111-1111-1111-1111-111111111111";
    const A_SETQ = "22222222-2222-2222-2222-222222222222";
    const A_XFER = "33333333-3333-3333-3333-333333333333";
    const A_END = "44444444-4444-4444-4444-444444444444";
    const chatFlowContent = {
      Version: "2019-10-30",
      StartAction: A_GREET,
      Metadata: {
        EntryPointPosition: { x: 20, y: 20 },
        ActionMetadata: {
          [A_GREET]: { Position: { x: 180, y: 20 } },
          [A_SETQ]: { Position: { x: 380, y: 20 } },
          [A_XFER]: { Position: { x: 580, y: 20 } },
          [A_END]: { Position: { x: 780, y: 20 } },
        },
      },
      Actions: [
        {
          Identifier: A_GREET,
          Type: "MessageParticipant",
          Parameters: {
            Text: "Thanks for contacting AnyCompanyPay support. Connecting you to the next available agent…",
          },
          Transitions: {
            NextAction: A_SETQ,
            Errors: [{ NextAction: A_SETQ, ErrorType: "NoMatchingError" }],
            Conditions: [],
          },
        },
        {
          Identifier: A_SETQ,
          Type: "UpdateContactTargetQueue",
          Parameters: { QueueId: supportQueue.attrQueueArn },
          Transitions: {
            NextAction: A_XFER,
            Errors: [{ NextAction: A_END, ErrorType: "NoMatchingError" }],
            Conditions: [],
          },
        },
        {
          Identifier: A_XFER,
          Type: "TransferContactToQueue",
          Parameters: {},
          // On SUCCESS the contact is placed in the queue and leaves this flow
          // (the queue then routes it to an agent); NextAction/Errors only apply
          // if the contact could not be queued.
          Transitions: {
            NextAction: A_END,
            Errors: [
              { NextAction: A_END, ErrorType: "QueueAtCapacity" },
              { NextAction: A_END, ErrorType: "NoMatchingError" },
            ],
            Conditions: [],
          },
        },
        { Identifier: A_END, Type: "DisconnectParticipant", Parameters: {}, Transitions: {} },
      ],
    };

    // Agentic self-service variant of the inbound flow. Enabled when BOTH the Lex
    // bot alias and the Q in Connect assistant ARNs are supplied as context (they
    // are created AFTER this stack — the Lex bot in AnyCompanyPayLexStack, the QIC domain
    // in the console — so this stack is first deployed with the simple flow above,
    // then redeployed with -c agenticBotAliasArn=... -c qicAssistantArn=... to
    // switch the same flow to the agentic path). Structure mirrors the proven
    // flow: set the Q in Connect session -> stamp the session-arn -> hand the chat
    // to the Lex (Q in Connect) bot, which drives the multi-turn conversation and
    // returns control only via a Tool (Escalate -> human queue, Complete -> end).
    const agenticBotAliasArn = this.node.tryGetContext("agenticBotAliasArn") as string | undefined;
    const qicAssistantArn = this.node.tryGetContext("qicAssistantArn") as string | undefined;
    const agentic = Boolean(agenticBotAliasArn && qicAssistantArn);

    const G_LOG = "a1000000-0000-0000-0000-000000000001";
    const G_WIS = "a1000000-0000-0000-0000-000000000002";
    const G_ATT = "a1000000-0000-0000-0000-000000000003";
    const G_LEX = "a1000000-0000-0000-0000-000000000004";
    const G_CMP = "a1000000-0000-0000-0000-000000000005";
    const agenticFlowContent = {
      Version: "2019-10-30",
      StartAction: G_LOG,
      Metadata: {
        EntryPointPosition: { x: 20, y: 20 },
        ActionMetadata: {
          [G_LOG]: { Position: { x: 160, y: 20 } },
          [G_WIS]: { Position: { x: 320, y: 20 } },
          [G_ATT]: { Position: { x: 480, y: 20 } },
          [G_LEX]: { Position: { x: 640, y: 20 } },
          [G_CMP]: { Position: { x: 800, y: 20 } },
          [A_SETQ]: { Position: { x: 960, y: 160 } },
          [A_XFER]: { Position: { x: 1120, y: 160 } },
          [A_END]: { Position: { x: 1120, y: 20 } },
        },
      },
      Actions: [
        {
          Identifier: G_LOG,
          Type: "UpdateFlowLoggingBehavior",
          Parameters: { FlowLoggingBehavior: "Enabled" },
          Transitions: { NextAction: G_WIS },
        },
        {
          Identifier: G_WIS,
          Type: "CreateWisdomSession",
          Parameters: { WisdomAssistantArn: qicAssistantArn ?? "" },
          Transitions: {
            NextAction: G_ATT,
            Errors: [{ NextAction: G_ATT, ErrorType: "NoMatchingError" }],
          },
        },
        {
          Identifier: G_ATT,
          Type: "UpdateContactAttributes",
          Parameters: { Attributes: { "x-amz-lex:q-in-connect:session-arn": "$.Wisdom.SessionArn" } },
          Transitions: {
            NextAction: G_LEX,
            Errors: [{ NextAction: G_LEX, ErrorType: "NoMatchingError" }],
          },
        },
        {
          Identifier: G_LEX,
          Type: "ConnectParticipantWithLexBot",
          Parameters: {
            Text: "Hi, I'm the AnyCompanyPay assistant. How can I help with your account or transactions today?",
            LexV2Bot: { AliasArn: agenticBotAliasArn },
            LexSessionAttributes: { "x-amz-lex:q-in-connect:session-arn": "$.Wisdom.SessionArn" },
          },
          Transitions: {
            NextAction: G_CMP,
            Errors: [
              { NextAction: A_END, ErrorType: "NoMatchingError" },
              { NextAction: G_CMP, ErrorType: "NoMatchingCondition" },
            ],
            Conditions: [],
          },
        },
        {
          Identifier: G_CMP,
          Type: "Compare",
          Parameters: { ComparisonValue: "$.Lex.SessionAttributes.Tool" },
          Transitions: {
            NextAction: A_END,
            Errors: [{ NextAction: A_END, ErrorType: "NoMatchingCondition" }],
            Conditions: [
              { NextAction: A_SETQ, Condition: { Operator: "Equals", Operands: ["Escalate"] } },
              { NextAction: A_END, Condition: { Operator: "Equals", Operands: ["Complete"] } },
            ],
          },
        },
        {
          Identifier: A_SETQ,
          Type: "UpdateContactTargetQueue",
          Parameters: { QueueId: supportQueue.attrQueueArn },
          Transitions: {
            NextAction: A_XFER,
            Errors: [{ NextAction: A_END, ErrorType: "NoMatchingError" }],
          },
        },
        {
          Identifier: A_XFER,
          Type: "TransferContactToQueue",
          Parameters: {},
          Transitions: {
            NextAction: A_END,
            Errors: [
              { NextAction: A_END, ErrorType: "QueueAtCapacity" },
              { NextAction: A_END, ErrorType: "NoMatchingError" },
            ],
          },
        },
        { Identifier: A_END, Type: "DisconnectParticipant", Parameters: {}, Transitions: {} },
      ],
    };

    const chatFlow = new connect.CfnContactFlow(this, "ChatInboundFlow", {
      instanceArn: connectInstance.attrArn,
      name: `anycompany-pay-chat-inbound${sfx}`,
      description: agentic
        ? "Inbound merchant chat - agentic self-service (Q in Connect) then escalate to support queue"
        : "Inbound merchant chat - route to support queue",
      type: "CONTACT_FLOW",
      content: this.toJsonString(agentic ? agenticFlowContent : chatFlowContent),
    });

    // Separate flow for chats started FROM an existing support case. Same
    // routing (support queue) but a distinct greeting so the merchant knows the
    // chat is linked to their case. The case_id travels as a contact attribute
    // (stamped server-side by the chat Lambda after verifying tenant ownership).
    const C_GREET = "55555555-5555-5555-5555-555555555555";
    const C_SETQ = "66666666-6666-6666-6666-666666666666";
    const C_XFER = "77777777-7777-7777-7777-777777777777";
    const C_END = "88888888-8888-8888-8888-888888888888";
    const caseFlowContent = {
      Version: "2019-10-30",
      StartAction: C_GREET,
      Metadata: {
        EntryPointPosition: { x: 20, y: 20 },
        ActionMetadata: {
          [C_GREET]: { Position: { x: 180, y: 20 } },
          [C_SETQ]: { Position: { x: 380, y: 20 } },
          [C_XFER]: { Position: { x: 580, y: 20 } },
          [C_END]: { Position: { x: 780, y: 20 } },
        },
      },
      Actions: [
        {
          Identifier: C_GREET,
          Type: "MessageParticipant",
          Parameters: {
            Text: "Thanks — we've linked this chat to your support case. Connecting you to the next available agent…",
          },
          Transitions: {
            NextAction: C_SETQ,
            Errors: [{ NextAction: C_SETQ, ErrorType: "NoMatchingError" }],
            Conditions: [],
          },
        },
        {
          Identifier: C_SETQ,
          Type: "UpdateContactTargetQueue",
          Parameters: { QueueId: supportQueue.attrQueueArn },
          Transitions: {
            NextAction: C_XFER,
            Errors: [{ NextAction: C_END, ErrorType: "NoMatchingError" }],
            Conditions: [],
          },
        },
        {
          Identifier: C_XFER,
          Type: "TransferContactToQueue",
          Parameters: {},
          Transitions: {
            NextAction: C_END,
            Errors: [
              { NextAction: C_END, ErrorType: "QueueAtCapacity" },
              { NextAction: C_END, ErrorType: "NoMatchingError" },
            ],
            Conditions: [],
          },
        },
        { Identifier: C_END, Type: "DisconnectParticipant", Parameters: {}, Transitions: {} },
      ],
    };
    const chatCaseFlow = new connect.CfnContactFlow(this, "ChatCaseFlow", {
      instanceArn: connectInstance.attrArn,
      name: `anycompany-pay-chat-case${sfx}`,
      description: "Merchant chat started from a support case — route to support queue",
      type: "CONTACT_FLOW",
      content: this.toJsonString(caseFlowContent),
    });

    // OPT-IN routing module (connect-routing/): when its routed chat flow ARN is
    // supplied, merchant chats (standalone and case-initiated) start in that flow
    // instead — case-owner routing + after-hours backlog. Omitted -> the flows
    // above are used exactly as before.
    const routingFlowArn = this.node.tryGetContext("routingFlowArn") as string | undefined;

    // ------------------------------------------------------------------
    // Cases API: Lambda behind an API Gateway HTTP API + Cognito JWT authorizer.
    // ------------------------------------------------------------------
    const casesApiFn = new lambdaNode.NodejsFunction(this, "CasesApiFn", {
      entry: path.join(__dirname, "..", "lambda", "cases-api", "index.ts"),
      runtime: lambda.Runtime.NODEJS_22_X,
      timeout: cdk.Duration.seconds(20),
      memorySize: 256,
      bundling: { minify: true, target: "node22", externalModules: [] },
      environment: {
        CASES_DOMAIN_ID: casesDomain.attrDomainId,
        CASES_TEMPLATE_ID: caseTemplate.attrTemplateId,
        FIELD_SUMMARY: fSummary.attrFieldId,
        FIELD_PRIORITY: fPriority.attrFieldId,
        FIELD_STATUS: fStatus.attrFieldId,
        FIELD_MERCHANT: fMerchant.attrFieldId,
        FIELD_MERCHANT_ID: fMerchantId.attrFieldId,
      },
    });
    casesApiFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          "cases:SearchCases",
          "cases:CreateCase",
          "cases:GetCase",
          "cases:UpdateCase",
          "cases:GetTemplate",
          "cases:CreateRelatedItem",
          "cases:SearchRelatedItems",
        ],
        resources: [casesDomain.attrDomainArn, `${casesDomain.attrDomainArn}/*`],
      })
    );

    const casesJwtAuthorizer = new apigwAuth.HttpJwtAuthorizer(
      "CasesJwtAuthorizer",
      `https://cognito-idp.${this.region}.amazonaws.com/${props.userPoolId}`,
      { jwtAudience: [props.userPoolClientId] }
    );
    const casesApi = new apigwv2.HttpApi(this, "CasesApi", {
      corsPreflight: {
        allowOrigins: [props.distUrl],
        allowMethods: [
          apigwv2.CorsHttpMethod.GET,
          apigwv2.CorsHttpMethod.POST,
          apigwv2.CorsHttpMethod.PATCH,
        ],
        allowHeaders: ["authorization", "content-type"],
      },
    });
    const casesIntegration = new apigwInteg.HttpLambdaIntegration("CasesInteg", casesApiFn);
    casesApi.addRoutes({
      path: "/cases",
      methods: [apigwv2.HttpMethod.GET, apigwv2.HttpMethod.POST],
      integration: casesIntegration,
      authorizer: casesJwtAuthorizer,
    });
    casesApi.addRoutes({
      path: "/cases/{id}",
      methods: [apigwv2.HttpMethod.GET, apigwv2.HttpMethod.PATCH],
      integration: casesIntegration,
      authorizer: casesJwtAuthorizer,
    });
    casesApi.addRoutes({
      path: "/cases/{id}/comments",
      methods: [apigwv2.HttpMethod.POST],
      integration: casesIntegration,
      authorizer: casesJwtAuthorizer,
    });
    const casesApiUrl = casesApi.apiEndpoint;

    // ------------------------------------------------------------------
    // Chat API: StartChatContact Lambda on the SAME HTTP API + JWT authorizer.
    // It stamps merchant identity from the validated JWT as contact attributes
    // (ignoring client input) and returns a per-contact ParticipantToken.
    // ------------------------------------------------------------------
    const chatApiFn = new lambdaNode.NodejsFunction(this, "ChatApiFn", {
      entry: path.join(__dirname, "..", "lambda", "chat-api", "index.ts"),
      runtime: lambda.Runtime.NODEJS_22_X,
      timeout: cdk.Duration.seconds(20),
      memorySize: 256,
      bundling: { minify: true, target: "node22", externalModules: [] },
      environment: {
        CONNECT_INSTANCE_ID: connectInstance.attrId,
        CONTACT_FLOW_ARN: routingFlowArn || chatFlow.attrContactFlowArn,
        CASE_CONTACT_FLOW_ARN: routingFlowArn || chatCaseFlow.attrContactFlowArn,
        // For validating that a case-initiated chat's caseId belongs to the caller.
        CASES_DOMAIN_ID: casesDomain.attrDomainId,
        FIELD_MERCHANT_ID: fMerchantId.attrFieldId,
      },
    });
    chatApiFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["connect:StartChatContact"],
        // Scope to this instance and its contacts/flows.
        resources: [connectInstance.attrArn, `${connectInstance.attrArn}/*`],
      })
    );
    // Read-only case lookup to verify tenant ownership of a case-initiated chat.
    chatApiFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["cases:GetCase"],
        resources: [casesDomain.attrDomainArn, `${casesDomain.attrDomainArn}/*`],
      })
    );
    const chatIntegration = new apigwInteg.HttpLambdaIntegration("ChatInteg", chatApiFn);
    casesApi.addRoutes({
      path: "/chat/start",
      methods: [apigwv2.HttpMethod.POST],
      integration: chatIntegration,
      authorizer: casesJwtAuthorizer,
    });
    // Same HTTP API base; the client calls `${chatApiUrl}/chat/start`.
    const chatApiUrl = casesApi.apiEndpoint;

    // ------------------------------------------------------------------
    // Merge the Connect values into the shared SSM runtime config, then force
    // the app's ECS service to redeploy so tasks pick up the new config.
    // ------------------------------------------------------------------
    const serviceArn = cdk.Arn.format(
      { service: "ecs", resource: "service", resourceName: `${props.clusterName}/${props.serviceName}` },
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
        resources: [props.runtimeConfigParamArn],
      })
    );
    configWriterFn.addToRolePolicy(
      new iam.PolicyStatement({ actions: ["ecs:UpdateService"], resources: [serviceArn] })
    );
    const configProvider = new cr.Provider(this, "ConfigWriterProvider", {
      onEventHandler: configWriterFn,
    });
    const connectPatch = { connect: { ccpUrl, region: this.region, casesApiUrl, chatApiUrl } };
    new cdk.CustomResource(this, "ConnectRuntimeConfig", {
      serviceToken: configProvider.serviceToken,
      properties: {
        ParameterName: props.runtimeConfigParamName,
        Patch: JSON.stringify(connectPatch),
        EcsCluster: props.clusterName,
        EcsService: props.serviceName,
        // re-run on any change to the connect values
        PatchHash: `${ccpUrl}|${casesApiUrl}|${chatApiUrl}`,
      },
    });

    // ------------------------------------------------------------------
    // Outputs
    // ------------------------------------------------------------------
    new cdk.CfnOutput(this, "ConnectInstanceId", { value: connectInstance.attrId });
    new cdk.CfnOutput(this, "ConnectInstanceArn", { value: connectInstance.attrArn });
    new cdk.CfnOutput(this, "CcpUrl", { value: ccpUrl });
    new cdk.CfnOutput(this, "CasesApiUrl", { value: casesApiUrl });
    new cdk.CfnOutput(this, "ChatApiUrl", { value: chatApiUrl });
    new cdk.CfnOutput(this, "CasesDomainId", { value: casesDomain.attrDomainId });
    new cdk.CfnOutput(this, "CustomerProfilesDomainName", {
      value: `anycompany-pay-customer-profile${sfx}`,
    });
    new cdk.CfnOutput(this, "ConnectAdminSecretName", { value: adminSecret.secretName });
    new cdk.CfnOutput(this, "ConnectAgentSecretName", { value: agentSecret.secretName });
    new cdk.CfnOutput(this, "ChatContactFlowArn", { value: chatFlow.attrContactFlowArn });
    new cdk.CfnOutput(this, "ChatCaseFlowArn", { value: chatCaseFlow.attrContactFlowArn });
    new cdk.CfnOutput(this, "SupportQueueArn", { value: supportQueue.attrQueueArn });
    new cdk.CfnOutput(this, "ChatRoutingProfileArn", {
      value: chatRoutingProfile.attrRoutingProfileArn,
    });
  }
}
