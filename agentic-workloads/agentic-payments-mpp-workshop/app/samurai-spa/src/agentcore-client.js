/**
 * Direct AgentCore Runtime invocation from the browser.
 *
 * Flow:
 *   1. User signs in via Amplify Auth (USER_SRP_AUTH).
 *   2. Amplify internally swaps the ID token for AWS credentials via the
 *      Cognito Identity Pool.
 *   3. We call bedrock-agentcore:InvokeAgentRuntime with those credentials.
 *      The SDK signs the request with SigV4.
 *
 * The response from AgentCore is an event stream (HTTP chunked body). We
 * decode it incrementally so the UI can render progress. MPP protocol
 * events are embedded as text chunks with a known prefix, or they come in
 * as the final JSON payload.
 */
import {
  BedrockAgentCoreClient,
  InvokeAgentRuntimeCommand,
} from '@aws-sdk/client-bedrock-agentcore'
import { fetchAuthSession } from 'aws-amplify/auth'
import { AWS_CONFIG, refreshRuntimeArn } from './amplify-config.js'

function makeSessionId() {
  // AgentCore requires runtimeSessionId >= 33 chars.
  const rand = () => Math.random().toString(36).slice(2, 12)
  return `sess_${Date.now().toString(36)}_${rand()}${rand()}`
}

export function getSessionId() {
  let id = sessionStorage.getItem('samurai-session')
  if (!id) {
    id = makeSessionId()
    sessionStorage.setItem('samurai-session', id)
  }
  return id
}

export function newSessionId() {
  const id = makeSessionId()
  sessionStorage.setItem('samurai-session', id)
  return id
}

async function buildClient() {
  const session = await fetchAuthSession()
  const credentials = session.credentials
  if (!credentials) throw new Error('No AWS credentials — please sign in.')
  return new BedrockAgentCoreClient({
    region: AWS_CONFIG.region,
    credentials,
  })
}

/**
 * Invoke the Samurai AgentCore Runtime. Returns an async iterator over text
 * chunks as they arrive from AgentCore.
 */
export async function* invokeAgentStream(prompt, sessionId) {
  if (!AWS_CONFIG.samuraiAgentRuntimeArn) {
    // Re-check: participant may have just deployed the runtime.
    await refreshRuntimeArn()
  }
  if (!AWS_CONFIG.samuraiAgentRuntimeArn) {
    throw new Error('Samurai AgentCore Runtime ARN not configured yet — have you finished the participant deploy?')
  }
  const client = await buildClient()
  const payload = new TextEncoder().encode(JSON.stringify({ prompt, sessionId }))
  const resp = await client.send(new InvokeAgentRuntimeCommand({
    agentRuntimeArn: AWS_CONFIG.samuraiAgentRuntimeArn,
    runtimeSessionId: sessionId,
    payload,
    contentType: 'application/json',
    accept: 'application/json',
  }))

  // Response body is either a ReadableStream (streaming) or a Uint8Array.
  const body = resp.response ?? resp.body
  if (!body) return

  const decoder = new TextDecoder()
  if (typeof body.transformToWebStream === 'function') {
    const stream = body.transformToWebStream()
    const reader = stream.getReader()
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      yield decoder.decode(value, { stream: true })
    }
    yield decoder.decode() // flush
    return
  }
  if (body[Symbol.asyncIterator]) {
    for await (const chunk of body) {
      yield decoder.decode(chunk, { stream: true })
    }
    return
  }
  if (body instanceof Uint8Array) {
    yield decoder.decode(body)
    return
  }
  if (typeof body === 'string') {
    yield body
  }
}

/**
 * Convenience: collect the whole streamed response into a single string.
 */
export async function invokeAgent(prompt, sessionId) {
  let buf = ''
  for await (const chunk of invokeAgentStream(prompt, sessionId)) {
    buf += chunk
  }
  try { return JSON.parse(buf) } catch { return { raw: buf } }
}
