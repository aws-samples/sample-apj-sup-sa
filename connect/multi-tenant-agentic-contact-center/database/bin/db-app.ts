#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import { AuroraStack } from "../lib/aurora-stack";

const app = new cdk.App();

// Account comes from the caller's AWS session (CDK_DEFAULT_ACCOUNT). Region
// resolves from the session, falling back to a us-west-2 default; override with
// `-c region=<region>` or AWS_REGION at deploy time.
const region =
  app.node.tryGetContext("region") ||
  process.env.CDK_DEFAULT_REGION ||
  process.env.AWS_REGION ||
  "us-west-2";
new AuroraStack(app, "AnyCompanyPayAuroraStack", {
  env: { account: process.env.CDK_DEFAULT_ACCOUNT, region },
  description:
    "AnyCompanyPay — private Aurora PostgreSQL (db.t4g.large) with a seeded multi-tenant transactions table",
});
