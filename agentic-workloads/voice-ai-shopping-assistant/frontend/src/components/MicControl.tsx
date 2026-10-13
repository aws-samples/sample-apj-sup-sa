import { useConversation } from "../store/conversation";
import { startSession } from "../lib/session-client";
import { joinRoom, leaveRoom, setMicMuted, primeAudioPlayback } from "../lib/daily-client";

/** Connect / mute / end. The single call-to-action that starts the demo. */
export function MicControl() {
  const connection = useConversation((s) => s.connection);
  const micMuted = useConversation((s) => s.micMuted);
  const setConnection = useConversation((s) => s.setConnection);
  const reset = useConversation((s) => s.reset);

  const connecting = connection === "connecting";
  const live = connection === "connected";

  async function connect() {
    try {
      // Unlock audio autoplay synchronously inside this click gesture, so the
      // agent's audio (arriving ~10s later) is allowed to play.
      primeAudioPlayback();
      setConnection("connecting");
      const { room_url } = await startSession();
      await joinRoom(room_url);
    } catch (e) {
      setConnection("error", e instanceof Error ? e.message : "failed to connect");
    }
  }

  async function end() {
    await leaveRoom();
    reset();
  }

  if (!live) {
    return (
      <div className="mic-control">
        <button className="btn primary big" onClick={connect} disabled={connecting}>
          {connecting ? "Connecting…" : "🎤 Start talking"}
        </button>
      </div>
    );
  }

  return (
    <div className="mic-control">
      <button
        className={`btn ${micMuted ? "muted" : ""}`}
        onClick={() => setMicMuted(!micMuted)}
      >
        {micMuted ? "🔇 Unmute" : "🎤 Mute"}
      </button>
      <button className="btn danger" onClick={end}>
        End
      </button>
    </div>
  );
}
