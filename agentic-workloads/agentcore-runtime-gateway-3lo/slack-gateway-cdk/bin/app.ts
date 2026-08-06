#!/usr/bin/env node
import * as dotenv from 'dotenv';
import * as cdk from 'aws-cdk-lib';
import { SlackGatewayStack } from '../lib/slack-gateway-stack';

// Load Slack credentials (and optional overrides) from .env in the project root
// (the CDK CLI runs the app from the project root, so cwd is the root).
dotenv.config();

// This stack deploys to us-east-1. Override with AWS_REGION / CDK_DEFAULT_REGION if needed.
const region = process.env.CDK_DEFAULT_REGION ?? process.env.AWS_REGION ?? 'us-east-1';

const slackClientId = process.env.SLACK_CLIENT_ID;
const slackClientSecret = process.env.SLACK_CLIENT_SECRET;

if (!slackClientId || !slackClientSecret) {
  throw new Error(
    'Missing SLACK_CLIENT_ID and/or SLACK_CLIENT_SECRET. ' +
      'Copy .env.example to .env and fill in the Slack OAuth2 app credentials.'
  );
}

const testUserPassword = process.env.TEST_USER_PASSWORD;

if (!testUserPassword) {
  throw new Error(
    'Missing TEST_USER_PASSWORD. Copy .env.example to .env and set the password ' +
      'for the seeded test users (must satisfy the Cognito password policy).'
  );
}

const app = new cdk.App();

new SlackGatewayStack(app, 'SlackGatewayStack', {
  slackClientId,
  slackClientSecret,
  cognitoDomainPrefix: process.env.COGNITO_DOMAIN_PREFIX,
  resourceNameSuffix: process.env.RESOURCE_SUFFIX,
  testUserPassword,
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
