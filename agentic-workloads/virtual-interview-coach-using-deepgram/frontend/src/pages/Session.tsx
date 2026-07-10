// The only screen G1 needs (T027): sign in, then connect / talk / hang-up.
// Drives the full US1 happy path and surfaces the failure messages (FR-013) a student can hit:
// microphone denied, connection could not be established, session ended unexpectedly.

import { useEffect, useRef, useState } from "react";
import { reportClientEvent, setClientEventsToken } from "../lib/clientEvents";
import { endSession, type CreateSessionResponse } from "../lib/sessionApi";
import { connectMedia, type MediaSessionHandle, type TurnMode } from "../lib/webrtcClient";
import DeviceCheck, { type DeviceSelection } from "../components/DeviceCheck";
import { ReportArea } from "./Report";
import Setup from "./Setup";
import Dashboard from "./Dashboard";
import Privacy from "./Privacy";
import {
  completeNewPassword,
  devToken,
  isCognitoConfigured,
  signIn,
} from "../lib/auth";

type Status = "setup" | "idle" | "connecting" | "live" | "ended" | "error";

export default function Session() {
  // Marketing landing is the entry screen; "Get started" advances to sign-in (prototype S1 -> S2).
  const [entered, setEntered] = useState(false);
  // Auth: with Cognito configured the student signs in; otherwise we use a dev token locally.
  const [token, setToken] = useState<string | null>(
    isCognitoConfigured() ? null : devToken()
  );

  // Make the token available to the client-events beacon (incl. the root ErrorBoundary, which
  // sits above this component and cannot read its state). Cleared on unmount/sign-out so a
  // stale (possibly deleted-account) token is never reported with a later event.
  useEffect(() => {
    setClientEventsToken(token);
    return () => setClientEventsToken(null);
  }, [token]);

  if (!entered) {
    return <Landing onStart={() => setEntered(true)} />;
  }
  if (!token) {
    return <SignIn onToken={setToken} />;
  }
  return <Practice accessToken={token} />;
}

// S1 landing/hero — the first thing a visitor sees. A calm pitch + "Get started" CTA, then sign-in.
function Landing({ onStart }: { onStart: () => void }) {
  return (
    <div className="app">
      <AppBar />
      <div className="hero">
        <div className="logo-lg">🎤</div>
        <h1>Practice your interview. As many times as you want.</h1>
        <p className="lead">
          A calm, private place to rehearse real job interviews out loud — with personalized feedback
          that helps you improve, session after session.
        </p>
        <button className="btn primary" onClick={onStart}>Get started</button>
        <div className="feats">
          <div className="feat"><b>🗣️ Real voice practice</b>Talk through a realistic interview, not a text box.</div>
          <div className="feat"><b>🎯 Personalized feedback</b>Tailored to your resume and the job you want.</div>
          <div className="feat"><b>📈 Watch yourself improve</b>Track your progress over weeks of prep.</div>
        </div>
      </div>
    </div>
  );
}

function SignIn({ onToken }: { onToken: (t: string) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [session, setSession] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (session) {
        const res = await completeNewPassword(email, newPassword, session);
        onToken(res.idToken);
        return;
      }
      const res = await signIn(email, password);
      if (res.challenge === "NEW_PASSWORD_REQUIRED") {
        setSession(res.session ?? "");
        return;
      }
      onToken(res.idToken);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app">
      <AppBar />
      <div className="pad">
        <div className="center-wrap">
          <h2 className="title">Welcome 👋</h2>
          <div className="card">
            <form onSubmit={submit}>
              <div className="field">
                <label>Email</label>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  autoComplete="username"
                  required
                />
              </div>
              {!session && (
                <div className="field">
                  <label>Password</label>
                  <input
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    autoComplete="current-password"
                    required
                  />
                </div>
              )}
              {session && (
                <div className="field">
                  <label>Set a new password</label>
                  <input
                    type="password"
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    autoComplete="new-password"
                    required
                  />
                </div>
              )}
              <button type="submit" disabled={busy} className="btn primary lg">
                {busy ? "Signing in…" : session ? "Set password & continue" : "Sign in"}
              </button>
            </form>
            {error && <p className="alert" role="alert" style={{ marginTop: 12 }}>{error}</p>}
            <p className="hint" style={{ textAlign: "center", marginTop: 16 }}>
              By continuing you agree to our Privacy Notice. We store your resume and job description
              securely, encrypted, and delete them automatically after 30 days.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

// Shared brand app bar (prototype look). `right` lets a screen add a step pill / sign-out / avatar.
export function AppBar({ right }: { right?: React.ReactNode }) {
  return (
    <div className="appbar">
      <div className="brand"><div className="logo">🎤</div>InterviewCoach</div>
      {right}
    </div>
  );
}

function Practice({ accessToken }: { accessToken: string }) {
  const [status, setStatusState] = useState<Status>("setup");
  // Mirror of `status` for callbacks that outlive a render (onClosed fires from the ICE layer long
  // after start() ran — reading the captured `status` there saw the stale "connecting" value, so a
  // mid-interview network drop showed the user nothing).
  const statusRef = useRef<Status>("setup");
  function setStatus(s: Status) {
    statusRef.current = s;
    setStatusState(s);
  }
  const [message, setMessage] = useState<string>("");
  // The personalized session is created during Setup (it assembles the blueprint before returning);
  // Practice then connects media to that already-prepared session — no second createSession call.
  const [session, setSession] = useState<CreateSessionResponse | null>(null);
  const [checking, setChecking] = useState(false);
  // Turn-taking mode: "auto" = hands-free (the coach decides when you're done); "ptt" = hold to
  // talk (you press to speak, release to hand over — no false triggers on a pause).
  const [turnMode, setTurnMode] = useState<TurnMode>("auto");
  const [holding, setHolding] = useState(false);
  // Elapsed seconds for the live-interview timer (prototype S6). Counts up while status === "live".
  const [elapsed, setElapsed] = useState(0);
  // Post-session: the wrap-up card shows first; revealing the report unlocks the report/dashboard/
  // privacy nav. `reportOpen` gates that; `endedView` switches between the three post-session views.
  const [reportOpen, setReportOpen] = useState(false);
  const [endedView, setEndedView] = useState<"report" | "dashboard" | "privacy">("report");
  // F008: which session the Report view shows. Defaults to the just-ended session; the Dashboard's
  // "View report" buttons set it (session selection lives on the Dashboard, not the report page).
  const [reportSessionId, setReportSessionId] = useState<string | undefined>(undefined);
  const audioRef = useRef<HTMLAudioElement>(null);
  const handleRef = useRef<MediaSessionHandle | null>(null);

  // Drive the live-interview elapsed timer; reset whenever we leave the live state.
  useEffect(() => {
    if (status !== "live") {
      setElapsed(0);
      return;
    }
    const iv = window.setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => window.clearInterval(iv);
  }, [status]);

  // Unmount cleanup: release the mic + peer connection if the component goes away mid-session
  // (StrictMode re-mounts, future routing) — otherwise the mic indicator stays on after navigation.
  useEffect(() => {
    return () => {
      handleRef.current?.stop().catch(() => {});
      handleRef.current = null;
    };
  }, []);

  function changeMode(mode: TurnMode) {
    setTurnMode(mode);
    handleRef.current?.setMode(mode);
  }

  // Setup completed and the personalized session (with its blueprint) is ready: move to the
  // pre-session screen (mode + device check) without creating another session.
  function onSetupReady(created: CreateSessionResponse) {
    setSession(created);
    setStatus("idle");
    setMessage("");
  }

  // Return to setup for a fresh interview. Used by "Start a new session" after a session ends.
  function reset() {
    setStatus("setup");
    setChecking(false);
    setMessage("");
    setSession(null);
    setReportOpen(false);
    setEndedView("report");
    handleRef.current = null;
  }

  function pressStart() {
    setHolding(true);
    handleRef.current?.turnStart();
  }
  function pressEnd() {
    if (!holding) return;
    setHolding(false);
    handleRef.current?.turnEnd();
  }

  async function start(devices: DeviceSelection = {}) {
    setChecking(false);
    setStatus("connecting");
    setMessage("");
    try {
      if (!session) throw new Error("session not prepared");
      if (!audioRef.current) throw new Error("audio element missing");
      handleRef.current = await connectMedia({
        mediaEndpoint: session.media_endpoint,
        voiceToken: session.voice_token,
        iceServers: session.ice_servers,
        remoteAudioEl: audioRef.current,
        inputDeviceId: devices.inputDeviceId,
        outputDeviceId: devices.outputDeviceId,
        onConnected: () => {
          setStatus("live");
          // Push the current mode to the worker once the control channel is up (default "auto").
          handleRef.current?.setMode(turnMode);
        },
        onClosed: () => {
          // statusRef (not the captured `status`) — this fires long after start()'s render.
          if (statusRef.current === "live") {
            setStatus("ended");
            setMessage("The session ended unexpectedly. Your conversation so far was saved.");
            reportClientEvent("session_dropped");
          }
        },
      });
    } catch (err) {
      setStatus("error");
      if (err instanceof DOMException && err.name === "NotAllowedError") {
        setMessage(
          "A microphone is required for the interview. Please allow microphone access and try again."
        );
        reportClientEvent("mic_denied");
      } else {
        setMessage("We couldn't start the session. Please check your connection and try again.");
        reportClientEvent("connect_failed");
      }
    }
  }

  if (status === "setup") {
    return <Setup accessToken={accessToken} onReady={onSetupReady} />;
  }

  async function hangUp() {
    // Leave "live" BEFORE stop() — stop() fires onClosed, whose statusRef check must not mistake
    // this voluntary hang-up for an unexpected drop (it would overwrite the friendly message).
    setStatus("ended");
    setMessage("Session ended. Nice work!");
    try {
      await handleRef.current?.stop();
      if (session) await endSession(accessToken, session.session_id);
    } catch {
      /* the session UI has already moved on; backend end_session is idempotent */
    }
  }

  // Hidden audio sink for the coach's voice (present in every state once past setup).
  const audio = <audio ref={audioRef} autoPlay />;

  // --- Live interview: full-bleed prototype look (difficulty·role pill, timer, coach avatar,
  // transcript, animated waveform + controls). The waveform animates while the mic is "open" —
  // always in hands-free mode, only while the button is held in push-to-talk. ---
  if (status === "live") {
    const waveOn = turnMode === "auto" || holding;
    return (
      <div className="interview">
        {audio}
        <div className="iv-top">
          <span className="pill warm">{liveScopePill(session)}</span>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <span className="iv-timer">{mmss(elapsed)}</span>
            <button className="btn ghost sm" onClick={hangUp}>End interview</button>
          </div>
        </div>
        <div className="iv-body">
          <div className="coach-avatar speaking"><span className="ring" />🤖</div>
          <div className="speaker-label">Your coach</div>
          <div className="transcript-live" aria-live="polite">
            {turnMode === "auto"
              ? "You're connected — go ahead and answer out loud. I'll reply when you pause."
              : "You're connected — hold the button below while you speak, then release."}
          </div>
        </div>
        <div className="iv-controls">
          <div className={"wave" + (waveOn ? " on" : "")} aria-hidden>
            <span /><span /><span /><span /><span />
          </div>
          {turnMode === "ptt" ? (
            <button
              className={"talk-btn" + (holding ? " holding" : "")}
              onPointerDown={(e) => { e.preventDefault(); pressStart(); }}
              onPointerUp={pressEnd}
              onPointerLeave={pressEnd}
              onPointerCancel={pressEnd}
            >
              {holding ? "Listening… release when done" : "🎤 Hold to talk"}
            </button>
          ) : (
            <span className="pill teal">Hands-free — just speak</span>
          )}
          <button className="btn ghost sm" onClick={hangUp}>End session</button>
        </div>
      </div>
    );
  }

  // --- Ended: spoken debrief already played. The wrap-up bridges to the async report; from there a
  // top nav switches between the Report, the (design-preview) progress Dashboard, and Privacy. ---
  if (status === "ended" && session) {
    const nav = (
      <nav>
        <button className={endedView === "dashboard" ? "on" : ""} onClick={() => setEndedView("dashboard")}>Dashboard</button>
        <button className={endedView === "report" ? "on" : ""} onClick={() => setEndedView("report")}>Report</button>
        <button className={endedView === "privacy" ? "on" : ""} onClick={() => setEndedView("privacy")}>Privacy</button>
      </nav>
    );
    return (
      <div className="app">
        {audio}
        <AppBar right={
          <>
            {reportOpen && nav}
            <button className="signout" onClick={reset}>New session</button>
          </>
        } />
        {!reportOpen ? (
          <div className="center-stage">
            <div className="wrap-orb"><span className="ring" />✅</div>
            <span className="pill good" style={{ marginBottom: 14 }}>Nicely done — that's a wrap!</span>
            <p className="sub" style={{ maxWidth: 420 }}>
              {message || "Your spoken debrief just played. Your detailed written report is being prepared."}
            </p>
            <button className="btn primary" onClick={() => setReportOpen(true)}>
              See my full report →
            </button>
            <p className="hint" style={{ marginTop: 14, maxWidth: 380 }}>
              The scored report processes in the background — all the numbers live there.
            </p>
          </div>
        ) : endedView === "dashboard" ? (
          <Dashboard
            accessToken={accessToken}
            onPracticeAgain={reset}
            onOpenReport={(sid) => {
              setReportSessionId(sid);
              setEndedView("report");
            }}
          />
        ) : endedView === "privacy" ? (
          <Privacy
            accessToken={accessToken}
            initialRecording={session.recordAudio ?? true}
            onAccountDeleted={reset}
          />
        ) : (
          <ReportArea
            accessToken={accessToken}
            sessionId={reportSessionId ?? session.session_id}
          />
        )}
      </div>
    );
  }

  // --- Idle (pre-session: turn mode + mic check), connecting, error ---
  return (
    <div className="app">
      {audio}
      <AppBar right={<button className="signout" onClick={reset}>Exit</button>} />
      <div className="pad">
        <div className="center-wrap">
          {status === "idle" && (
            <>
              <h2 className="title">Almost there — let's check your setup</h2>
              <p className="sub">Choose how you'd like to take turns, then we'll do a quick mic check before your interview begins.</p>
              <TurnModeChooser turnMode={turnMode} onChange={changeMode} disabled={checking} />
              {!checking && (
                <button className="btn primary lg" onClick={() => setChecking(true)}>Continue to mic check →</button>
              )}
              {checking && (
                <div className="card">
                  <DeviceCheck onReady={start} readyLabel="Mic looks good — start session" />
                </div>
              )}
            </>
          )}
          {status === "connecting" && (
            <div className="center-stage">
              <div className="proc-ring" />
              <h2 className="title">Connecting your audio…</h2>
              <p className="sub">One moment while we set up the live connection.</p>
            </div>
          )}
          {status === "error" && (
            <>
              <h2 className="title">Something interrupted the session</h2>
              <p className="alert" role="alert">{message}</p>
              <button className="btn primary" onClick={reset} style={{ marginTop: 12 }}>Start a new session</button>
            </>
          )}
          {message && status === "connecting" && <p className="alert" role="alert">{message}</p>}
        </div>
      </div>
    </div>
  );
}

// Turn-taking mode selector, shown before a session starts. Disabled once the device check is up
// so the choice can't change mid-setup; it is sent to the worker when the session connects.
function TurnModeChooser({
  turnMode,
  onChange,
  disabled,
}: {
  turnMode: TurnMode;
  onChange: (m: TurnMode) => void;
  disabled?: boolean;
}) {
  return (
    <fieldset className="group" style={{ opacity: disabled ? 0.6 : 1 }} disabled={disabled}>
      <legend>How would you like to take turns?</legend>
      <label style={radioRow}>
        <input
          type="radio"
          name="turnmode"
          checked={turnMode === "auto"}
          onChange={() => onChange("auto")}
        />
        <span>
          <strong>Hands-free</strong> — the coach replies when you pause.
        </span>
      </label>
      <label style={radioRow}>
        <input
          type="radio"
          name="turnmode"
          checked={turnMode === "ptt"}
          onChange={() => onChange("ptt")}
        />
        <span>
          <strong>Hold to talk</strong> — press and hold while you speak, release to reply.
        </span>
      </label>
    </fieldset>
  );
}

const radioRow: React.CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  gap: "0.5rem",
  margin: "0.5rem 0",
  cursor: "pointer",
};

// mm:ss for the live-interview elapsed timer.
function mmss(secs: number): string {
  const m = String(Math.floor(secs / 60)).padStart(2, "0");
  const s = String(secs % 60).padStart(2, "0");
  return `${m}:${s}`;
}

// The warm "<emoji> <Difficulty> · <role>" pill shown in the live interview top bar. Falls back to a
// generic label when the session carries no job scope (the G1 generic path).
const DIFF_EMOJI: Record<string, string> = { easy: "🌱", moderate: "⚖️", difficult: "🔥" };
function liveScopePill(session: CreateSessionResponse | null): string {
  const diff = session?.difficulty;
  const role = session?.jobTitle;
  if (diff && role) {
    return `${DIFF_EMOJI[diff] ?? "🎙️"} ${diff[0].toUpperCase()}${diff.slice(1)} · ${role}`;
  }
  if (role) return `🎙️ ${role}`;
  return "🎙️ Interview in progress";
}
