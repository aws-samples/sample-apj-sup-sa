/**
 * AgentCore Memory short-term session helper.
 *
 * Each /invocations request rebuilds a fresh Strands Agent (see index.ts), so
 * conversation continuity has to be loaded from somewhere external. AgentCore
 * Memory is that "somewhere": loadHistory() reads prior turns by sessionId,
 * saveTurn() writes the new pair.
 *
 * Workshop notes:
 * - actorId is hard-coded to 'participant'. In production you would derive it
 *   from the authenticated user (Cognito sub, B2B tenant id, etc.). The
 *   workshop runs one user per sandbox, so a constant is fine.
 * - HISTORY_LIMIT caps the prompt token count. AgentCore Memory's default
 *   maxResults is also 20; we set it explicitly to make the bound visible.
 */
import {
  BedrockAgentCoreClient,
  CreateEventCommand,
  ListEventsCommand,
  type Event,
} from "@aws-sdk/client-bedrock-agentcore";
import type { MessageData } from "@strands-agents/sdk";

const client = new BedrockAgentCoreClient({});
const ACTOR_ID = "participant";
const HISTORY_LIMIT = 20;

function memoryId(): string | undefined {
  return process.env.MEMORY_ID;
}

/**
 * Maps AgentCore Memory events into Strands MessageData[].
 * Sorts ascending by timestamp (ListEvents returns newest-first by default),
 * walks payload.conversational, drops non-USER/ASSISTANT turns (tools etc.),
 * and lowercases roles for Strands.
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
      const conv = (p as { conversational?: { role?: string; content?: { text?: string } } }).conversational;
      const role = conv?.role;
      const text = conv?.content?.text;
      if (!role || !text) continue;
      // AgentCore Memory uses uppercase roles (USER / ASSISTANT / OTHER / TOOL);
      // Strands MessageData.role is lowercase 'user' | 'assistant'. Drop
      // anything that isn't a USER/ASSISTANT turn — tools etc. are not part of
      // the persistent conversation we replay.
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
 * not configured (Stage A) or when the session has no events yet.
 */
export async function loadHistory(sessionId: string): Promise<MessageData[]> {
  const id = memoryId();
  if (!id) return [];

  const resp = await client.send(
    new ListEventsCommand({
      memoryId: id,
      actorId: ACTOR_ID,
      sessionId,
      includePayloads: true,
      maxResults: HISTORY_LIMIT,
    }),
  );
  return eventsToMessages(resp.events ?? []);
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

  await client.send(
    new CreateEventCommand({
      memoryId: id,
      actorId: ACTOR_ID,
      sessionId,
      eventTimestamp: new Date(),
      payload: [
        { conversational: { role: "USER", content: { text: userMsg } } },
        { conversational: { role: "ASSISTANT", content: { text: assistantMsg } } },
      ],
    }),
  );
}
