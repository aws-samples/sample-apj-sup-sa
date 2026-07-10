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
      GATEWAY_PUBLIC_URL: "https://claude-gateway.corp.example.com",
      OIDC_ALLOWED_EMAIL_DOMAINS: "corp.example.com",
      OIDC_CLIENT_ID: "example-client-id",
      OIDC_ISSUER: "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_example"
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
  ["upstreams", 0, "provider"]
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

console.log("Rendered gateway.yaml is valid YAML.");
