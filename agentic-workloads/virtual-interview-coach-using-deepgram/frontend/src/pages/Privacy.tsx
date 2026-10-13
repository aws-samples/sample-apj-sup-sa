// Privacy & data-control screen (S11, prototype look). Wires the real backend where it exists:
//   - the recording/consent toggle -> PUT /api/me/consent (FR-220)
//   - "Delete my account & all data" -> DELETE /api/me (bounded hard delete, FR-219)
// Retention (fixed 30 days) and the "improve the product" toggle are policy/aspirational and shown
// for transparency. A toast confirms each action. The auto-delete + train-on-data rows are presented
// as settings but are not yet backed by dedicated endpoints, so they only reflect/keep local state.

import { useState } from "react";
import { deleteAccount, setConsent } from "../lib/setupApi";

interface Props {
  accessToken: string;
  // Best-known current recording consent (from setup); the toggle starts here.
  initialRecording?: boolean;
  // Called after a successful account delete so the shell can return to a signed-out/landing state.
  onAccountDeleted?: () => void;
}

export default function Privacy({ accessToken, initialRecording = true, onAccountDeleted }: Props) {
  const [recording, setRecording] = useState(initialRecording);
  const [autoDelete, setAutoDelete] = useState(true);
  const [improve, setImprove] = useState(false);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState("");

  function flash(msg: string) {
    setToast(msg);
    window.setTimeout(() => setToast(""), 2600);
  }

  async function toggleRecording() {
    const next = !recording;
    setRecording(next); // optimistic
    setBusy(true);
    try {
      await setConsent(accessToken, next, 30);
      flash(next ? "Recording on — your answers will be saved" : "Recording off — no audio will be stored");
    } catch {
      setRecording(!next); // revert on failure
      flash("Couldn't update that setting. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  async function onDeleteAccount() {
    if (!window.confirm("Permanently delete your account and ALL data — recordings, transcripts, and scores? This cannot be undone.")) {
      return;
    }
    setBusy(true);
    try {
      await deleteAccount(accessToken);
      flash("Account deletion requested — removing everything we hold.");
      window.setTimeout(() => onAccountDeleted?.(), 1200);
    } catch {
      flash("Could not delete your data. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="pad">
      <div className="center-wrap">
        <h2 className="title">Your data, your control 🔒</h2>
        <p className="sub">
          We record your practice sessions so you can re-listen and track progress. You decide what's
          kept — and you can delete anything instantly.
        </p>

        <div className="card">
          <div className="priv-row">
            <div className="pl">
              <b>Record &amp; store my sessions</b>
              <p>Lets you replay your answers and see progress over time. Stored encrypted, in one region.</p>
            </div>
            <button
              className={"toggle" + (recording ? "" : " off")}
              onClick={toggleRecording}
              disabled={busy}
              aria-pressed={recording}
              aria-label="Record and store my sessions"
            />
          </div>
          <div className="priv-row">
            <div className="pl">
              <b>Auto-delete recordings after 30 days</b>
              <p>Sessions you don't "keep" are removed automatically.</p>
            </div>
            <button
              className={"toggle" + (autoDelete ? "" : " off")}
              onClick={() => { setAutoDelete((v) => !v); flash("Preference saved"); }}
              aria-pressed={autoDelete}
              aria-label="Auto-delete recordings after 30 days"
            />
          </div>
          <div className="priv-row">
            <div className="pl">
              <b>Use my data to improve InterviewCoach</b>
              <p>Off by default. We never train on your voice without explicit opt-in.</p>
            </div>
            <button
              className={"toggle" + (improve ? "" : " off")}
              onClick={() => { setImprove((v) => !v); flash("Preference saved"); }}
              aria-pressed={improve}
              aria-label="Use my data to improve the product"
            />
          </div>
        </div>

        <div className="card" style={{ marginTop: 16 }}>
          <b style={{ fontSize: 14 }}>Delete data</b>
          <div className="priv-row" style={{ marginTop: 8 }}>
            <div className="pl">
              <b>Delete my account &amp; all data</b>
              <p>Permanent. Removes every recording, transcript, and score we hold for you.</p>
            </div>
            <button className="btn ghost sm danger" onClick={onDeleteAccount} disabled={busy}>
              Delete account
            </button>
          </div>
        </div>
      </div>
      {toast && <div className="toast show">{toast}</div>}
    </div>
  );
}
