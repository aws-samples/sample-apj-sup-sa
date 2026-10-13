#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import { ConnectAiAgentStack } from "../lib/connect-ai-agent-stack";
import { LexSelfServiceStack } from "../lib/lex-stack";
import { QicDomainStack } from "../lib/qic-domain-stack";

const app = new cdk.App();

// Deploys the tenant-isolated transaction Q&A tool (Lambda in the Aurora VPC)
// and the AgentCore Gateway request interceptor. Defaults below match the
// deployed AnyCompanyPay environment; override with `-c key=value`. The AgentCore
// Gateway + Lambda target + Cognito JWT inbound auth are created out-of-band by
// provision-gateway.sh (see README), since Gateway CloudFormation support is
// still limited in preview.
// Region resolves from the AWS session, defaulting to us-west-2; override with
// `-c region=<region>` or AWS_REGION. Account comes from the session.
const region =
  app.node.tryGetContext("region") ||
  process.env.CDK_DEFAULT_REGION ||
  process.env.AWS_REGION ||
  "us-west-2";
const env = { account: process.env.CDK_DEFAULT_ACCOUNT, region };

// This app hosts two independent stacks. Each is constructed only when its own
// required context is present, so deploying one never triggers the other stack's
// required-context validation (they synth in the same app). The module's
// deploy.sh always passes `collectionEndpoint`; deploy-lex.sh passes `assistantArn`.
if (app.node.tryGetContext("collectionEndpoint")) {
  new ConnectAiAgentStack(app, "AnyCompanyPayConnectAiAgentStack", {
    env,
    description:
      "AnyCompanyPay AI transaction Q&A: AgentCore Gateway MCP tool (tenant-isolated OpenSearch) + request interceptor",
  });
}

// Agentic self-service Lex bot (Q in Connect).
if (app.node.tryGetContext("assistantArn")) {
  new LexSelfServiceStack(app, "AnyCompanyPayLexStack", {
    env,
    description: "AnyCompanyPay agentic self-service - Amazon Lex V2 bot (Q in Connect) + Connect association",
  });
}

// OPTIONAL, opt-in: create the Q in Connect "AI domain" (assistant) as IaC
// instead of the console. Inert unless -c deployQicDomain=true is passed, so it
// never affects the normal deploys above. An instance can have only ONE domain,
// so only use this on an instance that does NOT already have one (see deploy-qic-domain.sh).
if (app.node.tryGetContext("deployQicDomain")) {
  new QicDomainStack(app, "AnyCompanyPayQicDomainStack", {
    env,
    description: "AnyCompanyPay Q in Connect AI domain (assistant) + WISDOM_ASSISTANT instance association",
  });
}
