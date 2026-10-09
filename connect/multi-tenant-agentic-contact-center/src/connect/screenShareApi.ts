import { loadConnectConfig } from "./config";
import { loadTokens } from "../auth/tokens";

export interface ScreenShareStartResult {
  contactId: string;
  participantId: string;
  participantToken: string;
  /** Amazon Chime SDK meeting + attendee (MeetingSessionConfiguration inputs). */
  meeting: Record<string, unknown>;
  attendee: Record<string, unknown>;
}

/**
 * Starts an Amazon Connect web call with screen sharing via the backend
 * StartWebRTCContact Lambda. As with chat, the merchant identity on the contact
 * is stamped SERVER-SIDE from the validated JWT, and a caseId is only accepted
 * if that case belongs to the caller's tenant.
 */
export async function startScreenShare(opts: { caseId?: string } = {}): Promise<ScreenShareStartResult> {
  const cfg = await loadConnectConfig();
  const base = cfg?.screenShareApiUrl;
  if (!base) throw new Error("Screen sharing is not configured");
  const tokens = loadTokens();
  if (!tokens?.idToken) throw new Error("Not authenticated");

  const res = await fetch(`${base.replace(/\/+$/, "")}/screenshare/start`, {
    method: "POST",
    headers: { "content-type": "application/json", authorization: `Bearer ${tokens.idToken}` },
    body: JSON.stringify(opts.caseId ? { caseId: opts.caseId } : {}),
  });
  if (!res.ok) {
    let message = `Failed to start screen sharing (${res.status})`;
    try {
      const err = await res.json();
      if (err?.message) message = err.message;
    } catch {
      // ignore
    }
    throw new Error(message);
  }
  return (await res.json()) as ScreenShareStartResult;
}

/** "waiting" until an agent is connected, then "connected"; "ended" once the call ends. */
export async function getScreenShareStatus(contactId: string): Promise<"waiting" | "connected" | "ended"> {
  const cfg = await loadConnectConfig();
  const base = cfg?.screenShareApiUrl;
  const tokens = loadTokens();
  if (!base || !tokens?.idToken) throw new Error("Screen sharing is not configured");
  const res = await fetch(`${base.replace(/\/+$/, "")}/screenshare/status/${encodeURIComponent(contactId)}`, {
    headers: { authorization: `Bearer ${tokens.idToken}` },
  });
  if (!res.ok) throw new Error(`Status check failed (${res.status})`);
  return ((await res.json()) as { state: "waiting" | "connected" | "ended" }).state;
}
