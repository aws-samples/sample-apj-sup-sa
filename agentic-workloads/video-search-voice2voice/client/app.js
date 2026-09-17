/* Talk to your Video — browser client.
 *
 * Speaks Pipecat's SmallWebRTC signalling (POST /api/offer) and the RTVI protocol
 * over the peer's data channel directly, so there is no SDK, no bundler and no
 * CDN in the hot path.
 *
 * Playback rule that shapes this file: the user owns the video. Nothing here ever
 * *starts* playback — connecting the mic does not autoplay, and a tool-driven jump
 * moves the playhead while leaving play/pause exactly as it was. The one automatic
 * move is pausing: the agent must never talk over the clip, so taking the floor
 * (opening the mic, speaking, typing) pauses it. Pressing play stays your call.
 */

const $ = (id) => document.getElementById(id);
const els = {
  picker: $('picker'), library: $('library'), heroStats: $('heroStats'),
  stage: $('stage'), video: $('video'), track: $('track'),
  videoTitle: $('videoTitle'), videoTag: $('videoTag'),
  playBtn: $('playBtn'), playIcon: $('playIcon'), playOverlay: $('playOverlay'),
  pausedFlag: $('pausedFlag'), timeNow: $('timeNow'), timeTotal: $('timeTotal'),
  latency: $('latency'), mic: $('mic'), micLabel: $('micLabel'), level: $('level'),
  state: $('state'), stateText: $('stateText'), log: $('log'), activity: $('activity'),
  starters: $('starters'), chapters: $('chapters'), chapterCount: $('chapterCount'),
  botAudio: $('botAudio'), toast: $('toast'), ask: $('ask'), askForm: $('askForm'),
  back: $('back'), home: $('home'), clear: $('clear'),
};

const PLAY_PATH = 'M8 5v14l11-7z';
const PAUSE_PATH = 'M7 5h4v14H7zm6 0h4v14h-4z';

let videos = [];
let current = null;
let transportKind = 'webrtc';   // from /api/health; 'daily' when deployed
let session = null;             // active transport driver
let pc = null, dc = null, pcId = null, micStream = null;
let call = null;                // Daily call object
let audioCtx = null, analyser = null, levelTimer = null, pingTimer = null, playheadTimer = null;
let outbox = [];
let botBubble = null;
let lastUserSpokeAt = 0;
// Hard cap on one spoken turn, from /api/health. A room with people talking in
// it keeps the server's VAD in its speaking state, and the turn controller will
// not finalize a turn while it still hears a voice — so a turn opened by room
// noise never ends on its own, and everything said near the laptop piles into
// one question. Muting the mic is the one thing that reliably ends it.
let maxListenSecs = 5;
let capTimer = null, reopenTimer = null;

/* ---------------------------------------------------------------- helpers */
const fmt = (s) => {
  s = Math.max(0, Math.floor(s || 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
};
const toSecs = (tc) => {
  const [m, s] = String(tc).split(':').map(Number);
  return (m || 0) * 60 + (s || 0);
};
function toast(msg) {
  els.toast.textContent = msg;
  els.toast.classList.add('show');
  setTimeout(() => els.toast.classList.remove('show'), 4600);
}
function setState(kind, text) {
  els.state.className = `state ${kind}`;
  els.stateText.textContent = text;
}

/* ------------------------------------------------------------- library */
async function loadVideos() {
  let health = {};
  try {
    [videos, health] = await Promise.all([
      fetch('/api/videos').then((r) => r.json()),
      fetch('/api/health').then((r) => r.json()).catch(() => ({})),
    ]);
  } catch {
    els.library.innerHTML = '<p class="empty">Could not reach the server.</p>';
    return;
  }
  transportKind = health.transport === 'daily' ? 'daily' : 'webrtc';
  if (health.max_listen_secs > 0) maxListenSecs = Number(health.max_listen_secs);

  const totalSecs = videos.reduce((a, v) => a + v.duration, 0);
  const segments = videos.reduce((a, v) => a + v.segments, 0);
  els.heroStats.innerHTML = [
    `<b>${videos.length}</b> videos`,
    `<b>${fmt(totalSecs)}</b> indexed`,
    `<b>${segments}</b> searchable moments`,
    health.world_knowledge ? '<b>web</b> access via AgentCore' : '',
  ].filter(Boolean).map((t) => `<span>${t}</span>`).join('');

  if (!videos.length) {
    els.library.innerHTML = `<p class="empty">No videos indexed yet — drop an mp4 in <code>videos/</code> then run
      <code>uv run talk2vid-ingest videos/yourfile.mp4 --scenario cloud</code></p>`;
    return;
  }

  // One grid, filtered by scenario. Shelves-per-scenario looked sparse with a
  // handful of clips; a single row of cards fills the width and reads better.
  const scenarios = [...new Set(videos.map((v) => v.scenario_label))];
  els.library.innerHTML = `
    <div class="filters" id="filters"></div>
    <div class="row" id="row"></div>`;
  const filters = $('filters');
  ['All', ...scenarios].forEach((label, i) => {
    const chip = document.createElement('button');
    chip.className = 'chip' + (i === 0 ? ' on' : '');
    chip.textContent = label;
    chip.onclick = () => {
      filters.querySelectorAll('.chip').forEach((c) => c.classList.toggle('on', c === chip));
      renderRow(label === 'All' ? videos : videos.filter((v) => v.scenario_label === label));
    };
    filters.appendChild(chip);
  });
  renderRow(videos);
}

function renderRow(items) {
  const row = $('row');
  row.innerHTML = '';
  items.forEach((v) => row.appendChild(card(v)));
}

function card(v) {
  const el = document.createElement('button');
  el.className = 'card' + (v.playable ? '' : ' disabled');
  el.style.setProperty('--card-accent', v.accent);
  el.innerHTML = `
    <div class="shot" style="background-image:url('/api/thumb/${v.video_id}')">
      <span class="badge"></span>
      <span class="len">${fmt(v.duration)}</span>
    </div>
    <div class="meat">
      <h3></h3>
      <p></p>
      <div class="foot">
        <i>${v.segments} moments</i>
        <i>${v.chapters.length} chapters</i>
        <i>${v.has_embeddings ? 'multimodal' : 'text only'}</i>
      </div>
    </div>`;
  el.querySelector('.badge').textContent = v.scenario_label;
  el.querySelector('h3').textContent = v.title;
  el.querySelector('p').textContent = v.summary || '';
  el.onclick = () => selectVideo(v);
  return el;
}

function selectVideo(v, { push = true } = {}) {
  current = v;
  // A history entry per clip, so the browser's own back button leaves the stage
  // instead of leaving the app.
  if (push) history.pushState({ video: v.video_id }, '', `#${v.video_id}`);
  els.picker.hidden = true;
  els.stage.hidden = false;
  els.back.hidden = false;
  window.scrollTo({ top: 0 });
  document.documentElement.style.setProperty('--accent', v.accent || '#ff9900');
  els.video.src = `/media/${v.video_id}`;
  els.video.load();
  els.videoTitle.textContent = v.title;
  els.videoTag.textContent = v.scenario_label;
  els.timeTotal.textContent = fmt(v.duration);
  els.timeNow.textContent = '0:00';
  drawChapters();
  renderChapterList();
  els.starters.innerHTML = '';
  (v.starters || []).forEach((text) => {
    const b = document.createElement('button');
    b.textContent = text;
    b.onclick = () => sendTyped(text);
    els.starters.appendChild(b);
  });
  els.log.innerHTML = `<div class="hint"><b>Pause the video</b> where you are curious, then press the mic and ask.
    Try “what is happening here?” or “show me where they explain that”.</div>`;
  syncPlayIcon();
}

function showLibrary() {
  teardown();
  els.video.pause();
  // Drop the source too: otherwise the hidden element keeps streaming range
  // requests for a clip nobody is watching.
  els.video.removeAttribute('src');
  els.video.load();
  els.botAudio.srcObject = null;
  current = null;
  els.latency.textContent = '';
  els.stage.hidden = true;
  els.back.hidden = true;
  els.picker.hidden = false;
  window.scrollTo({ top: 0 });
}

/* --------------------------------------------------------------- player */
function syncPlayIcon() {
  const paused = els.video.paused;
  els.playIcon.setAttribute('d', paused ? PLAY_PATH : PAUSE_PATH);
  $('overlayIcon')?.setAttribute('d', paused ? PLAY_PATH : PAUSE_PATH);
  els.playOverlay.classList.toggle('show', paused);
  els.pausedFlag.hidden = !(paused && els.video.currentTime > 0.2);
}
function togglePlay() {
  if (els.video.paused) els.video.play().catch(() => {});
  else els.video.pause();
}
els.playBtn.onclick = togglePlay;
els.playOverlay.onclick = togglePlay;
// 'seeked' matters too: jumping while already paused fires no play/pause event,
// and the "paused — ask away" badge has to appear then as well.
['play', 'pause', 'seeked'].forEach((ev) => els.video.addEventListener(ev, syncPlayIcon));
els.video.addEventListener('loadedmetadata', () => {
  els.timeTotal.textContent = fmt(els.video.duration || current?.duration);
  syncPlayIcon();
});
document.addEventListener('keydown', (ev) => {
  if (ev.code === 'Space' && !els.stage.hidden && ev.target.tagName !== 'INPUT') {
    ev.preventDefault();
    togglePlay();
  }
});

// Move the playhead without ever changing play/pause state: if you paused to ask
// a question, you stay paused — the frame you were looking at is the context.
function seek(seconds) {
  els.video.currentTime = Math.max(0, Math.min(seconds, (current?.duration || 0) - 0.2));
  syncPlayIcon();
}

/* The agent must never talk over the clip. Every time the floor changes hands to
 * the user — the mic opens (the agent greets you), speech starts, a question is
 * typed — the video pauses on the frame being discussed. Pausing is the only
 * automatic playback move; pressing play again is always the user's call. */
function holdVideo() {
  if (!els.video.paused) els.video.pause();   // 'pause' event refreshes the UI
}

function drawChapters() {
  els.track.innerHTML = '';
  const total = current.duration || 1;
  (current.chapters || []).forEach((ch) => {
    const el = document.createElement('div');
    el.className = 'chapter';
    el.style.left = `${(ch.start / total) * 100}%`;
    el.style.width = `${(Math.max(ch.end - ch.start, 1) / total) * 100}%`;
    el.textContent = ch.title;
    el.title = `${fmt(ch.start)} — ${ch.title}`;
    els.track.appendChild(el);
  });
  const played = document.createElement('div');
  played.className = 'played';
  played.id = 'played';
  els.track.appendChild(played);
  // A 36-chapter video turns labels into unreadable slivers; drop the text on
  // narrow slices and let them act as tick marks (the panel lists them anyway).
  requestAnimationFrame(() => {
    els.track.querySelectorAll('.chapter').forEach((el) => {
      if (el.getBoundingClientRect().width < 64) el.textContent = '';
    });
  });
}
function renderChapterList() {
  const chapters = current.chapters || [];
  els.chapterCount.textContent = chapters.length || '';
  els.chapters.innerHTML = '';
  if (!chapters.length) {
    els.chapters.innerHTML = '<p class="none">No chapters for this video.</p>';
    return;
  }
  chapters.forEach((ch, i) => {
    const row = document.createElement('button');
    row.className = 'chapter-row';
    row.dataset.i = i;
    row.innerHTML = `<time>${fmt(ch.start)}</time><span></span>`;
    row.querySelector('span').textContent = ch.title;
    row.onclick = () => seek(ch.start);
    els.chapters.appendChild(row);
  });
}
function markCues(moments) {
  els.track.querySelectorAll('.cue').forEach((n) => n.remove());
  const total = current.duration || 1;
  moments.forEach((m) => {
    const el = document.createElement('div');
    el.className = 'cue';
    el.style.left = `${(m.start / total) * 100}%`;
    el.style.width = `${(Math.max(m.end - m.start, 2) / total) * 100}%`;
    els.track.appendChild(el);
  });
}
els.track.onclick = (ev) => {
  const rect = els.track.getBoundingClientRect();
  seek(((ev.clientX - rect.left) / rect.width) * (current?.duration || 0));
};
els.video.addEventListener('timeupdate', () => {
  if (!current) return;
  const pct = (els.video.currentTime / (current.duration || 1)) * 100;
  const played = $('played');
  if (played) played.style.width = `${pct}%`;
  els.timeNow.textContent = fmt(els.video.currentTime);
  const chapters = current.chapters || [];
  const active = chapters.findIndex(
    (c) => els.video.currentTime >= c.start && els.video.currentTime < c.end,
  );
  els.chapters.querySelectorAll('.chapter-row').forEach((row) => {
    row.classList.toggle('now', Number(row.dataset.i) === active);
  });
});

/* ------------------------------------------------------------ transcript */
function bubble(role, text) {
  const el = document.createElement('div');
  el.className = `msg ${role}`;
  el.textContent = text;
  els.log.querySelector('.hint')?.remove();
  els.log.appendChild(el);
  els.log.scrollTop = els.log.scrollHeight;
  return el;
}

/* The agent speaks timestamps as words ("about eighteen seconds in"), because
 * that is how they should sound. Parse both words and digits so every timestamp
 * in the transcript is clickable. */
const ONES = {
  zero: 0, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9,
  ten: 10, eleven: 11, twelve: 12, thirteen: 13, fourteen: 14, fifteen: 15, sixteen: 16,
  seventeen: 17, eighteen: 18, nineteen: 19,
};
const TENS = { twenty: 20, thirty: 30, forty: 40, fifty: 50 };
const NUMWORD = `(?:${[...Object.keys(TENS), ...Object.keys(ONES)].join('|')})`;

function wordsToNumber(text) {
  let total = 0;
  for (const word of text.toLowerCase().split(/[\s-]+/)) {
    if (word in TENS) total += TENS[word];
    else if (word in ONES) total += ONES[word];
  }
  return total;
}
const numOf = (raw) => (/^\d+$/.test(raw.trim()) ? Number(raw) : wordsToNumber(raw));

function linkifyTimes(el) {
  const N = `(?:\\d{1,2}|${NUMWORD}(?:[\\s-]${NUMWORD})?)`;
  const wrap = (m, secs) =>
    secs > 0 && secs < 36000 ? `<span class="ts" data-t="${secs}">${m}</span>` : m;
  el.innerHTML = el.textContent
    .replace(/\b(\d{1,2}):([0-5]\d)\b/g, (m) => wrap(m, toSecs(m)))
    .replace(new RegExp(`\\b(${N})\\s+minutes?(?:\\s+and)?\\s+(${N})\\b`, 'gi'),
      (m, a, b) => wrap(m, numOf(a) * 60 + numOf(b)))
    .replace(new RegExp(`\\b(${N})\\s+minutes?\\b`, 'gi'), (m, a) => wrap(m, numOf(a) * 60))
    .replace(new RegExp(`\\b(${N})\\s+seconds?\\s+in\\b`, 'gi'), (m, a) => wrap(m, numOf(a)));
  el.querySelectorAll('.ts').forEach((n) => (n.onclick = () => seek(Number(n.dataset.t))));
}
function activity(text, running) {
  els.activity.innerHTML = running ? `<span class="run">${text}</span>` : text;
}

/* -------------------------------------------------------------- RTVI I/O */
/* The same RTVI messages ride a WebRTC data channel locally and Daily app
 * messages when deployed, so everything above and below this layer is identical. */
function rtviSend(type, data) {
  const msg = { label: 'rtvi-ai', type, id: `c${Date.now()}`, data: data || {} };
  if (session?.isOpen()) session.send(msg);
  else outbox.push(msg);
}
function flushOutbox() {
  outbox.forEach((m) => session.send(m));
  outbox = [];
}
// Custom app data rides RTVI's client-message channel rather than a bare app
// message, so the bot receives it through a supported handler instead of the
// pipeline logging every playhead tick as an unrecognised message.
const send = (t, d) => rtviSend('client-message', { t, d });

async function sendTyped(text) {
  if (!text.trim()) return;
  holdVideo();
  // Typing is a complete way to use the demo, not a fallback for a broken mic: if
  // there is no session yet this opens a text-only one, with no getUserMedia call
  // at all. In a loud room that is the reliable path — no microphone means no VAD,
  // so nothing competes with the question for the floor.
  if (!session) {
    await connect({ mic: false });
    if (!session) return;   // connect() already explained why
  }
  // A live mic is muted for this turn. The server will not end a turn while its
  // VAD still hears a voice, so with a noisy room on the line a typed question
  // would otherwise join a turn that never closes and never gets answered.
  if (micStream) {
    setMicOpen(false);
    clearCap();
    clearTimeout(reopenTimer);
    reopenTimer = setTimeout(reopenMic, (maxListenSecs + 8) * 1000);
  }
  // No local bubble: the typed text enters the pipeline as a transcription, so
  // the user-transcription event renders it exactly like a spoken turn.
  lastUserSpokeAt = performance.now();
  send('say', { text });
}

function onServerMessage(data) {
  if (!data || typeof data !== 'object') return;
  if (data.t === 'ready') {
    activity('video index loaded', false);
    // Text, never speech: the agent does not take the floor on connect.
    if (data.opener) bubble('bot', data.opener);
  } else if (data.t === 'tool') {
    const label = {
      find_moment: 'searching this video',
      look_closer: 'looking at the frames',
      ask_the_world: 'asking the research agent',
    }[data.name] || data.name;
    activity(`${label}${data.detail ? ` — ${data.detail}` : ''}`, data.status === 'running');
  } else if (data.t === 'cue') {
    markCues(data.moments || []);
    const wrap = document.createElement('div');
    wrap.className = 'cues';
    (data.moments || []).forEach((m) => {
      const b = document.createElement('button');
      b.textContent = `▶ ${m.timecode}`;
      b.title = m.label || '';
      b.onclick = () => seek(m.start);
      wrap.appendChild(b);
    });
    els.log.appendChild(wrap);
    els.log.scrollTop = els.log.scrollHeight;
    if (data.seek && data.moments?.length) seek(data.moments[0].start);
  } else if (data.t === 'said') {
    // Something the agent spoke outside the LLM stream (e.g. a holding phrase).
    if (data.text) bubble('bot', data.text);
  } else if (data.t === 'research') {
    const box = document.createElement('div');
    box.className = 'research';
    const queries = (data.queries || []).map((q) => `“${q}”`).join(', ');
    box.innerHTML = `<b>AgentCore web search</b>`;
    if (queries) box.innerHTML += `<span>${queries}</span>`;
    if (data.latency_ms) box.innerHTML += `<span>${(data.latency_ms / 1000).toFixed(1)}s</span>`;
    els.log.appendChild(box);
    els.log.scrollTop = els.log.scrollHeight;
  }
}

function onData(msg) {
  if (!msg || msg.label !== 'rtvi-ai') return;
  const d = msg.data || {};
  switch (msg.type) {
    case 'bot-ready':
      setState('listening', 'Listening');
      break;
    case 'user-started-speaking':
      // Silero heard you: stop the clip so your question and the answer land in
      // silence, on the frame you were looking at.
      holdVideo();
      setState('listening', 'Listening');
      armCap();
      break;
    case 'user-stopped-speaking':
      lastUserSpokeAt = performance.now();
      setState('thinking', 'Thinking');
      clearCap();
      break;
    case 'user-transcription':
      if (d.final && d.text) bubble('user', d.text.replace(/^\[watching [^\]]+\]\s*/, ''));
      break;
    case 'bot-llm-started':
      setState('thinking', 'Thinking');
      break;
    case 'bot-llm-text':
      if (!botBubble) botBubble = bubble('bot', '');
      botBubble.classList.add('partial');
      botBubble.textContent += d.text || '';
      els.log.scrollTop = els.log.scrollHeight;
      break;
    case 'bot-started-speaking':
      // Backstop: whatever got the agent talking — holding phrase, answer — it
      // does not compete with the clip's own audio.
      holdVideo();
      setState('speaking', 'Speaking');
      if (lastUserSpokeAt) {
        els.latency.textContent = `answered in ${((performance.now() - lastUserSpokeAt) / 1000).toFixed(2)}s`;
        lastUserSpokeAt = 0;
      }
      break;
    case 'bot-stopped-speaking':
      setState('listening', micStream ? 'Listening' : 'Text only');
      // The floor is yours again, so a mic closed by the cap comes back now.
      reopenMic();
      break;
    case 'bot-transcription':
      // Fires once per detected sentence, so it must not close the bubble — that
      // chopped answers into three fragments, sometimes mid-word. It is only used
      // for text that never streamed through the LLM, e.g. a spoken holding phrase.
      if (!botBubble && d.text) linkifyTimes(bubble('bot', d.text));
      break;
    case 'bot-llm-stopped':
      if (botBubble) {
        botBubble.classList.remove('partial');
        linkifyTimes(botBubble);
        botBubble = null;
      }
      activity('', false);
      break;
    case 'server-message':
      onServerMessage(d);
      break;
    case 'error':
      toast(d.message || 'Pipeline error');
      break;
  }
}

/* ------------------------------------------------------------ connection */

/** Peer-to-peer WebRTC straight to the bot process. Lowest latency; local only. */
const webrtcDriver = {
  isOpen: () => dc?.readyState === 'open',
  send: (msg) => dc.send(JSON.stringify(msg)),
  // Nothing to do: the tracks setMicOpen just toggled are the ones on this peer
  // connection, so disabling them already stopped the audio reaching the bot.
  setMicMuted() {},
  async connect(videoId) {
    pc = new RTCPeerConnection({ iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] });
    dc = pc.createDataChannel('chat');
    dc.onopen = () => {
      onSessionOpen();
      // The bot treats these pings as its liveness signal for the peer.
      pingTimer = setInterval(() => dc.readyState === 'open' && dc.send(`ping-${Date.now()}`), 1000);
    };
    dc.onmessage = (ev) => {
      try { onData(JSON.parse(ev.data)); } catch { /* keepalive ping */ }
    };

    if (micStream) micStream.getTracks().forEach((t) => pc.addTrack(t, micStream));
    else pc.addTransceiver('audio', { direction: 'recvonly' });
    pc.ontrack = (ev) => { els.botAudio.srcObject = ev.streams[0]; };
    pc.onconnectionstatechange = () => {
      if (['failed', 'closed', 'disconnected'].includes(pc.connectionState)) teardown();
    };

    await pc.setLocalDescription(await pc.createOffer());
    await iceComplete(pc);
    const body = { sdp: pc.localDescription.sdp, type: pc.localDescription.type };
    if (pcId) body.pc_id = pcId;
    const res = await fetch(`/api/offer?video_id=${encodeURIComponent(videoId)}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`signalling failed (${res.status})`);
    const answer = await res.json();
    pcId = answer.pc_id;
    await pc.setRemoteDescription({ sdp: answer.sdp, type: answer.type });
  },
  stop() {
    clearInterval(pingTimer);
    pingTimer = null;
    try { dc?.close(); } catch {}
    try { pc?.close(); } catch {}
    pc = dc = null;
    pcId = null;
  },
};

/** Media over Daily's infrastructure — what the deployed build uses, because a
 *  server behind CloudFront and an ALB has no inbound UDP path. */
const dailyDriver = {
  isOpen: () => !!call && ['joined-meeting'].includes(call.meetingState?.()),
  send: (msg) => call.sendAppMessage(msg, '*'),
  // Daily owns the microphone track it is publishing, so the cap has to mute it
  // here. Nothing then reaches the bot at all, and the pipeline's
  // audio_idle_timeout closes the turn about a second later.
  setMicMuted: (muted) => call?.setLocalAudio(!muted),
  async connect(videoId) {
    const res = await fetch(`/api/connect?video_id=${encodeURIComponent(videoId)}`, {
      method: 'POST',
    });
    if (!res.ok) throw new Error(`room setup failed (${res.status})`);
    const { room_url: roomUrl, token } = await res.json();

    const Daily = (await import('/static/vendor/daily-esm.js')).default;
    call = Daily.createCallObject({
      subscribeToTracksAutomatically: true,
      dailyConfig: { useDevicePreferenceCookies: false },
    });
    call.on('app-message', (ev) => onData(ev?.data));
    call.on('track-started', (ev) => {
      // Only the bot's audio: our own mic must never be played back.
      if (ev.track?.kind === 'audio' && !ev.participant?.local) {
        els.botAudio.srcObject = new MediaStream([ev.track]);
        els.botAudio.play().catch(() => {});
      }
    });
    call.on('error', (ev) => {
      toast(ev?.errorMsg || 'Call error');
      teardown();
    });
    call.on('left-meeting', () => teardown());

    await call.join({ url: roomUrl, token, startVideoOff: true, startAudioOff: !micStream });
    onSessionOpen();
  },
  stop() {
    const leaving = call;
    call = null;
    if (leaving) {
      leaving.leave().catch(() => {}).finally(() => leaving.destroy().catch(() => {}));
    }
  },
};

/** Shared once either driver is connected. */
function onSessionOpen() {
  rtviSend('client-ready', { version: '1.0.0', about: { library: 'talk2vid-web' } });
  flushOutbox();
  playheadTimer = setInterval(
    () => send('playhead', { position: els.video.currentTime || 0, playing: !els.video.paused }),
    500,
  );
}

/* -------------------------------------------------------- listening window */
/* A spoken turn may run for maxListenSecs, timed from the moment the server says
 * it heard you start. At the cap the mic is muted, which is what actually ends
 * the turn: the server's VAD goes silent, the answer comes back on what it got,
 * and the mic re-opens once the agent has finished speaking. The re-open is also
 * on a timer, because a turn that goes wrong (tool error, dropped reply) must
 * never leave the mic shut for the rest of the demo. */
function setMicOpen(open) {
  if (!micStream) return;
  // Locally the tracks in micStream are the ones on the wire, so toggling them
  // both stops the audio and flattens the level meter. Under Daily they are only
  // the meter's source — Daily opened a microphone track of its own — so the
  // transport has to be muted through its own API too, or the cap is cosmetic.
  micStream.getAudioTracks().forEach((track) => (track.enabled = open));
  session?.setMicMuted(!open);
  els.mic.classList.toggle('held', !open);
  els.micLabel.textContent = open
    ? 'Listening — just talk'
    : `Got that — ${maxListenSecs}s per question`;
}

function armCap() {
  if (!micStream || capTimer) return;
  capTimer = setTimeout(closeTurnAtCap, maxListenSecs * 1000);
}

function clearCap() {
  clearTimeout(capTimer);
  capTimer = null;
}

function closeTurnAtCap() {
  capTimer = null;
  if (!micStream) return;
  setMicOpen(false);
  setState('thinking', 'Thinking');
  clearTimeout(reopenTimer);
  reopenTimer = setTimeout(reopenMic, (maxListenSecs + 8) * 1000);
}

function reopenMic() {
  clearTimeout(reopenTimer);
  reopenTimer = null;
  // Only the capped case needs reopening; an ordinary turn never closed the mic,
  // and this must not overwrite the label in that case.
  if (micStream && !micStream.getAudioTracks().some((track) => track.enabled)) {
    setMicOpen(true);
    setState('listening', 'Listening');
  }
}

async function connect({ mic = true } = {}) {
  if (!current) return;
  // Opening the mic is taking the floor, so the clip stops here — the "first time
  // of activation" case. The agent stays silent until you speak.
  holdVideo();
  els.mic.disabled = true;
  setState('thinking', 'Connecting');

  // A missing, blocked or unanswered mic prompt must not kill the demo: fall back
  // to receive-only so typed questions still work and the agent can still be
  // heard. getUserMedia never settles while a prompt sits unanswered, hence the
  // explicit timeout. `mic: false` skips the prompt altogether — that is a typed
  // question opening its own session, and it must not grab the microphone.
  micStream = null;
  if (mic) {
    try {
      micStream = await Promise.race([
        navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        }),
        new Promise((_, reject) => setTimeout(() => reject(new Error('mic timeout')), 8000)),
      ]);
    } catch {
      micStream = null;
      toast('No microphone available — continuing in text-only mode.');
    }
  }

  session = transportKind === 'daily' ? dailyDriver : webrtcDriver;
  try {
    await session.connect(current.video_id);
  } catch (err) {
    toast(`Could not start the session: ${err.message}`);
    teardown();
    return;
  }

  if (micStream) startLevelMeter();
  els.mic.disabled = false;
  els.mic.classList.add('live');
  // A session with no microphone gets the muted look, because that is the truth:
  // the conversation is open, nothing is listening.
  els.mic.classList.toggle('held', !micStream);
  els.micLabel.textContent = micStream ? 'Listening — just talk' : 'Text only — press to talk';
  setState('listening', micStream ? 'Listening' : 'Text only');
  // Never starts playback: the clip is held, and resuming it is up to you.
}

function iceComplete(peer) {
  if (peer.iceGatheringState === 'complete') return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => {
      if (peer.iceGatheringState === 'complete') {
        peer.removeEventListener('icegatheringstatechange', done);
        resolve();
      }
    };
    peer.addEventListener('icegatheringstatechange', done);
    setTimeout(resolve, 2500); // don't stall the demo on a slow STUN
  });
}

function startLevelMeter() {
  audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  analyser = audioCtx.createAnalyser();
  analyser.fftSize = 512;
  audioCtx.createMediaStreamSource(micStream).connect(analyser);
  const buf = new Uint8Array(analyser.frequencyBinCount);
  levelTimer = setInterval(() => {
    analyser.getByteFrequencyData(buf);
    const avg = buf.reduce((a, b) => a + b, 0) / buf.length / 255;
    els.level.style.transform = `scale(${(1 + Math.min(avg * 3.4, 1) * 0.26).toFixed(3)})`;
  }, 80);
}

function teardown() {
  [pingTimer, playheadTimer, levelTimer].forEach(clearInterval);
  pingTimer = playheadTimer = levelTimer = null;
  [capTimer, reopenTimer].forEach(clearTimeout);
  capTimer = reopenTimer = null;
  els.mic.classList.remove('held');
  session?.stop();
  session = null;
  micStream?.getTracks().forEach((t) => t.stop());
  audioCtx?.close().catch(() => {});
  micStream = audioCtx = null;
  botBubble = null;
  outbox = [];
  els.mic.classList.remove('live');
  els.mic.disabled = false;
  els.micLabel.textContent = 'Start talking';
  els.level.style.transform = 'scale(1)';
  setState('', 'Idle');
  activity('', false);
}

/* ----------------------------------------------------------------- wiring */
els.mic.onclick = () => {
  // Pressing the mic during a text-only session means "let me talk now", not "hang
  // up". That restarts the bot session, so the agent's own memory of the chat so
  // far goes with it; the transcript on screen stays.
  if (session && !micStream) { teardown(); connect(); return; }
  return session ? teardown() : connect();
};
els.askForm.onsubmit = (ev) => { ev.preventDefault(); sendTyped(els.ask.value); els.ask.value = ''; };
// Going back unwinds the history entry pushed on select, so the button and the
// browser's own back arrow end up in the same place.
function goBack() {
  if (els.stage.hidden) { window.scrollTo({ top: 0 }); return; }  // already home
  if (history.state?.video) history.back();
  else showLibrary();
}
els.back.onclick = goBack;
// The wordmark is the other way home; on the library it just returns to the top.
els.home.onclick = goBack;
window.addEventListener('popstate', (ev) => {
  const id = ev.state?.video;
  if (!id) { showLibrary(); return; }
  const v = videos.find((x) => x.video_id === id);
  if (v && v.video_id !== current?.video_id) selectVideo(v, { push: false });
});
document.addEventListener('keydown', (ev) => {
  if (ev.key === 'Escape' && !els.stage.hidden && ev.target.tagName !== 'INPUT') goBack();
});
els.clear.onclick = () => (els.log.innerHTML = '');
window.addEventListener('beforeunload', teardown);
// Small debug surface for troubleshooting a live session from the console.
window.__t2v = {
  get transport() { return transportKind; },
  get connection() { return call ? call.meetingState?.() : pc?.connectionState; },
  get channel() { return session?.isOpen() ? 'open' : 'closed'; },
  get queued() { return outbox.length; },
  get video() { return current?.video_id; },
  get playhead() { return els.video.currentTime; },
  send, seek,
};
// A reload always lands on the library, so drop any leftover #clip in the URL —
// otherwise the first back press unwinds to a stage that was never opened.
if (location.hash) history.replaceState({}, '', location.pathname + location.search);
loadVideos();
