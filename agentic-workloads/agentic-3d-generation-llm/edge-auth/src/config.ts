// Lambda@Edge functions can't have environment variables, so their settings live in
// Secrets Manager (us-east-1) under fixed names that the CDK stack creates.
import { GetSecretValueCommand, SecretsManagerClient } from '@aws-sdk/client-secrets-manager';

export const CONFIG_SECRET_ID = '3d-generation-llm/edge-auth/config';
export const NONCE_SECRET_ID = '3d-generation-llm/edge-auth/nonce-signing';

export interface EdgeAuthConfig {
  region: string;
  userPoolId: string;
  clientId: string;
  clientSecret: string;
  domain: string;
}

const sm = new SecretsManagerClient({ region: 'us-east-1' });

export async function readSecret(secretId: string): Promise<string> {
  const res = await sm.send(new GetSecretValueCommand({ SecretId: secretId }));
  if (!res.SecretString) throw new Error(`Secret ${secretId} is empty`);
  return res.SecretString;
}

export async function loadConfig(): Promise<EdgeAuthConfig> {
  const parsed = JSON.parse(await readSecret(CONFIG_SECRET_ID)) as EdgeAuthConfig;
  for (const key of ['region', 'userPoolId', 'clientId', 'clientSecret', 'domain'] as const) {
    if (!parsed[key]) throw new Error(`Edge auth config is missing ${key}`);
  }
  return parsed;
}

/** Cache a cold-start promise, but retry after a failure instead of caching the error. */
export function memo<T>(load: () => Promise<T>): () => Promise<T> {
  let cached: Promise<T> | undefined;
  return () => {
    if (!cached) {
      cached = load().catch((err) => {
        cached = undefined;
        throw err;
      });
    }
    return cached;
  };
}
