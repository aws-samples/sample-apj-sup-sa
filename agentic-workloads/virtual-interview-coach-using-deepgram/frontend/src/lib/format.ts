// Defensive numeric coercion for API-supplied scores. Postgres NUMERIC columns serialize as JSON
// strings, and a stray string crashing the render was the cause of the earlier blank report screen
// — every score the SPA renders must pass through num()/fmt() (Report.tsx).

export function num(v: unknown): number | null {
  if (v == null) return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

export function fmt(v: unknown): string {
  const n = num(v);
  return n != null ? n.toFixed(1) : "—";
}

export function pct(v: unknown): number {
  const n = num(v);
  return n != null ? Math.max(0, Math.min(100, n * 10)) : 0;
}
