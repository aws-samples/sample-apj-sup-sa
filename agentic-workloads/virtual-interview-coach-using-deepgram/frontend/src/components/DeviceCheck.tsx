// Pre-flight mic + speaker check (D-fix#2 follow-up). Runs ENTIRELY in the browser with the same
// getUserMedia path the WebRTC client uses, so it isolates "can this browser capture sound at all?"
// from the worker/WebRTC media path. The repeated SILENT verdict on the worker (session_peak_amp
// ~10, voiced_frames=0) means the browser was handing over a dead track — usually the wrong/virtual
// input device. This lets the user SEE their own level move and pick the right devices before a
// session, then hands the chosen deviceIds to connectMedia so capture pins to them.

import { useCallback, useEffect, useRef, useState } from "react";

export interface DeviceSelection {
  inputDeviceId?: string;
  outputDeviceId?: string;
}

interface DeviceCheckProps {
  onReady: (sel: DeviceSelection) => void;
  readyLabel?: string;
}

type MicState = "idle" | "starting" | "live" | "denied" | "error";

// Live RMS-based level (0..100). Speech pushes this well above ~5; a dead/muted device stays at 0.
function useMicLevel() {
  const [level, setLevel] = useState(0);
  const [peak, setPeak] = useState(0); // highest level seen this session — proves the mic ever worked
  const [state, setState] = useState<MicState>("idle");
  const streamRef = useRef<MediaStream | null>(null);
  const ctxRef = useRef<AudioContext | null>(null);
  const rafRef = useRef<number | null>(null);

  const stop = useCallback(() => {
    if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    rafRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    ctxRef.current?.close().catch(() => {});
    ctxRef.current = null;
  }, []);

  const start = useCallback(
    async (deviceId?: string) => {
      stop();
      setState("starting");
      setPeak(0);
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: deviceId
            ? { deviceId: { exact: deviceId }, echoCancellation: true, noiseSuppression: true, autoGainControl: true }
            : { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        });
        streamRef.current = stream;
        const Ctx =
          window.AudioContext ||
          (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
        const ctx = new Ctx();
        ctxRef.current = ctx;
        const source = ctx.createMediaStreamSource(stream);
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 1024;
        source.connect(analyser);
        const buf = new Uint8Array(analyser.fftSize);
        setState("live");

        const tick = () => {
          analyser.getByteTimeDomainData(buf);
          // RMS deviation from the 128 midpoint, scaled to 0..100.
          let sumSq = 0;
          for (let i = 0; i < buf.length; i++) {
            const v = (buf[i] - 128) / 128;
            sumSq += v * v;
          }
          const rms = Math.sqrt(sumSq / buf.length);
          const lvl = Math.min(100, Math.round(rms * 400));
          setLevel(lvl);
          setPeak((p) => (lvl > p ? lvl : p));
          rafRef.current = requestAnimationFrame(tick);
        };
        tick();
      } catch (err) {
        setState(
          err instanceof DOMException && err.name === "NotAllowedError" ? "denied" : "error"
        );
      }
    },
    [stop]
  );

  useEffect(() => stop, [stop]);
  return { level, peak, state, start, stop };
}

export default function DeviceCheck({ onReady, readyLabel = "Start session" }: DeviceCheckProps) {
  const { level, peak, state, start, stop } = useMicLevel();
  const [inputs, setInputs] = useState<MediaDeviceInfo[]>([]);
  const [outputs, setOutputs] = useState<MediaDeviceInfo[]>([]);
  const [inputId, setInputId] = useState<string>("");
  const [outputId, setOutputId] = useState<string>("");

  // Enumerate devices (labels only populate after a getUserMedia grant, so we start the mic first).
  const refreshDevices = useCallback(async () => {
    try {
      const devs = await navigator.mediaDevices.enumerateDevices();
      setInputs(devs.filter((d) => d.kind === "audioinput"));
      setOutputs(devs.filter((d) => d.kind === "audiooutput"));
    } catch {
      /* enumeration unsupported — leave lists empty, pickers just won't render */
    }
  }, []);

  useEffect(() => {
    // Kick off the default mic on mount; that grant unlocks device labels.
    start().then(refreshDevices);
    navigator.mediaDevices.addEventListener?.("devicechange", refreshDevices);
    return () => {
      navigator.mediaDevices.removeEventListener?.("devicechange", refreshDevices);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function onPickInput(id: string) {
    setInputId(id);
    start(id || undefined); // restart the meter on the newly chosen device
  }

  // Speaker test: play a short 440Hz tone through the chosen output device via Web Audio (no
  // asset/network needed). AudioContext.setSinkId (Chromium 110+) routes to the picked speaker;
  // where unsupported it plays on the system default.
  async function testSpeaker() {
    try {
      const Ctx =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      const ctx = new Ctx();
      const sinkable = ctx as AudioContext & { setSinkId?: (id: string) => Promise<void> };
      if (outputId && sinkable.setSinkId) {
        await sinkable.setSinkId(outputId).catch(() => {});
      }
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.frequency.value = 440;
      gain.gain.value = 0.18; // gentle volume
      osc.connect(gain).connect(ctx.destination);
      osc.start();
      // ~0.4s tone, then tidy up.
      window.setTimeout(() => {
        osc.stop();
        ctx.close().catch(() => {});
      }, 400);
    } catch {
      /* Web Audio unavailable — skip the test silently */
    }
  }

  const micWorking = peak >= 8; // peak comfortably above the idle floor => a real signal was seen
  // The orb glows "live" once the meter is reacting to real input (prototype S5b look).
  const orbLive = state === "live" && level > 5;

  return (
    <section className="mic-stage">
      {/* Animated mic orb + clean level meter (prototype S5b) */}
      <div className={"mic-orb" + (orbLive ? " live" : "")}>
        <span className="ring" />
        <span className="ico">🎙️</span>
      </div>
      <div className="level" aria-hidden>
        <i
          style={{
            width: `${level}%`,
            background: level > 5 ? "var(--good)" : "var(--ink-faint)",
          }}
        />
      </div>
      <p className="hint" aria-live="polite" style={{ marginTop: 4 }}>
        {state === "starting" && "Starting microphone…"}
        {state === "denied" &&
          "Microphone blocked. Allow mic access in the address-bar permission, then reload."}
        {state === "error" && "Could not open the microphone. Try a different device."}
        {state === "live" &&
          (micWorking
            ? "Microphone is working — we can hear you. 🎉"
            : "Speak now — the bar should move. If it stays flat, pick another microphone below.")}
      </p>

      {/* Device pickers + speaker test, presented as secondary controls under the orb. */}
      <div style={{ width: "100%", textAlign: "left", marginTop: 14 }}>
        {inputs.length > 0 && (
          <label style={label}>
            Microphone
            <select style={select} value={inputId} onChange={(e) => onPickInput(e.target.value)}>
              <option value="">Default microphone</option>
              {inputs.map((d) => (
                <option key={d.deviceId} value={d.deviceId}>
                  {d.label || `Microphone ${d.deviceId.slice(0, 6)}`}
                </option>
              ))}
            </select>
          </label>
        )}
        {outputs.length > 0 && (
          <label style={label}>
            Speaker
            <select style={select} value={outputId} onChange={(e) => setOutputId(e.target.value)}>
              <option value="">Default speaker</option>
              {outputs.map((d) => (
                <option key={d.deviceId} value={d.deviceId}>
                  {d.label || `Speaker ${d.deviceId.slice(0, 6)}`}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", marginTop: "0.75rem", justifyContent: "center" }}>
        <button type="button" onClick={testSpeaker} className="btn ghost sm">
          Test speaker
        </button>
        <button
          type="button"
          onClick={() => {
            stop();
            onReady({ inputDeviceId: inputId || undefined, outputDeviceId: outputId || undefined });
          }}
          className="btn accent"
        >
          {readyLabel}
        </button>
      </div>
      {!micWorking && state === "live" && (
        <p className="hint" style={{ color: "#9a5a1c", marginBottom: 0 }}>
          You can still start, but the coach won&apos;t hear you until the bar moves when you speak.
        </p>
      )}
    </section>
  );
}

const label: React.CSSProperties = { display: "block", margin: "0.5rem 0", fontSize: 13, fontWeight: 600, color: "var(--ink)" };
const select: React.CSSProperties = {
  display: "block",
  width: "100%",
  padding: "10px 12px",
  marginTop: "0.4rem",
  fontSize: 14,
  fontFamily: "var(--font)",
  border: "1px solid var(--line)",
  borderRadius: 10,
  background: "var(--surface-2)",
  boxSizing: "border-box",
};
