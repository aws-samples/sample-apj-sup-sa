#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib/core';
import * as path from 'path';
import { ToolsStack } from '../lib/tools-stack';
import { DataStack } from '../lib/data-stack';
import { WebStack } from '../lib/web-stack';

// Sydney, pinned for all services. Account from CDK env.
const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_DEFAULT_REGION ?? 'ap-southeast-2',
};

const app = new cdk.App();

// Database & Seed. Aurora SV2 PostgreSQL, seeded with the Aisle catalogue.
new DataStack(app, 'AisleDataStack', { env });

// Tools & Gateway. Stands up the AgentCore Gateway and the tool Lambda targets.
new ToolsStack(app, 'AisleToolsStack', { env });

// Frontend — static site (S3 + CloudFront), serves frontend/dist.
new WebStack(app, 'AisleWebStack', {
  env,
  // frontend/dist relative to backend/infra
  distPath: path.join(__dirname, '..', '..', '..', 'frontend', 'dist'),
});
