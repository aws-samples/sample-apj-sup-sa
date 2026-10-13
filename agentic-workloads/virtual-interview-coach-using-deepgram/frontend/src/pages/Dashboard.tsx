// Coaching dashboard (F008 US4 — delivers Gate G5). Replaces the former design-preview screen.
//
// The primary content is the COACH'S PROSE: cross-session guidance the report-worker regenerates
// after each scored session (recurring strengths, recurring improvement areas, an honest trend
// note, and 2-3 prioritized next actions), always stamped with when it was generated and how many
// sessions informed it. Prose over charts by explicit product decision — and per Constitution II
// no fabricated trend visuals: the old placeholder ladder/score-trend mocks are gone.
// A compact session history (the US1 list) sits below so each report is one click away.

import { useEffect, useState } from "react";
import { getGuidance, type Guidance } from "../lib/guidanceApi";
import { listSessions, type SessionSummary } from "../lib/sessionApi";

interface Props {
  accessToken: string;
  onPracticeAgain: () => void;
  // Open a specific past session's report (the US1 picker owns the report surface).
  onOpenReport?: (sessionId: string) => void;
}

export default function Dashboard({ accessToken, onPracticeAgain, onOpenReport }: Props) {
  const [guidance, setGuidance] = useState<Guidance | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    Promise.allSettled([getGuidance(accessToken), listSessions(accessToken)]).then(
      ([g, s]) => {
        if (cancelled) return;
        if (g.status === "fulfilled") setGuidance(g.value);
        else setError("Could not load your coaching notes right now.");
        if (s.status === "fulfilled") setSessions(s.value);
      }
    );
    return () => {
      cancelled = true;
    };
  }, [accessToken]);

  if (guidance === null && !error) {
    return (
      <div className="pad">
        <div className="center-stage"><div className="proc-ring" /></div>
      </div>
    );
  }

  // Empty state: no guidance yet (no scored sessions, or the very first one is still scoring).
  if (!guidance?.available) {
    return (
      <div className="pad">
        <div className="empty-state">
          <div className="big">🎤</div>
          <h2>Your coach's notes appear here</h2>
          <p>
            {sessions.length > 0
              ? "Your first session is being analyzed — coaching notes appear shortly after your report is ready."
              : "Practice as many times as you want — after each session your coach distills what to work on next."}
          </p>
          <button className="btn primary" onClick={onPracticeAgain}>
            {sessions.length > 0 ? "Practice again" : "Start your first interview"}
          </button>
          {error && <p className="hint" style={{ marginTop: 10 }}>{error}</p>}
        </div>
      </div>
    );
  }

  const updated = guidance.generated_at
    ? new Date(guidance.generated_at).toLocaleString(undefined, {
        month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
      })
    : "recently";

  return (
    <div className="pad">
      <h2 className="title">Your coach's notes 📝</h2>
      <p className="sub">
        Updated {updated} · based on {guidance.sessions_analyzed}{" "}
        {guidance.sessions_analyzed === 1 ? "session" : "sessions"}
      </p>

      <div className="summary-grid" style={{ marginTop: 14 }}>
        <div className="sumcard good">
          <h4>💪 What keeps showing up as a strength</h4>
          <ul>{(guidance.strengths || []).map((s, i) => <li key={i}>{s}</li>)}</ul>
        </div>
        <div className="sumcard improve">
          <h4>🎯 What keeps recurring to work on</h4>
          <ul>{(guidance.improvement_areas || []).map((s, i) => <li key={i}>{s}</li>)}</ul>
        </div>
      </div>

      {guidance.trend_note && (
        <div className="progress-card" style={{ marginTop: 16 }}>
          <b style={{ fontSize: 15 }}>Your trend</b>
          <p style={{ margin: "6px 0 0" }}>{guidance.trend_note}</p>
        </div>
      )}

      {(guidance.next_actions?.length ?? 0) > 0 && (
        <div className="progress-card" style={{ marginTop: 16 }}>
          <b style={{ fontSize: 15 }}>Before your next session</b>
          <ol style={{ margin: "8px 0 0", paddingLeft: 20 }}>
            {guidance.next_actions!.map((a, i) => (
              <li key={i} style={{ margin: "6px 0" }}>{a}</li>
            ))}
          </ol>
        </div>
      )}

      {sessions.length > 0 && (
        <div className="progress-card" style={{ marginTop: 16 }}>
          <b style={{ fontSize: 15 }}>Recent sessions</b>
          <div style={{ marginTop: 8 }}>
            {sessions.slice(0, 8).map((s) => (
              <div className="sess-row" key={s.session_id}>
                <div className="sess-meta">
                  <b>{s.job_title || "Practice interview"}</b>
                  <br />
                  <span>
                    {s.created_at
                      ? new Date(s.created_at).toLocaleString(undefined, {
                          month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
                        })
                      : ""}
                    {s.difficulty ? ` · ${s.difficulty}` : ""}
                    {s.duration_minutes ? ` · ${s.duration_minutes} min` : ""}
                  </span>
                </div>
                <div>
                  {s.report_status === "scored" && onOpenReport ? (
                    <button className="btn ghost sm" onClick={() => onOpenReport(s.session_id)}>
                      View report
                    </button>
                  ) : (
                    <span className="hint">
                      {s.report_status === "failed" ? "scoring failed" : s.report_status}
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div style={{ textAlign: "center", marginTop: 18 }}>
        <button className="btn primary" onClick={onPracticeAgain}>Practice again</button>
      </div>
    </div>
  );
}
