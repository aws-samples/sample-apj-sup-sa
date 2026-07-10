// Session API client (T026) — talks to the backend control plane (/api/sessions).
// In production this goes through CloudFront (/api/* behavior); in dev, Vite proxies it.

export interface IceServer {
  urls: string[];
  username?: string;
  credential?: string;
}

export interface CreateSessionResponse {
  session_id: string;
  voice_token: string;
  media_endpoint: string;
  ice_servers: IceServer[];
  reply_provider: string;
  // Present only for a personalized (F002) session; absent on the generic G1 path.
  difficulty?: string;
  blueprint_ready?: boolean;
  domain_coverage_reduced?: boolean;
  // Client-side convenience: the chosen job title, attached by Setup so the live interview screen
  // can show "<difficulty> · <role>" (the backend does not echo it on create).
  jobTitle?: string;
  // Client-side convenience: the consent choice made in Setup, attached so post-session screens
  // (Privacy) reflect the session's ACTUAL recording state rather than a hardcoded default.
  recordAudio?: boolean;
}

export type Difficulty = "easy" | "moderate" | "difficult";

// Supported interview lengths (minutes). The backend maps each to a main-question count (~90s each).
// 3 is the "quick test drive" tier (F008 US5): a complete miniature interview (~2 questions).
export type DurationMinutes = 3 | 5 | 10 | 15 | 30;

// The job scope + difficulty + duration for a personalized session (T022). When omitted,
// createSession() falls back to the generic G1 session (no blueprint), so existing call sites keep
// working unchanged.
export interface SessionScope {
  jobTitle: string;
  jobDescription: string;
  difficulty: Difficulty;
  durationMinutes: DurationMinutes;
  // F006 (G6): record this interview's audio? Defaults true server-side if omitted.
  recordAudio?: boolean;
}

function authHeaders(accessToken: string): HeadersInit {
  return { Authorization: `Bearer ${accessToken}`, "Content-Type": "application/json" };
}

export async function createSession(
  accessToken: string,
  scope?: SessionScope
): Promise<CreateSessionResponse> {
  const body = scope
    ? JSON.stringify({
        job_title: scope.jobTitle,
        job_description: scope.jobDescription,
        difficulty: scope.difficulty,
        duration_minutes: scope.durationMinutes,
        record_audio: scope.recordAudio ?? true,
      })
    : "{}";
  const resp = await fetch("/api/sessions", {
    method: "POST",
    headers: authHeaders(accessToken),
    body,
  });
  if (!resp.ok) {
    // Surface the backend's reason (e.g. 409 consent/resume, 503 no plan) so Setup can guide the user.
    let detail = `Could not start a session (HTTP ${resp.status}).`;
    try {
      const err = await resp.json();
      if (err?.detail) detail = String(err.detail);
    } catch {
      /* keep the fallback */
    }
    throw new Error(detail);
  }
  const data = (await resp.json()) as CreateSessionResponse;
  // A personalized session must have its plan assembled before media starts (FR-208 / setup-api.md).
  if (scope && data.blueprint_ready !== true) {
    throw new Error("Your interview plan isn't ready yet. Please try again.");
  }
  return data;
}

export async function endSession(accessToken: string, sessionId: string): Promise<void> {
  await fetch(`/api/sessions/${sessionId}/end`, {
    method: "POST",
    headers: authHeaders(accessToken),
  });
}

// F008 (US1): one entry per past practice session, newest first, for the session picker.
export interface SessionSummary {
  session_id: string;
  created_at: string | null;
  ended_at: string | null;
  end_reason: string | null;
  job_title: string | null;
  difficulty: string | null;
  duration_minutes: number | null;
  report_status: "none" | "queued" | "processing" | "scored" | "failed";
}

export async function listSessions(accessToken: string): Promise<SessionSummary[]> {
  const resp = await fetch("/api/sessions", { headers: authHeaders(accessToken) });
  if (!resp.ok) throw new Error(`Could not load your sessions (HTTP ${resp.status}).`);
  const data = (await resp.json()) as { sessions: SessionSummary[] };
  return data.sessions;
}
