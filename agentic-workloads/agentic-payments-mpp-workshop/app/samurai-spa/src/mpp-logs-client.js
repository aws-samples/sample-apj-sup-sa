/**
 * Direct DynamoDB reader for the MPP event log table.
 *
 * Samurai's AgentCore Runtime writes MPP protocol steps to a DynamoDB table.
 * The SPA (via Cognito Identity Pool creds) has dynamodb:Query on that
 * table scoped to the signed-in user's session rows. Adaptive polling
 * (100ms in-flight, 1s idle) mimics the prior behaviour.
 */
import { DynamoDBClient } from '@aws-sdk/client-dynamodb'
import { DynamoDBDocumentClient, QueryCommand } from '@aws-sdk/lib-dynamodb'
import { fetchAuthSession } from 'aws-amplify/auth'
import { AWS_CONFIG } from './amplify-config.js'

async function buildDocClient() {
  const session = await fetchAuthSession()
  const credentials = session.credentials
  if (!credentials) throw new Error('No AWS credentials — please sign in.')
  const ddb = new DynamoDBClient({ region: AWS_CONFIG.region, credentials })
  return DynamoDBDocumentClient.from(ddb)
}

export async function fetchMppLogs(sessionId, sinceSeq = 0) {
  const table = AWS_CONFIG.mppLogsTable
  if (!table) return { items: [], lastSeq: sinceSeq }
  const client = await buildDocClient()
  const resp = await client.send(new QueryCommand({
    TableName: table,
    KeyConditionExpression: 'sessionId = :s AND seq > :seq',
    ExpressionAttributeValues: { ':s': sessionId, ':seq': sinceSeq },
    ScanIndexForward: true,
    Limit: 200,
  }))
  const items = resp.Items ?? []
  const lastSeq = items.length ? items[items.length - 1].seq : sinceSeq
  return { items, lastSeq }
}
