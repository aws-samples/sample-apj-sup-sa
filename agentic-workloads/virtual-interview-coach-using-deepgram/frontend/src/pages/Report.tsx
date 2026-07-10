// Feedback-report screen (F003 logic, F007 styling; F008 session picker + transcript). Polls
// GET /api/sessions/{id}/report until the async Report Worker finishes, showing a designed
// "Analyzing your interview…" processing bridge, then renders the scored report in the prototype
// look: score circle + 0-10 sub-scores, strengths/improvements, the 1-5 evidence-anchored
// competency scorecard, voice metrics, and per-question feedback. F008 adds: a dropdown of the
// user's past sessions (ReportArea), the full transcript in EVERY report state (the transcript
// does not depend on scoring — FR-005), and hardened per-answer playback.

import { useEffect, useRef, useState } from "react";
import {
  getReport,
  type CompetencyScore,
  type QuestionFeedback,
  type Report as ReportData,
  type ReportEnvelope,
} from "../lib/reportApi";
import { getTurnAudioUrl } from "../lib/recordingApi";
import { listSessions, type SessionSummary } from "../lib/sessionApi";
import { reportClientEvent } from "../lib/clientEvents";
import { fmt, num, pct } from "../lib/format";

interface Props {
  accessToken: string;
  sessionId: string;
  // Static header label identifying the practice being viewed ("Jun 12, 09:49 AM · Senior Cloud
  // Engineer (easy)"). Shown only WITH a rendered report — hidden on the summarizing/processing
  // screen (per live-use feedback).
  sessionLabel?: string;
}

// F006: turn_index -> { turn_id, has_audio } so a per-answer play button can mint a signed URL.
type AudioByIndex = Record<number, { turnId: string; hasAudio: boolean }>;

// F008 (US2): the session's turn sequence as returned by GET /api/sessions/{id}.
interface SessionTurn {
  turn_id: string;
  turn_index: number;
  speaker: "student" | "coach";
  transcript: string;
}

// --- F008 (US1): the report AREA. ---------------------------------------------------------------
// The report page always shows ONE session: an explicitly chosen one (the just-ended session after
// a live interview, or a session picked from the Dashboard's "View report") — else the LATEST
// practice. The session is identified by a static label in the header position (selection happens
// on the Dashboard, not here — per live-use feedback that a dropdown reads as the wrong control).

export function ReportArea({
  accessToken,
  sessionId,
}: {
  accessToken: string;
  // Explicit session to show (just-ended live session, or a Dashboard pick). Absent -> latest.
  sessionId?: string;
}) {
  const [resolved, setResolved] = useState<string | undefined>(sessionId);
  const [label, setLabel] = useState<string>("");
  const [empty, setEmpty] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setResolved(sessionId);
    setLabel("");
    setEmpty(false);
    listSessions(accessToken)
      .then((list) => {
        if (cancelled) return;
        const target = sessionId ?? list[0]?.session_id; // newest first -> [0] is the latest
        if (!target) {
          setEmpty(true);
          return;
        }
        setResolved(target);
        const meta = list.find((s) => s.session_id === target);
        if (meta) setLabel(sessionLabel(meta));
      })
      .catch(() => {
        // List failure: an explicit session still renders, just without the label.
      });
    return () => {
      cancelled = true;
    };
  }, [accessToken, sessionId]);

  if (empty) {
    return (
      <div className="center-stage">
        <h2 className="title">No practice sessions yet</h2>
        <p className="sub">Your reports will appear here after your first practice interview.</p>
      </div>
    );
  }

  if (!resolved) return null; // list still loading and no explicit session

  return (
    <Report
      key={resolved}
      accessToken={accessToken}
      sessionId={resolved}
      sessionLabel={label}
    />
  );
}

function sessionLabel(s: SessionSummary): string {
  const when = s.created_at
    ? new Date(s.created_at).toLocaleString(undefined, {
        month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
      })
    : "Unknown time";
  const role = s.job_title || "Practice interview";
  const status = s.report_status === "scored" ? "" :
    s.report_status === "failed" ? " — scoring failed" : " — report in progress";
  return `${when} · ${role}${s.difficulty ? ` (${s.difficulty})` : ""}${status}`;
}

const PROC_STEPS = [
  "Transcribing your answers",
  "Scoring content & structure (STAR)",
  "Measuring pace & filler words",
  "Writing your strong-answer examples",
];

export default function Report({ accessToken, sessionId, sessionLabel }: Props) {
  const [envelope, setEnvelope] = useState<ReportEnvelope | null>(null);
  const [error, setError] = useState("");
  const [procStep, setProcStep] = useState(0);
  const [audioByIndex, setAudioByIndex] = useState<AudioByIndex>({});
  // F008 (US2): the full turn sequence, kept from the same fetch that builds the audio map.
  const [turns, setTurns] = useState<SessionTurn[]>([]);
  const timer = useRef<number | null>(null);

  // One fetch serves both: the audio map (F006 playback) AND the transcript (F008 US2).
  // Best-effort: if it fails, the report shows no play controls and no transcript section.
  useEffect(() => {
    let cancelled = false;
    fetch(`/api/sessions/${sessionId}`, { headers: { Authorization: `Bearer ${accessToken}` } })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (cancelled || !data?.turns) return;
        const map: AudioByIndex = {};
        for (const t of data.turns) {
          if (typeof t.turn_index === "number" && t.turn_id) {
            map[t.turn_index] = { turnId: t.turn_id, hasAudio: Boolean(t.has_audio) };
          }
        }
        setAudioByIndex(map);
        setTurns(data.turns as SessionTurn[]);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [accessToken, sessionId]);

  useEffect(() => {
    let cancelled = false;
    let reported = false; // beacon once per mount, not once per failed poll cycle
    async function poll() {
      try {
        const env = await getReport(accessToken, sessionId);
        if (cancelled) return;
        setEnvelope(env);
        setError(""); // recovered — clear any transient-failure message
        if (!env || env.status === "queued" || env.status === "processing") {
          timer.current = window.setTimeout(poll, 3000);
        }
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : "Could not load the report.");
        if (!reported) {
          reported = true;
          reportClientEvent("report_load_failed");
        }
        // Keep polling: a transient network blip must not permanently strand the user on the
        // error screen while the report finishes in the background. Backed-off retry.
        timer.current = window.setTimeout(poll, 8000);
      }
    }
    poll();
    return () => {
      cancelled = true;
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [accessToken, sessionId]);

  // Advance the processing checklist while we wait (cosmetic — a designed moment, not a dead spinner).
  const status = envelope?.status;
  const processing = !envelope || status === "queued" || status === "processing";
  useEffect(() => {
    if (!processing) return;
    const iv = window.setInterval(() => setProcStep((s) => Math.min(s + 1, PROC_STEPS.length - 1)), 1600);
    return () => window.clearInterval(iv);
  }, [processing]);

  // F008 (US2/FR-005): the transcript renders in EVERY state — it comes from the session turns,
  // not the scoring pipeline, so processing/failed states still let the student review what was said.
  const transcript = <Transcript turns={turns} />;

  // The practice-session identity label, in the header position. Deliberately ABSENT while the
  // report is summarizing/processing (the user knows which session just ended; the label is for
  // telling PAST reports apart) and on errors.
  const header = sessionLabel ? (
    <div className="report-picker" style={{ padding: "12px 16px 0" }}>
      <span style={{ marginRight: 8, color: "var(--ink-faint)" }}>Practice session</span>
      <b>{sessionLabel}</b>
    </div>
  ) : null;

  if (error) {
    return (
      <div className="center-stage">
        <p className="alert" role="alert">{error}</p>
        {transcript}
      </div>
    );
  }

  if (processing) {
    return (
      <div className="center-stage">
        <div className="proc-ring" />
        <h2 className="title">Analyzing your interview…</h2>
        <p className="sub" style={{ maxWidth: 380 }}>
          This usually takes a moment. Feel free to leave — your report will be here when you're back.
        </p>
        <ul className="proc-steps">
          {PROC_STEPS.map((label, i) => (
            <li key={i} className={i <= procStep ? "done" : ""}>
              <span className="bx">{i <= procStep ? "✓" : i + 1}</span> {label}
            </li>
          ))}
        </ul>
        {transcript}
      </div>
    );
  }

  if (status === "failed") {
    return (
      <div className="center-stage">
        <h2 className="title">We couldn't finish scoring</h2>
        <p className="sub">Something interrupted the report for this session. Please try another session.</p>
        {transcript}
      </div>
    );
  }
  if (!envelope.report) {
    return (
      <div className="center-stage">
        <p className="sub">No report is available for this session.</p>
        {transcript}
      </div>
    );
  }
  return (
    <>
      {header}
      <ScoredReport
        report={envelope.report}
        accessToken={accessToken}
        sessionId={sessionId}
        audioByIndex={audioByIndex}
        transcript={transcript}
      />
    </>
  );
}

// --- F008 (US2): the full conversation, fragment rows grouped per speaker run (research R6). ---

interface SpeakerGroup {
  speaker: "student" | "coach";
  text: string;
}

// Exported for tests: consecutive same-speaker fragment rows (long answers arrive as 3-5 VAD
// fragments) merge into one readable bubble; storage stays fragment-level (audio/timing per row).
export function groupTurns(turns: SessionTurn[]): SpeakerGroup[] {
  const groups: SpeakerGroup[] = [];
  for (const t of turns) {
    const text = (t.transcript || "").trim();
    if (!text) continue;
    const last = groups[groups.length - 1];
    if (last && last.speaker === t.speaker) {
      last.text += " " + text;
    } else {
      groups.push({ speaker: t.speaker, text });
    }
  }
  return groups;
}

function Transcript({ turns }: { turns: SessionTurn[] }) {
  const groups = groupTurns(turns);
  if (groups.length === 0) return null;
  return (
    <details className="transcript" style={{ width: "100%", maxWidth: 720, margin: "18px auto 0", textAlign: "left" }}>
      <summary style={{ cursor: "pointer", fontWeight: 600 }}>
        Full transcript ({groups.length} exchanges)
      </summary>
      <div style={{ marginTop: 10 }}>
        {groups.map((g, i) => (
          <div key={i} style={{ margin: "10px 0" }}>
            <b style={{ color: g.speaker === "coach" ? "var(--accent)" : "var(--ink)" }}>
              {g.speaker === "coach" ? "Coach" : "You"}
            </b>
            <p style={{ margin: "2px 0 0" }}>{g.text}</p>
          </div>
        ))}
      </div>
    </details>
  );
}

// num()/fmt()/pct() live in lib/format.ts (shared NUMERIC-string defense; unit-tested).
function barColor(v: unknown): string {
  const n = num(v);
  if (n == null) return "var(--line)";
  return n >= 6.5 ? "var(--good)" : "var(--accent)";
}

function ScoredReport({
  report: r,
  accessToken,
  sessionId,
  audioByIndex,
  transcript,
}: {
  report: ReportData;
  accessToken: string;
  sessionId: string;
  audioByIndex: AudioByIndex;
  transcript?: React.ReactNode;
}) {
  const assessed = (r.competency_scorecard || []).filter((c) => c.assessed);
  const hasUnassessed = (r.competency_scorecard || []).some((c) => !c.assessed);
  const m = r.metrics || {};
  return (
    <>
      <div className="report-head">
        <div className="score-big">
          <div className="score-circle"><b>{fmt(r.overall)}</b><span>/ 10</span></div>
          <div>
            <h2>Your interview feedback 📋</h2>
            <p>{cap(r.difficulty)} · rubric {r.rubric_version ?? "—"}</p>
          </div>
        </div>
        <span className="pill warm">Difficulty shown for context — never blended into the scores</span>
      </div>

      <div className="subscores">
        <SubScore label="Content / Relevance" v={r.score_content} />
        <SubScore label="Structure (STAR)" v={r.score_structure} />
        <SubScore label="Communication / Clarity" v={r.score_communication} />
        <SubScore label="Confidence" v={r.score_confidence} />
      </div>

      <div className="report-body">
        <p className="hint" style={{ margin: "0 0 4px" }}>
          Scores are absolute on a fixed rubric — a 7 means the same at every difficulty. The tier is
          recorded for context only.
        </p>

        {(r.summary_strengths?.length || r.summary_improvements?.length) ? (
          <div className="summary-grid">
            {r.summary_strengths?.length > 0 && (
              <div className="sumcard good">
                <h4>💪 What you did well</h4>
                <ul>{r.summary_strengths.map((s, i) => <li key={i}>{s}</li>)}</ul>
              </div>
            )}
            {r.summary_improvements?.length > 0 && (
              <div className="sumcard improve">
                <h4>🎯 What to work on</h4>
                <ul>{r.summary_improvements.map((s, i) => <li key={i}>{s}</li>)}</ul>
              </div>
            )}
          </div>
        ) : null}

        {assessed.length > 0 && (
          <>
            <div className="sec-title">🎯 Competency scorecard
              <span style={{ fontWeight: 400, color: "var(--ink-faint)", fontSize: 13 }}>
                · every score anchored to something you said
              </span>
            </div>
            <div className="comp">
              {assessed.map((c, i) => <CompCard key={i} c={c} />)}
            </div>
            {hasUnassessed && (
              <p className="hint" style={{ marginTop: 6 }}>
                Some competencies weren't assessed — there wasn't a clear enough example in your answers
                to score them honestly. We never invent a quote.
              </p>
            )}
          </>
        )}

        <div className="sec-title">🎧 Communication metrics</div>
        <div className="metrics">
          <Metric mv={m.filler_count ?? "—"} ml="filler words" />
          <Metric mv={m.wpm != null ? `${m.wpm}` : "—"} ml="words / min" />
          <Metric mv={m.long_pauses ?? "—"} ml="long pauses" />
          <Metric mv={m.conciseness ?? "—"} ml="avg words / answer" />
          <Metric mv={m.hedging_rate != null ? `${m.hedging_rate}` : "—"} ml="hedging / 100 words" />
          <Metric mv={m.responsiveness ?? "—"} ml="responsiveness" />
        </div>

        {r.question_feedback?.length > 0 && (
          <>
            <div className="sec-title">📝 Per-question feedback</div>
            {r.question_feedback.map((q, i) => {
              const a = q.turn_index != null ? audioByIndex[q.turn_index] : undefined;
              return (
                <QaItem
                  key={i}
                  q={q}
                  open={i === 0}
                  audio={a?.hasAudio ? { accessToken, sessionId, turnId: a.turnId } : undefined}
                />
              );
            })}
          </>
        )}

        {transcript && (
          <>
            <div className="sec-title">🗒️ Full transcript</div>
            {transcript}
          </>
        )}
      </div>
    </>
  );
}

function SubScore({ label, v }: { label: string; v: number | null }) {
  return (
    <div className="ss">
      <div className="lbl">{label}</div>
      <div className="val">{fmt(v)}</div>
      <div className="bar"><i style={{ width: `${pct(v)}%`, background: barColor(v) }} /></div>
    </div>
  );
}

function Metric({ mv, ml }: { mv: string | number; ml: string }) {
  return <div className="metric"><div className="mv">{mv}</div><div className="ml">{ml}</div></div>;
}

function CompCard({ c }: { c: CompetencyScore }) {
  // Same NUMERIC-string defense as the 0-10 sub-scores: score_1_5 may arrive as a string.
  const score = num(c.score_1_5);
  const n = Math.round(score ?? 0);
  const stars = "★".repeat(Math.max(0, Math.min(5, n))) + "☆".repeat(Math.max(0, 5 - n));
  return (
    <div className="comp-card">
      <div className="comp-head">
        <b>{c.competency}</b>
        <span className="stars">{stars} <span className="s15">{score ?? "—"} / 5</span></span>
      </div>
      {c.star_element && <div className="comp-star">{c.star_element}</div>}
      {c.evidence_quote && <blockquote>"{c.evidence_quote}"</blockquote>}
    </div>
  );
}

interface PlaybackRef {
  accessToken: string;
  sessionId: string;
  turnId: string;
}

function QaItem({ q, open, audio }: { q: QuestionFeedback; open: boolean; audio?: PlaybackRef }) {
  const [isOpen, setOpen] = useState(open);
  return (
    <div className={"qa" + (isOpen ? " open" : "")}>
      <div className="qhead" onClick={() => setOpen((o) => !o)}>
        <b>Q · {q.question_text}</b>
        {q.competency && <span className="qtag">{q.competency}</span>}
      </div>
      <div className="qbody">
        <div className="you-said">{q.student_transcript}</div>
        {audio && <RecordingPlayer {...audio} />}
        {q.what_worked && <p><b>What worked:</b> {q.what_worked}</p>}
        {q.what_to_improve && <div className="tip"><b>To improve:</b> {q.what_to_improve}</div>}
        {q.strong_answer_example && (
          <div className="strong"><b>A strong answer (from your background):</b> {q.strong_answer_example}</div>
        )}
      </div>
    </div>
  );
}

// F006: minimal per-answer playback; F008 (US3) hardening. Mints a short-lived signed URL on
// demand and plays it; the URL is held only in transient state (never persisted). An <audio>
// error is never silent: the player re-mints ONCE (the URL may simply have expired, FR-008),
// then shows a visible message and reports playback_failed via the beacon — the live bug was
// exactly an S3-rejected URL that the audio element swallowed without a trace.
function RecordingPlayer({ accessToken, sessionId, turnId }: PlaybackRef) {
  const [url, setUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const retried = useRef(false);

  async function mint(): Promise<string | null> {
    const res = await getTurnAudioUrl(accessToken, sessionId, turnId);
    return res.available && res.url ? res.url : null;
  }

  async function load() {
    setBusy(true);
    setMsg("");
    try {
      const fresh = await mint();
      if (fresh) setUrl(fresh);
      else setMsg("Recording unavailable.");
    } catch {
      setMsg("Could not load the recording.");
    } finally {
      setBusy(false);
    }
  }

  async function onAudioError() {
    if (!retried.current) {
      // One transparent re-mint: an expired URL is the benign cause; anything else will fail
      // again immediately and fall through to the visible error + beacon.
      retried.current = true;
      try {
        const fresh = await mint();
        if (fresh && fresh !== url) {
          setUrl(fresh);
          return;
        }
      } catch {
        /* fall through to the error state */
      }
    }
    setUrl(null);
    setMsg("Playback failed — we couldn't play this recording.");
    reportClientEvent("playback_failed");
  }

  if (url) {
    return (
      <audio
        controls
        src={url}
        onError={onAudioError}
        style={{ width: "100%", margin: "6px 0" }}
      />
    );
  }
  return (
    <div style={{ margin: "6px 0" }}>
      <button type="button" className="btn ghost sm" onClick={load} disabled={busy}>
        {busy ? "Loading…" : "▶ Play my answer"}
      </button>
      {msg && <span className="hint" role="alert" style={{ marginLeft: 8 }}>{msg}</span>}
    </div>
  );
}

function cap(s: string | null): string {
  if (!s) return "—";
  return s.charAt(0).toUpperCase() + s.slice(1);
}
