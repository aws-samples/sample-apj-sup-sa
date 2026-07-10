import { useConversation, type AgentState } from "../store/conversation";

const LABEL: Record<AgentState, string> = {
  idle: "Tap to talk",
  listening: "Listening…",
  thinking: "Thinking…",
  speaking: "Speaking…",
};

/**
 * The hero. A layered orb whose scale/glow react to live audio level and whose
 * palette reflects the derived agent state. Pure CSS + inline transforms — no
 * canvas — so it stays crisp on a projector.
 */
export function VoiceOrb() {
  const agentState = useConversation((s) => s.agentState);
  const connection = useConversation((s) => s.connection);
  const micLevel = useConversation((s) => s.micLevel);
  const agentLevel = useConversation((s) => s.agentAudioLevel);

  const active = connection === "connected";
  const level = agentState === "speaking" ? agentLevel : micLevel;
  // Map 0..1 level → a gentle scale; idle breathes via CSS animation.
  const scale = active ? 1 + Math.min(level, 1) * 0.18 : 1;

  return (
    <div className={`orb-wrap state-${agentState} ${active ? "live" : "off"}`}>
      <div className="orb-rings" />
      <div
        className="orb-core"
        style={{ transform: `scale(${scale.toFixed(3)})` }}
      >
        <div className="orb-sheen" />
      </div>
      <div className="orb-label">{active ? LABEL[agentState] : "Aisle"}</div>
    </div>
  );
}
