import { Card, CardHeader, CardBody } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { ChatConversation } from "@/components/chat/ChatConversation";
import { useConnectChat, type UiMessage } from "@/connect/useConnectChat";
import { addComment } from "@/connect/casesApi";

interface SupportChatProps {
  /** The case this chat is bound to: it uses the case chat flow and the
   *  transcript is saved back to the case as a comment when the chat ends. */
  caseId: string;
  /** Called after the transcript comment is posted (so the case view can refresh). */
  onTranscriptSaved?: () => void;
}

function formatTranscript(msgs: UiMessage[]): string {
  const lines = msgs.map((m) => {
    const who = m.role === "customer" ? "Merchant" : m.name || "Agent";
    const t = m.time ? new Date(m.time).toLocaleString() : "";
    return `${t ? `[${t}] ` : ""}${who}: ${m.text}`;
  });
  return `Live chat transcript\n${lines.join("\n")}`;
}

export default function SupportChat({ caseId, onTranscriptSaved }: SupportChatProps) {
  // Case-bound chat: when it ends, append the transcript to the case as a
  // comment (tenant-isolated server-side via the JWT).
  const chat = useConnectChat({
    caseId,
    onEnded: async (msgs) => {
      await addComment(caseId, formatTranscript(msgs));
      onTranscriptSaved?.();
    },
  });

  return (
    <Card className="flex h-full flex-col">
      <CardHeader
        title="Live chat"
        subtitle="Linked to this case — the transcript is saved to the case when you end."
        action={
          chat.active ? (
            <Button size="sm" variant="secondary" onClick={() => chat.end()}>
              End chat
            </Button>
          ) : undefined
        }
      />
      <CardBody className="flex min-h-[320px] flex-1 flex-col">
        <ChatConversation
          chat={chat}
          idlePrompt="Start a live chat with support about this case."
          endedText="Chat ended. The transcript was saved to this case."
        />
      </CardBody>
    </Card>
  );
}
