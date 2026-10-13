import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as wisdom from "aws-cdk-lib/aws-wisdom";
import * as connect from "aws-cdk-lib/aws-connect";

/**
 * AnyCompanyPay Q in Connect "AI domain" as CloudFormation.
 *
 * In the console this is the "AI agents -> Add domain" step. A domain is an
 * Amazon Q in Connect (Wisdom) ASSISTANT associated to the Connect instance.
 * This stack creates that assistant (AWS-owned encryption key) and associates it
 * to the instance via AWS::Connect::IntegrationAssociation (WISDOM_ASSISTANT).
 *
 * IMPORTANT — this is an OPTIONAL, opt-in stack, provided so a clean-room deploy
 * can be fully IaC. It is NOT deployed by default and is NOT part of the running
 * Tokyo environment (whose domain was created in the console). Two hard rules:
 *   1. An instance can be associated with only ONE Q in Connect domain at a time.
 *      Deploying this against an instance that already has a domain will conflict.
 *      Run it INSTEAD of the console domain step, on an instance with no domain.
 *   2. This creates a BARE assistant. The console "Add domain" also seeds the
 *      default system AI agents/prompts; those are not reproduced here. For this
 *      solution that is fine — the orchestration agent is created separately by
 *      provision-ai-agent.sh — but it is not a 1:1 replica of the console domain.
 *
 * No knowledge base is created: this use case answers from the query_transactions
 * tool, not a KB (add an AWS::Wisdom::KnowledgeBase + AWS::Wisdom::AssistantAssociation
 * if you later want grounded policy answers).
 */
export class QicDomainStack extends cdk.Stack {
  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // Optional env discriminator. Empty → no suffix (clean common names).
    const envName = ((this.node.tryGetContext("envName") as string) ?? "").trim();
    const sfx = envName ? `-${envName}` : "";
    const label = envName || "default";
    const connectInstanceArn = this.node.tryGetContext("connectInstanceArn") as string | undefined;
    if (!connectInstanceArn) {
      throw new Error(
        'Missing required context "-c connectInstanceArn=...": the Amazon Connect instance ARN ' +
          "(AnyCompanyPayConnectStack[-<env>] output ConnectInstanceArn). Run deploy-qic-domain.sh, which discovers it."
      );
    }
    const domainName = (this.node.tryGetContext("domainName") as string) ?? `anycompany-pay-ai-domain${sfx}`;

    // The Q in Connect assistant (a.k.a. the "domain"). No
    // ServerSideEncryptionConfiguration => AWS-owned key, which lets Amazon Lex
    // reach the assistant without a customer-managed-key grant (the #1 setup trap).
    const assistant = new wisdom.CfnAssistant(this, "QicAssistant", {
      name: domainName,
      type: "AGENT",
      description: `AnyCompanyPay Q in Connect domain (${label})`,
    });

    // Associate the assistant (domain) with the Connect instance.
    const association = new connect.CfnIntegrationAssociation(this, "WisdomAssistantAssociation", {
      instanceId: connectInstanceArn,
      integrationType: "WISDOM_ASSISTANT",
      integrationArn: assistant.attrAssistantArn,
    });
    association.addDependency(assistant);

    new cdk.CfnOutput(this, "AssistantId", { value: assistant.attrAssistantId });
    new cdk.CfnOutput(this, "AssistantArn", { value: assistant.attrAssistantArn });
    new cdk.CfnOutput(this, "DomainName", { value: domainName });
  }
}
