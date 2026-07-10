// Client-events beacon — report client-side failures the server otherwise never sees (mic
// denied, connect failure, mid-session drop, render crash). Fire-and-forget: a reporting
// failure must never affect the user, so errors are swallowed. Only allowlisted event NAMES
// are sent — no payloads, no free text (the backend rejects anything else).

export type ClientEvent =
  | "connect_failed"
  | "mic_denied"
  | "mic_unavailable"
  | "session_dropped"
  | "render_error"
  | "report_load_failed"
  | "playback_failed";

// Module-level token so call sites that sit ABOVE the auth state (the root ErrorBoundary) can
// still report. Session sets it once sign-in completes; null before that (events are dropped —
// pre-auth surfaces have nothing session-shaped to report anyway).
let token: string | null = null;

export function setClientEventsToken(accessToken: string | null): void {
  token = accessToken;
}

export function reportClientEvent(event: ClientEvent): void {
  if (!token) return;
  void fetch("/api/client-events", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ event }),
    keepalive: true, // survives page unload (e.g. reload right after a render error)
  }).catch(() => {
    /* never let telemetry break the app */
  });
}
