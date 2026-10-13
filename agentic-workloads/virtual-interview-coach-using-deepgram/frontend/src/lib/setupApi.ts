// Setup API client (T021) — talks to the backend setup control plane
// (/api/me/consent, /api/me/resume, /api/me/resume/confirm, GET /api/me/resume).
// All of these run off the response_gap clock (pre-session setup window).
// See specs/002-personalization-question-intelligence/contracts/setup-api.md.

// The confirmed parsed-facts shape (mirrors backend/src/resume_parse.py). These — once confirmed —
// are authoritative for grounding (FR-204). Kept small + structured so manual entry is feasible.
export interface ExperienceItem {
  title: string;
  organization?: string | null;
  duration?: string | null;
  highlights: string[];
}

export interface EducationItem {
  qualification: string;
  institution?: string | null;
  year?: string | null;
}

export interface ParsedFacts {
  name?: string | null;
  summary?: string | null;
  skills: string[];
  experience: ExperienceItem[];
  education: EducationItem[];
}

export type ParseStatus = "parsed" | "low_confidence" | "failed";
export type Confidence = "high" | "medium" | "low";

export interface UploadResumeResponse {
  resume_uri: string;
  parsed_facts: ParsedFacts;
  parse_status: ParseStatus;
  confidence: Confidence;
}

export interface ConsentResponse {
  consent_store_materials: boolean;
  retention_days: number;
  consent_recording_at: string | null;
}

export interface GetResumeResponse {
  parsed_facts: ParsedFacts;
  resume_confirmed_at: string | null;
  still_accurate_prompt: boolean;
}

// An empty, well-formed facts object for the manual-entry fallback (SC-006) so the form always has
// a valid shape to edit even when parsing failed.
export function emptyFacts(): ParsedFacts {
  return { name: "", summary: "", skills: [], experience: [], education: [] };
}

function bearer(accessToken: string): HeadersInit {
  return { Authorization: `Bearer ${accessToken}` };
}

function jsonHeaders(accessToken: string): HeadersInit {
  return { ...bearer(accessToken), "Content-Type": "application/json" };
}

// A typed error so the UI can branch on 409 (consent required) vs other failures.
export class SetupApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "SetupApiError";
  }
}

async function ensureOk(resp: Response, fallback: string): Promise<void> {
  if (resp.ok) return;
  let detail = fallback;
  try {
    const body = await resp.json();
    if (body?.detail) detail = String(body.detail);
  } catch {
    /* non-JSON error body — keep the fallback message */
  }
  throw new SetupApiError(resp.status, detail);
}

// PUT /me/consent — must be true before any resume/job is persisted (FR-220).
export async function setConsent(
  accessToken: string,
  consentStoreMaterials: boolean,
  retentionDays = 30
): Promise<ConsentResponse> {
  const resp = await fetch("/api/me/consent", {
    method: "PUT",
    headers: jsonHeaders(accessToken),
    body: JSON.stringify({
      consent_store_materials: consentStoreMaterials,
      retention_days: retentionDays,
    }),
  });
  await ensureOk(resp, "Could not save your consent choice.");
  return (await resp.json()) as ConsentResponse;
}

// PUT /me/resume — multipart upload + off-gap-clock parse-back. 409 if consent is missing.
export async function uploadResume(
  accessToken: string,
  file: File
): Promise<UploadResumeResponse> {
  const form = new FormData();
  form.append("file", file, file.name);
  const resp = await fetch("/api/me/resume", {
    method: "PUT",
    headers: bearer(accessToken), // do NOT set Content-Type — the browser sets the multipart boundary
    body: form,
  });
  await ensureOk(resp, "Could not upload your resume.");
  return (await resp.json()) as UploadResumeResponse;
}

// POST /me/resume/confirm — the confirmed/corrected (or manually entered) facts become authoritative.
export async function confirmResume(
  accessToken: string,
  parsedFacts: ParsedFacts,
  manualEntry = false
): Promise<{ resume_confirmed_at: string }> {
  const resp = await fetch("/api/me/resume/confirm", {
    method: "POST",
    headers: jsonHeaders(accessToken),
    body: JSON.stringify({ parsed_facts: parsedFacts, manual_entry: manualEntry }),
  });
  await ensureOk(resp, "Could not save your confirmed details.");
  return (await resp.json()) as { resume_confirmed_at: string };
}

// GET /me/resume — reuse-with-confirm for a returning student (FR-202). Returns null on 404.
export async function getResume(accessToken: string): Promise<GetResumeResponse | null> {
  const resp = await fetch("/api/me/resume", { headers: bearer(accessToken) });
  if (resp.status === 404) return null;
  await ensureOk(resp, "Could not load your saved resume.");
  return (await resp.json()) as GetResumeResponse;
}

// DELETE /me — hard-delete the account's PII (RDS rows + S3 resume/audio objects) (FR-219). Used by
// the Privacy screen's "Delete my account & all data". Irreversible.
export async function deleteAccount(accessToken: string): Promise<{ ok: boolean }> {
  const resp = await fetch("/api/me", { method: "DELETE", headers: bearer(accessToken) });
  await ensureOk(resp, "Could not delete your data. Please try again.");
  return (await resp.json()) as { ok: boolean };
}

export interface ScrapeJobResponse {
  job_title: string | null;
  job_description: string;
  // "scraped" = model extracted a clean posting; "partial" = raw page text returned as a fallback
  // (the student should review/trim it).
  scrape_status: "scraped" | "partial";
}

// POST /me/job/scrape — fetch a job-posting URL and extract {job_title, job_description} to prefill
// the role step (mirrors resume parse-back). Nothing is persisted; the student reviews/edits the
// result. Throws SetupApiError (422) with a friendly message when the link can't be read.
export async function scrapeJob(accessToken: string, url: string): Promise<ScrapeJobResponse> {
  const resp = await fetch("/api/me/job/scrape", {
    method: "POST",
    headers: jsonHeaders(accessToken),
    body: JSON.stringify({ url }),
  });
  await ensureOk(resp, "We couldn't read that link. Please paste the description instead.");
  return (await resp.json()) as ScrapeJobResponse;
}
