#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { BedrockMeterCoreStack } from '../lib/bedrock-meter-core-stack';

const app = new cdk.App();
const split = (value: string | undefined): string[] =>
  (value ?? '').split(',').map((item) => item.trim()).filter(Boolean);
const bool = (value: string): boolean => value.toLowerCase() === 'true';
// CDK supplies its resolved default region when it executes this app. The deploy
// script additionally pins it to the terminal/profile target verified with STS.
const region = process.env.CDK_DEFAULT_REGION || process.env.AWS_REGION || process.env.AWS_DEFAULT_REGION;
if (!region) {
  throw new Error('AWS region is required. Set AWS_REGION or configure a region in your AWS profile.');
}

new BedrockMeterCoreStack(app, 'BedrockMeterCoreStack', {
  env: {
    account: process.env.CDK_DEFAULT_ACCOUNT,
    region,
  },
  meterConfig: {
    bedrockRegion: process.env.BEDROCK_METER_BEDROCK_REGION ?? region,
    adminEmails: split(process.env.BEDROCK_METER_ADMIN_EMAILS),
    adminUsernames: split(process.env.BEDROCK_METER_ADMIN_USERNAMES),
    initialQuotaTimeZone: process.env.BEDROCK_METER_QUOTA_TIME_ZONE ?? 'UTC',
    consoleAllowedUsers: split(process.env.BEDROCK_METER_CONSOLE_EMERGENCY_ADMINS),
    ...(process.env.BEDROCK_METER_ALLOW_LOGGING_OVERWRITE
      ? { allowBedrockLoggingOverwrite: bool(process.env.BEDROCK_METER_ALLOW_LOGGING_OVERWRITE) }
      : {}),
    ...(process.env.BEDROCK_METER_ROLE_ENFORCEMENT_ENABLED
      ? { roleEnforcementEnabled: bool(process.env.BEDROCK_METER_ROLE_ENFORCEMENT_ENABLED) }
      : {}),
    ...(process.env.BEDROCK_METER_ENABLE_DASHBOARD
      ? { enableDashboard: bool(process.env.BEDROCK_METER_ENABLE_DASHBOARD) }
      : {}),
    ...(process.env.BEDROCK_METER_CREATE_REGION_GUARDRAIL_POLICY
      ? { createRegionGuardrailPolicy: bool(process.env.BEDROCK_METER_CREATE_REGION_GUARDRAIL_POLICY) }
      : {}),
  },
});
