#!/usr/bin/env node
import * as cdk from "aws-cdk-lib";
import { GatewayStack } from "../lib/gateway-stack";
import { loadGatewayConfig } from "../lib/config";

const app = new cdk.App();
const config = loadGatewayConfig(app);

new GatewayStack(app, "ClaudeAppsGatewayStack", {
  env: {
    account: config.awsAccount,
    region: config.awsRegion
  },
  config
});
