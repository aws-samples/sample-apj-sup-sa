import * as path from "path";
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as apigwv2 from "aws-cdk-lib/aws-apigatewayv2";
import { WebSocketLambdaAuthorizer } from "aws-cdk-lib/aws-apigatewayv2-authorizers";
import { WebSocketLambdaIntegration } from "aws-cdk-lib/aws-apigatewayv2-integrations";
import * as connect from "aws-cdk-lib/aws-connect";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as iam from "aws-cdk-lib/aws-iam";
import * as kms from "aws-cdk-lib/aws-kms";
import * as lex from "aws-cdk-lib/aws-lex";
import * as cr from "aws-cdk-lib/custom-resources";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as lambdaNode from "aws-cdk-lib/aws-lambda-nodejs";
import * as logs from "aws-cdk-lib/aws-logs";
import * as secrets from "aws-cdk-lib/aws-secretsmanager";
import { a2aChatFlow } from "./flows";

/**
 * Opt-in agent-to-agent (A2A) collaboration demo: Amazon Connect hands a chat to an
 * EXTERNAL AI agent ("Level3", mocked here) over the Connect A2A extension.
 *
 * Customer side (what a Connect customer creates for any partner agent):
 *   - customer managed KMS key that connect.amazonaws.com can decrypt
 *   - Secrets Manager secret holding the partner API key (encrypted with that key,
 *     resource policy for connect.amazonaws.com)
 *   - [provision-a2a.sh] AppIntegrations A2A_SERVER application, instance
 *     association, security-profile allow-list, ORCHESTRATION AI agent with an
 *     immediate handoffAgentConfiguration (API-only; no CloudFormation support yet)
 *   - a dedicated Lex V2 bot (only AMAZON.QInConnectIntent) associated with the
 *     instance; the self-service bot is not touched
 *   - [2nd deploy, -c a2aAgentArn] a chat flow that opens the AI session and routes
 *     ESCALATE (or any failure) to the human support queue
 *
 * Partner side (what Level3 would host; mocked so the demo is self-contained):
 *   - API Gateway WebSocket API (public wss://), Bearer-key authorizer on $connect
 *   - Lambda implementing the A2A text session (INIT_SESSION, turns, TRACING_SPAN,
 *     FINISH), DynamoDB for per-session state across chat reconnections
 */
export class ConnectA2aStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);
    const ctx = (k: string, d = "") => ((this.node.tryGetContext(k) as string) ?? d).trim();
    const envName = ctx("envName");
    const sfx = envName ? `-${envName}` : "";
    const instanceArn = ctx("connectInstanceArn");
    if (!instanceArn) throw new Error("Missing -c connectInstanceArn (deploy.sh discovers it)");

    // ---- customer side: API key storage Connect can read ------------------------
    const key = new kms.Key(this, "A2aKey", {
      description: "AnyCompanyPay A2A partner API key (readable by Amazon Connect)",
      enableKeyRotation: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      pendingWindow: cdk.Duration.days(7),
    });
    // The AWS managed aws/secretsmanager key does not work: Connect needs a key
    // policy grant (admin guide, external AI agent setup, step 1).
    key.addToResourcePolicy(
      new iam.PolicyStatement({
        sid: "AllowConnectDecrypt",
        principals: [new iam.ServicePrincipal("connect.amazonaws.com")],
        actions: ["kms:Decrypt", "kms:DescribeKey"],
        resources: ["*"],
      })
    );
    const apiKey = new secrets.Secret(this, "A2aApiKey", {
      secretName: `anycompany-pay/a2a/level3-api-key${sfx}`,
      description: "API key Connect presents (Bearer) to the Level3 A2A endpoint",
      encryptionKey: key,
      generateSecretString: { passwordLength: 48, excludePunctuation: true },
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    apiKey.addToResourcePolicy(
      new iam.PolicyStatement({
        principals: [new iam.ServicePrincipal("connect.amazonaws.com")],
        actions: ["secretsmanager:GetSecretValue", "secretsmanager:DescribeSecret"],
        resources: ["*"],
      })
    );

    // ---- partner side: mock Level3 A2A server ----------------------------------
    const table = new dynamodb.Table(this, "A2aSessions", {
      partitionKey: { name: "pk", type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      timeToLiveAttribute: "ttl",
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    const fn = (name: string, dir: string, env: Record<string, string>) =>
      new lambdaNode.NodejsFunction(this, name, {
        entry: path.join(__dirname, "..", "lambda", dir, "index.ts"),
        runtime: lambda.Runtime.NODEJS_22_X,
        architecture: lambda.Architecture.ARM_64,
        timeout: cdk.Duration.seconds(15),
        memorySize: 256,
        environment: env,
        logRetention: logs.RetentionDays.ONE_WEEK,
        bundling: { minify: true, target: "node22", externalModules: [] as string[] },
      });
    const authFn = fn("A2aAuthorizerFn", "ws-authorizer", { API_KEY_SECRET_ARN: apiKey.secretArn });
    apiKey.grantRead(authFn);
    const connectFn = fn("A2aConnectFn", "ws-connect", { TABLE_NAME: table.tableName });
    const agentFn = fn("Level3MockFn", "level3-mock", {
      TABLE_NAME: table.tableName,
      AGENT_NAME: "level3-mock",
      LOG_FRAMES: ctx("logFrames", "true"),
    });
    table.grantReadWriteData(connectFn);
    table.grantReadWriteData(agentFn);
    // Partner writes its result to the contact (in production: a cross-account
    // role limited to these two actions on the customer's instance).
    agentFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["connect:UpdateContactAttributes", "connect:GetContactAttributes"],
        resources: [`${instanceArn}/contact/*`],
      })
    );

    const api = new apigwv2.WebSocketApi(this, "Level3A2aApi", {
      apiName: `anycompany-pay-level3-a2a${sfx}`,
      description: "Mock Level3 external AI agent (Amazon Connect A2A extension, chat)",
      connectRouteOptions: {
        integration: new WebSocketLambdaIntegration("ConnectInt", connectFn),
        authorizer: new WebSocketLambdaAuthorizer("BearerAuth", authFn, {
          identitySource: ["route.request.header.Authorization"],
        }),
      },
      defaultRouteOptions: { integration: new WebSocketLambdaIntegration("DefaultInt", agentFn) },
    });
    const stage = new apigwv2.WebSocketStage(this, "A2aStage", { webSocketApi: api, stageName: "a2a", autoDeploy: true });
    api.grantManageConnections(agentFn);
    const wssUrl = stage.url; // wss://<id>.execute-api.<region>.amazonaws.com/a2a

    // ---- customer side: a dedicated Lex bot (only AMAZON.QInConnectIntent) ------
    // Separate from the self-service bot so this demo never changes it; conversation
    // logs are on so Lex-side errors are visible.
    const assistantArn = ctx("qicAssistantArn");
    if (!assistantArn) throw new Error("Missing -c qicAssistantArn (deploy.sh discovers it)");
    const assistantId = (assistantArn.split(":assistant/")[1] ?? "").split("/")[0];
    const botName = `anycompany-pay-a2a${sfx}`;
    const botRole = new iam.Role(this, "A2aBotRole", {
      assumedBy: new iam.ServicePrincipal("lexv2.amazonaws.com"),
      description: "AnyCompanyPay A2A demo bot - Q in Connect",
    });
    botRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ["wisdom:CreateSession", "wisdom:GetAssistant"],
        resources: [assistantArn, `${assistantArn}/*`],
      })
    );
    botRole.addToPolicy(
      new iam.PolicyStatement({
        actions: ["wisdom:SendMessage", "wisdom:GetNextMessage"],
        resources: [`${assistantArn.split(":assistant/")[0]}:session/${assistantId}/*`],
      })
    );
    const botLogs = new logs.LogGroup(this, "A2aBotLogs", {
      logGroupName: `/aws/lex/${botName}`,
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });
    botLogs.grantWrite(botRole);
    const bot = new lex.CfnBot(this, "A2aBot", {
      name: botName,
      roleArn: botRole.roleArn,
      dataPrivacy: { ChildDirected: false },
      idleSessionTtlInSeconds: 300,
      description: "AnyCompanyPay A2A demo - Q in Connect (hands off to the external agent)",
      autoBuildBotLocales: true,
      botLocales: [
        {
          localeId: "en_US",
          nluConfidenceThreshold: 0.4,
          intents: [
            {
              name: "QInConnectIntent",
              parentIntentSignature: "AMAZON.QInConnectIntent",
              qInConnectIntentConfiguration: { qInConnectAssistantConfiguration: { assistantArn } },
            },
            { name: "FallbackIntent", parentIntentSignature: "AMAZON.FallbackIntent" },
          ],
        },
      ],
    });
    const botVersion = new lex.CfnBotVersion(this, "A2aBotVersion", {
      botId: bot.attrId,
      botVersionLocaleSpecification: [{ localeId: "en_US", botVersionLocaleDetails: { sourceBotVersion: "DRAFT" } }],
    });
    botVersion.addDependency(bot);
    const botAlias = new lex.CfnBotAlias(this, "A2aBotAlias", {
      botId: bot.attrId,
      botAliasName: `${botName}-live`,
      botVersion: botVersion.attrBotVersion,
      botAliasLocaleSettings: [{ localeId: "en_US", botAliasLocaleSetting: { enabled: true } }],
      conversationLogSettings: {
        textLogSettings: [
          { enabled: true, destination: { cloudWatch: { cloudWatchLogGroupArn: botLogs.logGroupArn, logPrefix: "a2a" } } },
        ],
      },
    });
    botAlias.addDependency(botVersion);
    // Attach the bot to the instance (also writes the Connect invoke policy on the alias).
    const lexV2Bot = { AliasArn: botAlias.attrArn };
    const instanceId = instanceArn.split("/").pop()!;
    const botAssoc = new cr.AwsCustomResource(this, "A2aBotAssociation", {
      onCreate: {
        service: "Connect",
        action: "associateBot",
        parameters: { InstanceId: instanceId, LexV2Bot: lexV2Bot },
        physicalResourceId: cr.PhysicalResourceId.of(`a2a-bot-${botName}`),
        ignoreErrorCodesMatching: "ResourceConflictException|DuplicateResourceException",
      },
      onDelete: {
        service: "Connect",
        action: "disassociateBot",
        parameters: { InstanceId: instanceId, LexV2Bot: lexV2Bot },
        ignoreErrorCodesMatching: "ResourceNotFoundException|InvalidRequestException",
      },
      policy: cr.AwsCustomResourcePolicy.fromStatements([
        new iam.PolicyStatement({
          actions: ["connect:AssociateBot", "connect:DisassociateBot"],
          resources: [instanceArn, `${instanceArn}/*`],
        }),
        new iam.PolicyStatement({
          actions: ["lex:DescribeBotAlias", "lex:CreateResourcePolicy", "lex:UpdateResourcePolicy",
            "lex:DeleteResourcePolicy", "lex:DescribeResourcePolicy"],
          resources: [botAlias.attrArn],
        }),
      ]),
      installLatestAwsSdk: false,
    });
    botAssoc.node.addDependency(botAlias);

    // ---- customer side: chat flow (after provision-a2a.sh created the AI agent) ----
    const a2aAgentArn = ctx("a2aAgentArn");
    if (a2aAgentArn) {
      const flow = new connect.CfnContactFlow(this, "A2aChatFlow", {
        instanceArn,
        name: `anycompany-pay-chat-a2a${sfx}`,
        type: "CONTACT_FLOW",
        description: "Chat handed to the external Level3 agent (A2A); ESCALATE or any failure -> human queue",
        content: JSON.stringify(
          a2aChatFlow({
            assistantArn,
            botAliasArn: botAlias.attrArn,
            aiAgentArn: a2aAgentArn,
            queueArn: ctx("supportQueueArn"),
          })
        ),
      });
      flow.node.addDependency(botAssoc);
      new cdk.CfnOutput(this, "A2aChatFlowArn", { value: flow.attrContactFlowArn });
      new cdk.CfnOutput(this, "A2aAgentArn", { value: a2aAgentArn });
    }

    new cdk.CfnOutput(this, "A2aEndpointUrl", { value: wssUrl });
    new cdk.CfnOutput(this, "A2aApiKeySecretArn", { value: apiKey.secretArn });
    new cdk.CfnOutput(this, "A2aKmsKeyArn", { value: key.keyArn });
    new cdk.CfnOutput(this, "A2aBotAliasArn", { value: botAlias.attrArn });
    new cdk.CfnOutput(this, "Level3MockFnName", { value: agentFn.functionName });
  }
}
