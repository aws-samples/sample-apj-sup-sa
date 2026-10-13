const { execFileSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");
const YAML = require("yaml");

const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "claude-gateway-config-"));
const configPath = path.join(tmpDir, "gateway.yaml");

const rendered = execFileSync(
  path.join(__dirname, "..", "docker", "render-gateway-config.sh"),
  ["--render-only"],
  {
    encoding: "utf8",
    env: {
      ...process.env,
      BEDROCK_REGION: "us-east-1",
      GATEWAY_CONFIG_PATH: configPath,
      GATEWAY_DB_HOST: "gateway-db.example.internal",
      GATEWAY_DB_NAME: "claude_gateway",
      GATEWAY_DB_PORT: "5432",
      GATEWAY_AVAILABLE_MODELS: "claude-opus-4-8,claude-sonnet-5",
      GATEWAY_PUBLIC_URL: "https://claude-gateway.corp.example.com",
      OIDC_ALLOWED_EMAIL_DOMAINS: "corp.example.com",
      OIDC_CLIENT_ID: "example-client-id",
      OIDC_ISSUER: "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_example",
      TELEMETRY_FORWARD_URL: "https://claude-gateway.corp.example.com:4318",
      TELEMETRY_RESOURCE_ATTRIBUTES: "organization=example-org,cost_center=1234"
    }
  }
);

const parsed = YAML.parse(rendered);

const requiredPaths = [
  ["listen", "public_url"],
  ["oidc", "issuer"],
  ["oidc", "client_secret"],
  ["session", "jwt_secret"],
  ["store", "postgres_url"],
  ["upstreams", 0, "provider"],
  ["managed", "policies", 0, "cli", "enforceAvailableModels"]
];

for (const parts of requiredPaths) {
  let cursor = parsed;
  for (const part of parts) {
    cursor = cursor && cursor[part];
  }
  if (!cursor) {
    throw new Error(`Rendered gateway config is missing ${parts.join(".")}`);
  }
}

const availableModels = parsed.managed.policies[0].cli.availableModels;
if (!Array.isArray(availableModels) || availableModels.length !== 2 || availableModels[0] !== "claude-opus-4-8") {
  throw new Error(`managed.policies availableModels did not render as a list: ${JSON.stringify(availableModels)}`);
}

const destination = parsed.telemetry && parsed.telemetry.forward_to && parsed.telemetry.forward_to[0];
if (!destination || destination.url !== "https://claude-gateway.corp.example.com:4318" ||
    destination.metrics !== true || destination.logs !== false || destination.traces !== false) {
  throw new Error(`telemetry.forward_to did not render as a metrics-only destination: ${JSON.stringify(destination)}`);
}
const attributes = parsed.telemetry.resource_attributes;
if (!attributes || attributes.organization !== "example-org" || attributes.cost_center !== "1234") {
  throw new Error(`telemetry.resource_attributes did not render as string values: ${JSON.stringify(attributes)}`);
}

console.log("Rendered gateway.yaml is valid YAML.");
