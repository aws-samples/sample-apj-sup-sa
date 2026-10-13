import { loadConnectConfig } from "./config";
import { loadTokens } from "../auth/tokens";

export interface ChatStartResult {
  contactId: string;
  participantId: string;
  participantToken: string;
  region: string;
}

/**
 * Starts a chat contact via the backend StartChatContact Lambda. The merchant
 * identity attached to the contact is stamped SERVER-SIDE from the validated
 * JWT (the client cannot influence which tenant the chat belongs to). Returns
 * the per-contact chat credentials used to open a ChatJS session.
 */
export async function startChat(
  opts: { caseId?: string; message?: string } = {}
): Promise<ChatStartResult> {
  const cfg = await loadConnectConfig();
  const base = cfg?.chatApiUrl;
  if (!base) throw new Error("Chat is not configured");
  const tokens = loadTokens();
  if (!tokens?.idToken) throw new Error("Not authenticated");

  const payload: Record<string, string> = {};
  if (opts.message) payload.message = opts.message;
  // When present, binds the chat to an existing case (server validates tenant
  // ownership and routes through the separate case chat flow).
  if (opts.caseId) payload.caseId = opts.caseId;

  const res = await fetch(`${base.replace(/\/+$/, "")}/chat/start`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${tokens.idToken}`,
    },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    let message = `Failed to start chat (${res.status})`;
    try {
      const err = await res.json();
      if (err?.message) message = err.message;
    } catch {
      // ignore
    }
    throw new Error(message);
  }
  return (await res.json()) as ChatStartResult;
}
