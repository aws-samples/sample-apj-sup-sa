// ============================================================================
// Mock "Level3" external AI agent — Amazon Connect A2A protocol server (chat)
// ============================================================================
// Stands in for a third-party AI agent that Connect hands a conversation to
// (agent-to-agent collaboration, handoffAgentConfiguration). It implements the
// Connect A2A extension v1 over WebSocket, the parts a TEXT session needs:
//
//   INIT_SESSION        -> INIT_SESSION_RESPONSE (statusCode 200, extension echoed)
//   CHANNEL_STATE READY -> output is allowed from now on
//   text turn           -> task -> TEXT_RESPONSE_CHUNK -> TRACING_SPAN -> terminal
//                          statusUpdate (with FINISH when the conversation ends)
//   DTMF_INPUT_COMPLETE -> treated as the turn's text
//   FRAGMENT            -> buffered and reassembled
//   ERROR (from Connect)-> logged (informational)
//
// The "AI" is deliberately rule-based so the demo is deterministic:
//   "agent" / "human" / "person" / "escalate"  -> FINISH ESCALATE
//   "bye" / "that's all" / "no thanks"         -> FINISH COMPLETE
//   "simulate failure"                         -> TASK_STATE_FAILED (tests the fallback)
//   anything else                              -> a short answer, conversation continues
// Before it finishes it writes its result (outcome, intent, summary, escalation
// reason) to the Connect contact with UpdateContactAttributes, so the human agent
// sees it (design R4) and the flow can route on it. A real partner would use a
// cross-account role for this call.
//
// Chat opens a NEW WebSocket connection for each customer message with the same
// contextId and re-sends INIT_SESSION, so per-session state lives in DynamoDB.
// ----------------------------------------------------------------------------
import { randomBytes, randomUUID } from "node:crypto";
import { ApiGatewayManagementApiClient, PostToConnectionCommand } from "@aws-sdk/client-apigatewaymanagementapi";
import { ConnectClient, UpdateContactAttributesCommand } from "@aws-sdk/client-connect";
import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import { DynamoDBDocumentClient, GetCommand, PutCommand, UpdateCommand } from "@aws-sdk/lib-dynamodb";

const EXT = "https://docs.aws.amazon.com/connect/a2a/ext/v1";
const EVENT_TYPE = `${EXT}/eventType`;
const TABLE = process.env.TABLE_NAME!;
const AGENT_NAME = process.env.AGENT_NAME || "level3-mock";
const LOG_FRAMES = process.env.LOG_FRAMES === "true";
const TTL_SECONDS = 24 * 3600;

const ddb = DynamoDBDocumentClient.from(new DynamoDBClient({}));
const connect = new ConnectClient({});

/* eslint-disable @typescript-eslint/no-explicit-any */
type Json = Record<string, any>;
type Part = { text?: string; data?: Json; raw?: string; mediaType?: string };

interface WsEvent {
  body?: string;
  isBase64Encoded?: boolean;
  requestContext: { connectionId: string; domainName: string; stage: string };
}

interface Session {
  pk: string;
  contactArn?: string;
  instanceArn?: string;
  supportedFinishTypes?: string[];
  tracing?: boolean;
  ready?: boolean;
  turns?: number;
  finished?: boolean;
}

const nowNs = () => (BigInt(Date.now()) * 1_000_000n).toString();
const hex = (bytes: number) => randomBytes(bytes).toString("hex");
const ttl = () => Math.floor(Date.now() / 1000) + TTL_SECONDS;
const log = (msg: string, data?: unknown) =>
  console.log(JSON.stringify({ msg, ...(data === undefined ? {} : { data }) }).slice(0, 4000));

// ---- transport ---------------------------------------------------------------
function sender(ev: WsEvent) {
  const api = new ApiGatewayManagementApiClient({
    endpoint: `https://${ev.requestContext.domainName}/${ev.requestContext.stage}`,
  });
  return async (frame: Json) => {
    if (LOG_FRAMES) log("out", frame);
    await api.send(
      new PostToConnectionCommand({ ConnectionId: ev.requestContext.connectionId, Data: Buffer.from(JSON.stringify(frame)) })
    );
  };
}

const result = (id: string | number, value: Json) => ({ jsonrpc: "2.0", id, result: value });
const rpcError = (id: string | number | null, code: number, message: string, reason: string) => ({
  jsonrpc: "2.0",
  id,
  error: {
    code,
    message,
    data: [{ "@type": "type.googleapis.com/google.rpc.ErrorInfo", reason, domain: "a2a-protocol.org" }],
  },
});

// ---- session state -----------------------------------------------------------
async function getSession(contextId: string): Promise<Session> {
  const r = await ddb.send(new GetCommand({ TableName: TABLE, Key: { pk: `ctx#${contextId}` } }));
  return (r.Item as Session) ?? { pk: `ctx#${contextId}` };
}
async function putSession(s: Session) {
  await ddb.send(new PutCommand({ TableName: TABLE, Item: { ...s, ttl: ttl() } }));
}
async function traceparentFor(connectionId: string): Promise<string> {
  const r = await ddb.send(new GetCommand({ TableName: TABLE, Key: { pk: `conn#${connectionId}` } }));
  return (r.Item?.traceparent as string) ?? "";
}

// Inbound FRAGMENTs: buffer by fragmentId; return the payload once complete.
async function reassemble(f: Json): Promise<Json | null> {
  const key = { pk: `frag#${f.fragmentId}` };
  // Create the parts map on the first fragment, then set this part in it.
  await ddb.send(
    new UpdateCommand({
      TableName: TABLE,
      Key: key,
      UpdateExpression: "SET #p = if_not_exists(#p, :empty), #ttl = :ttl",
      ExpressionAttributeNames: { "#p": "parts", "#ttl": "ttl" },
      ExpressionAttributeValues: { ":empty": {}, ":ttl": ttl() },
    })
  );
  const upd = await ddb.send(
    new UpdateCommand({
      TableName: TABLE,
      Key: key,
      UpdateExpression: "SET #p.#n = :v",
      ExpressionAttributeNames: { "#p": "parts", "#n": String(f.partNumber) },
      ExpressionAttributeValues: { ":v": f.payload },
      ReturnValues: "ALL_NEW",
    })
  );
  const parts = (upd.Attributes?.parts ?? {}) as Record<string, string>;
  if (Object.keys(parts).length < Number(f.totalParts)) return null;
  const b64 = Array.from({ length: Number(f.totalParts) }, (_, i) => parts[String(i)]).join("");
  return JSON.parse(Buffer.from(b64, "base64").toString("utf8"));
}

// ---- the mock "AI" -------------------------------------------------------------
interface Reply {
  text: string;
  finish?: { type: "COMPLETE" | "ESCALATE"; reason: string };
  intent: string;
}

function decide(input: string, turn: number): Reply {
  const t = input.trim().toLowerCase();
  const hello = turn === 0 ? "Hi, this is the Level3 assistant (demo). " : "";
  if (t === "<empty_user_input>")
    return { text: "Are you still there? Tell me what you need help with, or ask for an agent.", intent: "silence" };
  if (/\b(agent|human|person|someone|escalate|representative)\b/.test(t))
    return {
      text: `${hello}No problem. I'm handing you over to a human agent now, with a summary of our chat.`,
      finish: { type: "ESCALATE", reason: "Customer asked for a human agent" },
      intent: "talk_to_agent",
    };
  if (/\b(bye|goodbye|that'?s all|no thanks|nothing else|all good)\b/.test(t))
    return {
      text: "Glad I could help. Goodbye!",
      finish: { type: "COMPLETE", reason: "Customer finished" },
      intent: "end_conversation",
    };
  if (/\brefund|chargeback|dispute\b/.test(t))
    return {
      text: `${hello}Refunds and disputes need a person to review them. Say "agent" and I'll hand you over, or ask me something else.`,
      intent: "refund_or_dispute",
    };
  if (/\bpayout|settlement|transfer\b/.test(t))
    return {
      text: `${hello}Payouts are usually settled within 2 business days. Anything else? Say "agent" for a person.`,
      intent: "payout_question",
    };
  return {
    text: `${hello}I received: "${input.slice(0, 200)}". I'm a demo partner agent: ask about refunds or payouts, say "agent" for a person, or "bye" to finish.`,
    intent: "general_question",
  };
}

async function writeContactContext(s: Session, reply: Reply, input: string) {
  // contactArn: arn:aws:connect:<region>:<acct>:instance/<instanceId>/contact/<contactId>
  const m = /instance\/([^/]+)\/contact\/([^/]+)$/.exec(s.contactArn ?? "");
  if (!m) return;
  try {
    await connect.send(
      new UpdateContactAttributesCommand({
        InstanceId: m[1],
        InitialContactId: m[2],
        Attributes: {
          l3_intent: reply.intent,
          l3_summary: `Customer said: "${input.slice(0, 200)}" (${(s.turns ?? 0) + 1} turn(s) with Level3).`,
          l3_outcome: reply.finish?.type ?? "IN_PROGRESS",
          l3_escalation_reason: reply.finish?.type === "ESCALATE" ? reply.finish.reason : "",
        },
      })
    );
  } catch (err) {
    console.warn("UpdateContactAttributes failed (the hand-off continues without it)", err);
  }
}

function traceSpan(opts: {
  traceparent: string;
  start: string;
  end: string;
  input: string;
  output: string;
  failed?: string;
}) {
  // W3C traceparent: 00-<traceId 32 hex>-<parentSpanId 16 hex>-<flags>. Not always
  // sent (not seen on chat upgrades): then use a fresh trace id and no parent.
  const [, traceId, parentSpanId] = opts.traceparent.split("-");
  const msg = (role: string, participant: string, content: string, at: string) => ({
    name: "aws.connect.span.message",
    timeUnixNano: at,
    attributes: [
      { key: "aws.connect.span.message.role", value: { stringValue: role } },
      { key: "aws.connect.span.message.type", value: { stringValue: "TEXT_MESSAGE" } },
      { key: "aws.connect.span.message.participant", value: { stringValue: participant } },
      { key: "aws.connect.span.message.content", value: { stringValue: content } },
    ],
  });
  return {
    otlp: {
      resourceSpans: [
        {
          resource: { attributes: [{ key: "service.name", value: { stringValue: AGENT_NAME } }] },
          scopeSpans: [
            {
              scope: { name: "amazon.connect.ai-agent", version: "1.0.0" },
              spans: [
                {
                  traceId: traceId && traceId.length === 32 ? traceId : hex(16),
                  spanId: hex(8),
                  ...(parentSpanId && parentSpanId.length === 16 ? { parentSpanId } : {}),
                  name: `invoke_agent ${AGENT_NAME}`,
                  kind: 2,
                  startTimeUnixNano: opts.start,
                  endTimeUnixNano: opts.end,
                  attributes: [
                    { key: "gen_ai.agent.name", value: { stringValue: AGENT_NAME } },
                    { key: "gen_ai.operation.name", value: { stringValue: "invoke_agent" } },
                  ],
                  events: [msg("input", "user", opts.input, opts.start), msg("output", "assistant", opts.output, opts.end)],
                  status: opts.failed ? { code: 2, message: opts.failed } : { code: 1 },
                },
              ],
            },
          ],
        },
      ],
    },
  };
}

async function turn(send: (f: Json) => Promise<void>, ev: WsEvent, reqId: string, contextId: string, input: string) {
  const start = nowNs();
  const s = await getSession(contextId);
  const taskId = `task-${randomUUID()}`;
  const meta = (eventType: string) => ({ [EVENT_TYPE]: eventType });
  const reply = decide(input, s.turns ?? 0);
  const allowed = new Set(s.supportedFinishTypes ?? ["COMPLETE", "ESCALATE"]);
  const finish = reply.finish && allowed.has(reply.finish.type) ? reply.finish : undefined;
  if (!s.ready) log("turn before CHANNEL_STATE READY seen on this session (continuing)", { contextId });

  await send(result(reqId, { task: { id: taskId, contextId, status: { state: "TASK_STATE_WORKING" } } }));
  if (/simulate (a )?failure/i.test(input)) {
    // Demo of the partner failing mid-turn: a structured task failure (Connect then
    // takes the fallback path; the flow routes the customer to a human).
    await send(
      result(reqId, {
        statusUpdate: {
          taskId,
          contextId,
          status: {
            state: "TASK_STATE_FAILED",
            message: {
              messageId: randomUUID(),
              role: "ROLE_AGENT",
              parts: [
                {
                  data: { error: { errorCode: 424, errorReason: "Simulated backend failure", errorCategory: "DEPENDENCY_FAILURE" } },
                  mediaType: "application/json",
                },
              ],
              metadata: meta("ERROR"),
            },
          },
        },
      })
    );
    log("turn failed (simulated)", { contextId });
    return;
  }
  // Write the outcome on every finish (the flow also routes on it, see lib/flows.ts).
  if (finish) await writeContactContext(s, reply, input);
  await send(
    result(reqId, {
      artifactUpdate: {
        taskId,
        contextId,
        artifact: {
          artifactId: `resp-${randomUUID()}`,
          parts: [{ text: reply.text, mediaType: "text/plain" }],
          metadata: meta("TEXT_RESPONSE_CHUNK"),
        },
        append: false,
        lastChunk: true,
      },
    })
  );
  const end = nowNs();
  if (s.tracing !== false) {
    // Traces ride on the turn's task, after the response, before the terminal status.
    await send(
      result(reqId, {
        artifactUpdate: {
          taskId,
          contextId,
          artifact: {
            artifactId: `trace-${randomUUID()}`,
            parts: [
              {
                data: { tracingSpan: traceSpan({ traceparent: await traceparentFor(ev.requestContext.connectionId), start, end, input, output: reply.text }) },
                mediaType: "application/json",
              },
            ],
            metadata: meta("TRACING_SPAN"),
          },
          append: false,
          lastChunk: true,
        },
      })
    );
  }
  await send(
    result(reqId, {
      statusUpdate: {
        taskId,
        contextId,
        status: {
          state: "TASK_STATE_COMPLETED",
          timestamp: new Date().toISOString(),
          ...(finish
            ? {
                message: {
                  messageId: randomUUID(),
                  role: "ROLE_AGENT",
                  parts: [{ data: { finish }, mediaType: "application/json" }],
                  metadata: meta("FINISH"),
                },
              }
            : {}),
        },
      },
    })
  );
  await putSession({ ...s, turns: (s.turns ?? 0) + 1, finished: Boolean(finish) });
  log("turn", { contextId, intent: reply.intent, finish: finish?.type ?? null });
}

// ---- dispatcher ------------------------------------------------------------------
async function dispatch(ev: WsEvent, frame: Json, send: (f: Json) => Promise<void>) {
  const reqId = frame.id ?? null;
  if (frame.jsonrpc !== "2.0" || frame.method !== "SendMessage" || !frame.params?.message) {
    await send(rpcError(reqId, -32601, `Unsupported request: ${frame.method ?? "no method"}`, "UNSUPPORTED_OPERATION"));
    return;
  }
  const message = frame.params.message as Json;
  const contextId: string = message.contextId ?? "";
  const eventType: string = message.metadata?.[EVENT_TYPE] ?? "";
  const parts: Part[] = message.parts ?? [];
  const data = parts.find((p) => p.data)?.data ?? {};

  if (eventType === "FRAGMENT" || data.fragment) {
    const whole = await reassemble(data.fragment);
    if (whole) await dispatch(ev, whole, send);
    return;
  }
  switch (eventType) {
    case "INIT_SESSION": {
      const init = data.initSession ?? {};
      const s = await getSession(contextId);
      await putSession({
        ...s,
        contactArn: init.contactArn,
        instanceArn: init.instanceArn,
        supportedFinishTypes: init.supportedFinishTypes,
        tracing: init.subscribeToTracingEvents !== false,
      });
      log("INIT_SESSION", {
        contextId,
        contactArn: init.contactArn,
        supportedFinishTypes: init.supportedFinishTypes,
        historyTurns: (init.history ?? []).length,
        audio: Boolean(init.audioInputConfiguration),
      });
      await send(
        result(reqId, {
          message: {
            messageId: randomUUID(),
            contextId,
            role: "ROLE_AGENT",
            parts: [{ data: { initSessionResponse: { statusCode: "200" } }, mediaType: "application/json" }],
            extensions: [EXT],
            metadata: { [EVENT_TYPE]: "INIT_SESSION_RESPONSE" },
          },
        })
      );
      return;
    }
    case "CHANNEL_STATE": {
      const s = await getSession(contextId);
      await putSession({ ...s, ready: data.channelState?.state === "READY" || s.ready });
      log("CHANNEL_STATE", { contextId, state: data.channelState?.state });
      return;
    }
    case "ERROR":
      log("ERROR from Connect (informational)", { contextId, error: data.error });
      return;
    case "INTERRUPTION":
      log("INTERRUPTION", { contextId, interruption: data.interruption });
      return;
    case "DTMF_INPUT_COMPLETE":
      await turn(send, ev, reqId, contextId, String(data.dtmfInputComplete?.inputString ?? ""));
      return;
  }
  const text = parts.filter((p) => typeof p.text === "string").map((p) => p.text).join(" ").trim();
  if (text) {
    await turn(send, ev, reqId, contextId, text);
    return;
  }
  if (parts.some((p) => p.raw)) {
    // Voice streaming is out of scope for this chat demo.
    await send(rpcError(reqId, -32602, "Audio input is not supported by this demo agent", "AUDIO_NOT_SUPPORTED"));
    return;
  }
  log("unhandled frame", { eventType, contextId });
}

export const handler = async (ev: WsEvent) => {
  const send = sender(ev);
  const raw = ev.isBase64Encoded ? Buffer.from(ev.body ?? "", "base64").toString("utf8") : (ev.body ?? "");
  let frame: Json;
  try {
    frame = JSON.parse(raw);
  } catch {
    await send(rpcError(null, -32700, "Parse error", "PARSE_ERROR"));
    return { statusCode: 200 };
  }
  if (LOG_FRAMES) log("in", frame);
  try {
    await dispatch(ev, frame, send);
  } catch (err) {
    console.error("dispatch failed", err);
    await send(rpcError(frame.id ?? null, -32603, "Internal error", "INTERNAL_ERROR")).catch(() => {});
  }
  return { statusCode: 200 };
};
