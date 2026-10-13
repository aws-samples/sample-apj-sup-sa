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
  readonly enableTelemetry: boolean;
  readonly telemetryResourceAttributes: Record<string, string>;
  readonly collectorImage: string;
  readonly collectorDesiredCount: number;
}

/** OTLP/HTTP port the ALB exposes for the gateway's telemetry relay to the collector. */
export const OTLP_LISTENER_PORT = 4318;
/** ADOT collector health_check extension port (ALB target-group health check). */
export const COLLECTOR_HEALTH_PORT = 13133;

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
  // telemetry.resource_attributes needs gateway >= 2.1.281.
  claudeVersion: "2.1.285",
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
  createVpcEndpoints: true,
  // Relay Claude Code OTLP metrics through an ADOT collector into the CloudWatch
  // OTLP endpoint, which populates CloudWatch Coding Agent Insights.
  enableTelemetry: true,
  // Fixed OTel resource attributes stamped on every session's telemetry. The
  // dashboards slice by organization, department, team.id and cost_center.
  telemetryResourceAttributes: { organization: "example-org" },
  // Pin by digest (image@sha256:...) for production.
  collectorImage: "public.ecr.aws/aws-observability/aws-otel-collector:v0.50.0",
  collectorDesiredCount: 2
};

const RESERVED_ATTRIBUTE_PREFIXES = ["user.", "enduser.", "identity."];
const RESERVED_ATTRIBUTES = [
  "service.name", "service.version", "claude.deployment_mode", "host.arch", "os.type", "os.version", "wsl.version"
];

/**
 * Enforces the gateway's telemetry.resource_attributes rules at synth time, so a
 * bad label fails `cdk synth` instead of the gateway boot.
 */
export function validateResourceAttributes(attributes: Record<string, string>): void {
  for (const [name, value] of Object.entries(attributes)) {
    const lower = name.toLowerCase();
    if (!/^[A-Za-z0-9._-]+$/.test(name)) {
      throw new Error(`telemetryResourceAttributes: invalid name "${name}" (letters, digits, '.', '_', '-' only)`);
    }
    if (RESERVED_ATTRIBUTE_PREFIXES.some((p) => lower.startsWith(p)) || RESERVED_ATTRIBUTES.includes(lower)) {
      throw new Error(`telemetryResourceAttributes: "${name}" is reserved by the gateway`);
    }
    if (typeof value !== "string" || value.length === 0 || !/^[\x21-\x7e]+$/.test(value) || /[,;=\\"%]/.test(value)) {
      throw new Error(`telemetryResourceAttributes: invalid value for "${name}" (non-empty printable ASCII, no spaces or , ; = \\ " %)`);
    }
    if (encodeURIComponent(value).length > 255) {
      throw new Error(`telemetryResourceAttributes: value for "${name}" exceeds 255 characters once percent-encoded`);
    }
  }
}

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

  const readRecord = (key: ConfigKeys<Record<string, string>>): Record<string, string> => {
    const value = app.node.tryGetContext(key);
    if (value && typeof value === "object" && !Array.isArray(value)) {
      return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, String(v)]));
    }
    if (typeof value === "string") {
      // "organization=acme,department=eng" (for `cdk -c key=value`)
      return Object.fromEntries(
        value.split(",").map((pair) => pair.trim()).filter((pair) => pair.length > 0).map((pair) => {
          const i = pair.indexOf("=");
          return [pair.slice(0, i).trim(), pair.slice(i + 1).trim()];
        })
      );
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
    createVpcEndpoints: readBoolean("createVpcEndpoints"),
    enableTelemetry: readBoolean("enableTelemetry"),
    telemetryResourceAttributes: readRecord("telemetryResourceAttributes"),
    collectorImage: readString("collectorImage"),
    collectorDesiredCount: readNumber("collectorDesiredCount")
  };
}
