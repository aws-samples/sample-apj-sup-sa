/**
 * Runtime config loader.
 *
 * The SPA bundle is identical across every workshop deployment. All
 * stack-specific values — Cognito Pool IDs, Identity Pool ID, region, the
 * MPP logs DynamoDB table name, and the Samurai AgentCore Runtime ARN —
 * are served from `/config.json` on the same CloudFront origin. The
 * spa_deployer custom resource writes this file at stack-create time, and
 * `participant-deploy.sh` rewrites it once the participant deploys their
 * AgentCore Runtime.
 *
 * main.jsx awaits loadRuntimeConfig() before rendering the app.
 */
import { Amplify } from 'aws-amplify'

export const AWS_CONFIG = {
  region: '',
  userPoolId: '',
  userPoolClientId: '',
  identityPoolId: '',
  mppLogsTable: '',
  samuraiAgentRuntimeArn: '',
}

let loaded = false

/** Re-fetch only the volatile samuraiAgentRuntimeArn field. Cognito config
 * never changes within a deployed stack. */
export async function refreshRuntimeArn() {
  try {
    const resp = await fetch(`/config.json?t=${Date.now()}`)
    if (!resp.ok) return AWS_CONFIG
    const cfg = await resp.json()
    if (cfg.samuraiAgentRuntimeArn) {
      AWS_CONFIG.samuraiAgentRuntimeArn = cfg.samuraiAgentRuntimeArn
    }
  } catch { /* keep current value */ }
  return AWS_CONFIG
}

export async function loadRuntimeConfig() {
  if (loaded) return AWS_CONFIG
  const resp = await fetch(`/config.json?t=${Date.now()}`)
  if (!resp.ok) throw new Error(`/config.json: HTTP ${resp.status}`)
  const cfg = await resp.json()
  Object.assign(AWS_CONFIG, {
    region: cfg.region || 'us-east-1',
    userPoolId: cfg.userPoolId || '',
    userPoolClientId: cfg.userPoolClientId || '',
    identityPoolId: cfg.identityPoolId || '',
    mppLogsTable: cfg.mppLogsTable || '',
    samuraiAgentRuntimeArn: cfg.samuraiAgentRuntimeArn || '',
  })

  if (AWS_CONFIG.userPoolId && AWS_CONFIG.userPoolClientId && AWS_CONFIG.identityPoolId) {
    Amplify.configure({
      Auth: {
        Cognito: {
          userPoolId: AWS_CONFIG.userPoolId,
          userPoolClientId: AWS_CONFIG.userPoolClientId,
          identityPoolId: AWS_CONFIG.identityPoolId,
          // User Pool uses UsernameAttributes: [email] — sign in with email.
          loginWith: { email: true },
        },
      },
    })
  } else {
    console.warn('Cognito config missing in /config.json; SPA will not authenticate.')
  }

  loaded = true
  return AWS_CONFIG
}
