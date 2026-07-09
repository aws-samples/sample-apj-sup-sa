import * as cdk from "aws-cdk-lib";

export const GATEWAY_CONTAINER_PORT = 8080;

export interface GatewayConfig {
  readonly gatewayHost: string;
  readonly hostedZoneName: string;
  readonly allowedClientCidrs: string[];
  readonly allowedEmailDomains: string[];
  readonly availableModels: string[];
  readonly claudeVersion: string;
  readonly cognitoDomainPrefix: string;
  readonly bedrockRegion: string;
  readonly awsAccount: string;
  readonly awsRegion: string;
  readonly databaseName: string;
  readonly desiredCount: number;
  readonly maxAzs: number;
  readonly natGateways: number;
  readonly createVpcEndpoints: boolean;
}

// Placeholder defaults — set your real deployment values in cdk.context.json
// (or via `cdk -c key=value`); context always overrides these.
export const defaultGatewayConfig: GatewayConfig = {
  gatewayHost: "claude-gateway.corp.example.com",
  hostedZoneName: "corp.example.com",
  allowedClientCidrs: ["10.0.0.0/8"],
  allowedEmailDomains: ["corp.example.com"],
  // Model allowlist rendered into managed.policies — keep in sync with the
  // models you have actually enabled access for in Bedrock, or the picker
  // offers models that fail with AccessDenied mid-conversation.
  availableModels: ["claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5", "claude-fable-5"],
  claudeVersion: "2.1.195",
  cognitoDomainPrefix: "claude-gateway-example",
  bedrockRegion: "us-east-1",
  awsAccount: "111122223333",
  awsRegion: "us-east-1",
  databaseName: "claude_gateway",
  desiredCount: 2,
  maxAzs: 2,
  natGateways: 1,
  // Interface endpoints cost ~$0.01/AZ/hour each; set false to opt out and
  // send AWS-service traffic through the NAT gateway instead.
  createVpcEndpoints: true
};

type ConfigKeys<V> = {
  [K in keyof GatewayConfig]: GatewayConfig[K] extends V ? K : never;
}[keyof GatewayConfig];

export function loadGatewayConfig(app: cdk.App): GatewayConfig {
  const readString = (key: ConfigKeys<string>): string => {
    const value = app.node.tryGetContext(key);
    return typeof value === "string" ? value : defaultGatewayConfig[key];
  };

  const readNumber = (key: ConfigKeys<number>): number => {
    const value = app.node.tryGetContext(key);
    if (typeof value === "number") {
      return value;
    }
    if (typeof value === "string" && value.trim() !== "") {
      return Number(value);
    }
    return defaultGatewayConfig[key];
  };

  const readBoolean = (key: ConfigKeys<boolean>): boolean => {
    const value = app.node.tryGetContext(key);
    if (typeof value === "boolean") {
      return value;
    }
    if (typeof value === "string") {
      return value.trim().toLowerCase() === "true";
    }
    return defaultGatewayConfig[key];
  };

  const readStringArray = (key: ConfigKeys<string[]>): string[] => {
    const value = app.node.tryGetContext(key);
    if (Array.isArray(value)) {
      return value.map((item) => String(item));
    }
    if (typeof value === "string") {
      return value
        .split(",")
        .map((item) => item.trim())
        .filter((item) => item.length > 0);
    }
    return defaultGatewayConfig[key];
  };

  return {
    gatewayHost: readString("gatewayHost"),
    hostedZoneName: readString("hostedZoneName"),
    allowedClientCidrs: readStringArray("allowedClientCidrs"),
    allowedEmailDomains: readStringArray("allowedEmailDomains"),
    availableModels: readStringArray("availableModels"),
    claudeVersion: readString("claudeVersion"),
    cognitoDomainPrefix: readString("cognitoDomainPrefix"),
    bedrockRegion: readString("bedrockRegion"),
    awsAccount: readString("awsAccount"),
    awsRegion: readString("awsRegion"),
    databaseName: readString("databaseName"),
    desiredCount: readNumber("desiredCount"),
    maxAzs: readNumber("maxAzs"),
    natGateways: readNumber("natGateways"),
    createVpcEndpoints: readBoolean("createVpcEndpoints")
  };
}
