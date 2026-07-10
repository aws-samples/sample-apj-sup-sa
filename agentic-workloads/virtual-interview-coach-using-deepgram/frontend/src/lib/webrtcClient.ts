// WebRTC media client (T025) — establishes the DIRECT media path to the voice worker
// (via the NLB in production), with TURN fallback supplied in ice_servers (US3/FR-009).
//
// Captures the microphone, opens an RTCPeerConnection, plays the coach's audio, and reports
// the chosen ICE path (direct | relayed) so connect time and network_path can be measured.

import type { IceServer } from "./sessionApi";

export type NetworkPath = "direct" | "relayed" | "unknown";

export type TurnMode = "auto" | "ptt";

export interface MediaSessionHandle {
  pc: RTCPeerConnection;
  stop: () => Promise<void>;
  networkPath: () => NetworkPath;
  // Push-to-talk control channel. setMode switches turn-taking; turnStart/turnEnd bracket a
  // held button press (turnStart also barges in if the coach is mid-reply). No-ops until the
  // control data channel is open.
  setMode: (mode: TurnMode) => void;
  turnStart: () => void;
  turnEnd: () => void;
}

export interface ConnectOptions {
  mediaEndpoint: string;
  voiceToken: string;
  iceServers: IceServer[];
  remoteAudioEl: HTMLAudioElement;
  // Device ids chosen in the pre-flight check. When set, capture uses THIS exact mic (not the
  // browser default, which can be a silent/virtual device) and routes coach audio to THIS speaker.
  inputDeviceId?: string;
  outputDeviceId?: string;
  onConnected?: () => void;
  onClosed?: () => void;
}

// Establish the media session. Returns once the peer connection is connected (ready for the
// first spoken turn — measured against SC-005, connect < 5s).
export async function connectMedia(opts: ConnectOptions): Promise<MediaSessionHandle> {
  const pc = new RTCPeerConnection({
    iceServers: opts.iceServers.map((s) => ({
      urls: s.urls,
      username: s.username,
      credential: s.credential,
    })),
  });

  let path: NetworkPath = "unknown";

  // Microphone in; AEC/NS/AGC enabled — WebRTC's browser-side echo cancellation is one of the
  // reasons WebRTC (not WSS) is the chosen transport (reliable barge-in). When the pre-flight
  // check picked a specific device, pin to it with deviceId.exact so we don't silently fall back
  // to a dead "Default" input.
  const audioConstraints: MediaTrackConstraints = {
    echoCancellation: true,
    noiseSuppression: true,
    autoGainControl: true,
  };
  if (opts.inputDeviceId) {
    audioConstraints.deviceId = { exact: opts.inputDeviceId };
  }
  const mic = await navigator.mediaDevices.getUserMedia({ audio: audioConstraints });
  mic.getTracks().forEach((t) => pc.addTrack(t, mic));

  // Control data channel for push-to-talk turn signals + mode selection. Created before the
  // offer so it is negotiated in the same SDP exchange; the worker reads it as the "control"
  // channel.
  const control = pc.createDataChannel("control");
  // The channel often is NOT "open" yet when onConnected fires (ICE connects a beat before the
  // SCTP data channel finishes opening), so setMode(turnMode) was being silently dropped — the
  // worker then ran in default auto regardless of the user's choice. Buffer the latest mode (an
  // earlier mode is superseded) until the channel opens, then flush it.
  let pendingMode: Record<string, unknown> | null = null;
  const sendControl = (msg: Record<string, unknown>) => {
    if (control.readyState === "open") {
      try {
        control.send(JSON.stringify(msg));
      } catch {
        /* channel closing — drop the signal */
      }
    } else if (msg.type === "mode") {
      // Persist the desired mode across the open; transient turn signals are not worth queueing.
      pendingMode = msg;
    }
  };
  control.onopen = () => {
    if (pendingMode) {
      try {
        control.send(JSON.stringify(pendingMode));
      } catch {
        /* channel closing — drop the signal */
      }
      pendingMode = null;
    }
  };

  // Coach audio out.
  pc.ontrack = (ev) => {
    opts.remoteAudioEl.srcObject = ev.streams[0];
    // Route playback to the chosen speaker if the browser supports setSinkId (Chromium).
    if (opts.outputDeviceId) {
      const el = opts.remoteAudioEl as HTMLAudioElement & {
        setSinkId?: (id: string) => Promise<void>;
      };
      el.setSinkId?.(opts.outputDeviceId).catch(() => {
        /* sink selection unsupported (e.g. Safari/Firefox) — fall back to system default */
      });
    }
  };

  // onClosed must fire exactly once per session: a voluntary stop() both closes the pc (which
  // fires iceconnectionstatechange -> "closed") AND calls the callback explicitly, and a network
  // drop can emit "failed" then "closed". Without the guard the caller would handle the close twice.
  let closedFired = false;
  const fireClosed = () => {
    if (closedFired) return;
    closedFired = true;
    opts.onClosed?.();
  };

  // Detect whether the winning candidate pair is relayed (TURN) or direct.
  pc.addEventListener("iceconnectionstatechange", async () => {
    if (pc.iceConnectionState === "connected" || pc.iceConnectionState === "completed") {
      path = await detectNetworkPath(pc);
      opts.onConnected?.();
    }
    if (pc.iceConnectionState === "failed" || pc.iceConnectionState === "closed") {
      fireClosed();
    }
  });

  // Signaling: exchange SDP with the media endpoint, authenticated by the short-lived
  // voice_token. INTEGRATION POINT: the exact signaling channel depends on the worker's
  // transport (e.g. an HTTP offer/answer endpoint or a WS signaling socket at mediaEndpoint).
  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);
  const answer = await signal(opts.mediaEndpoint, opts.voiceToken, offer);
  await pc.setRemoteDescription(answer);

  return {
    pc,
    networkPath: () => path,
    setMode: (mode: TurnMode) => sendControl({ type: "mode", value: mode }),
    turnStart: () => sendControl({ type: "turn_start" }),
    turnEnd: () => sendControl({ type: "turn_end" }),
    stop: async () => {
      mic.getTracks().forEach((t) => t.stop());
      pc.getSenders().forEach((s) => s.track?.stop());
      pc.close();
      fireClosed();
    },
  };
}

async function detectNetworkPath(pc: RTCPeerConnection): Promise<NetworkPath> {
  try {
    const stats = await pc.getStats();
    let path: NetworkPath = "direct";
    stats.forEach((report) => {
      if (report.type === "candidate-pair" && (report as any).nominated) {
        const localId = (report as any).localCandidateId;
        const local = stats.get(localId) as any;
        if (local && local.candidateType === "relay") path = "relayed";
      }
    });
    return path;
  } catch {
    return "unknown";
  }
}

// Placeholder signaling exchange. Replace with the worker's actual offer/answer transport.
async function signal(
  endpoint: string,
  voiceToken: string,
  offer: RTCSessionDescriptionInit
): Promise<RTCSessionDescriptionInit> {
  const resp = await fetch(`${endpoint}/offer`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${voiceToken}` },
    body: JSON.stringify({ sdp: offer.sdp, type: offer.type }),
  });
  if (!resp.ok) throw new Error("media signaling failed");
  return (await resp.json()) as RTCSessionDescriptionInit;
}
