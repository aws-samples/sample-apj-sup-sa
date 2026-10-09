import * as path from "path";
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as iam from "aws-cdk-lib/aws-iam";
import * as connect from "aws-cdk-lib/aws-connect";
import * as cases from "aws-cdk-lib/aws-cases";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as lambdaNode from "aws-cdk-lib/aws-lambda-nodejs";
import * as sns from "aws-cdk-lib/aws-sns";
import * as subs from "aws-cdk-lib/aws-sns-subscriptions";
import * as lex from "aws-cdk-lib/aws-lex";
import * as cr from "aws-cdk-lib/custom-resources";
import { oohTaskFlow, routedInboundFlow } from "./flows";

/**
 * AnyCompanyPay Connect routing module (OPT-IN) — implements the "Case-Owner
 * Reply Routing & After-Hours (OOH) Backlog" design for the chat + task channels.
 *
 *   Pattern A — a merchant's chat on an open case is offered to the CASE OWNER
 *               first (routing criteria: preferred agent + expiry, then fallback
 *               to the queue) at priority 1, ahead of new contacts.
 *   Pattern B — out of hours, the contact becomes a case (deduplicated per
 *               merchant) plus ONE scheduled, tier-prioritised task that routes to
 *               the backlog agents at the next opening.
 *
 * Everything here is ADDITIVE on the existing instance: new hours, queues,
 * routing profiles, Cases fields + template, two Lambdas, and two NEW flows. The
 * existing inbound / case chat flows, support queue, and routing profile are not
 * touched. Merchant chats only use the routed flow once AnyCompanyPayConnectStack
 * is redeployed with `-c routingFlowArn=<RoutedChatFlowArn>` (deploy.sh does that);
 * redeploying it without that context switches back to the original flows.
 */
export class ConnectRoutingStack extends cdk.Stack {
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

    // --- Inputs (discovered by deploy.sh from AnyCompanyPayConnectStack / AnyCompanyPayLexStack) ---
    const instanceArn = reqCtx("connectInstanceArn", "Connect instance ARN (AnyCompanyPayConnectStack ConnectInstanceArn).");
    const instanceId = instanceArn.split("/").pop()!;
    const casesDomainId = reqCtx("casesDomainId", "Cases domain id (AnyCompanyPayConnectStack CasesDomainId).");
    const casesDomainArn = `arn:aws:cases:${this.region}:${this.account}:domain/${casesDomainId}`;
    const profilesDomain = ctx("profilesDomainName", `anycompany-pay-customer-profile${sfx}`);
    const profilesDomainArn = `arn:aws:profile:${this.region}:${this.account}:domains/${profilesDomain}`;
    const botAliasArn = this.node.tryGetContext("agenticBotAliasArn") as string | undefined;
    const assistantArn = this.node.tryGetContext("qicAssistantArn") as string | undefined;
    const agentic = botAliasArn && assistantArn ? { botAliasArn, assistantArn } : undefined;

    // Demo mode: the inbound flow checks an always-CLOSED hours object (so the OOH
    // path can be exercised during the day) and tasks are scheduled a couple of
    // minutes out against ALWAYS-OPEN hours, instead of the real next opening.
    const demoMode = ctx("demoMode", "false") === "true";
    const csTimeZone = ctx("csTimeZone", "Asia/Singapore");
    const [openH, openM] = ctx("csOpen", "08:00").split(":").map(Number);
    const [closeH, closeM] = ctx("csClose", "18:00").split(":").map(Number);
    const csDays = ctx("csDays", "MONDAY,TUESDAY,WEDNESDAY,THURSDAY,FRIDAY").split(",");
    const tierDefaults = ctx("tierDefaults", JSON.stringify({ mch_luxe: "VIP", mch_nova: "key" }));
    const alertEmail = this.node.tryGetContext("alertEmail") as string | undefined;

    // ------------------------------------------------------------------
    // Hours of operation (design §6.5)
    // ------------------------------------------------------------------
    const csHours = new connect.CfnHoursOfOperation(this, "CsHours", {
      instanceArn,
      name: `anycompany-pay-cs-hours${sfx}`,
      description: "AnyCompanyPay customer-service business hours",
      timeZone: csTimeZone,
      config: csDays.map((day) => ({
        day: day.trim(),
        startTime: { hours: openH, minutes: openM },
        endTime: { hours: closeH, minutes: closeM },
      })),
    });
    // Closed except one minute a week: drives the OOH branch on demand.
    const demoClosedHours = new connect.CfnHoursOfOperation(this, "DemoClosedHours", {
      instanceArn,
      name: `anycompany-pay-demo-closed${sfx}`,
      description: "Demo only - effectively always closed (forces the after-hours path)",
      timeZone: "UTC",
      config: [{ day: "SUNDAY", startTime: { hours: 0, minutes: 0 }, endTime: { hours: 0, minutes: 1 } }],
    });
    const alwaysOpenHours = new connect.CfnHoursOfOperation(this, "DemoOpenHours", {
      instanceArn,
      name: `anycompany-pay-demo-open${sfx}`,
      description: "Demo only - always open (lets demo OOH tasks route immediately)",
      timeZone: "UTC",
      config: ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"].map((day) => ({
        day,
        startTime: { hours: 0, minutes: 0 },
        endTime: { hours: 23, minutes: 59 },
      })),
    });
    const inboundCheckHours = demoMode ? demoClosedHours : csHours;
    const taskCheckHours = demoMode ? alwaysOpenHours : csHours;

    // ------------------------------------------------------------------
    // Queues (design §6.1, chat channel) + the OOH task queue
    // ------------------------------------------------------------------
    const mkQueue = (logicalId: string, name: string, description: string) =>
      new connect.CfnQueue(this, logicalId, {
        instanceArn,
        name: `anycompany-pay-${name}${sfx}`,
        description,
        hoursOfOperationArn: csHours.attrHoursOfOperationArn,
      });
    const vipQueue = mkQueue("VipChatQueue", "vip-chat", "VIP merchants (chat)");
    const keyQueue = mkQueue("KeyAccountChatQueue", "key-account-chat", "Key-account merchants (chat)");
    const sharedQueue = mkQueue("SharedChatQueue", "cs-shared-chat", "All other merchants (chat)");
    const oohQueue = mkQueue("OohFollowupQueue", "ooh-followup", "After-hours follow-up tasks (Pattern B)");
    const tierQueueArns = {
      VIP: vipQueue.attrQueueArn,
      key: keyQueue.attrQueueArn,
      shared: sharedQueue.attrQueueArn,
    };

    // ------------------------------------------------------------------
    // Routing profiles (design §6.2). Every agent who can OWN a case has every
    // tier queue on the matching channel — Pattern A needs the owner to have the
    // queue the reply lands in (test A-12).
    // ------------------------------------------------------------------
    const chatQ = (q: connect.CfnQueue, priority: number, delay = 0) => ({
      priority,
      delay,
      queueReference: { channel: "CHAT", queueArn: q.attrQueueArn },
    });
    const liveOohDelay = Number(ctx("liveOohDelaySeconds", "1800"));
    const rpLive = new connect.CfnRoutingProfile(this, "RpLive", {
      instanceArn,
      name: `anycompany-pay-rp-live${sfx}`,
      description: "Live agents: tier chat queues; OOH backlog only when idle past the delay",
      defaultOutboundQueueArn: sharedQueue.attrQueueArn,
      mediaConcurrencies: [
        { channel: "CHAT", concurrency: 3 },
        { channel: "TASK", concurrency: 1 },
      ],
      queueConfigs: [
        chatQ(vipQueue, 1),
        chatQ(keyQueue, 1),
        chatQ(sharedQueue, 2),
        { priority: 3, delay: liveOohDelay, queueReference: { channel: "TASK", queueArn: oohQueue.attrQueueArn } },
      ],
    });
    const rpOoh = new connect.CfnRoutingProfile(this, "RpOohBacklog", {
      instanceArn,
      name: `anycompany-pay-rp-ooh-backlog${sfx}`,
      description: "Backlog agents: OOH tasks first (one at a time), tier chat queues second",
      defaultOutboundQueueArn: sharedQueue.attrQueueArn,
      mediaConcurrencies: [
        { channel: "TASK", concurrency: 1 },
        { channel: "CHAT", concurrency: 2 },
      ],
      queueConfigs: [
        { priority: 1, delay: 0, queueReference: { channel: "TASK", queueArn: oohQueue.attrQueueArn } },
        chatQ(vipQueue, 2),
        chatQ(keyQueue, 2),
        chatQ(sharedQueue, 2),
      ],
    });

    // ------------------------------------------------------------------
    // Cases: OOH fields + template on the EXISTING domain (design §7.1)
    // ------------------------------------------------------------------
    const mkField = (logicalId: string, name: string, description: string) =>
      new cases.CfnField(this, logicalId, { domainId: casesDomainId, name, type: "Text", description });
    const fTier = mkField("CaseFieldTier", "tier", "Merchant tier: VIP | key | shared");
    const fPending = mkField("CaseFieldOohPending", "ooh_task_pending", "true while an OOH follow-up task is scheduled");
    const fTaskId = mkField("CaseFieldOohTaskId", "ooh_task_id", "Contact id of the live OOH follow-up task");
    const oohTemplate = new cases.CfnTemplate(this, "OohCaseTemplate", {
      domainId: casesDomainId,
      name: `AnyCompanyPayOOH${sfx}`,
      description: "Case raised from an after-hours merchant contact",
      status: "Active",
      requiredFields: [{ fieldId: "title" }],
    });
    [fTier, fPending, fTaskId].forEach((f) => oohTemplate.addDependency(f));

    // ------------------------------------------------------------------
    // "Log a case?" confirmation bot (after hours). The flow — not the AI —
    // asks whether to log the request as a case, and reads the merchant's Yes /
    // No through this small Lex V2 bot. Attached to the instance by deploy.sh
    // (`connect associate-bot`), like the self-service bot.
    // ------------------------------------------------------------------
    const confirmBotName = `anycompany-pay-log-case${sfx}`;
    const confirmBotRole = new iam.Role(this, "LogCaseBotRole", {
      assumedBy: new iam.ServicePrincipal("lexv2.amazonaws.com"),
      description: "AnyCompanyPay after-hours 'log a case?' confirmation bot",
    });
    const utter = (...u: string[]) => u.map((utterance) => ({ utterance }));
    const confirmBot = new lex.CfnBot(this, "LogCaseBot", {
      name: confirmBotName,
      roleArn: confirmBotRole.roleArn,
      dataPrivacy: { ChildDirected: false },
      idleSessionTtlInSeconds: 300,
      description: "After hours: confirm whether to log the merchant's request as a support case",
      autoBuildBotLocales: true,
      botLocales: [
        {
          localeId: "en_US",
          nluConfidenceThreshold: 0.4,
          intents: [
            {
              name: "LogCaseYes",
              sampleUtterances: utter("Yes", "yes please", "yeah", "yep", "sure", "ok", "okay", "please do",
                "log it", "log a case", "yes log a case", "go ahead", "do it", "Yes, log a case"),
            },
            {
              name: "LogCaseNo",
              sampleUtterances: utter("No", "no thanks", "no thank you", "nope", "not now", "don't",
                "do not log it", "No, thanks", "never mind", "no need"),
            },
            { name: "FallbackIntent", parentIntentSignature: "AMAZON.FallbackIntent" },
          ],
        },
      ],
    });
    const confirmVersion = new lex.CfnBotVersion(this, "LogCaseBotVersion", {
      botId: confirmBot.attrId,
      botVersionLocaleSpecification: [{ localeId: "en_US", botVersionLocaleDetails: { sourceBotVersion: "DRAFT" } }],
    });
    confirmVersion.addDependency(confirmBot);
    const confirmAlias = new lex.CfnBotAlias(this, "LogCaseBotAlias", {
      botId: confirmBot.attrId,
      botAliasName: `${confirmBotName}-live`,
      botVersion: confirmVersion.attrBotVersion,
      botAliasLocaleSettings: [{ localeId: "en_US", botAliasLocaleSetting: { enabled: true } }],
    });
    confirmAlias.addDependency(confirmVersion);
    // Attach the bot to the instance BEFORE the flow that references it. AssociateBot
    // also writes the Connect invoke resource policy on the alias. Create/delete only
    // (no update), so stack updates don't churn the association.
    const lexV2Bot = { AliasArn: confirmAlias.attrArn };
    const confirmBotAssoc = new cr.AwsCustomResource(this, "LogCaseBotAssociation", {
      onCreate: {
        service: "Connect",
        action: "associateBot",
        parameters: { InstanceId: instanceId, LexV2Bot: lexV2Bot },
        physicalResourceId: cr.PhysicalResourceId.of(`log-case-bot-${confirmBotName}`),
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
          resources: [confirmAlias.attrArn],
        }),
      ]),
      installLatestAwsSdk: false,
    });
    confirmBotAssoc.node.addDependency(confirmAlias);
    const pad = (n: number) => String(n).padStart(2, "0");
    const dayAbbr = (d: string) => d.trim().slice(0, 1) + d.trim().slice(1, 3).toLowerCase();
    const hoursText = `${dayAbbr(csDays[0])}–${dayAbbr(csDays[csDays.length - 1])} ${pad(openH)}:${pad(openM)}–${pad(closeH)}:${pad(closeM)}`;

    // ------------------------------------------------------------------
    // Supervisor alerts (OOH scheduling failures, test B-07)
    // ------------------------------------------------------------------
    const alertTopic = new sns.Topic(this, "OohAlertTopic", {
      displayName: "AnyCompanyPay OOH scheduling alerts",
      enforceSSL: true,
    });
    if (alertEmail) alertTopic.addSubscription(new subs.EmailSubscription(alertEmail));

    // ------------------------------------------------------------------
    // Lambdas (bundled SDK: GetEffectiveHoursOfOperations/StartTaskContact
    // scheduling need a recent client)
    // ------------------------------------------------------------------
    const common = {
      runtime: lambda.Runtime.NODEJS_22_X,
      memorySize: 256,
      timeout: cdk.Duration.seconds(8),
      bundling: { minify: true, target: "node22", externalModules: [] as string[] },
    };
    const sharedEnv = {
      CONNECT_INSTANCE_ID: instanceId,
      CASES_DOMAIN_ID: casesDomainId,
      PROFILES_DOMAIN_NAME: profilesDomain,
      TIER_DEFAULTS: tierDefaults,
      ACCOUNT_ID: this.account,
    };
    const casesRead = new iam.PolicyStatement({
      actions: ["cases:GetCase", "cases:ListFields", "cases:CreateRelatedItem"],
      resources: [casesDomainArn, `${casesDomainArn}/*`],
    });
    const profilesRead = new iam.PolicyStatement({
      actions: ["profile:SearchProfiles"],
      resources: [profilesDomainArn, `${profilesDomainArn}/*`],
    });
    // The profiles domain is encrypted with a customer-managed key (connect
    // stack); reading profiles needs Decrypt on it, only via Customer Profiles.
    const profilesKeyArn = this.node.tryGetContext("profilesKeyArn") as string | undefined;
    const profilesKey = profilesKeyArn
      ? new iam.PolicyStatement({
          actions: ["kms:Decrypt"],
          resources: [profilesKeyArn],
          conditions: { StringEquals: { "kms:ViaService": `profile.${this.region}.amazonaws.com` } },
        })
      : undefined;

    const contextFn = new lambdaNode.NodejsFunction(this, "ContactContextFn", {
      ...common,
      entry: path.join(__dirname, "..", "lambda", "contact-context", "index.ts"),
      description: "Routing Pattern A: tier + case-owner routing criteria for the routed chat flow",
      environment: {
        ...sharedEnv,
        TIER_QUEUE_ARNS: this.toJsonString(tierQueueArns),
        OWNER_EXPIRY_SECONDS: JSON.stringify({
          CHAT: Number(ctx("ownerExpiryChatSeconds", "60")),
          EMAIL: Number(ctx("ownerExpiryEmailSeconds", "900")),
        }),
        OWNER_PRESENCE_CHECK: ctx("ownerPresenceCheck", "true"),
      },
    });
    contextFn.addToRolePolicy(casesRead);
    contextFn.addToRolePolicy(profilesRead);
    if (profilesKey) contextFn.addToRolePolicy(profilesKey);
    contextFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["connect:GetCurrentUserData", "connect:DescribeContact"],
        resources: [instanceArn, `${instanceArn}/*`],
      })
    );

    const oohTaskFlowName = `anycompany-pay-ooh-task${sfx}`;
    const oohFn = new lambdaNode.NodejsFunction(this, "OohSchedulerFn", {
      ...common,
      entry: path.join(__dirname, "..", "lambda", "ooh-scheduler", "index.ts"),
      description: "Routing Pattern B: OOH case + scheduled follow-up task (intake / re-schedule)",
      environment: {
        ...sharedEnv,
        OOH_TEMPLATE_ID: oohTemplate.attrTemplateId,
        OOH_TASK_FLOW_NAME: oohTaskFlowName,
        CS_HOURS_ARN: csHours.attrHoursOfOperationArn,
        OPEN_OFFSET_SECONDS: ctx("openOffsetSeconds", "300"),
        DEMO_DELAY_SECONDS: demoMode ? ctx("demoTaskDelaySeconds", "120") : "0",
        ALERT_TOPIC_ARN: alertTopic.topicArn,
      },
    });
    oohFn.addToRolePolicy(casesRead);
    oohFn.addToRolePolicy(profilesRead);
    if (profilesKey) oohFn.addToRolePolicy(profilesKey);
    oohFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ["cases:SearchCases", "cases:CreateCase", "cases:UpdateCase"],
        resources: [casesDomainArn, `${casesDomainArn}/*`],
      })
    );
    oohFn.addToRolePolicy(
      new iam.PolicyStatement({
        actions: [
          "connect:StartTaskContact",
          "connect:DescribeContact",
          "connect:ListContactFlows",
          "connect:GetEffectiveHoursOfOperations",
        ],
        resources: [instanceArn, `${instanceArn}/*`],
      })
    );
    alertTopic.grantPublish(oohFn);

    // Let the instance invoke both functions from flows.
    const assocs = [contextFn, oohFn].map((fn, i) => {
      fn.addPermission(`ConnectInvoke${i}`, {
        principal: new iam.ServicePrincipal("connect.amazonaws.com"),
        sourceArn: instanceArn,
        sourceAccount: this.account,
      });
      return new connect.CfnIntegrationAssociation(this, `LambdaAssoc${i}`, {
        instanceId: instanceArn,
        integrationType: "LAMBDA_FUNCTION",
        integrationArn: fn.functionArn,
      });
    });

    // ------------------------------------------------------------------
    // Flows (NEW — the existing flows are not modified)
    // ------------------------------------------------------------------
    const taskFlow = new connect.CfnContactFlow(this, "OohTaskFlow", {
      instanceArn,
      name: oohTaskFlowName,
      description: "OOH follow-up task: tier priority -> ooh-followup queue; re-schedule if still closed",
      type: "CONTACT_FLOW",
      content: this.toJsonString(
        oohTaskFlow({
          oohFnArn: oohFn.functionArn,
          checkHoursArn: taskCheckHours.attrHoursOfOperationArn,
          oohQueueArn: oohQueue.attrQueueArn,
        })
      ),
    });
    const routedFlow = new connect.CfnContactFlow(this, "RoutedChatFlow", {
      instanceArn,
      name: `anycompany-pay-chat-routed${sfx}`,
      description:
        "Merchant chat with case-owner routing (Pattern A) and after-hours case + task backlog (Pattern B)" +
        (agentic ? "; agentic self-service for new issues" : ""),
      type: "CONTACT_FLOW",
      content: this.toJsonString(
        routedInboundFlow({
          contextFnArn: contextFn.functionArn,
          oohFnArn: oohFn.functionArn,
          checkHoursArn: inboundCheckHours.attrHoursOfOperationArn,
          fallbackQueueArn: sharedQueue.attrQueueArn,
          agentic,
          confirmBotAliasArn: confirmAlias.attrArn,
          hoursText,
        })
      ),
    });
    for (const a of assocs) {
      taskFlow.addDependency(a);
      routedFlow.addDependency(a);
    }
    routedFlow.node.addDependency(confirmBotAssoc);

    // ------------------------------------------------------------------
    // Outputs
    // ------------------------------------------------------------------
    const out = (k: string, v: string) => new cdk.CfnOutput(this, k, { value: v });
    out("RoutedChatFlowArn", routedFlow.attrContactFlowArn);
    out("OohTaskFlowArn", taskFlow.attrContactFlowArn);
    out("VipChatQueueArn", vipQueue.attrQueueArn);
    out("KeyAccountChatQueueArn", keyQueue.attrQueueArn);
    out("SharedChatQueueArn", sharedQueue.attrQueueArn);
    out("OohFollowupQueueArn", oohQueue.attrQueueArn);
    out("RpLiveName", `anycompany-pay-rp-live${sfx}`);
    out("RpOohBacklogName", `anycompany-pay-rp-ooh-backlog${sfx}`);
    out("CsHoursArn", csHours.attrHoursOfOperationArn);
    out("OohCaseTemplateId", oohTemplate.attrTemplateId);
    out("ContactContextFnName", contextFn.functionName);
    out("OohSchedulerFnName", oohFn.functionName);
    out("OohAlertTopicArn", alertTopic.topicArn);
    out("DemoMode", String(demoMode));
    out("LogCaseBotAliasArn", confirmAlias.attrArn);
  }
}
