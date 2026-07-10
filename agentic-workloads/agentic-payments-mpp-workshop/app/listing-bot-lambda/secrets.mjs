/**
 * Lazy secret fetching with a small TTL cache so TODO 1 (replacing the
 * PLACEHOLDER Stripe key in Secrets Manager) takes effect quickly without
 * a Lambda redeploy.
 */
import { SecretsManagerClient, GetSecretValueCommand } from '@aws-sdk/client-secrets-manager'

const sm = new SecretsManagerClient({})
const cache = new Map()
const TTL_MS = 30_000

export async function getSecret(arn) {
  if (!arn) return null
  const entry = cache.get(arn)
  const now = Date.now()
  if (entry && now - entry.at < TTL_MS) return entry.value

  const resp = await sm.send(new GetSecretValueCommand({ SecretId: arn }))
  const value = resp.SecretString ?? null
  cache.set(arn, { value, at: now })
  return value
}

export function clearSecretsCache() {
  cache.clear()
}
