import { useEffect, useRef } from "react";
import { useConversation } from "../store/conversation";

/**
 * Renders the Tavus avatar. The stream carries BOTH the avatar's video and its
 * audio so the browser keeps them lip-synced (a separate <audio> element drifts
 * ahead of the video). We start muted so autoplay is allowed, then unmute — the
 * "Start talking" click earlier in the session grants the activation needed.
 */
export function AgentVideo() {
  const stream = useConversation((s) => s.agentVideoStream);
  const ref = useRef<HTMLVideoElement | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el || !stream) return;
    if (el.srcObject !== stream) el.srcObject = stream;
    el.muted = true;
    el.play().then(
      () => {
        el.muted = false; // unmute for synced avatar audio
      },
      () => {
        // Autoplay blocked: resume + unmute on the next user interaction.
        const resume = () => {
          el.muted = false;
          el.play().catch(() => {});
          document.removeEventListener("pointerdown", resume);
        };
        document.addEventListener("pointerdown", resume, { once: true });
      },
    );
  }, [stream]);

  if (!stream) return null;

  return (
    <div className="agent-video-wrap">
      <video ref={ref} autoPlay playsInline className="agent-video" />
    </div>
  );
}
