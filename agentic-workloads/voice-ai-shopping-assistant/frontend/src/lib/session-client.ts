import { SESSION_API_URL } from "../config";
import type { StartResponse } from "../types/contracts.live";

/**
 * Start a session: POST the deployed start endpoint, which invokes the AgentCore
 * runtime (the bot joins the Daily room) and returns the room URL to join.
 *
 * Deployed shape (verified live): { "room_url": "...", "status": "ok" }.
 */
export async function startSession(): Promise<StartResponse> {
  const res = await fetch(SESSION_API_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  if (!res.ok) {
    throw new Error(`start session failed: HTTP ${res.status}`);
  }
  const json = (await res.json()) as StartResponse;
  if (!json.room_url) {
    throw new Error("start session: no room_url in response");
  }
  return json;
}
