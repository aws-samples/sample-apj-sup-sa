#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { AwsSolutionsChecks } from 'cdk-nag';
import { ThreeDGenerationStack } from '../lib/stack';

const app = new cdk.App();

const allowedCidrs = String(app.node.tryGetContext('allowedCidrs') ?? '')
  .split(',')
  .map((c) => c.trim())
  .filter(Boolean);

new ThreeDGenerationStack(app, 'ThreeDGenerationLlmStack', {
  // us-east-1: CloudFront-scoped WAF, Lambda@Edge and the models' us.* inference profiles.
  env: { account: process.env.CDK_DEFAULT_ACCOUNT, region: 'us-east-1' },
  description: '3D object generation from text with four LLMs on Amazon Bedrock AgentCore',
  allowedCidrs,
  deploySite: String(app.node.tryGetContext('deploySite') ?? 'true') !== 'false',
});

cdk.Tags.of(app).add('project', '3d-generation-llm');
cdk.Tags.of(app).add('environment', 'demo');
cdk.Validations.of(app).addPlugins(new AwsSolutionsChecks(app, { verbose: true }));
