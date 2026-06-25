#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib/core';
import * as path from 'path';
import { ToolsStack } from '../lib/tools-stack';
import { DataStack } from '../lib/data-stack';
import { WebStack } from '../lib/web-stack';

// Sydney, pinned for all services (SPEC §intro). Account 597437436235.
const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_DEFAULT_REGION ?? 'ap-southeast-2',
};

const app = new cdk.App();

// Silo 1 — Database & Seed. Aurora SV2 PostgreSQL, seeded with Woolworths data.
new DataStack(app, 'AisleDataStack', { env });

// Silo 2 — Tools & Gateway. Stands up the AgentCore Gateway (no targets yet).
new ToolsStack(app, 'AisleToolsStack', { env });

// Frontend — static site (S3 + CloudFront), serves frontend/dist.
new WebStack(app, 'AisleWebStack', {
  env,
  // frontend/dist relative to backend/infra
  distPath: path.join(__dirname, '..', '..', '..', 'frontend', 'dist'),
});
