// WebSocket $connect for the mock Level3 A2A endpoint (runs after the authorizer).
//   - Echoes the A2A-Extensions header to confirm the Connect extension is active.
//   - Keeps the W3C traceparent sent on the upgrade, so the agent's TRACING_SPANs
//     adopt Connect's trace id and parent span (required trace data).
import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import { DynamoDBDocumentClient, PutCommand } from "@aws-sdk/lib-dynamodb";

const EXT = "https://docs.aws.amazon.com/connect/a2a/ext/v1";
const TABLE = process.env.TABLE_NAME!;
const ddb = DynamoDBDocumentClient.from(new DynamoDBClient({}));

export const handler = async (event: {
  headers?: Record<string, string>;
  requestContext: { connectionId: string };
}) => {
  const h = Object.fromEntries(Object.entries(event.headers ?? {}).map(([k, v]) => [k.toLowerCase(), v]));
  console.log(
    JSON.stringify({
      msg: "A2A upgrade",
      connectionId: event.requestContext.connectionId,
      extensions: h["a2a-extensions"] ?? null,
      traceparent: h["traceparent"] ?? null,
    })
  );
  await ddb.send(
    new PutCommand({
      TableName: TABLE,
      Item: {
        pk: `conn#${event.requestContext.connectionId}`,
        traceparent: h["traceparent"] ?? "",
        tracestate: h["tracestate"] ?? "",
        ttl: Math.floor(Date.now() / 1000) + 3600,
      },
    })
  );
  return { statusCode: 200, headers: { "A2A-Extensions": EXT } };
};
