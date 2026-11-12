/**
 * voiceClient — thin wrapper over the Pipecat JS client for the analytics
 * dashboard's Voice Mode (presenter). Supports TWO deploy modes, chosen by which
 * runtime-config URL is present:
 *
 *  • AgentCore mode (VOICE_SIGNALING_URL): SmallWebRTC transport + KVS managed TURN.
 *    The browser POSTs its SDP offer (and PATCHes trickled ICE) to
 *    {VOICE_SIGNALING_URL}/api/offer. A JWT-gated signaling proxy validates the
 *    Cognito token at the API-Gateway edge, forwards it as a Bearer to the JWT-only
 *    voice AgentCore Runtime /invocations, and unwraps the runtime's SSE answer into
 *    the plain JSON SDP answer the transport expects. Media flows browser ↔ KVS TURN
 *    ↔ runtime.
 *
 *  • Pipecat Cloud mode (VOICE_START_URL): Daily transport + Daily SFU. The browser
 *    POSTs its Cognito Bearer token (+ shared runtimeSessionId) to the JWT start
 *    proxy ({VOICE_START_URL}); the proxy validates the token, calls Pipecat Cloud
 *    /start server-side with the PCC key, and returns {dailyRoom, dailyToken}. The
 *    browser then joins that Daily room via DailyTransport. Media flows browser ↔
 *    Daily SFU ↔ PCC-hosted bot.
 *
 * In BOTH modes the same Cognito access token is the identity the bot forwards to
 * the analytics runtime (per-user RBAC/RLS), and the same RTVI events surface to
 * ChatPanel via the callbacks below. Voice is additive: this module is only loaded
 * when the user turns Voice Mode ON; text chat (awsAgentCore.invokeAgent) is
 * unaffected.
 *
 * Verified against @pipecat-ai/client-js (PipecatClient, RTVIEvent),
 * @pipecat-ai/small-webrtc-transport (SmallWebRTCTransport, webrtcRequestParams),
 * and @pipecat-ai/daily-transport (DailyTransport; connect({url, token})).
 */
import { PipecatClient, RTVIEvent } from '@pipecat-ai/client-js';
import { SmallWebRTCTransport } from '@pipecat-ai/small-webrtc-transport';
import { DailyTransport } from '@pipecat-ai/daily-transport';
import { fetchAccessToken } from './authService';
import { getRuntimeSessionId } from './awsAgentCore';

// Coerce a transcript value (string, { text }, { text: { text } }, or other
// nested shapes Pipecat client/transport versions emit) into a plain string, so
// the chat never renders "[object Object]". Returns '' if nothing string-like.
function _asText(v, depth = 0) {
  if (v == null || depth > 4) return '';
  if (typeof v === 'string') return v;
  if (typeof v === 'object') return _asText(v.text, depth + 1);
  return '';
}

// A single hidden <audio> element that plays the bot's audio track. Without
// this, the bot generates speech and sends it over WebRTC but nothing in the
// page renders it, so the user hears nothing (the bare Playground does this for
// you; our custom integration must do it explicitly).
let _botAudioEl = null;
function getBotAudioEl() {
  if (_botAudioEl) return _botAudioEl;
  const el = document.createElement('audio');
  el.id = 'pipecat-bot-audio';
  el.autoplay = true;
  el.style.display = 'none';
  document.body.appendChild(el);
  _botAudioEl = el;
  return el;
}
function attachBotAudio(track) {
  const el = getBotAudioEl();
  const stream = new MediaStream([track]);
  el.srcObject = stream;
  const p = el.play();
  if (p && p.catch) p.catch(() => { /* autoplay gated; user gesture already happened on toggle */ });
}
function detachBotAudio() {
  if (_botAudioEl) {
    _botAudioEl.srcObject = null;
  }
}

// Runtime config: window.__APP_CONFIG__ (Amplify-injected) or REACT_APP_* (local).
//  • VOICE_SIGNALING_URL → AgentCore (SmallWebRTC) voice signaling proxy base URL.
//  • VOICE_START_URL     → Pipecat Cloud (Daily) JWT start proxy URL.
// Exactly one is normally set per deploy (CFN injects VOICE_SIGNALING_URL for
// VoiceMode=agentcore; deploy_voice_pcc.sh patches VOICE_START_URL for
// VoiceMode=pipecat-cloud). When neither is set, voiceConfigured() is false and the
// UI hides the Voice button entirely. If BOTH are set, AgentCore takes precedence.
const RC = (typeof window !== 'undefined' && window.__APP_CONFIG__) || {};
export const VOICE_SIGNALING_URL =
  RC.VOICE_SIGNALING_URL ||
  process.env.REACT_APP_VOICE_SIGNALING_URL ||
  '';
export const VOICE_START_URL =
  RC.VOICE_START_URL ||
  process.env.REACT_APP_VOICE_START_URL ||
  '';

// 'agentcore' (SmallWebRTC+KVS) | 'pipecat-cloud' (Daily) | '' (not configured).
export function voiceMode() {
  if (VOICE_SIGNALING_URL) return 'agentcore';
  if (VOICE_START_URL) return 'pipecat-cloud';
  return '';
}

export function voiceConfigured() {
  return !!voiceMode();
}

/**
 * Create and connect a voice session.
 *
 * @param {object} opts
 * @param {(text:string)=>void} opts.onUserTranscript  final user speech → text
 * @param {(text:string)=>void} opts.onBotSpoken       bot's spoken narrative (TTS text)
 * @param {(markdown:string)=>void} opts.onDisplay      displayed-track markdown (RTVI display-text)
 * @param {(data:object)=>void} opts.onServerMessage    any other RTVI server message (chart, panel, sql-approval)
 * @param {()=>void} opts.onReady                       bot ready (connected end-to-end)
 * @param {(e:any)=>void} opts.onError
 * @param {()=>void} opts.onDisconnected
 * @returns {Promise<PipecatClient>}
 */
export async function startVoiceSession(opts) {
  const {
    onUserTranscript,
    onBotSpoken,
    onDisplay,
    onServerMessage,
    onReady,
    onError,
    onDisconnected,
    onThinking,
    sessionId,          // app session id, shared with the text chat
  } = opts;

  const mode = voiceMode();
  if (!mode) throw new Error('Voice is not configured (no VOICE_SIGNALING_URL or VOICE_START_URL).');

  // The user's Cognito access token is the identity the bot forwards to the analytics
  // runtime (per-user RBAC/RLS). Resolve it FIRST — the AgentCore TURN fetch below
  // needs it, and we refresh it right before connect so a long-idle tab doesn't open
  // with an expired one.
  let userToken = null;
  try {
    userToken = await fetchAccessToken();
  } catch (e) {
    // proxy will reject with 401 if the token is absent/expired
  }
  // Shared session id so voice + text chat share one AgentCore Memory thread.
  const sharedSessionId = sessionId ? getRuntimeSessionId(sessionId) : '';

  // For AgentCore, the browser must use the SAME Amazon KVS managed-TURN servers the
  // runtime uses — otherwise the browser only gathers `host` candidates (LAN IPs) and,
  // since the runtime lives in a VPC with no public IP, ICE can never find a route
  // through NAT (the connection just times out). Fetch the TURN creds from the
  // signaling proxy (GET /api/ice, JWT-gated) and hand them to the transport so it
  // generates relay candidates. waitForICEGathering avoids a trickle race with the
  // stateless runtime (ICE candidates can otherwise land before the peer registers).
  let iceServers;
  if (mode === 'agentcore' && VOICE_SIGNALING_URL && userToken) {
    try {
      const iceResp = await fetch(`${VOICE_SIGNALING_URL}/api/ice`, {
        headers: { Authorization: `Bearer ${userToken}` },
      });
      if (iceResp.ok) {
        const data = await iceResp.json();
        if (Array.isArray(data.iceServers) && data.iceServers.length) {
          iceServers = data.iceServers;
        }
      }
    } catch (e) {
      console.warn('[voice] could not fetch TURN ICE servers; falling back to host-only', e);
    }
  }

  // Pick the transport for the deploy mode. SmallWebRTC for AgentCore (the browser
  // does the SDP/ICE handshake against the signaling proxy); Daily for Pipecat Cloud
  // (the browser joins a Daily room minted by the start proxy).
  const transport = mode === 'agentcore'
    ? new SmallWebRTCTransport(
        iceServers ? { iceServers, waitForICEGathering: true } : { waitForICEGathering: true })
    : new DailyTransport();
  const client = new PipecatClient({
    transport,
    enableMic: true,
    enableCam: false,
  });

  // Fire onReady exactly once, from whichever signal arrives first. We do NOT
  // rely solely on RTVIEvent.BotReady: on a hosted (Pipecat Cloud) cold start it
  // can be slow or, if the RTVI handshake hiccups, never arrive — which left the
  // UI stuck on "enabling voice…" forever. The bot's audio track starting is an
  // equally good "we're connected" signal. A watchdog (below) bounds the wait.
  let _readyFired = false;
  const fireReady = () => {
    if (_readyFired) return;
    _readyFired = true;
    if (readyWatchdog) { clearTimeout(readyWatchdog); readyWatchdog = null; }
    if (onReady) onReady();
  };
  let readyWatchdog = null;

  // Bot's spoken text (mirror of TTS) → chat as the spoken-echo bubble.
  client.on(RTVIEvent.BotTtsText, (data) => {
    const text = data?.text ?? data;
    if (text && onBotSpoken) onBotSpoken(text);
  });

  // User speech transcript. TranscriptData = { text, final }. userTranscript
  // fires for BOTH partials and finals — only surface FINALs (final === true),
  // so interim/self-corrected partials don't each become a separate bubble.
  client.on(RTVIEvent.UserTranscript, (data) => {
    if (!data || data.final !== true) return;
    // `text` is normally a string, but some client/transport versions wrap it
    // (e.g. { text: { text: "…" } } or a TranscriptData object) — coerce to a
    // plain string so the chat never renders "[object Object]".
    const text = _asText(data.text) || _asText(data);
    if (text && onUserTranscript) onUserTranscript(text);
  });

  // Server messages: our bot pushes {type:'display-text', markdown} and may push
  // {type:'chart'|'panel'|'sql-approval', ...}. Route display-text specially.
  client.on(RTVIEvent.ServerMessage, (msg) => {
    const data = msg?.data ?? msg;
    if (!data) return;
    if (data.type === 'display-text' && onDisplay) {
      onDisplay(data.markdown || '');
      return;
    }
    if (onServerMessage) onServerMessage(data);
  });

  // Play the bot's audio track. TrackStarted fires with (track, participant);
  // the bot is the non-local participant. Attach its audio track so it's heard.
  client.on(RTVIEvent.TrackStarted, (track, participant) => {
    if (track && track.kind === 'audio' && (!participant || !participant.local)) {
      attachBotAudio(track);
      // Bot audio is flowing → we're connected. Clears the "enabling…" state even
      // if BotReady is delayed/missed.
      fireReady();
    }
  });

  // "Thinking" window: user stopped talking → agent is working. Cleared when the
  // real answer arrives (onDisplay in ChatPanel) or on error/disconnect.
  client.on(RTVIEvent.UserStoppedSpeaking, () => onThinking && onThinking(true));

  client.on(RTVIEvent.BotReady, () => fireReady());
  client.on(RTVIEvent.Error, (e) => onError && onError(e));
  client.on(RTVIEvent.Disconnected, () => { detachBotAudio(); if (onDisconnected) onDisconnected(); });

  // (userToken + sharedSessionId were resolved above, before the TURN fetch.)

  // Watchdog: if neither BotReady nor bot audio arrives within the bound, stop
  // hanging on "enabling voice…" — tear down and report an error the UI can show.
  // 45s comfortably covers an AgentCore microVM cold start or a PCC cold start
  // (~10s) + WebRTC/Daily setup.
  readyWatchdog = setTimeout(() => {
    if (_readyFired) return;
    _readyFired = true;
    try { client.disconnect(); } catch (e) { /* best effort */ }
    if (onError) onError(new Error('Voice timed out while connecting. Please try again.'));
  }, 45000);

  try {
    if (mode === 'agentcore') {
      // AgentCore: the SmallWebRTC transport POSTs its SDP offer (and PATCHes
      // trickled ICE) to {VOICE_SIGNALING_URL}/api/offer with the Bearer token +
      // shared session id headers; requestData carries the session id into the body.
      const headers = new Headers();
      if (userToken) headers.set('Authorization', `Bearer ${userToken}`);
      if (sharedSessionId) headers.set('X-Amzn-Bedrock-AgentCore-Runtime-Session-Id', sharedSessionId);
      await client.connect({
        webrtcRequestParams: {
          endpoint: `${VOICE_SIGNALING_URL}/api/offer`,
          headers,
          requestData: sharedSessionId ? { runtimeSessionId: sharedSessionId } : undefined,
        },
      });
    } else {
      // Pipecat Cloud: call the JWT start proxy ourselves (it isn't the stock
      // Pipecat /start shape — it validates our Cognito token, calls PCC with the
      // secret key, and returns {dailyRoom, dailyToken}). Then join that Daily room.
      const startHeaders = { 'Content-Type': 'application/json' };
      if (userToken) startHeaders['Authorization'] = `Bearer ${userToken}`;
      const startResp = await fetch(VOICE_START_URL, {
        method: 'POST',
        headers: startHeaders,
        body: JSON.stringify(sharedSessionId ? { runtimeSessionId: sharedSessionId } : {}),
      });
      if (!startResp.ok) {
        let detail = '';
        try { detail = (await startResp.json()).error || ''; } catch (e) { /* ignore */ }
        throw new Error(`Voice start failed: HTTP ${startResp.status}${detail ? ' — ' + detail : ''}`);
      }
      const { dailyRoom, dailyToken } = await startResp.json();
      if (!dailyRoom) throw new Error('Voice start returned no Daily room.');
      // DailyTransport.connect expects Daily call options: { url, token }.
      await client.connect({ url: dailyRoom, token: dailyToken });
    }
  } catch (e) {
    if (readyWatchdog) { clearTimeout(readyWatchdog); readyWatchdog = null; }
    throw e;
  }

  return client;
}

export async function stopVoiceSession(client) {
  detachBotAudio();
  if (!client) return;
  try {
    await client.disconnect();
  } catch (e) {
    // best-effort teardown
    // eslint-disable-next-line no-console
    console.warn('voice disconnect error', e);
  }
}
