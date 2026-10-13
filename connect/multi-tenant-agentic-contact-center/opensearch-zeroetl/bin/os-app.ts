#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import { ZeroEtlStack } from "../lib/zeroetl-stack";

const app = new cdk.App();

// Wired to the Aurora module's outputs. Defaults below match the deployed
// AnyCompanyPayAuroraStack; override any of them with `-c key=value` (the deploy.sh
// discovers them automatically from the Aurora stack).
// Region resolves from the AWS session, defaulting to us-west-2; override with
// `-c region=<region>` or AWS_REGION. Account comes from the session.
const region =
  app.node.tryGetContext("region") ||
  process.env.CDK_DEFAULT_REGION ||
  process.env.AWS_REGION ||
  "us-west-2";
new ZeroEtlStack(app, "AnyCompanyPayZeroEtlStack", {
  env: { account: process.env.CDK_DEFAULT_ACCOUNT, region },
  description:
    "AnyCompanyPay zero-ETL: Aurora PostgreSQL -> private OpenSearch Serverless via OpenSearch Ingestion",
});
