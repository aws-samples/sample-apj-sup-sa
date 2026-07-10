/**
 * Writes MPP protocol events to DynamoDB so the SPA can poll and render the
 * debug panel. Each write increments a per-session sequence counter.
 *
 * Session context is set per-request in index.ts (via AgentCore's
 * X-Amzn-Bedrock-AgentCore-Runtime-Session-Id header).
 */
import { DynamoDBClient } from '@aws-sdk/client-dynamodb'
import { DynamoDBDocumentClient, PutCommand, QueryCommand } from '@aws-sdk/lib-dynamodb'

const ddb = DynamoDBDocumentClient.from(new DynamoDBClient({}))

let currentSessionId: string | null = null
const seqBySession = new Map<string, number>()

export function setSessionContext(sessionId: string) {
  currentSessionId = sessionId
  if (!seqBySession.has(sessionId)) seqBySession.set(sessionId, 0)
}

export async function logMppEvent(event: {
  type: 'request' | '402' | 'spt' | 'retry' | '200' | 'error'
  label: string
  detail?: unknown
}) {
  if (!currentSessionId) return
  const table = process.env.MPP_LOGS_TABLE
  if (!table) {
    console.warn('MPP_LOGS_TABLE not configured; dropping MPP event')
    return
  }

  // Bootstrap seq if this is a fresh cold-start hit
  if (!seqBySession.has(currentSessionId)) {
    const last = await fetchLastSeq(currentSessionId)
    seqBySession.set(currentSessionId, last)
  }
  const seq = (seqBySession.get(currentSessionId) ?? 0) + 1
  seqBySession.set(currentSessionId, seq)

  try {
    await ddb.send(new PutCommand({
      TableName: table,
      Item: {
        sessionId: currentSessionId,
        seq,
        type: event.type,
        label: event.label,
        detail: event.detail ?? null,
        ts: new Date().toISOString(),
        ttl: Math.floor(Date.now() / 1000) + 24 * 3600,
      },
    }))
  } catch (err) {
    console.warn('logMppEvent failed (non-fatal):', err)
  }
}

async function fetchLastSeq(sessionId: string): Promise<number> {
  const table = process.env.MPP_LOGS_TABLE
  if (!table) return 0
  try {
    const resp = await ddb.send(new QueryCommand({
      TableName: table,
      KeyConditionExpression: 'sessionId = :s',
      ExpressionAttributeValues: { ':s': sessionId },
      ScanIndexForward: false,
      Limit: 1,
    }))
    const last = resp.Items?.[0]
    return typeof last?.seq === 'number' ? last.seq : 0
  } catch {
    return 0
  }
}
