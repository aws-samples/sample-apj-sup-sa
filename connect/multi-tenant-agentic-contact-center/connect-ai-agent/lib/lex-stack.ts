import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lex from "aws-cdk-lib/aws-lex";

/**
 * AnyCompanyPay agentic self-service - Amazon Lex V2 bot (Q in Connect), fully in
 * CloudFormation. This replaces the console-only "build a Lex bot with the
 * AMAZON.QInConnectIntent" step.
 *
 * What it creates:
 *   - A custom Lex V2 bot role. Because the bot uses a CUSTOM role (not the Lex
 *     service-linked role), Amazon Lex will NOT auto-attach the Q in Connect
 *     permissions, so we attach them ourselves (per the QInConnectIntent docs):
 *       wisdom:CreateSession + wisdom:GetAssistant on the assistant, and
 *       wisdom:SendMessage + wisdom:GetNextMessage on its sessions.
 *     (The KMS statement from the docs is only needed for a customer-managed key;
 *     the Tokyo Q in Connect domain uses the AWS-owned key, so it is omitted.)
 *   - The bot (en_US) with the AMAZON.QInConnectIntent pointed at the Q in Connect
 *     assistant ARN, plus the required AMAZON.FallbackIntent. autoBuildBotLocales
 *     builds the DRAFT locale at deploy.
 *   - A published version (from DRAFT) and an alias (en_US enabled).
 *   - An AWS::Connect::IntegrationAssociation (LEX_BOT) that attaches the bot
 *     ALIAS to the Connect instance, so a contact flow can use it.
 *
 * Inputs (context, discovered by deploy-lex.sh):
 *   assistantArn        - the Q in Connect assistant ARN (wisdom:...:assistant/<id>)
 *   connectInstanceArn  - the Amazon Connect instance ARN
 * The bot + assistant must be in the SAME region (a QInConnectIntent requirement).
 */
export class LexSelfServiceStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // Optional env discriminator. Empty → no suffix (clean common names).
    const envName = ((this.node.tryGetContext("envName") as string) ?? "").trim();
    const sfx = envName ? `-${envName}` : "";
    const label = envName || "default";
    const ctx = (k: string, d: string) => (this.node.tryGetContext(k) as string) ?? d;
    const reqCtx = (k: string, hint: string): string => {
      const v = this.node.tryGetContext(k) as string | undefined;
      if (!v) {
        throw new Error(
          `Missing required context "-c ${k}=...": ${hint} ` +
            `Run this module's deploy-lex.sh (it discovers these from the Connect instance), ` +
            `or pass -c ${k}=... explicitly.`
        );
      }
      return v;
    };

    // The Q in Connect assistant ARN, e.g.
    //   arn:aws:wisdom:ap-northeast-1:123456789012:assistant/<uuid>
    const assistantArn = reqCtx(
      "assistantArn",
      "Q in Connect assistant ARN (wisdom:...:assistant/<id>) - the WISDOM_ASSISTANT integration on the instance."
    );
    const botName = ctx("botName", `anycompany-pay-agentic-selfservice${sfx}`);

    // Derive the session-ARN resource from the assistant ARN (plain strings from
    // context, so ordinary string ops are fine - no CFN tokens involved).
    //   assistant ARN: arn:aws:wisdom:<region>:<acct>:assistant/<assistantId>[/...]
    //   session  ARN:  arn:aws:wisdom:<region>:<acct>:session/<assistantId>/*
    const arnBase = assistantArn.split(":assistant/")[0];
    const assistantId = (assistantArn.split(":assistant/")[1] ?? "").split("/")[0];
    const sessionArn = `${arnBase}:session/${assistantId}/*`;

    // ------------------------------------------------------------------
    // Custom Lex V2 bot role + Q in Connect permissions.
    // ------------------------------------------------------------------
    const botRole = new iam.Role(this, "BotRole", {
      assumedBy: new iam.ServicePrincipal("lexv2.amazonaws.com"),
      description: `AnyCompanyPay Lex self-service bot (${label}) - Q in Connect`,
    });
    botRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "QInConnectAssistantPolicy",
        actions: ["wisdom:CreateSession", "wisdom:GetAssistant"],
        resources: [assistantArn, `${assistantArn}/*`],
      })
    );
    botRole.addToPolicy(
      new iam.PolicyStatement({
        sid: "QInConnectSessionsPolicy",
        actions: ["wisdom:SendMessage", "wisdom:GetNextMessage"],
        resources: [sessionArn],
      })
    );

    // ------------------------------------------------------------------
    // The bot: en_US locale with the QInConnectIntent + FallbackIntent.
    // ------------------------------------------------------------------
    const bot = new lex.CfnBot(this, "SelfServiceBot", {
      name: botName,
      roleArn: botRole.roleArn,
      dataPrivacy: { ChildDirected: false },
      idleSessionTtlInSeconds: 300,
      description: `AnyCompanyPay agentic self-service (${label}) - Q in Connect`,
      autoBuildBotLocales: true,
      botLocales: [
        {
          localeId: "en_US",
          nluConfidenceThreshold: 0.4,
          description: "AnyCompanyPay agentic self-service (Q in Connect)",
          intents: [
            {
              name: "QInConnectIntent",
              parentIntentSignature: "AMAZON.QInConnectIntent",
              qInConnectIntentConfiguration: {
                qInConnectAssistantConfiguration: { assistantArn },
              },
            },
            // A locale must include the fallback intent.
            {
              name: "FallbackIntent",
              parentIntentSignature: "AMAZON.FallbackIntent",
            },
          ],
        },
      ],
    });

    // Published version from the (auto-built) DRAFT locale.
    const version = new lex.CfnBotVersion(this, "BotVersion", {
      botId: bot.attrId,
      botVersionLocaleSpecification: [
        { localeId: "en_US", botVersionLocaleDetails: { sourceBotVersion: "DRAFT" } },
      ],
    });
    version.addDependency(bot);

    // Alias pointing at that version, with en_US enabled.
    const alias = new lex.CfnBotAlias(this, "BotAlias", {
      botId: bot.attrId,
      botAliasName: `${botName}-live`,
      botVersion: version.attrBotVersion,
      botAliasLocaleSettings: [
        { localeId: "en_US", botAliasLocaleSetting: { enabled: true } },
      ],
    });
    alias.addDependency(version);

    // NOTE on the Connect association: attaching a Lex *V2* bot to the instance
    // is intentionally NOT done here. AWS::Connect::IntegrationAssociation
    // (LEX_BOT) is a no-op for V2 (it never populates the instance bot store the
    // "Get customer input" block reads), and an AssociateBot custom resource is
    // unreliable/churns the association on any stack change. The association is
    // performed as the final step of deploy-lex.sh via `connect associate-bot`
    // (idempotent), which reliably registers the bot and writes the Connect
    // invoke resource-policy on the alias.

    // ------------------------------------------------------------------
    // Outputs
    // ------------------------------------------------------------------
    new cdk.CfnOutput(this, "BotId", { value: bot.attrId });
    new cdk.CfnOutput(this, "BotName", { value: botName });
    new cdk.CfnOutput(this, "BotArn", { value: bot.attrArn });
    new cdk.CfnOutput(this, "BotAliasId", { value: alias.attrBotAliasId });
    new cdk.CfnOutput(this, "BotAliasArn", { value: alias.attrArn });
    new cdk.CfnOutput(this, "AssistantArn", { value: assistantArn });
  }
}
