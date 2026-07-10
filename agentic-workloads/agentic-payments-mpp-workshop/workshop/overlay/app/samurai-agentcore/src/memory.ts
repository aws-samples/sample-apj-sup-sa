/**
 * AgentCore Memory short-term session helper — WORKSHOP STARTER VERSION.
 *
 * The container is stateless across requests: every /invocations rebuilds a
 * fresh Strands Agent (see index.ts). Continuity has to come from somewhere
 * external — that's what AgentCore Memory is for. loadHistory() reads prior
 * turns by sessionId; saveTurn() writes the new pair.
 *
 * Both helpers no-op when MEMORY_ID is unset, so Stage A (the "before"
 * demo) deploys cleanly. Stage B is participants filling in TODO 5.1 and
 * TODO 5.2 below.
 */
import {
  BedrockAgentCoreClient,
  CreateEventCommand,
  ListEventsCommand,
  type Event,
} from "@aws-sdk/client-bedrock-agentcore";
import type { MessageData } from "@strands-agents/sdk";

const client = new BedrockAgentCoreClient({});
const ACTOR_ID = "participant"; // workshop constant; production: Cognito sub
const HISTORY_LIMIT = 20;

function memoryId(): string | undefined {
  return process.env.MEMORY_ID;
}

/**
 * Pre-baked for the workshop — you don't need to write this. The interesting
 * bit is the SDK call in loadHistory() below.
 *
 * Maps AgentCore Memory events into Strands MessageData[]: sorts ascending by
 * timestamp (ListEvents returns newest-first), walks payload.conversational,
 * drops non-USER/ASSISTANT turns, and lowercases roles for Strands.
 */
function eventsToMessages(events: Event[]): MessageData[] {
  const sorted = [...events].sort((a, b) => {
    const at = a.eventTimestamp ? new Date(a.eventTimestamp).getTime() : 0;
    const bt = b.eventTimestamp ? new Date(b.eventTimestamp).getTime() : 0;
    return at - bt;
  });

  const messages: MessageData[] = [];
  for (const ev of sorted) {
    for (const p of ev.payload ?? []) {
      const conv = (
        p as { conversational?: { role?: string; content?: { text?: string } } }
      ).conversational;
      const role = conv?.role;
      const text = conv?.content?.text;
      if (!role || !text) continue;
      if (role !== "USER" && role !== "ASSISTANT") continue;
      messages.push({
        role: role === "USER" ? "user" : "assistant",
        content: [{ text }],
      });
    }
  }
  return messages;
}

/**
 * Returns prior turns for this session as Strands MessageData[], ready to be
 * passed to `new Agent({ messages: history })`. Returns [] when MEMORY_ID is
 * not configured (Stage A).
 */
export async function loadHistory(sessionId: string): Promise<MessageData[]> {
  const id = memoryId();
  if (!id) return [];

  // ═════════════════════════════════════════════════════════════════════════
  // TODO 5.1 — Call ListEventsCommand on the AgentCore Memory data plane.
  // Replace `return [];` with reference code.
  //
  //   const resp = await client.send(new ListEventsCommand({
  //   ...
  //   return eventsToMessages(resp.events ?? [])
  //
  // eventsToMessages (above) handles payload walking + role-case mapping.
  // The lesson here is the SDK call, not the ETL.
  // ═════════════════════════════════════════════════════════════════════════
  return [];
}

/**
 * Persists one user turn + one assistant turn as a single Memory event.
 * No-op when MEMORY_ID is not configured (Stage A).
 */
export async function saveTurn(
  sessionId: string,
  userMsg: string,
  assistantMsg: string,
): Promise<void> {
  const id = memoryId();
  if (!id) return;

  // ═════════════════════════════════════════════════════════════════════════
  // TODO 5.2 — Call CreateEventCommand on the AgentCore Memory data plane.
  //
  //   await client.send(new CreateEventCommand({
  //     memoryId: id,
  //     actorId: ACTOR_ID,
  //     sessionId,
  //     eventTimestamp: new Date(),
  //     payload: [
  //       { conversational: { role: 'USER',      content: { text: userMsg } } },
  //       { conversational: { role: 'ASSISTANT', content: { text: assistantMsg } } },
  //     ],
  //   }))
  // ═════════════════════════════════════════════════════════════════════════

  //   await client.send(new CreateEventCommand({
  //   ...
  //   }))
}
