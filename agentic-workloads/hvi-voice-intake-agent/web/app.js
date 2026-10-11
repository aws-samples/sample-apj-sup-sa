/*
 * Browser half of the SmallWebRTC pair.
 *
 * WHAT THIS SPEAKS, exactly, because it is hand-rolled rather than using the
 * Pipecat JS SDK (see index.html for why):
 *
 *   signalling   POST /api/offer {sdp, type, pc_id, request_data}
 *                -> {sdp, type, pc_id}
 *   data channel opened BY THE BROWSER. The server attaches to whatever channel
 *                arrives, so the label is ours to choose, but it must exist
 *                before the offer is created or it will not be in the SDP.
 *   keepalive    the string "ping". aiortc gives the server no disconnect event,
 *                so Pipecat infers liveness from these. Stop sending them and a
 *                closed tab looks like an open call holding a pipeline.
 *   messages     JSON, {label: "rtvi-ai", type, data} for RTVI, and
 *                {type: "signalling", message: {...}} reserved for transport
 *                business (renegotiate, peerLeft).
 *
 * WHERE THE SCREEN CONTENT COMES FROM
 *   caller speech  the "hvi-turn" message, NOT RTVI's own "user-transcription".
 *                  Interim transcripts do come from RTVI, because they are live
 *                  and have no equivalent on hvi-turn, but a FINAL one is ignored
 *                  there: hvi-turn carries the caller's words alongside the reply
 *                  they produced, so the two halves of a turn cannot separate,
 *                  and it carries only what the agent actually acted on rather
 *                  than fragments the bridge discarded as its own echo.
 *   agent speech   the same "hvi-turn". Pipecat DOES emit bot text of its own -
 *                  TTSService defaults push_text_frames=True and the observer
 *                  defaults bot_output_enabled=True, so "bot-output" arrives on
 *                  every turn - but none of those messages carry the
 *                  orchestrator's turn index, so they can be neither ordered nor
 *                  de-duplicated. They are dropped. See onRtviMessage.
 *   form + record  the same "hvi-turn", assembled in media/bot_web.py.
 *   animation      "user-audio-level" / "bot-audio-level" for amplitude, and the
 *                  speaking start/stop messages for state.
 *
 * hvi-turn is a BARE message on the data channel, not an RTVI "server-message".
 * media/bot_web.py explains why: routed through RTVI it would travel the whole
 * frame pipeline and could arrive behind the following turn.
 */

'use strict';

const RTVI_LABEL = 'rtvi-ai';
const KEEPALIVE_MS = 1000;
const BAR_COUNT = 14;

/*
 * AUTHENTICATION
 *
 * Two modes, chosen by whether the page was served with a `window.HVI_AUTH`
 * config block (bot_web.py injects it when HVI_WEB_AUTH=cognito):
 *
 *   token  (default, the demo path)  - the shared secret from ?token=..., sent
 *          in the POST body as request_data.token alongside the caller phone.
 *          Loopback binding alone does not stop another tab POSTing to this
 *          port; the browser would block it from reading the reply, but the
 *          session would already have started against a real customer record.
 *          This path is left byte-for-byte as it was when HVI_AUTH is absent.
 *
 *   cognito - OAuth2 Authorization Code + PKCE against the Cognito Hosted UI.
 *          The page proves the user signed in and sends the resulting id_token
 *          as request_data.idToken. The server verifies the JWT and derives the
 *          caller phone from a VERIFIED claim, so the page sends NO phone in
 *          this mode - anything it sent would be ignored, and sending it would
 *          only invite the impersonation the verified claim exists to close.
 *
 * Public client, no secret: PKCE (S256) is what stands in for the secret a SPA
 * cannot hold. See RFC 7636.
 */

/** The mode config, normalised. `mode` is always one of 'token' | 'cognito'. */
const AUTH = (() => {
  const cfg = window.HVI_AUTH;
  if (cfg && cfg.mode === 'cognito' && cfg.cognito) {
    const c = cfg.cognito;
    return {
      mode: 'cognito',
      domain: String(c.domain || '').replace(/\/+$/, ''),
      clientId: c.clientId,
      region: c.region,
      redirectUri: c.redirectUri || (window.location.origin + window.location.pathname),
      scope: c.scope || 'openid',
    };
  }
  // Default / demo path: unchanged token behaviour.
  return {
    mode: 'token',
    token: new URLSearchParams(window.location.search).get('token') || '',
  };
})();

// sessionStorage keys, namespaced so they cannot collide with anything the
// Hosted UI or a future feature stashes.
const SS_ID_TOKEN = 'hvi.cognito.id_token';
const SS_EXPIRY = 'hvi.cognito.expiry';     // epoch ms
const SS_PKCE_VERIFIER = 'hvi.cognito.pkce_verifier';

/* --------------------------------------------------------------- pkce util */

function base64UrlEncode(bytes) {
  let s = '';
  const b = new Uint8Array(bytes);
  for (let i = 0; i < b.length; i += 1) s += String.fromCharCode(b[i]);
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function randomVerifier() {
  // 32 random bytes -> 43-char base64url string, comfortably inside the 43-128
  // range RFC 7636 allows.
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return base64UrlEncode(bytes);
}

async function challengeFromVerifier(verifier) {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier));
  return base64UrlEncode(digest);
}

/* ------------------------------------------------------------ cognito flow */

/** Redirect the browser to the Hosted UI to sign in. Does not return. */
async function beginCognitoLogin() {
  const verifier = randomVerifier();
  sessionStorage.setItem(SS_PKCE_VERIFIER, verifier);
  const challenge = await challengeFromVerifier(verifier);
  const url = new URL(AUTH.domain + '/oauth2/authorize');
  url.searchParams.set('response_type', 'code');
  url.searchParams.set('client_id', AUTH.clientId);
  url.searchParams.set('redirect_uri', AUTH.redirectUri);
  url.searchParams.set('scope', AUTH.scope);
  url.searchParams.set('code_challenge_method', 'S256');
  url.searchParams.set('code_challenge', challenge);
  window.location.assign(url.toString());
}

/**
 * Exchange a `?code=` for tokens using the stored PKCE verifier, persist the
 * id_token and its expiry in sessionStorage, and scrub the code out of the URL
 * so a reload (or a shared/pasted address) cannot replay a spent code.
 */
async function exchangeCodeForTokens(code) {
  const verifier = sessionStorage.getItem(SS_PKCE_VERIFIER);
  if (!verifier) throw new Error('no PKCE verifier for this code');
  const res = await fetch(AUTH.domain + '/oauth2/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      grant_type: 'authorization_code',
      client_id: AUTH.clientId,
      code,
      redirect_uri: AUTH.redirectUri,
      code_verifier: verifier,
    }).toString(),
  });
  if (!res.ok) throw new Error('token exchange failed: ' + res.status);
  const tok = await res.json();
  if (!tok.id_token) throw new Error('token response carried no id_token');
  const expiresInMs = (Number(tok.expires_in) || 3600) * 1000;
  sessionStorage.setItem(SS_ID_TOKEN, tok.id_token);
  sessionStorage.setItem(SS_EXPIRY, String(Date.now() + expiresInMs));
  sessionStorage.removeItem(SS_PKCE_VERIFIER);
}

/** A stored id_token that is present and not within 30s of expiry, else ''. */
function validStoredIdToken() {
  const idToken = sessionStorage.getItem(SS_ID_TOKEN);
  const expiry = Number(sessionStorage.getItem(SS_EXPIRY) || 0);
  // 30s skew so a token that expires mid-handshake is treated as already gone.
  if (idToken && expiry && Date.now() < expiry - 30000) return idToken;
  return '';
}

/**
 * Return a usable id_token, or trigger re-login and return '' (the caller must
 * abort - beginCognitoLogin navigates away). Only meaningful in cognito mode.
 */
async function getIdToken() {
  const stored = validStoredIdToken();
  if (stored) return stored;
  await beginCognitoLogin();
  return '';
}

const el = (id) => document.getElementById(id);

const ui = {
  connect: el('connect'),
  hangup: el('hangup'),
  phone: el('phone'),
  dot: el('conn-dot'),
  connText: el('conn-text'),
  orb: el('orb'),
  orbWrap: document.querySelector('.orb-wrap'),
  ringA: el('ring-a'),
  ringB: el('ring-b'),
  coreLabel: el('core-label'),
  bars: el('bars'),
  transcript: el('transcript'),
  items: el('items'),
  progressFill: el('progress-fill'),
  progressText: el('progress-text'),
  chipLang: el('chip-lang'),
  chipLatency: el('chip-latency'),
  chipTurn: el('chip-turn'),
  chipAudio: el('chip-audio'),
  recordBox: el('record-link'),
  recordId: el('record-id'),
  recordAnchor: el('record-anchor'),
  audio: el('bot-audio'),
  enableSound: el('enable-sound'),
  headphones: el('headphones'),
  signIn: el('sign-in'),
};

/*
 * How long to keep the microphone shut after the agent stops.
 *
 * Room reverb and the audio element's own buffer both outlast the "stopped
 * speaking" event, and re-opening the moment it fires lets the tail of the
 * agent's last word through - which is exactly the fragment that gets
 * transcribed as a spurious "Haan".
 */
const MIC_REOPEN_DELAY_MS = 350;

const state = {
  pc: null,
  channel: null,
  localStream: null,
  /** Timer for the delayed mic re-open, so overlapping turns cannot race it. */
  micTimer: null,
  keepalive: null,
  pcId: null,
  /**
   * The orchestrator call id of the CURRENT session. A change means a new call -
   * the first connect, or a reconnect that resumed/restarted the session - and a
   * new call restarts the orchestrator's turn counter at 1. Those low turn
   * numbers would sort ABOVE the previous call's lines (see seqKey) and invert
   * the transcript, which is exactly the "transcript out of order" seen after a
   * dropped connection was reconnected. Tracked so a new call id can reset the
   * transcript instead of interleaving two conversations.
   */
  callId: null,
  /** itemId -> {row, valueEl, item} so a turn updates rows instead of re-rendering. */
  rows: new Map(),
  schemaRendered: false,
  /**
   * Which language the row labels are currently written in.
   *
   * Tracked because the schema arrives on the FIRST turn, and on the first turn
   * the session language is still the default - the caller has not been asked
   * which language they want yet. So the form was drawn in Hinglish and then
   * never redrawn when they answered "English", leaving the whole questionnaire
   * in a language they had explicitly declined.
   */
  schemaLang: null,
  answered: new Set(),
  language: 'en',
  /** The interim transcript node, reused so partials replace rather than stack. */
  interimNode: null,
  /**
   * seqKey -> line node for every FINAL transcript line already drawn. Lets a
   * duplicate turn (reconnect, retransmit) update its line in place instead of
   * stacking a second copy, and lets ordering stay a pure function of the key.
   */
  lineBySeq: new Map(),
  /**
   * Whether a single hvi-turn has ever arrived on this connection.
   *
   * Tracked because the failure it detects is the one that wasted the most time:
   * RTVI messages kept flowing - the orb animated, audio played, the agent could
   * be heard - while not one hvi-turn landed. Everything the page knows about the
   * CONVERSATION rides on that message: the caller's words, the form, the
   * language/latency/turn chips. So the page looked like it was working and was
   * quietly showing an agent-only, unordered, form-less transcript, which reads
   * as three separate bugs rather than one dead message. See noteTurnState.
   */
  sawTurnMessage: false,
  /** Amplitude decay so the bars fall smoothly when messages stop arriving. */
  level: 0,
  decayTimer: null,
};

/* ------------------------------------------------------------------ helpers */

function setStatus(text, cls) {
  ui.connText.textContent = text;
  ui.dot.className = 'dot' + (cls ? ' ' + cls : '');
}

function buildBars() {
  ui.bars.innerHTML = '';
  for (let i = 0; i < BAR_COUNT; i += 1) ui.bars.appendChild(document.createElement('i'));
}

/**
 * Drive the orb and bars from one amplitude reading.
 *
 * The bar heights are shaped by a fixed curve rather than real per-band FFT.
 * A spectrum would mean tapping the decoded audio through an AnalyserNode; the
 * level already arrives over the data channel, and inventing a second audio
 * graph to decorate it would cost main-thread time on the call.
 */
function applyLevel(value) {
  state.level = Math.max(state.level, Math.min(1, Math.max(0, value)));
  const v = state.level;
  ui.ringA.style.transform = `scale(${1 + v * 0.30})`;
  ui.ringB.style.transform = `scale(${1 + v * 0.55})`;
  ui.ringA.style.opacity = String(0.18 + v * 0.5);
  ui.ringB.style.opacity = String(0.10 + v * 0.3);

  const bars = ui.bars.children;
  for (let i = 0; i < bars.length; i += 1) {
    // Centre bars react hardest, so the shape reads as a voice rather than a
    // row of equal sliders.
    const centre = 1 - Math.abs(i - (bars.length - 1) / 2) / ((bars.length - 1) / 2);
    const jitter = 0.75 + 0.25 * Math.sin(i * 1.7 + Date.now() / 90);
    const h = Math.max(0.12, v * (0.35 + 0.65 * centre) * jitter);
    bars[i].style.transform = `scaleY(${h})`;
  }
}

function startDecay() {
  if (state.decayTimer) return;
  state.decayTimer = setInterval(() => {
    if (state.level <= 0.02) {
      state.level = 0;
      applyLevel(0);
      return;
    }
    state.level *= 0.82;
    applyLevel(0);
  }, 70);
}

/**
 * Start playback, and if the browser refuses, SAY SO ON THE PAGE.
 *
 * Autoplay ought to be allowed - the click that started the call is the user
 * gesture browsers require - but it can still be denied: Safari is stricter,
 * per-site sound can be blocked, and an output device can disappear. Previously
 * a rejection went to console.warn only, so the page looked connected, the
 * server logged twelve seconds of speech, and the caller heard nothing with
 * nothing on screen to explain it. That is the worst version of this bug,
 * because it sends you looking at the audio pipeline instead of at the browser.
 */
function tryPlay() {
  const attempt = ui.audio.play();
  if (!attempt || typeof attempt.catch !== 'function') return;
  attempt
    .then(() => ui.enableSound.classList.add('hidden'))
    .catch((err) => {
      console.warn('playback blocked by the browser', err);
      ui.enableSound.classList.remove('hidden');
      setStatus('Connected - click "Enable sound"', 'connecting');
    });
}

/**
 * Open or close the microphone.
 *
 * Done by disabling the outgoing TRACK rather than by ignoring transcripts later.
 * A disabled track sends silence, so the agent's echoed voice never reaches the
 * VAD, never reaches speech recognition, and never costs an inference. Filtering
 * after the fact would still let a garbled fragment through, and the server-side
 * echo guard cannot catch those: it compares against the agent's exact words, and
 * a mis-transcription of them matches nothing.
 */
function setMic(open) {
  if (!state.localStream) return;
  state.localStream.getAudioTracks().forEach((t) => { t.enabled = open; });
  ui.dot.classList.toggle('muted', !open);
}

/** Close the mic while the agent talks, unless the caller says they can't be heard. */
function gateMicForBot(botSpeaking) {
  if (ui.headphones.checked) {
    setMic(true);
    return;
  }
  if (state.micTimer) { clearTimeout(state.micTimer); state.micTimer = null; }
  if (botSpeaking) {
    setMic(false);
  } else {
    state.micTimer = setTimeout(() => {
      state.micTimer = null;
      setMic(true);
    }, MIC_REOPEN_DELAY_MS);
  }
}

function setSpeaker(who) {
  // who: 'agent' | 'user' | 'thinking' | null
  ui.orb.classList.remove('agent', 'user', 'thinking');
  ui.orbWrap.classList.remove('agent', 'user');
  if (who) ui.orb.classList.add(who);
  if (who === 'agent' || who === 'user') ui.orbWrap.classList.add(who);
  ui.coreLabel.textContent =
    who === 'agent' ? 'speaking' :
    who === 'user' ? 'listening' :
    who === 'thinking' ? 'thinking' : 'idle';
}

function addLine(who, text, interim, seq) {
  const empty = ui.transcript.querySelector('.empty');
  if (empty) empty.remove();

  if (interim) {
    if (!state.interimNode) {
      state.interimNode = document.createElement('div');
      state.interimNode.className = 'line user interim';
      state.interimNode.innerHTML = '<span class="who">you</span><span class="txt"></span>';
      ui.transcript.appendChild(state.interimNode);
    }
    state.interimNode.querySelector('.txt').textContent = text;
    ui.transcript.scrollTop = ui.transcript.scrollHeight;
    return;
  }

  // A final transcript replaces the interim bubble rather than adding a second
  // copy of the same sentence.
  if (who === 'user' && state.interimNode) {
    state.interimNode.remove();
    state.interimNode = null;
  }

  // A turn that arrives twice (reconnect, retransmit - the reason ordering is not
  // trusted to the transport) must not stack a second bubble. If we have already
  // drawn a final line at this exact key, update its text in place and stop.
  if (Number.isFinite(seq) && state.lineBySeq.has(seq)) {
    const existing = state.lineBySeq.get(seq);
    const bodyEl = existing.querySelector('.txt');
    if (bodyEl && text) bodyEl.textContent = text;
    return;
  }

  const node = document.createElement('div');
  node.className = 'line ' + who;
  const label = document.createElement('span');
  label.className = 'who';
  label.textContent = who === 'agent' ? 'agent' : 'you';
  const body = document.createElement('span');
  body.className = 'txt';
  body.textContent = text;
  node.append(label, body);

  insertOrdered(node, seq);
  if (Number.isFinite(seq)) state.lineBySeq.set(seq, node);
  ui.transcript.scrollTop = ui.transcript.scrollHeight;
}

/**
 * Place a line by its position in the CONVERSATION, not by when it arrived.
 *
 * Ordering is not trusted to the transport at all. Every line carries a
 * conversation key from `seqKey()` built on the turn index the orchestrator
 * assigned - and the orchestrator is the only thing that knows the true order,
 * because it owns the turn counter - so the DOM is kept sorted by that key. The
 * key is even for the caller and odd for the agent within the same turn, so a
 * caller's answer can never sort above the agent question it answered, and a
 * line that arrives late lands where it belongs instead of at the bottom.
 *
 * Insertion, not a full re-render, so existing lines keep their identity and
 * only the new one animates. Ties (only the `Infinity` fallback for messages
 * that carry no index) append in arrival order rather than inverting, because
 * the scan inserts before the first key STRICTLY greater.
 */
function insertOrdered(node, seq) {
  node.dataset.seq = String(seq);
  const lines = ui.transcript.querySelectorAll('.line');
  for (let i = 0; i < lines.length; i += 1) {
    const other = Number(lines[i].dataset.seq);
    if (Number.isFinite(other) && other > seq) {
      ui.transcript.insertBefore(node, lines[i]);
      return;
    }
  }
  ui.transcript.appendChild(node);
}

/**
 * The conversation-order key for a line. The orchestrator numbers turns, writing
 * the caller's words at `turnIndex-1` and its own reply at `turnIndex` - which
 * means the agent reply of turn N and the caller utterance of turn N+1 both fell
 * on the same integer and could invert. Doubling separates them: caller = 2*turn
 * (even), agent = 2*turn+1 (odd), strictly increasing across the whole call.
 */
function seqKey(turnIndex, who) {
  return turnIndex * 2 + (who === 'agent' ? 1 : 0);
}

/* --------------------------------------------------------------- form panel */

function promptFor(item) {
  if (state.language === 'hi' && item.promptHi) return item.promptHi;
  if (state.language === 'hinglish' && item.promptHinglish) return item.promptHinglish;
  return item.prompt || item.label || item.itemId;
}

/** Trim the spoken prompt down to something that reads as a form label. */
function shortPrompt(text) {
  const t = String(text || '').replace(/\s+/g, ' ').trim();
  const q = t.indexOf('?');
  const cut = q > 0 && q < 130 ? t.slice(0, q + 1) : t;
  return cut.length > 130 ? cut.slice(0, 127) + '\u2026' : cut;
}

function renderValue(value) {
  if (value === true) return 'Yes';
  if (value === false) return 'No';
  if (value === null || value === undefined || value === '') return '\u2014';
  if (Array.isArray(value)) return value.join(', ');
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function renderSchema(items) {
  ui.items.innerHTML = '';
  state.rows.clear();
  let section = null;

  items.forEach((item) => {
    if (item.section && item.section !== section) {
      section = item.section;
      const head = document.createElement('div');
      head.className = 'sec';
      head.textContent = section;
      ui.items.appendChild(head);
    }

    const row = document.createElement('div');
    row.className = 'item' + (item.sensitivity === 'high' ? ' high' : '');
    row.dataset.itemId = item.itemId;

    const tick = document.createElement('span');
    tick.className = 'tick';
    tick.textContent = '\u25CF';

    const q = document.createElement('span');
    q.className = 'q';
    q.textContent = shortPrompt(promptFor(item));

    const a = document.createElement('span');
    a.className = 'a';
    a.textContent = '';

    row.append(tick, q, a);
    ui.items.appendChild(row);
    state.rows.set(item.itemId, { row, valueEl: a, item });
  });

  state.schemaRendered = true;
  state.schemaLang = state.language;

  // TELL THE SERVER, so it stops re-sending the schema.
  //
  // The server cannot otherwise know this arrived. It writes the schema to the
  // data channel on the opening turn, before the channel is necessarily open, and
  // pipecat discards queued app messages once its open timeout has passed without
  // saying so. So the bridge keeps the schema on each turn until this ack, and a
  // call that loses the first copy still gets a labelled form instead of an empty
  // panel for its whole duration. Safe to send from here: we only got the schema
  // because a message just arrived on this channel.
  if (state.channel && state.channel.readyState === 'open') {
    state.channel.send(JSON.stringify({ type: 'hvi-schema-ack', items: items.length }));
  }
}

/**
 * Rewrite the row labels when the caller's language changes.
 *
 * In place rather than a full re-render, so answered values, the current-question
 * outline and the scroll position all survive the switch.
 */
function relabelSchema() {
  if (!state.schemaRendered || state.schemaLang === state.language) return;
  state.rows.forEach(({ row, item }) => {
    const q = row.querySelector('.q');
    if (q) q.textContent = shortPrompt(promptFor(item));
  });
  state.schemaLang = state.language;
}

function applyFormState(formState, askedRef) {
  if (!formState) return;

  if (Array.isArray(formState.items) && formState.items.length && !state.schemaRendered) {
    renderSchema(formState.items);
  } else {
    // Cheap no-op unless the language actually moved since the last turn.
    relabelSchema();
  }

  const answers = formState.answers || {};
  Object.keys(answers).forEach((itemId) => {
    const entry = state.rows.get(itemId);
    if (!entry) return;
    entry.valueEl.textContent = renderValue(answers[itemId]);
    entry.row.classList.add('answered');
    if (!state.answered.has(itemId)) {
      state.answered.add(itemId);
      // Retrigger the flash even if the class is already present.
      entry.row.classList.remove('landed');
      void entry.row.offsetWidth;
      entry.row.classList.add('landed');
    }
  });

  // Highlight what the agent just asked. askedRef is the reliable signal for
  // "the question on the table now"; currentItemId on the session records the
  // item last written, which is a different thing.
  state.rows.forEach(({ row }) => row.classList.remove('current'));
  const current = askedRef && state.rows.get(askedRef);
  if (current) {
    current.row.classList.add('current');
    current.row.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }
}

function applyProgress(progress) {
  if (!progress || typeof progress.total !== 'number' || !progress.total) return;
  const pct = Math.round((progress.answered / progress.total) * 100);
  ui.progressFill.style.width = pct + '%';
  ui.progressText.textContent = `${progress.answered} of ${progress.total} answered`;
}

function showRecord(record) {
  if (!record) return;
  const url = record.recordUrl;
  ui.recordId.textContent = record.recordId || '';
  if (url) {
    ui.recordAnchor.href = url;
    ui.recordAnchor.classList.remove('hidden');
  } else {
    // The Id resolved but the org host did not, so there is nothing to link to.
    // Showing a dead anchor would be worse than showing none.
    ui.recordAnchor.classList.add('hidden');
  }
  ui.recordBox.classList.remove('hidden');
}

/* ------------------------------------------------------------- rtvi routing */

/**
 * Say plainly, on the page, that the turn channel is dead.
 *
 * Called once the agent has finished speaking a turn, by which point an hvi-turn
 * must already have arrived - media/bot_web.py writes it to the data channel
 * BEFORE queueing the audio, so the message cannot still be in flight behind
 * speech the caller has already heard.
 *
 * The empty form placeholder is rewritten as well as the status line, because
 * "The form loads with the first turn" is actively misleading here: the first
 * turn has been and gone. A caller-visible surface that is half working is worse
 * than one that is plainly broken, since only the second sends anyone to look.
 */
function noteTurnState() {
  if (state.sawTurnMessage) return;
  console.error('no hvi-turn message has arrived; the data channel is carrying '
                + 'RTVI events but not turn state');
  setStatus('Connected - transcript and form unavailable', 'error');
  const placeholder = ui.items.querySelector('.empty');
  if (placeholder) {
    placeholder.textContent =
      'The agent is running, but this page is not receiving turn updates, '
      + 'so the form and the caller transcript cannot be shown.';
  }
}

function onServerMessage(data) {
  if (!data || data.type !== 'hvi-turn') return;
  state.sawTurnMessage = true;

  // A new call id means a fresh session: the first connect, or a reconnect that
  // resumed/restarted the orchestrator. Either way the orchestrator's turn
  // counter starts again at 1, so the new lines' seqKeys would sort ABOVE the
  // previous call's lines and invert the transcript. Start it clean for a new
  // call rather than interleaving two conversations. (The first call - callId
  // still null - has nothing to clear.)
  if (data.callId && data.callId !== state.callId) {
    if (state.callId !== null) {
      ui.transcript.innerHTML = '';
      state.lineBySeq.clear();
      state.interimNode = null;
    }
    state.callId = data.callId;
  }

  if (data.language) {
    state.language = data.language;
    ui.chipLang.textContent = 'language ' + data.language;
  }
  if (typeof data.latencyMs === 'number') {
    ui.chipLatency.textContent = 'latency ' + data.latencyMs + 'ms';
  }
  if (typeof data.turnIndex === 'number') {
    ui.chipTurn.textContent = 'turn ' + data.turnIndex;
  }

  // Ordered by the orchestrator's own turn index rather than by arrival. seqKey()
  // maps the caller's words to an even key and the agent's reply to the next odd
  // key, so the true sequence is reproduced even if the messages show up late or
  // out of order, and turn N's reply can never collide with turn N+1's question.
  const agentTurn = typeof data.turnIndex === 'number' ? data.turnIndex : null;
  if (agentTurn !== null) {
    if (data.userUtterance) addLine('user', data.userUtterance, false, seqKey(agentTurn, 'user'));
    if (data.agentUtterance) addLine('agent', data.agentUtterance, false, seqKey(agentTurn, 'agent'));
  } else {
    // No index to sort by; fall back to arrival order rather than dropping it.
    if (data.userUtterance) addLine('user', data.userUtterance, false, Infinity);
    if (data.agentUtterance) addLine('agent', data.agentUtterance, false, Infinity);
  }

  applyFormState(data.formState, data.askedRef);
  applyProgress(data.progress);
  if (data.underwritingRecord) showRecord(data.underwritingRecord);

  if (data.isComplete) {
    setStatus('Intake complete', 'live');
    setSpeaker(null);
  }
}

function onRtviMessage(msg) {
  switch (msg.type) {
    case 'bot-ready':
      setStatus('Connected', 'live');
      break;

    case 'user-transcription': {
      const d = msg.data || {};
      if (!d.text) break;
      // FINAL transcripts are deliberately IGNORED here and rendered from the
      // hvi-turn message instead, so the caller and the agent stay in order and
      // so transcripts the bridge discarded as echo never appear as things the
      // caller said.
      //
      // Interim transcripts are still drawn, because they are genuinely live and
      // have no equivalent on hvi-turn. Our STT is batch and never emits them
      // today; a streaming engine would light this up with no further change.
      if (d.final === false) addLine('user', d.text, true, Infinity);
      break;
    }

    // AGENT TEXT IS DELIBERATELY NOT DRAWN FROM THESE, and the reason is not
    // that they never fire - they do. An earlier comment here claimed our TTS
    // emits no TTSTextFrame so these were dormant. That is false on Pipecat 1.7:
    // TTSService defaults push_text_frames=True and RTVIObserverParams defaults
    // bot_output_enabled=True, so the observer emits aggregated bot text on
    // every turn (a 1.1KB bot-output was measured mid-call).
    //
    // They are dropped because they carry NO TURN INDEX, and without one a line
    // can be neither placed nor recognised again:
    //   * insertOrdered has nothing to sort by, so it appends - the line lands at
    //     the bottom no matter where in the conversation it belongs.
    //   * the de-duplication guard in addLine keys on that index, so a repeat
    //     stacks a second identical bubble instead of updating the first. It
    //     cannot fall back to matching on text either, because this agent
    //     legitimately repeats itself word for word when it re-asks a question it
    //     did not understand, and collapsing those would hide a real turn.
    // Rendering them alongside hvi-turn also double-draws every agent line.
    //
    // hvi-turn is the single source for agent text: it is the only one carrying
    // the orchestrator's turn index, and the only one filtered to what the agent
    // actually acted on rather than every fragment the TTS was handed.
    case 'bot-output':
    case 'bot-tts-text':
    case 'bot-transcription':
      break;

    case 'user-started-speaking':
      setSpeaker('user');
      break;
    case 'user-stopped-speaking':
      // The turn is now a round trip through STT and the orchestrator, and the
      // caller hears nothing during it.
      setSpeaker('thinking');
      break;
    case 'bot-started-speaking':
      setSpeaker('agent');
      gateMicForBot(true);
      break;
    case 'bot-stopped-speaking':
      setSpeaker(null);
      gateMicForBot(false);
      // A full agent turn has now been spoken, so its hvi-turn was written to the
      // channel before the audio was even queued. If none has arrived, the turn
      // channel is dead and the page must say so rather than look healthy.
      noteTurnState();
      break;

    case 'user-audio-level':
    case 'bot-audio-level':
      applyLevel((msg.data || {}).value || 0);
      break;

    case 'server-message':
      onServerMessage(msg.data);
      break;

    case 'error':
      console.error('bot error', msg.data);
      setStatus('Agent error', 'error');
      break;

    default:
      break;
  }
}

function onChannelMessage(raw) {
  if (typeof raw === 'string' && raw.startsWith('ping')) return;
  let msg;
  try {
    msg = JSON.parse(raw);
  } catch (err) {
    console.warn('unparsable data channel message', raw);
    return;
  }
  // Our own per-turn message, written straight to the data channel rather than
  // routed through RTVI's frame pipeline - which is what guarantees it arrives in
  // conversation order. So it is a bare object, not wrapped in an RTVI envelope.
  if (msg.type === 'hvi-turn') {
    onServerMessage(msg);
    return;
  }
  if (msg.label === RTVI_LABEL) {
    onRtviMessage(msg);
    return;
  }
  if (msg.type === 'signalling' && msg.message) {
    if (msg.message.type === 'peerLeft') disconnect('Agent ended the call');
    // "renegotiate" is not handled: this page never adds or drops tracks after
    // the initial offer, so the server has no reason to ask. Logged rather than
    // silently ignored so it is visible if that ever stops being true.
    else console.debug('signalling', msg.message);
  }
}

/* --------------------------------------------------------------- lifecycle */

/**
 * Drop IPv6 ICE candidates from an SDP, forcing the connection onto IPv4.
 *
 * This machine exposes several interfaces - a public IPv6 (mobile), a ULA IPv6,
 * a VPN, a Docker/OrbStack bridge - and ICE kept selecting an IPv6 pair that
 * died within ~30s ("Consent to send expired" / "Media stream error"), dropping
 * the call before the caller could answer a single question. Browser and server
 * are the SAME machine here, so the stable path is the LAN IPv4 host candidate
 * (192.168.x, which the ICE logs show connecting reliably every time). Applied
 * to BOTH the offer we send and the answer we receive, so neither side can offer
 * an IPv6 pair for ICE to pick. An IPv6 address is the one with ':' in it.
 */
function preferIpv4(sdp) {
  if (!sdp) return sdp;
  return sdp
    .split(/\r?\n/)
    .filter((line) => {
      if (!line.startsWith('a=candidate:')) return true;
      const addr = line.split(' ')[4] || '';
      return !addr.includes(':');
    })
    .join('\r\n');
}

/**
 * ICE servers for the peer connection, fetched from the server (/api/ice).
 *
 * The browser is the NAT'd side. With an empty config it offers only host
 * candidates - its private LAN address - which a server behind EC2's 1:1 NAT
 * cannot route back to, so a real call gets no media and ends as "Connection
 * failed". Fetching the in-region STUN/TURN gives it a reflexive candidate (via
 * STUN) and a relay candidate (via TURN) so the media traverses even a strict or
 * symmetric NAT. On loopback the server returns an empty list, so this is a
 * no-op there. A fetch failure is non-fatal: fall back to host-only candidates,
 * which is exactly the previous behaviour.
 */
async function fetchIceServers() {
  try {
    const qs = (AUTH.mode === 'token' && AUTH.token)
      ? '?token=' + encodeURIComponent(AUTH.token)
      : '';
    const res = await fetch('/api/ice' + qs);
    if (!res.ok) return [];
    const data = await res.json();
    return Array.isArray(data.iceServers) ? data.iceServers : [];
  } catch (err) {
    console.warn('could not fetch ICE servers; using host candidates only', err);
    return [];
  }
}

async function connect() {
  ui.connect.disabled = true;
  setStatus('Requesting microphone\u2026', 'connecting');

  try {
    // Constraints matter: this is the browser's echo canceller, and it is the
    // reason a WebRTC page needs no half-duplex gating where the AudioContext
    // test harness did.
    state.localStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
      video: false,
    });
  } catch (err) {
    console.error(err);
    setStatus('Microphone blocked', 'error');
    ui.connect.disabled = false;
    return;
  }

  setStatus('Connecting\u2026', 'connecting');

  // The NAT'd browser needs the in-region STUN/TURN (empty on loopback). See
  // fetchIceServers: without a relay candidate a strict NAT has no reachable
  // path to a server behind EC2's 1:1 NAT.
  const iceServers = await fetchIceServers();
  const pc = new RTCPeerConnection({ iceServers });
  state.pc = pc;

  state.localStream.getTracks().forEach((t) => pc.addTrack(t, state.localStream));

  pc.addEventListener('track', (event) => {
    if (event.track.kind !== 'audio') return;

    // Build the stream explicitly rather than trusting event.streams[0]. With
    // unified-plan SDP a track can arrive with an EMPTY streams array, and
    // assigning undefined to srcObject silently produces a player with nothing
    // in it - audio arriving correctly and no sound, which is exactly the
    // failure this replaced.
    const stream = (event.streams && event.streams[0]) || new MediaStream([event.track]);
    ui.audio.srcObject = stream;
    ui.audio.muted = false;
    ui.audio.volume = 1.0;

    // Proves frames are actually ARRIVING, as distinct from a track that exists
    // but is silent. Surfaced on the page, not just the console, because these
    // are different faults: "audio: waiting" means look at the network or the
    // server, "audio: live" plus silence means look at the browser's output
    // device or the Enable sound button.
    ui.chipAudio.textContent = 'audio waiting';
    event.track.addEventListener('unmute', () => {
      ui.chipAudio.textContent = 'audio live';
      console.info('audio track live');
    });
    event.track.addEventListener('mute', () => {
      ui.chipAudio.textContent = 'audio silent';
    });

    tryPlay();
  });

  pc.addEventListener('connectionstatechange', () => {
    if (pc.connectionState === 'connected') setStatus('Connected', 'live');
    if (pc.connectionState === 'failed') {
      // ICE found no working candidate pair. On loopback/LAN this is rare; on a
      // real network it means even the in-region TURN relay could not carry the
      // media - UDP fully blocked end to end, or the relay was unreachable.
      setStatus('Connection failed', 'error');
      disconnect();
    }
    if (pc.connectionState === 'closed') disconnect();
  });

  // Created before the offer so it is described in the SDP.
  const channel = pc.createDataChannel('hvi');
  state.channel = channel;
  channel.addEventListener('message', (e) => onChannelMessage(e.data));
  channel.addEventListener('open', () => {
    // RTVI handshake. The server answers with "bot-ready", and its own
    // audio-gating option keys off this, so it is sent even though this page
    // does not gate anything on the reply. `id` is required by the message
    // model, not optional.
    channel.send(JSON.stringify({
      label: RTVI_LABEL,
      type: 'client-ready',
      id: 'ready-' + Date.now(),
      // Named so the server log says what connected. Left empty it warns
      // "Client version unknown", which is true but unhelpful when the client is
      // this page rather than an SDK.
      data: { version: '1.7.0', library: 'hvi-web (hand-rolled)' },
    }));

    state.keepalive = setInterval(() => {
      if (channel.readyState === 'open') channel.send('ping');
    }, KEEPALIVE_MS);
  });

  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);
  // Trickle ICE would need a signalling channel that exists before the peer
  // connection does. Gathering to completion first keeps signalling to the one
  // POST the server already exposes, at the cost of a slightly slower start.
  await waitForIceGathering(pc);

  // The credential travels in the POST body rather than the query string so it
  // stays out of access logs and out of the Referer header on any later
  // navigation. What it IS depends on the auth mode:
  //   cognito - a verified id_token; the server derives the phone from a claim,
  //             so the page sends NO phone (the server ignores anything sent).
  //   token   - the shared secret plus the caller-entered phone (demo path).
  let request_data;
  if (AUTH.mode === 'cognito') {
    const idToken = await getIdToken();
    if (!idToken) {
      // getIdToken() has navigated to the Hosted UI; this call is being torn
      // down as the page unloads. Nothing more to do.
      return;
    }
    request_data = { idToken };
  } else {
    request_data = { phone: ui.phone.value.trim(), token: AUTH.token };
  }

  const body = {
    // IPv4-only (see preferIpv4): the IPv6 paths on this host die mid-call.
    sdp: preferIpv4(pc.localDescription.sdp),
    type: pc.localDescription.type,
    pc_id: state.pcId,
    request_data,
  };

  let answer;
  try {
    const res = await fetch('/api/offer', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (res.status === 403) {
      // Mode-aware, because the fix differs: in cognito the session most likely
      // expired between sign-in and Start call and the user must sign in again;
      // in token mode it is almost always a URL opened without its ?token=.
      if (AUTH.mode === 'cognito') {
        // Drop the stale token so the next attempt re-authenticates cleanly.
        sessionStorage.removeItem(SS_ID_TOKEN);
        sessionStorage.removeItem(SS_EXPIRY);
        setStatus('Session expired - sign in again', 'error');
        disconnect('Session expired - sign in again');
        refreshAuthUi();
      } else {
        setStatus('Rejected: missing or wrong token in the URL', 'error');
        disconnect('Rejected: missing or wrong token in the URL');
      }
      return;
    }
    if (!res.ok) throw new Error('offer rejected: ' + res.status);
    answer = await res.json();
  } catch (err) {
    console.error(err);
    setStatus('Could not reach the agent', 'error');
    disconnect();
    return;
  }

  if (!answer || !answer.sdp) {
    setStatus('No answer from the agent', 'error');
    disconnect();
    return;
  }

  state.pcId = answer.pc_id || null;
  // Strip the server's IPv6 candidates too, so ICE can only pair IPv4.
  await pc.setRemoteDescription({ sdp: preferIpv4(answer.sdp), type: answer.type });

  ui.hangup.disabled = false;
  startDecay();
  setSpeaker('thinking');
}

function waitForIceGathering(pc) {
  if (pc.iceGatheringState === 'complete') return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => {
      if (pc.iceGatheringState === 'complete') {
        pc.removeEventListener('icegatheringstatechange', done);
        resolve();
      }
    };
    pc.addEventListener('icegatheringstatechange', done);
    // Never hang the UI on a candidate that will not arrive.
    setTimeout(resolve, 2500);
  });
}

function disconnect(reason) {
  if (state.keepalive) { clearInterval(state.keepalive); state.keepalive = null; }
  if (state.decayTimer) { clearInterval(state.decayTimer); state.decayTimer = null; }
  if (state.micTimer) { clearTimeout(state.micTimer); state.micTimer = null; }
  ui.dot.classList.remove('muted');
  if (state.channel) { try { state.channel.close(); } catch (e) { /* closing */ } state.channel = null; }
  if (state.pc) { try { state.pc.close(); } catch (e) { /* closing */ } state.pc = null; }
  if (state.localStream) {
    state.localStream.getTracks().forEach((t) => t.stop());
    state.localStream = null;
  }
  state.pcId = null;
  state.interimNode = null;
  // Per-connection, so the next call is judged on its own delivery rather than
  // inheriting a verdict from this one.
  state.sawTurnMessage = false;
  applyLevel(0);
  setSpeaker(null);
  // Cleared so a stale "audio live" from the previous call cannot be read as the
  // state of the next one.
  ui.chipAudio.textContent = 'audio \u2014';
  ui.enableSound.classList.add('hidden');
  ui.audio.srcObject = null;
  ui.connect.disabled = false;
  ui.hangup.disabled = true;
  if (reason) setStatus(reason, null);
  else if (ui.dot.className.indexOf('error') === -1) setStatus('Call ended', null);
}

/* -------------------------------------------------------------------- boot */

/**
 * Reconcile the auth-related UI with the current mode and session state.
 *
 * token mode  - the phone field and Start button are the whole story; the
 *               sign-in button stays hidden. Nothing here changes the demo path.
 * cognito mode - the caller does not type a phone (it comes from the verified
 *               claim), so the phone field is hidden. If there is no valid
 *               id_token yet, show Sign in and disable Start until they have
 *               one; once signed in, hide Sign in and enable Start.
 */
function refreshAuthUi() {
  if (AUTH.mode !== 'cognito') {
    if (ui.signIn) ui.signIn.classList.add('hidden');
    return;
  }
  // Phone is server-derived in cognito mode; hide the input so it cannot look
  // like it matters.
  if (ui.phone) {
    const field = ui.phone.closest('.field');
    if (field) field.classList.add('hidden');
  }
  const signedIn = !!validStoredIdToken();
  if (ui.signIn) ui.signIn.classList.toggle('hidden', signedIn);
  ui.connect.disabled = !signedIn;
  if (!signedIn) setStatus('Sign in to start', null);
}

/**
 * On a Hosted-UI redirect back, exchange the code for tokens before anything
 * else runs. Strips ?code= (and ?state=) from the address bar afterwards so a
 * reload cannot replay a spent code and the URL is clean to share.
 */
async function initAuth() {
  if (AUTH.mode !== 'cognito') {
    refreshAuthUi();
    return;
  }
  const params = new URLSearchParams(window.location.search);
  const code = params.get('code');
  if (code) {
    try {
      await exchangeCodeForTokens(code);
    } catch (err) {
      console.error('code exchange failed', err);
      setStatus('Sign-in failed - try again', 'error');
    }
    // Scrub the OAuth params regardless of outcome; a failed code is spent too.
    params.delete('code');
    params.delete('state');
    const clean = window.location.pathname + (params.toString() ? '?' + params.toString() : '');
    history.replaceState({}, document.title, clean);
  }
  refreshAuthUi();
}

buildBars();
applyLevel(0);
ui.connect.addEventListener('click', connect);
if (ui.signIn) ui.signIn.addEventListener('click', () => { beginCognitoLogin(); });
ui.enableSound.addEventListener('click', () => {
  ui.audio.muted = false;
  tryPlay();
});
ui.hangup.addEventListener('click', () => disconnect('Call ended'));
// A closed tab must release the pipeline; the keepalive stopping is what the
// server notices, but closing explicitly is faster and tidier.
window.addEventListener('beforeunload', () => disconnect());

initAuth();
