import { useEffect } from "react";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader } from "@/components/ui/Card";
import { useScreenShare } from "@/connect/useScreenShare";

const STATUS_TEXT: Record<string, string> = {
  idle: "Start a call with support and share your screen so the agent can see what you see.",
  starting: "Starting the call…",
  waiting: "Connecting you to a support agent…",
  connected: "You're connected to a support agent.",
  ended: "The call has ended.",
  error: "Something went wrong.",
};

/**
 * Merchant screen-share session (Amazon Connect web call + Chime content share).
 * Voice is on for the whole call; the screen is only shared after the merchant
 * chooses "Share my screen" and picks what to share in the browser dialog.
 */
export default function ScreenShareModal({ caseId, onClose }: { caseId?: string; onClose: () => void }) {
  const ss = useScreenShare();
  const live = ss.status === "waiting" || ss.status === "connected" || ss.status === "starting";

  // Leaving the modal leaves the call.
  useEffect(() => () => ss.hangUp(), []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink-900/40 p-4">
      <div className="w-full max-w-lg">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-xs font-medium text-white/90">
            Screen share{caseId ? ` · ${caseId}` : ""}
          </span>
          <Button variant="ghost" size="sm" className="text-white" onClick={onClose}>
            Close
          </Button>
        </div>
        <Card>
          <CardHeader
            title="Share your screen with support"
            subtitle={caseId ? "Linked to this case" : "Live call with a support agent"}
          />
          <CardBody className="space-y-4">
            <p className="text-sm text-ink-600" data-testid="screenshare-status">
              {STATUS_TEXT[ss.status]}
              {ss.sharing && " Your screen is being shared."}
            </p>
            {ss.error && <p className="text-sm text-red-600">{ss.error}</p>}
            {ss.contactId && (
              <p className="text-xs text-ink-400">
                Call reference: <span data-testid="screenshare-contact-id">{ss.contactId}</span>
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              {(ss.status === "idle" || ss.status === "ended" || ss.status === "error") && (
                <Button variant="primary" onClick={() => void ss.start(caseId)}>
                  {ss.status === "idle" ? "Start call" : "Call again"}
                </Button>
              )}
              {ss.status === "connected" && !ss.sharing && (
                <Button variant="primary" onClick={() => void ss.share()}>
                  Share my screen
                </Button>
              )}
              {ss.sharing && (
                <Button variant="secondary" onClick={ss.stopShare}>
                  Stop sharing
                </Button>
              )}
              {live && (
                <Button variant="ghost" onClick={ss.hangUp}>
                  Hang up
                </Button>
              )}
            </div>
            <p className="text-xs text-ink-400">
              Uses your microphone for the call. Only the screen, window or tab you pick is shared, and only
              until you stop sharing or hang up.
            </p>
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
