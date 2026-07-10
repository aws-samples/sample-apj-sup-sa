import * as cdk from "aws-cdk-lib";

export const GATEWAY_CONTAINER_PORT = 8080;

export interface GatewayConfig {
  readonly gatewayHost: string;
  readonly hostedZoneName: string;
  readonly allowedClientCidrs: string[];
  readonly allowedEmailDomains: string[];
  readonly claudeVersion: string;
  readonly cognitoDomainPrefix: string;
  readonly bedrockRegion: string;
  readonly awsAccount: string;
  readonly awsRegion: string;
  readonly databaseName: string;
  readonly desiredCount: number;
  readonly maxAzs: number;
  readonly natGateways: number;
}

// Placeholder defaults — set your real deployment values in cdk.context.json
// (or via `cdk -c key=value`); context always overrides these.
export const defaultGatewayConfig: GatewayConfig = {
  gatewayHost: "claude-gateway.corp.example.com",
  hostedZoneName: "corp.example.com",
  allowedClientCidrs: ["10.0.0.0/8"],
  allowedEmailDomains: ["corp.example.com"],
  claudeVersion: "2.1.195",
  cognitoDomainPrefix: "claude-gateway-example",
  bedrockRegion: "us-east-1",
  awsAccount: "111122223333",
  awsRegion: "us-east-1",
  databaseName: "claude_gateway",
  desiredCount: 2,
  maxAzs: 2,
  natGateways: 1
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
    claudeVersion: readString("claudeVersion"),
    cognitoDomainPrefix: readString("cognitoDomainPrefix"),
    bedrockRegion: readString("bedrockRegion"),
    awsAccount: readString("awsAccount"),
    awsRegion: readString("awsRegion"),
    databaseName: readString("databaseName"),
    desiredCount: readNumber("desiredCount"),
    maxAzs: readNumber("maxAzs"),
    natGateways: readNumber("natGateways")
  };
}
