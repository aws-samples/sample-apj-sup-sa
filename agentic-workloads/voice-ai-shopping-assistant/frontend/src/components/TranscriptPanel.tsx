import { useEffect, useRef } from "react";
import { useConversation } from "../store/conversation";

/** Rolling transcript under the orb. Subtle; the orb stays the focal point. */
export function TranscriptPanel() {
  const transcript = useConversation((s) => s.transcript);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [transcript]);

  if (transcript.length === 0) {
    return (
      <div className="transcript empty">
        Say “I need to buy milk” or “what pasta do you have?”
      </div>
    );
  }

  // Show the last few turns as captions so the avatar stays visible.
  const recent = transcript.slice(-3);
  return (
    <div className="transcript">
      {recent.map((t) => (
        <div key={t.id} className={`turn ${t.role} ${t.final ? "final" : "interim"}`}>
          <span className="who">{t.role === "agent" ? "Aisle" : "You"}</span>
          <span className="text">{t.text}</span>
        </div>
      ))}
      <div ref={endRef} />
    </div>
  );
}
