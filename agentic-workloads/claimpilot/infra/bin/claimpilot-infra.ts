#!/usr/bin/env node
import { App } from 'aws-cdk-lib';

import { ClaimPilotStack } from '../lib/claimpilot-stack.js';

const app = new App();

new ClaimPilotStack(app, 'ClaimPilotStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region: process.env.CDK_DEFAULT_REGION ?? process.env.AWS_REGION ?? 'ap-southeast-2',
  },
});
