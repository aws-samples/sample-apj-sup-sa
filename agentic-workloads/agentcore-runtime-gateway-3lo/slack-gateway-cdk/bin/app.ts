#!/usr/bin/env node
import * as dotenv from 'dotenv';
import * as cdk from 'aws-cdk-lib';
import { SlackGatewayStack } from '../lib/slack-gateway-stack';

// Load Slack credentials (and optional overrides) from .env in the project root
// (the CDK CLI runs the app from the project root, so cwd is the root).
dotenv.config();

// This stack deploys to us-east-1. Override with AWS_REGION / CDK_DEFAULT_REGION if needed.
const region = process.env.CDK_DEFAULT_REGION ?? process.env.AWS_REGION ?? 'us-east-1';

// ARN of the pre-created Secrets Manager secret holding the Slack OAuth2 app
// credentials as JSON. An ARN (not a name) is required: the client secret is
// referenced via ClientSecretConfig (ClientSecretSource = EXTERNAL), which
// AgentCore Identity reads at runtime. The secret must exist before deploy and
// grant the AgentCore service principal secretsmanager:GetSecretValue via its
// resource policy (see README).
const slackSecretArn = process.env.SLACK_SECRET_ARN;

if (!slackSecretArn) {
  throw new Error(
    'Missing SLACK_SECRET_ARN. Copy .env.example to .env and set the ARN of the ' +
      'pre-created Secrets Manager secret holding the Slack OAuth2 client id and secret ' +
      '(JSON, e.g. {"client_id":"...","client_secret":"..."}).'
  );
}

const app = new cdk.App();

new SlackGatewayStack(app, 'SlackGatewayStack', {
  slackSecretArn,
  cognitoDomainPrefix: process.env.COGNITO_DOMAIN_PREFIX,
  resourceNameSuffix: process.env.RESOURCE_SUFFIX,
  bedrockModelId: process.env.BEDROCK_MODEL_ID,
  // Account comes from the deployer's active AWS credentials; region is pinned to
  // us-east-1 (overridable via CDK_DEFAULT_REGION / AWS_REGION).
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region,
  },
  description:
    'AgentCore Slack MCP gateway + Runtime (Cognito user pool, OAuth2 identity provider, target, IAM roles, JWT-forwarding runtime agent).',
});

app.synth();
