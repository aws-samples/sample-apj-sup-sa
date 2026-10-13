#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import { ConnectA2aStack } from "../lib/a2a-stack";

const app = new cdk.App();

// Opt-in A2A demo (Connect -> external AI agent). Instance-specific values are
// DISCOVERED by deploy.sh from the upstream stack outputs and passed with -c.
const region =
  app.node.tryGetContext("region") ||
  process.env.CDK_DEFAULT_REGION ||
  process.env.AWS_REGION ||
  "us-west-2";
const envName = ((app.node.tryGetContext("envName") as string) ?? "").trim();

new ConnectA2aStack(app, `AnyCompanyPayConnectA2aStack${envName ? `-${envName}` : ""}`, {
  env: { account: process.env.CDK_DEFAULT_ACCOUNT, region },
  description:
    "AnyCompanyPay Connect A2A demo (opt-in): chat handed to an external AI agent (mock Level3) over the Connect A2A extension. Additive.",
});
