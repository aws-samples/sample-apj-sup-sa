import { useEffect, useMemo, useState } from "react";
import { completeLogin, login, logout, token, type Config } from "./auth";
import { AuthError, DIMENSIONS, fmtMs, get, type Comparison, type Dim, type Filters, type Run } from "./api";

const METRICS = [
  ["ttft", "Δ TTFT"],
  ["ttfat", "Δ first answer token"],
  ["e2e", "Δ end-to-end"],
  ["itl", "Δ inter-token"],
] as const;

const LABELS: Record<Dim, string> = {
  model: "Model",
  endpoint: "Endpoint",
  api: "API",
  scope: "Scope",
  region: "Region",
  prompt_size: "Prompt size",
  cache: "Cache",
  tier: "Tier",
};

export function App({ cfg }: { cfg: Config }) {
  const [signedIn, setSignedIn] = useState(!!token());
  const [error, setError] = useState<string | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [options, setOptions] = useState<Partial<Record<Dim, string[]>>>({});
  const [filters, setFilters] = useState<Filters>({});
  const [rows, setRows] = useState<Comparison[]>([]);

  // One handler for every request: expired sessions go back to sign-in, other errors are shown.
  const fail = (e: Error) => {
    if (e instanceof AuthError) setSignedIn(false);
    setError(e.message);
  };

  useEffect(() => {
    completeLogin(cfg)
      .then(() => setSignedIn(!!token()))
      .catch((e: Error) => setError(e.message));
  }, [cfg]);

  useEffect(() => {
    if (!signedIn) return;
    get<Run[]>(cfg, "/runs").then(setRuns).catch(fail);
  }, [cfg, signedIn]);

  useEffect(() => {
    if (!signedIn) return;
    get<Partial<Record<Dim, string[]>>>(cfg, "/filters", { run_id: filters.run_id })
      .then(setOptions)
      .catch(fail);
  }, [cfg, signedIn, filters.run_id]);

  useEffect(() => {
    if (!signedIn) return;
    let live = true; // ignore responses for filters the user has already changed
    get<Comparison[]>(cfg, "/comparisons", filters)
      .then((r) => {
        if (!live) return;
        setRows(r);
        setError(null);
      })
      .catch((e: Error) => live && fail(e));
    return () => {
      live = false;
    };
  }, [cfg, signedIn, filters]);

  const sorted = useMemo(
    () => [...rows].sort((a, b) => (a.display_name ?? a.model).localeCompare(b.display_name ?? b.model)),
    [rows],
  );

  if (!signedIn) {
    return (
      <main className="center">
        <h1>Amazon Bedrock service tiers</h1>
        <p>Latency of Flex and Priority against Standard, measured daily.</p>
        <button onClick={() => login(cfg)}>Sign in</button>
        {error && <p className="error">{error}</p>}
      </main>
    );
  }

  const set = (k: Dim | "run_id", v: string) => setFilters((f) => ({ ...f, [k]: v || undefined }));

  return (
    <main>
      <header>
        <h1>Amazon Bedrock service tiers</h1>
        <button className="link" onClick={() => logout(cfg)}>
          Sign out
        </button>
      </header>
      <p className="note">
        Δ = tier p50 minus Standard p50 in the same context (model, endpoint, API, scope, region, prompt size,
        cache). <em>n.s.</em> = the 95% bootstrap confidence interval includes 0. Negative is faster.
      </p>
      <section className="filters">
        <label>
          Run
          <select value={filters.run_id ?? ""} onChange={(e) => set("run_id", e.target.value)}>
            <option value="">latest</option>
            {runs.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                {r.run_id}
              </option>
            ))}
          </select>
        </label>
        {DIMENSIONS.map((d) => (
          <label key={d}>
            {LABELS[d]}
            <select value={filters[d] ?? ""} onChange={(e) => set(d, e.target.value)}>
              <option value="">all</option>
              {(options[d] ?? []).filter((v) => d !== "tier" || v !== "default").map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </select>
          </label>
        ))}
      </section>
      {error && <p className="error">{error}</p>}
      <table>
        <thead>
          <tr>
            <th>Model</th>
            <th>Endpoint / API</th>
            <th>Scope / region</th>
            <th>Prompt / cache</th>
            <th>Tier vs Standard</th>
            {METRICS.map(([, label]) => (
              <th key={label}>{label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((c) => (
            <tr key={`${c.run_id}|${c.model}|${c.endpoint}|${c.api}|${c.scope}|${c.region}|${c.prompt_size}|${c.cache}|${c.tier}`}>
              <td>{c.display_name ?? c.model}</td>
              <td>
                {c.endpoint} / {c.api}
              </td>
              <td>
                {c.scope} / {c.region}
              </td>
              <td>
                {c.prompt_size} / {c.cache}
              </td>
              <td>{c.tier}</td>
              {METRICS.map(([m]) => {
                const d = c.deltas[m];
                const cls = !d || d.delta == null ? "" : !d.significant ? "ns" : d.delta < 0 ? "faster" : "slower";
                return (
                  <td key={m} className={cls}>
                    {fmtMs(d?.delta)}
                    {d && d.delta != null && !d.significant ? " n.s." : ""}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {!sorted.length && <p className="note">No comparisons match these filters.</p>}
    </main>
  );
}
