/**
 * Daily transport client. The deployed agent meets the browser in a Daily room
 * (WebRTC) — Daily handles mic capture and agent audio playback natively, so we
 * do NOT hand-roll AudioWorklet/PCM here (that path is only relevant to the
 * spec's future AgentCore /ws agent).
 *
 * Responsibilities:
 *   - join the room returned by the start endpoint
 *   - publish the mic; mute/unmute
 *   - play the agent's audio track (attach to an <audio> element)
 *   - sample audio levels (mic + agent) to drive the VoiceOrb
 *   - route data-channel app-messages → event-adapter
 */
import Daily, {
  type DailyCall,
  type DailyEventObjectAppMessage,
  type DailyEventObjectParticipant,
  type DailyEventObjectTrack,
} from "@daily-co/daily-js";
import { handleAgentMessage } from "./event-adapter";
import { useConversation } from "../store/conversation";

let call: DailyCall | null = null;
let agentAudioEl: HTMLAudioElement | null = null;
let levelTimer: number | null = null;
// Latest agent tracks. When the avatar publishes video, we play its audio and
// video together on the single <video> element (browser keeps them lip-synced)
// rather than on a separate <audio> element (which drifts ahead of the video).
let agentAudioTrack: MediaStreamTrack | null = null;
let agentVideoTrack: MediaStreamTrack | null = null;
// Session id of the avatar participant (the one publishing video). Its OWN audio
// is rendered in lip-sync with its video; we must play that, not the bot's raw
// TTS "stream" custom track (which arrives ahead of the rendered lips).
let avatarSessionId: string | null = null;

function ensureAudioEl(): HTMLAudioElement {
  if (!agentAudioEl) {
    agentAudioEl = document.createElement("audio");
    agentAudioEl.autoplay = true;
    agentAudioEl.style.display = "none";
    document.body.appendChild(agentAudioEl);
  }
  return agentAudioEl;
}

// A tiny silent WAV. Playing this inside the user's click gesture "unlocks"
// the audio element so the agent's audio (which arrives ~10s later, after the
// gesture's autoplay grant has expired) is allowed to play.
const SILENT_WAV =
  "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA=";

/**
 * Unlock browser audio autoplay. MUST be called synchronously inside a user
 * gesture (e.g. the "Start talking" click) — see MicControl.
 */
export function primeAudioPlayback(): void {
  const el = ensureAudioEl();
  try {
    el.srcObject = null;
    el.src = SILENT_WAV;
    el.muted = false;
    const p = el.play();
    if (p && typeof p.catch === "function") p.catch(() => {});
  } catch {
    /* ignore */
  }
}

function playAgentAudio(el: HTMLAudioElement) {
  el.play().catch((err) => {
    console.warn("[daily] agent audio autoplay blocked:", err?.name);
    // Fallback: resume on the user's next interaction anywhere on the page.
    const resume = () => {
      el.play().catch(() => {});
      document.removeEventListener("pointerdown", resume);
    };
    document.addEventListener("pointerdown", resume, { once: true });
  });
}

/**
 * Route the agent's audio/video to the right element(s):
 *   - Avatar present (video track): play audio + video together on the single
 *     <video> element so the browser keeps them lip-synced, and mute the
 *     separate <audio> element to avoid double (and out-of-sync) audio.
 *   - Audio-only: play the agent audio via the hidden <audio> element.
 */
function syncAgentPlayback() {
  const el = ensureAudioEl();
  if (agentVideoTrack) {
    // Avatar present: play its OWN audio (lip-synced with its video) together
    // with the video on one element. Prefer the avatar participant's audio track
    // over whatever fired last (which may be the bot's raw TTS "stream" track).
    let audio = agentAudioTrack;
    if (avatarSessionId && call) {
      const p = call.participants()[avatarSessionId];
      const t = p?.tracks?.audio?.persistentTrack || p?.tracks?.audio?.track;
      if (t) audio = t as MediaStreamTrack;
    }
    const tracks: MediaStreamTrack[] = [agentVideoTrack];
    if (audio) tracks.push(audio);
    el.muted = true; // avatar's audio comes from the <video> element instead
    useConversation.getState().setAgentVideo(new MediaStream(tracks));
  } else {
    el.muted = false;
    if (agentAudioTrack) {
      el.removeAttribute("src");
      el.srcObject = new MediaStream([agentAudioTrack]);
      el.volume = 1;
      playAgentAudio(el);
    }
    useConversation.getState().setAgentVideo(null);
  }
}

export async function joinRoom(roomUrl: string): Promise<void> {
  const store = useConversation.getState();
  if (call) await leaveRoom();

  call = Daily.createCallObject({
    audioSource: true,
    videoSource: false,
  });

  // Track the agent's audio/video; syncAgentPlayback decides where each plays.
  call.on("track-started", (ev?: DailyEventObjectTrack) => {
    if (!ev || ev.participant?.local) return;
    if (ev.track.kind === "audio") {
      agentAudioTrack = ev.track;
      syncAgentPlayback();
    } else if (ev.track.kind === "video") {
      agentVideoTrack = ev.track;
      avatarSessionId = ev.participant?.session_id ?? null;
      syncAgentPlayback();
    }
  });

  call.on("track-stopped", (ev?: DailyEventObjectTrack) => {
    if (!ev || ev.participant?.local) return;
    if (ev.track.kind === "video") {
      agentVideoTrack = null;
      avatarSessionId = null;
      syncAgentPlayback(); // fall back to the <audio> element
    } else if (ev.track.kind === "audio") {
      agentAudioTrack = null;
    }
  });

  // Data channel: tool results + transcripts.
  call.on("app-message", (ev?: DailyEventObjectAppMessage) => {
    if (ev?.data !== undefined) handleAgentMessage(ev.data);
  });

  call.on("participant-joined", (ev?: DailyEventObjectParticipant) => {
    if (ev && !ev.participant.local) store.setAgentState("listening");
  });

  call.on("left-meeting", () => {
    store.setConnection("ended");
    store.setAgentState("idle");
    store.setAgentVideo(null);
  });

  call.on("error", (ev) => {
    store.setConnection("error", (ev as { errorMsg?: string })?.errorMsg ?? "Daily error");
  });

  await call.join({ url: roomUrl });
  startLevelSampling();
  store.setConnection("connected");
  store.setAgentState("listening");
}

/**
 * Poll Daily's per-participant audio levels to animate the orb. Local mic level
 * drives "listening"; remote (agent) level drives "speaking".
 */
function startLevelSampling() {
  stopLevelSampling();
  levelTimer = window.setInterval(() => {
    if (!call) return;
    const store = useConversation.getState();
    const participants = call.participants();
    let agentLevel = 0;
    let micLevel = 0;
    for (const p of Object.values(participants)) {
      // audioLevel is provided when local audio level observation is on; fall
      // back to track presence so the orb still reacts if levels are unavailable.
      const lvl =
        (p as unknown as { audioLevel?: number }).audioLevel ??
        (p.tracks.audio?.state === "playable" ? 0.4 : 0);
      if (p.local) micLevel = lvl;
      else agentLevel = Math.max(agentLevel, lvl);
    }
    store.setLevels(micLevel, agentLevel);

    // Refine derived agent_state from levels.
    if (!store.micMuted && agentLevel < 0.05 && micLevel > 0.08) {
      if (store.agentState !== "speaking") store.setAgentState("listening");
    } else if (agentLevel >= 0.05) {
      store.setAgentState("speaking");
    }
  }, 120);

  // Ask Daily to compute local audio levels.
  call?.startLocalAudioLevelObserver?.(100).catch?.(() => {});
  call?.startRemoteParticipantsAudioLevelObserver?.(100).catch?.(() => {});
}

function stopLevelSampling() {
  if (levelTimer !== null) {
    clearInterval(levelTimer);
    levelTimer = null;
  }
}

export function setMicMuted(muted: boolean): void {
  call?.setLocalAudio(!muted);
  useConversation.getState().setMicMuted(muted);
}

export async function leaveRoom(): Promise<void> {
  stopLevelSampling();
  agentAudioTrack = null;
  agentVideoTrack = null;
  avatarSessionId = null;
  if (call) {
    try {
      await call.leave();
    } catch {
      /* ignore */
    }
    call.destroy();
    call = null;
  }
  if (agentAudioEl) {
    agentAudioEl.srcObject = null;
    agentAudioEl.muted = false;
  }
}
