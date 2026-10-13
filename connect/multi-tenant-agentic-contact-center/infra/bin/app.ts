#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import { AnyCompanyPayAppStack } from "../lib/app-stack";
import { AnyCompanyPayConnectStack } from "../lib/connect-stack";

const app = new cdk.App();

// OPTIONAL environment discriminator. Empty by default → clean common names with
// NO env suffix (AnyCompanyPayAppStack, AnyCompanyPayConnectStack). Pass `-c envName=foo` ONLY
// if you want a second, independent copy in the SAME account+region (names become
// AnyCompanyPayAppStack-foo, etc.). Account comes from the AWS session/CLI
// (CDK_DEFAULT_ACCOUNT). Region resolves from the session too, falling back to a
// us-west-2 default that you can override — so the same app deploys to any
// account/region unchanged.
const envName = ((app.node.tryGetContext("envName") as string) ?? "").trim();
const sfx = envName ? `-${envName}` : "";
const label = envName || "default";

const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  // Region resolves from the AWS session first (CDK_DEFAULT_REGION / AWS_REGION);
  // if nothing is set it defaults to us-west-2. Override at deploy time with either
  // env var or `-c region=<region>`.
  region:
    app.node.tryGetContext("region") ||
    process.env.CDK_DEFAULT_REGION ||
    process.env.AWS_REGION ||
    "us-west-2",
};

// Module 1 — web app + platform, no Amazon Connect dependency.
const appStack = new AnyCompanyPayAppStack(app, `AnyCompanyPayAppStack${sfx}`, {
  env,
  envName,
  description: `AnyCompanyPay (${label}) — SPA on ECS Fargate behind an internal ALB, CloudFront (VPC origin), Cognito auth. Runtime config via SSM.`,
});

// Module 2 — Amazon Connect integration (Cases, Customer Profiles, CCP).
const connectStack = new AnyCompanyPayConnectStack(app, `AnyCompanyPayConnectStack${sfx}`, {
  env,
  envName,
  userPoolId: appStack.userPool.userPoolId,
  userPoolClientId: appStack.userPoolClient.userPoolClientId,
  distUrl: appStack.distUrl,
  clusterName: appStack.cluster.clusterName,
  serviceName: appStack.service.serviceName,
  runtimeConfigParamName: appStack.runtimeConfigParamName,
  runtimeConfigParamArn: appStack.runtimeConfigParamArn,
  description: `AnyCompanyPay (${label}) — Amazon Connect integration (Cases, Customer Profiles, CCP). Depends on the app stack.`,
});
connectStack.addDependency(appStack);
