import { Fragment, useEffect, useMemo, useState } from "react";
import { completeLogin, login, logout, token, type Config } from "./auth";
import {
  AuthError,
  DIMENSIONS,
  PERCENTILES,
  at,
  fmtLatency,
  fmtMs,
  get,
  type Comparison,
  type Dim,
  type Filters,
  type Pct,
  type Run,
} from "./api";

const METRICS = [
  ["ttft", "Δ TTFT"],
  ["ttfat", "Δ first answer token"],
  ["e2e", "Δ end-to-end"],
  ["itl", "Δ inter-token"],
] as const;

const PCT_HELP: Record<Pct, string> = {
  p10: "fastest 10%",
  p50: "typical request",
  p90: "slow tail",
  p99: "worst case",
};

// With an identity provider (Midway), sign-in is a redirect with no prompt, so the app starts it
// itself. At most one automatic attempt per minute, so a rejected token cannot cause a redirect loop.
const AUTO = "auto_login_at";
function autoLoginAllowed(cfg: Config): boolean {
  if (!cfg.identityProvider) return false;
  const last = Number(sessionStorage.getItem(AUTO) ?? 0);
  return Date.now() - last > 60_000;
}

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
  const [pct, setPct] = useState<Pct>("p50");
  const [open, setOpen] = useState<string | null>(null);
  const [redirecting, setRedirecting] = useState(false);
  const autoLogin = () => {
    sessionStorage.setItem(AUTO, String(Date.now()));
    setRedirecting(true);
    void login(cfg);
  };

  // One handler for every request: expired sessions go back to sign-in, other errors are shown.
  const fail = (e: Error) => {
    if (e instanceof AuthError) setSignedIn(false);
    setError(e.message);
  };

  useEffect(() => {
    completeLogin(cfg)
      .then(() => {
        const ok = !!token();
        setSignedIn(ok);
        if (!ok && autoLoginAllowed(cfg)) autoLogin();
      })
      .catch((e: Error) => setError(e.message));
  }, [cfg]);

  // An expired session signs in again automatically (once per minute) instead of showing a button.
  useEffect(() => {
    if (signedIn || !autoLoginAllowed(cfg) || !error) return;
    autoLogin();
  }, [cfg, signedIn, error]);

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
        {redirecting ? (
          <p>Signing you in…</p>
        ) : (
          <>
            <p>Latency of Flex and Priority against Standard, measured daily.</p>
            <button onClick={() => login(cfg)}>Sign in</button>
          </>
        )}
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
        Each Δ is the tier's {pct} minus Standard's {pct} in the same context (model, endpoint, API, scope, region,
        prompt size, cache). Negative is faster. <em>n.s.</em> = not significant: the 95% bootstrap confidence
        interval includes 0. Click a row for p10, p50, p90 and p99 of both tiers.
      </p>
      <div className="pcts" role="radiogroup" aria-label="Percentile">
        <span>Compare at</span>
        {PERCENTILES.map((p) => (
          <button
            key={p}
            role="radio"
            aria-checked={pct === p}
            className={pct === p ? "on" : ""}
            title={PCT_HELP[p]}
            onClick={() => setPct(p)}
          >
            {p} <small>{PCT_HELP[p]}</small>
          </button>
        ))}
      </div>
      {pct !== "p50" && (
        <p className="note">
          Tail percentiles need many samples: with 5 per cell, p90 and p99 are close to the slowest request.
        </p>
      )}
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
          {sorted.map((c) => {
            const key = `${c.run_id}|${c.model}|${c.endpoint}|${c.api}|${c.scope}|${c.region}|${c.prompt_size}|${c.cache}|${c.tier}`;
            const isOpen = open === key;
            return (
              <Fragment key={key}>
                <tr className="row" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : key)}>
                  <td>
                    <span className="caret">{isOpen ? "▾" : "▸"}</span> {c.display_name ?? c.model}
                  </td>
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
                    const d = at(c.deltas[m], pct);
                    const cls = !d || d.delta == null ? "" : !d.significant ? "ns" : d.delta < 0 ? "faster" : "slower";
                    return (
                      <td key={m} className={cls}>
                        {fmtMs(d?.delta)}
                        {d && d.delta != null && !d.significant ? " n.s." : ""}
                      </td>
                    );
                  })}
                </tr>
                {isOpen && (
                  <tr className="detail">
                    <td colSpan={5 + METRICS.length}>
                      <table className="pct-table">
                        <thead>
                          <tr>
                            <th></th>
                            <th></th>
                            {PERCENTILES.map((p) => (
                              <th key={p}>{p}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {METRICS.map(([m, label]) =>
                            (["default", "tier"] as const).map((side, i) => (
                              <tr key={`${m}-${side}`} className={i ? "" : "first"}>
                                {i === 0 && <th rowSpan={2}>{label.replace("Δ ", "")}</th>}
                                <td>{side === "default" ? "Standard" : c.tier}</td>
                                {PERCENTILES.map((p) => {
                                  const d = at(c.deltas[m], p);
                                  return <td key={p}>{fmtLatency(d?.[side])}</td>;
                                })}
                              </tr>
                            )),
                          )}
                        </tbody>
                      </table>
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
      {!sorted.length && <p className="note">No comparisons match these filters.</p>}
    </main>
  );
}
