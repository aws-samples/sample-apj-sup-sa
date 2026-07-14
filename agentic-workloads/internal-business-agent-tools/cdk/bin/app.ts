#!/usr/bin/env node
import { App } from 'aws-cdk-lib';
import { DataStack } from '../lib/data-stack';
import { AgentCoreStack } from '../lib/agentcore-stack';

const app = new App();

// CDK_DEFAULT_ACCOUNT / CDK_DEFAULT_REGION are populated by the CDK CLI
// from the AWS profile that you authenticate with (`AWS_PROFILE=...`).
// Set them explicitly when running `cdk deploy` outside of an authenticated shell.
const account = process.env.CDK_DEFAULT_ACCOUNT;
const region = process.env.CDK_DEFAULT_REGION || 'ap-northeast-1';

if (!account) {
  throw new Error(
    'CDK_DEFAULT_ACCOUNT is not set. Run with `AWS_PROFILE=<profile>` or export CDK_DEFAULT_ACCOUNT explicitly.'
  );
}

const env = { account, region };

const dataStack = new DataStack(app, 'InternalAgent-Data', {
  env,
  description: 'Data layer: VPC, Aurora, S3, Cognito, SSM parameters',
});

new AgentCoreStack(app, 'InternalAgent-AgentCore', {
  env,
  userPoolId: dataStack.auth.userPool.userPoolId,
  userPoolClientId: dataStack.auth.client.userPoolClientId,
  bucketArn: dataStack.storage.bucket.bucketArn,
  clusterArn: dataStack.database.cluster.clusterArn,
  secretArn: dataStack.database.cluster.secret!.secretArn,
  description: 'AgentCore Runtime (PUBLIC) + Gateway',
});

app.synth();
