// Report API client (F003) — polls the async feedback report for a finished session.
// GET /api/sessions/{id}/report returns {status} while processing and {status:'scored', report} once
// the Report Worker has written it. Through CloudFront in prod; Vite proxy in dev.

export type ReportStatus = "queued" | "processing" | "scored" | "failed";

export interface CompetencyScore {
  competency: string;
  score_1_5: number;
  evidence_quote: string | null;
  star_element: string | null;
  turn_index: number | null;
  assessed: boolean;
}

export interface QuestionFeedback {
  turn_index: number | null;
  competency: string | null;
  question_text: string;
  student_transcript: string;
  what_worked: string | null;
  what_to_improve: string | null;
  strong_answer_example: string | null;
  star_coverage: Record<string, boolean>;
  evidence_quote: string | null;
}

export interface VoiceMetrics {
  filler_count?: number;
  wpm?: number | null;
  long_pauses?: number | null;
  conciseness?: number;
  hedging_rate?: number;
  responsiveness?: string;
}

export interface Report {
  id: string;
  status: ReportStatus;
  overall: number | null;
  score_content: number | null;
  score_structure: number | null;
  score_communication: number | null;
  score_confidence: number | null;
  difficulty: string | null;
  rubric_version: string | null;
  summary_strengths: string[];
  summary_improvements: string[];
  metrics: VoiceMetrics;
  competency_scorecard: CompetencyScore[];
  question_feedback: QuestionFeedback[];
}

export interface ReportEnvelope {
  status: ReportStatus;
  report: Report | null;
}

function authHeaders(accessToken: string): HeadersInit {
  return { Authorization: `Bearer ${accessToken}` };
}

export async function getReport(accessToken: string, sessionId: string): Promise<ReportEnvelope | null> {
  const resp = await fetch(`/api/sessions/${sessionId}/report`, { headers: authHeaders(accessToken) });
  if (resp.status === 404) return null; // no report job yet
  if (!resp.ok) throw new Error(`Could not load the report (HTTP ${resp.status}).`);
  return (await resp.json()) as ReportEnvelope;
}
