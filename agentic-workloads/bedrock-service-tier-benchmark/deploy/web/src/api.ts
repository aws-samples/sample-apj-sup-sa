import { token, type Config } from "./auth";

export const DIMENSIONS = ["model", "endpoint", "api", "scope", "region", "prompt_size", "cache", "tier"] as const;
export type Dim = (typeof DIMENSIONS)[number];
export type Filters = Partial<Record<Dim | "run_id", string>>;

export interface Delta {
  delta: number | null;
  pct: number | null;
  ci_low: number | null;
  ci_high: number | null;
  significant: boolean | null;
}
export interface Comparison {
  run_id: string;
  model: string;
  display_name: string | null;
  endpoint: string;
  api: string;
  scope: string;
  region: string;
  prompt_size: string;
  cache: string;
  tier: string;
  deltas: Record<string, Delta>;
}
export interface Run {
  run_id: string;
  started: string;
  finished: string;
}

export async function get<T>(cfg: Config, path: string, filters: Filters = {}): Promise<T> {
  const t = token();
  if (!t) throw new Error("not signed in");
  const q = new URLSearchParams(Object.entries(filters).filter(([, v]) => v) as [string, string][]);
  const r = await fetch(`${cfg.apiUrl}${path}${q.size ? `?${q}` : ""}`, {
    headers: { authorization: `Bearer ${t}` },
  });
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  const body = (await r.json()) as T | string;
  return (typeof body === "string" ? JSON.parse(body) : body) as T;
}

export function fmtMs(v: number | null | undefined): string {
  return v == null ? "—" : `${v >= 0 ? "+" : ""}${(v * 1000).toFixed(0)} ms`;
}
