import { loadConnectConfig } from "./config";
import { loadTokens } from "../auth/tokens";

export interface CaseSummary {
  caseId: string;
  title: string;
  summary: string;
  priority: string;
  status: string;
  merchant: string;
  createdAt: string;
}

export interface CaseComment {
  body: string;
  createdAt: string;
}

export interface CaseDetail extends CaseSummary {
  comments: CaseComment[];
}

export interface CreateCaseInput {
  title: string;
  summary?: string;
  priority?: string;
  status?: string;
  merchant?: string;
}

async function baseUrl(): Promise<string> {
  const cfg = await loadConnectConfig();
  const url = cfg?.casesApiUrl;
  if (!url) throw new Error("Cases API is not configured");
  return url.replace(/\/+$/, "");
}

async function request<T>(pathSuffix: string, init?: RequestInit): Promise<T> {
  const tokens = loadTokens();
  if (!tokens?.idToken) throw new Error("Not authenticated");
  const root = await baseUrl();
  const res = await fetch(`${root}${pathSuffix}`, {
    ...init,
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${tokens.idToken}`,
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    let message = `Request failed (${res.status})`;
    try {
      const err = await res.json();
      if (err?.message) message = err.message;
    } catch {
      // ignore parse error
    }
    throw new Error(message);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export function listCases(): Promise<{ cases: CaseSummary[] }> {
  return request("/cases");
}

export function createCase(input: CreateCaseInput): Promise<{ caseId: string }> {
  return request("/cases", { method: "POST", body: JSON.stringify(input) });
}

export function getCase(caseId: string): Promise<CaseDetail> {
  return request(`/cases/${encodeURIComponent(caseId)}`);
}

export function updateCase(
  caseId: string,
  patch: { status?: string; priority?: string; summary?: string }
): Promise<{ caseId: string; updated: boolean }> {
  return request(`/cases/${encodeURIComponent(caseId)}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export function addComment(caseId: string, body: string): Promise<{ added: boolean }> {
  return request(`/cases/${encodeURIComponent(caseId)}/comments`, {
    method: "POST",
    body: JSON.stringify({ body }),
  });
}
