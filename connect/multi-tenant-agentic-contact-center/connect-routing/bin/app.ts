#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import { ConnectRoutingStack } from "../lib/routing-stack";

const app = new cdk.App();

// Opt-in routing module (case-owner reply routing + after-hours backlog). Every
// instance-specific value is DISCOVERED by deploy.sh from the upstream stack
// outputs and passed with -c. Region resolves from the AWS session, defaulting
// to us-west-2; override with `-c region=<region>` or AWS_REGION.
const region =
  app.node.tryGetContext("region") ||
  process.env.CDK_DEFAULT_REGION ||
  process.env.AWS_REGION ||
  "us-west-2";
const envName = ((app.node.tryGetContext("envName") as string) ?? "").trim();

new ConnectRoutingStack(app, `AnyCompanyPayConnectRoutingStack${envName ? `-${envName}` : ""}`, {
  env: { account: process.env.CDK_DEFAULT_ACCOUNT, region },
  description:
    "AnyCompanyPay Connect routing (opt-in): case-owner reply routing + after-hours case/task backlog. Additive; existing flows untouched.",
});
