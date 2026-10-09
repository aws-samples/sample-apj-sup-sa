#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import { ConnectScreenShareStack } from "../lib/screenshare-stack";

const app = new cdk.App();

// Opt-in live screen sharing. Every instance-specific value is DISCOVERED by
// deploy.sh from the upstream stack outputs and passed with -c. Region resolves
// from the AWS session, defaulting to us-west-2; override with `-c region=<region>`.
const region =
  app.node.tryGetContext("region") ||
  process.env.CDK_DEFAULT_REGION ||
  process.env.AWS_REGION ||
  "us-west-2";
const envName = ((app.node.tryGetContext("envName") as string) ?? "").trim();

new ConnectScreenShareStack(app, `AnyCompanyPayConnectScreenShareStack${envName ? `-${envName}` : ""}`, {
  env: { account: process.env.CDK_DEFAULT_ACCOUNT, region },
  description:
    "AnyCompanyPay live screen sharing (opt-in): merchant web call + screen share to an agent. Additive; existing flows untouched.",
});
