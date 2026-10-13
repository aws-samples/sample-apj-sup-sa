// WebSocket $connect authorizer for the mock Level3 A2A endpoint.
// Connect sends the partner API key as "Authorization: Bearer <apiKey>" on the
// WebSocket upgrade. The key is the Secrets Manager secret the customer
// registered on the AppIntegrations A2A_SERVER application (in this demo the
// "partner" and the customer share one account, so they read the same secret).
import { timingSafeEqual } from "node:crypto";
import { GetSecretValueCommand, SecretsManagerClient } from "@aws-sdk/client-secrets-manager";

const SECRET_ARN = process.env.API_KEY_SECRET_ARN!;
const sm = new SecretsManagerClient({});
let cached: { value: string; at: number } | null = null;

async function apiKey(): Promise<string> {
  // Short cache so a rotated key is picked up within minutes.
  if (cached && Date.now() - cached.at < 5 * 60_000) return cached.value;
  const r = await sm.send(new GetSecretValueCommand({ SecretId: SECRET_ARN }));
  cached = { value: r.SecretString ?? "", at: Date.now() };
  return cached.value;
}

const same = (a: string, b: string) => {
  const x = Buffer.from(a);
  const y = Buffer.from(b);
  return x.length === y.length && timingSafeEqual(x, y);
};

export const handler = async (event: { methodArn: string; headers?: Record<string, string> }) => {
  const header = Object.entries(event.headers ?? {}).find(([k]) => k.toLowerCase() === "authorization")?.[1] ?? "";
  const token = header.replace(/^Bearer\s+/i, "");
  const ok = Boolean(token) && same(token, await apiKey());
  if (!ok) console.warn("A2A upgrade rejected: missing or wrong bearer token");
  return {
    principalId: "amazon-connect",
    policyDocument: {
      Version: "2012-10-17",
      Statement: [{ Action: "execute-api:Invoke", Effect: ok ? "Allow" : "Deny", Resource: event.methodArn }],
    },
  };
};
