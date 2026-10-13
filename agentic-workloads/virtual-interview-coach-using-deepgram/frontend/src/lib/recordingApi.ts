// Recording playback API client (F006 / G6) — fetch a short-lived signed URL for one answer's audio.
// GET /api/sessions/{id}/turns/{turnId}/audio-url returns {available:false} when there is no recording
// (consent off / not yet uploaded / aged out), or {available:true, url, expires_in} with a temporary
// owner-scoped S3 URL the browser plays directly. The URL is never persisted client-side.

export interface AudioUrlResponse {
  available: boolean;
  url?: string;
  expires_in?: number;
}

function authHeaders(accessToken: string): HeadersInit {
  return { Authorization: `Bearer ${accessToken}` };
}

export async function getTurnAudioUrl(
  accessToken: string,
  sessionId: string,
  turnId: string
): Promise<AudioUrlResponse> {
  const resp = await fetch(`/api/sessions/${sessionId}/turns/${turnId}/audio-url`, {
    headers: authHeaders(accessToken),
  });
  if (resp.status === 404) return { available: false }; // not owner / no such turn
  if (!resp.ok) throw new Error(`Could not load the recording (HTTP ${resp.status}).`);
  return (await resp.json()) as AudioUrlResponse;
}
