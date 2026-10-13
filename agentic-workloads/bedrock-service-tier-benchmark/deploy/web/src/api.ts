import { token, type Config } from "./auth";

export const DIMENSIONS = ["model", "endpoint", "api", "scope", "region", "prompt_size", "cache", "tier"] as const;
export type Dim = (typeof DIMENSIONS)[number];
export type Filters = Partial<Record<Dim | "run_id", string>>;

export const PERCENTILES = ["p10", "p50", "p90", "p99"] as const;
export type Pct = (typeof PERCENTILES)[number];

export interface PctDelta {
  default: number | null;
  tier: number | null;
  delta: number | null;
  pct: number | null;
  ci_low: number | null;
  ci_high: number | null;
  significant: boolean | null;
}
/** Top-level fields are the p50 view; runs from before 2026-10-09 have no by_percentile. */
export interface Delta {
  default_p50?: number | null;
  tier_p50?: number | null;
  delta: number | null;
  pct: number | null;
  ci_low: number | null;
  ci_high: number | null;
  significant: boolean | null;
  by_percentile?: Partial<Record<Pct, PctDelta>>;
}

/** The delta at one percentile, falling back to the top-level p50 fields for older runs. */
export function at(d: Delta | undefined, p: Pct): PctDelta | undefined {
  if (!d) return undefined;
  const v = d.by_percentile?.[p];
  if (v) return v;
  if (p !== "p50") return undefined;
  return { default: d.default_p50 ?? null, tier: d.tier_p50 ?? null, delta: d.delta, pct: d.pct, ci_low: d.ci_low, ci_high: d.ci_high, significant: d.significant };
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

/** Thrown when the session has no valid token or the API rejects it: the UI returns to sign-in. */
export class AuthError extends Error {}

export async function get<T>(cfg: Config, path: string, filters: Filters = {}): Promise<T> {
  const t = token();
  if (!t) throw new AuthError("Your session has ended. Sign in again.");
  const q = new URLSearchParams(Object.entries(filters).filter(([, v]) => v) as [string, string][]);
  const r = await fetch(`${cfg.apiUrl}${path}${q.size ? `?${q}` : ""}`, {
    headers: { authorization: `Bearer ${t}` },
  });
  if (r.status === 401 || r.status === 403) throw new AuthError("Your session has ended. Sign in again.");
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return (await r.json()) as T;
}

export function fmtMs(v: number | null | undefined): string {
  return v == null ? "—" : `${v >= 0 ? "+" : ""}${(v * 1000).toFixed(0)} ms`;
}

/** An absolute latency in seconds, as ms below 10 s. */
export function fmtLatency(v: number | null | undefined): string {
  if (v == null) return "—";
  return v < 10 ? `${(v * 1000).toFixed(0)} ms` : `${v.toFixed(1)} s`;
}
