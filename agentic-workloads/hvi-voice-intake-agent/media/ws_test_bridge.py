"""Browser-microphone test harness, served FROM the media host. TESTING ONLY.

WHY THIS EXISTS
Iterating on the voice pipeline cost a PSTN call per attempt, and the phone leg
itself kept injecting faults (silent windows, garbled digits) that masqueraded
as pipeline bugs. This gives a clean, repeatable audio leg from a browser tab:
mic in, agent audio out, through the SAME AudioSocket listener, VAD, STT, agent,
TTS and cache a phone call uses. Real calls remain the sign-off test - this
validates the pipeline, not the telephony.

WHY NOT API GATEWAY, since it was suggested: APIGW WebSocket APIs route each
message through a Lambda invocation. Telephony audio is a 50-frames-per-second
duplex stream, so that is ~100 invocations/second per call with per-message
latency added in both directions - the wrong shape entirely. A byte-stream
proxy on the box that already runs the pipeline needs none of that. HTTPS/WSS
(which browsers require before granting mic access) comes from CloudFront in
front of this process; see scripts/75-ws-test.sh.

SECURITY
  - Binds 0.0.0.0 ONLY because the security group restricts the port to
    CloudFront's origin-facing prefix list; nothing else can reach it.
  - Every request must carry the shared token (?token=...), generated at deploy
    time into /etc/hvi/ws-test.env. Wrong or missing token: 403, including for
    the page itself. This is a TEST harness for synthetic calls as the seeded
    test customer; it stores no health data of its own.
  - The ticket it writes names WS_TEST_PHONE (the seeded test record), so a
    session cannot impersonate an arbitrary customer.

Runs as the hvi-ws-test systemd unit; scripts/72-media-deploy.sh ships this
file with the rest of media/.
"""
from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
import os
import struct
import sys
import uuid as uuidlib

from aiohttp import WSMsgType, web

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(message)s")
log = logging.getLogger("hvi.wsbridge")

AUDIOSOCKET_HOST = "127.0.0.1"
AUDIOSOCKET_PORT = int(os.environ.get("AUDIOSOCKET_PORT", "8090"))
HTTP_PORT = int(os.environ.get("WS_HTTP_PORT", "8791"))
TEST_PHONE = os.environ.get("WS_TEST_PHONE", "+911234567890")
CALL_DIR = os.environ.get("HVI_CALL_DIR", "/var/lib/hvi/calls")
TOKEN = os.environ.get("WS_TEST_TOKEN", "")

TYPE_TERMINATE, TYPE_UUID, TYPE_AUDIO = 0x00, 0x01, 0x10
FRAME_BYTES = 320  # 20ms of 8kHz 16-bit mono, what Asterisk sends


def _msg(mtype: int, payload: bytes) -> bytes:
    return struct.pack(">BH", mtype, len(payload)) + payload


def _authorised(request: web.Request) -> bool:
    # compare_digest, not ==: a timing oracle on a shared token is a silly way
    # to lose a test harness that fronts a medical pipeline.
    supplied = request.query.get("token", "")
    return bool(TOKEN) and hmac.compare_digest(supplied, TOKEN)


PAGE = """<!doctype html><meta charset="utf-8"><title>HVI test call</title>
<style>body{font-family:system-ui;margin:3em;max-width:40em}button{font-size:1.2em;padding:.4em 1.2em}
#log{white-space:pre-wrap;background:#f4f4f4;padding:1em;margin-top:1em;font-family:monospace;font-size:.85em}</style>
<h2>HVI pipeline test call</h2>
<p>Same pipeline as a phone call, minus the phone. Click Start and speak after
the greeting. Sign-off testing still happens on a real call.</p>
<button id="b">Start call</button>
<label style="margin-left:1em"><input type="checkbox" id="hp"> I'm on headphones
(enables barge-in)</label>
<div id="log"></div>
<script>
const logEl = document.getElementById('log');
const say = m => { logEl.textContent += m + "\\n"; };
let ws, ctx, node, src, mic, playT = 0;

// EVERYTHING is torn down between calls. The first version left the old
// AudioContext, mic stream and ScriptProcessor connected on hang-up; a second
// Start then wired a SECOND mic path onto the new socket (the server measured
// inbound at exactly 2x the expected byte rate) and kept the old playback
// clock, so new agent audio was scheduled into the past - which plays as
// silence. Symptom: "I don't hear any greeting."
function teardown() {
  if (node) { try { node.disconnect(); } catch(e){} node = null; }
  if (src)  { try { src.disconnect(); }  catch(e){} src = null; }
  if (mic)  { mic.getTracks().forEach(t => t.stop()); mic = null; }
  if (ctx)  { try { ctx.close(); } catch(e){} ctx = null; }
  playT = 0; ws = null;
  document.getElementById('b').textContent = 'Start call';
}

document.getElementById('b').onclick = async () => {
  if (ws) { ws.close(); return; }
  // AT 8kHz NATIVELY, so Chrome's own resampler does the rate conversion.
  // The first version decimated 48k->8k by taking every 6th sample with no
  // low-pass filter, which aliases everything above 4kHz into the speech band
  // - real, audible noise on every frame sent, and a plausible cause of the
  // short-utterance mis-hearings seen through this harness. Browsers that
  // reject an 8kHz context fall back to a filtered decimation below.
  try { ctx = new AudioContext({sampleRate: 8000}); }
  catch (e) { ctx = new AudioContext(); }
  await ctx.resume();                     // Chrome can hand back a suspended one
  say(`audio: ${ctx.sampleRate}Hz, state ${ctx.state}` +
      (ctx.sampleRate === 8000 ? ' (native 8k, no manual resampling)' : ''));
  if (document.hidden !== undefined) {
    // ScriptProcessor is throttled in background tabs, which starves the call.
    document.addEventListener('visibilitychange', () => {
      if (document.hidden && ws) say('WARNING: tab in background - audio will stall');
    });
  }
  mic = await navigator.mediaDevices.getUserMedia({audio: {
    echoCancellation: true, noiseSuppression: true, channelCount: 1}});
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws${location.search}`);
  ws.binaryType = 'arraybuffer';
  ws.onopen = () => say('call started - speak after the greeting');
  ws.onerror = () => say('socket error - check the token and the bridge log');
  ws.onclose = () => { say('call ended'); teardown(); };

  // Mic in: decimate browser-rate float32 to 8k int16, ship 20ms frames.
  src = ctx.createMediaStreamSource(mic);
  node = ctx.createScriptProcessor(4096, 1, 1);
  const ratio = ctx.sampleRate / 8000;
  let acc = [], rmsAcc = 0, rmsN = 0;
  node.onaudioprocess = e => {
    if (!ws || ws.readyState !== 1) return;
    // ON SPEAKERS THE MIC HEARS THE AGENT. Chrome's echo cancellation covers
    // WebRTC and media-element output, NOT AudioContext-scheduled buffers, so
    // the agent's own voice came back as the caller's - its consent preamble
    // says "हाँ या ना", and it answered itself "हाँ" three questions in a row.
    // Default: drop mic frames while agent audio is scheduled (half-duplex,
    // like a speakerphone). Headphones checkbox restores full duplex so
    // barge-in can be tested.
    if (!document.getElementById('hp').checked && ctx.currentTime < playT + 0.3) {
      // Half-duplex mute: replace the window with SILENCE rather than sending
      // nothing. Dropping frames outright starved the server's stream cadence
      // (it logged "INBOUND AUDIO STOPPED" and under-delivery alarms for what
      // was actually this deliberate gate) and chopped the onset off answers
      // that began a beat early. Silence keeps the 20ms cadence intact.
      const n = Math.round(e.inputBuffer.getChannelData(0).length /
                           (ctx.sampleRate / 8000));
      for (let i = 0; i < n; i++) acc.push(0);
    } else {
    const f = e.inputBuffer.getChannelData(0);
    if (ratio === 1) {                    // native 8k context: pass through
      for (let i = 0; i < f.length; i++) {
        acc.push(Math.max(-32768, Math.min(32767, f[i] * 32767)));
      }
    } else {
      // Fallback decimation with a box filter: average the whole window
      // instead of point-sampling one value from it. A box average is a crude
      // low-pass, but it kills the worst of the aliasing that point-sampling
      // folds into the speech band.
      for (let i = 0; i < f.length / ratio; i++) {
        const a = Math.floor(i * ratio), b = Math.min(f.length, Math.floor((i + 1) * ratio));
        let s = 0;
        for (let j = a; j < b; j++) s += f[j];
        const v = s / Math.max(1, b - a);
        acc.push(Math.max(-32768, Math.min(32767, v * 32767)));
      }
    }
    }
    while (acc.length >= 160) {
      const frame = Int16Array.from(acc.splice(0, 160));
      let sq = 0; for (const v of frame) sq += v * v;
      rmsAcc += Math.sqrt(sq / frame.length); rmsN++;
      ws.send(frame.buffer);
    }
    // A visible level meter, so "the harness is noisy" and "the mic sent
    // nothing" stop being indistinguishable guesses. ~every 2s of speech.
    if (rmsN >= 100) {
      say(`mic level: RMS ${(rmsAcc / rmsN).toFixed(0)} `
          + `(speech is typically 500-5000; below ~50 is silence)`);
      rmsAcc = 0; rmsN = 0;
    }
  };
  src.connect(node); node.connect(ctx.destination);

  // Agent audio out: schedule 8k PCM chunks back to back.
  let heard = false;
  ws.onmessage = ev => {
    try {
      const i16 = new Int16Array(ev.data);
      if (!i16.length || !ctx) return;
      if (!heard) { heard = true; say('agent audio arriving'); }
      const buf = ctx.createBuffer(1, i16.length, 8000);
      const ch = buf.getChannelData(0);
      for (let i = 0; i < i16.length; i++) ch[i] = i16[i] / 32768;
      const s = ctx.createBufferSource();
      s.buffer = buf; s.connect(ctx.destination);
      playT = Math.max(playT, ctx.currentTime + 0.05);
      s.start(playT); playT += buf.duration;
    } catch (e) { say('playback error: ' + e); }
  };
  document.getElementById('b').textContent = 'Hang up';
};
</script>"""


async def ws_handler(request: web.Request) -> web.StreamResponse:
    if not _authorised(request):
        return web.Response(status=403, text="missing or wrong token")
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    call_uuid = str(uuidlib.uuid4())
    log.info("browser call %s as %s", call_uuid, TEST_PHONE)
    # The same ticket the dialplan leaves, so the orchestrator resolves the
    # seeded test customer exactly as it would a phone call from that number.
    try:
        with open(os.path.join(CALL_DIR, call_uuid), "w", encoding="utf-8") as fh:
            fh.write(TEST_PHONE)
    except OSError as e:
        log.error("cannot write the caller ticket: %s", e)
        await ws.close(message=b"ticket write failed")
        return ws

    try:
        reader, writer = await asyncio.open_connection(AUDIOSOCKET_HOST,
                                                       AUDIOSOCKET_PORT)
    except OSError as e:
        log.error("AudioSocket unreachable (%s) - is hvi-media running?", e)
        await ws.close(message=b"pipeline down")
        return ws

    writer.write(_msg(TYPE_UUID, uuidlib.UUID(call_uuid).bytes))
    await writer.drain()

    async def pump_to_pipeline() -> None:
        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                pcm = msg.data
                for i in range(0, len(pcm) - FRAME_BYTES + 1, FRAME_BYTES):
                    writer.write(_msg(TYPE_AUDIO, pcm[i:i + FRAME_BYTES]))
                await writer.drain()
        with contextlib.suppress(Exception):
            writer.write(_msg(TYPE_TERMINATE, b""))
            await writer.drain()

    async def pump_to_browser() -> None:
        buf = b""
        while True:
            data = await reader.read(4096)
            if not data:
                break
            buf += data
            while len(buf) >= 3:
                mtype, length = struct.unpack(">BH", buf[:3])
                if len(buf) < 3 + length:
                    break
                payload, buf = buf[3:3 + length], buf[3 + length:]
                if mtype == TYPE_AUDIO and not ws.closed:
                    await ws.send_bytes(payload)
                elif mtype == TYPE_TERMINATE:
                    await ws.close()
                    return

    try:
        await asyncio.gather(pump_to_pipeline(), pump_to_browser())
    except Exception as e:  # noqa: BLE001
        log.info("call %s ended: %s", call_uuid, e)
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
    log.info("call %s finished", call_uuid)
    return ws


async def page(request: web.Request) -> web.Response:
    if not _authorised(request):
        return web.Response(status=403, text="missing or wrong token")
    return web.Response(text=PAGE, content_type="text/html")


def main() -> int:
    if not TOKEN:
        log.error("WS_TEST_TOKEN is not set; refusing to serve without auth")
        return 1
    app = web.Application()
    app.router.add_get("/", page)
    app.router.add_get("/ws", ws_handler)
    log.info("ws test bridge on :%d as %s", HTTP_PORT, TEST_PHONE)
    web.run_app(app, host="0.0.0.0", port=HTTP_PORT, print=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
