// Coaching guidance API client (F008 US4) — the dashboard's cross-session coach notes.
// Generated asynchronously by the report-worker after each scoring; this only reads.

export interface Guidance {
  available: boolean;
  generated_at?: string | null;
  sessions_analyzed?: number;
  strengths?: string[];
  improvement_areas?: string[];
  trend_note?: string;
  next_actions?: string[];
}

export async function getGuidance(accessToken: string): Promise<Guidance> {
  const resp = await fetch("/api/me/guidance", {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!resp.ok) throw new Error(`Could not load your coaching notes (HTTP ${resp.status}).`);
  return (await resp.json()) as Guidance;
}
